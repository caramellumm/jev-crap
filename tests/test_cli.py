"""A linha de comando: principalmente os códigos de saída.

Os códigos são o contrato com o CI, e é a parte que ninguém percebe estar
quebrada — um pipeline que sai 0 por engano passa meses "avaliando".
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jev_crap.cli import (
    _argumentos,
    _diretorio_atual,
    _imprimir,
    _limiar,
    _linhas_da_funcao,
    _pct,
    _sem_chave,
    principal,
)
from jev_crap.julgamento.jev import obter_julgador


@pytest.fixture(autouse=True)
def sem_chave_no_ambiente(monkeypatch, tmp_path):
    """Isola do `.env` do repositório e da variável da máquina.

    Sem isto, a suíte passaria ou falharia conforme a máquina em que roda — e a
    versão que falha é a do CI de outra pessoa, seis meses depois.
    """
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


class TestSelecaoDoJulgador:
    """Qual julgador a CLI monta, e o que isso muda na saída.

    A CLI pergunta a `obter_julgador` uma vez, antes de medir qualquer coisa —
    é o que faz falta de chave sair como erro de configuração (3) em vez de
    aparecer no meio de uma batelada já paga.
    """

    def test_sem_chave_obter_julgador_devolve_o_desligado(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        assert obter_julgador(ambiente={}).ativo is False

    def test_com_chave_obter_julgador_devolve_o_ativo(self):
        assert obter_julgador(ambiente={"TYPESAFE_API_KEY": "sk-x"}).ativo is True

    def test_a_cli_recusa_rodar_com_o_julgador_desligado(self, projeto, capsys):
        """Sem isto, um pipeline sairia 1 e passaria meses achando que avalia."""
        assert principal([str(projeto / "src")]) == 3
        assert "TYPESAFE_API_KEY" in capsys.readouterr().err

    def test_a_cli_aceita_o_julgador_desligado_com_sem_julgamento(self, projeto):
        assert principal([str(projeto / "src"), "--sem-julgamento"]) in (0, 1)

    def test_a_cli_usa_o_julgador_que_obter_julgador_devolveu(self, monkeypatch, projeto):
        from tests.conftest import RESPOSTAS_BOAS

        from jev_crap import cli
        from jev_crap.julgamento.jev import JulgadorFake

        fake = JulgadorFake(RESPOSTAS_BOAS)
        monkeypatch.setattr(cli, "obter_julgador", lambda: fake)
        principal([str(projeto / "src"), "--limiar", "0", "--quieto"])
        assert fake.chamadas


class TestCodigosDeSaida:
    def test_sem_chave_e_sem_flag_sai_erro_de_configuracao(self, projeto, capsys):
        """O 3 existe separado de propósito: sem ele, falta de chave sairia como
        1 e um pipeline passaria meses achando que avalia."""
        assert principal([str(projeto / "src")]) == 3
        assert "TYPESAFE_API_KEY" in capsys.readouterr().err

    def test_a_mensagem_separa_sem_env_de_env_sem_a_chave(self, projeto, capsys, tmp_path):
        """Os dois enganos produzem a mesma tela em branco e pedem ações opostas."""
        principal([str(projeto / "src")])
        assert "nenhum .env encontrado" in capsys.readouterr().err

        (tmp_path / ".env").write_text("OUTRA_COISA=1\n", encoding="utf-8")
        principal([str(projeto / "src")])
        assert "não define a variável" in capsys.readouterr().err

    def test_sem_julgamento_roda_sem_chave_e_sai_um(self, projeto, capsys):
        """`sem_julgamento` não é aprovação: o que não foi julgado não foi aprovado."""
        assert principal([str(projeto / "src"), "--sem-julgamento"]) == 1
        assert "SEM_JULGAMENTO" in capsys.readouterr().out

    def test_caminho_inexistente_sai_erro_de_uso(self, capsys):
        assert principal(["/nao/existe/mesmo", "--sem-julgamento"]) == 3
        assert "caminho_inexistente" in capsys.readouterr().err

    def test_cobertura_ilegivel_sai_erro_de_uso(self, projeto, tmp_path, capsys):
        ruim = tmp_path / "r.json"
        ruim.write_text('{"nao": "e lcov"}', encoding="utf-8")
        codigo = principal([str(projeto / "src"), "--sem-julgamento", "--cobertura", str(ruim)])
        assert codigo == 3
        assert "cobertura_ilegivel" in capsys.readouterr().err


class TestSaida:
    def test_quieto_imprime_so_o_resultado(self, projeto, capsys):
        principal([str(projeto / "src"), "--sem-julgamento", "--quieto"])
        saida = capsys.readouterr().out.strip()
        assert saida.startswith("resultado:")
        assert len(saida.splitlines()) == 1

    def test_o_conselho_repetido_nao_polui_a_saida(self, projeto, capsys):
        """Sem julgamento, o conselho é o mesmo para todas as funções: repeti-lo
        por função esconde as linhas que de fato variam."""
        principal([str(projeto / "src"), "--sem-julgamento"])
        saida = capsys.readouterr().out
        assert saida.count("sem julgamento: com só o eixo contável") <= 1

    def test_json_grava_o_relatorio_inteiro(self, projeto, tmp_path, capsys):
        import json

        destino = tmp_path / "saida.json"
        principal([str(projeto / "src"), "--sem-julgamento", "--json", str(destino)])
        relatorio = json.loads(destino.read_text(encoding="utf-8"))
        assert relatorio["resumo"]["funcoes_medidas"] == 2
        assert "como_ler" in relatorio

    def test_json_em_caminho_impossivel_sai_erro_de_uso(self, projeto, capsys):
        codigo = principal(
            [str(projeto / "src"), "--sem-julgamento", "--json", "/nao/existe/x.json"]
        )
        assert codigo == 3

    def test_os_avisos_aparecem(self, projeto, capsys):
        principal([str(projeto / "src"), "--sem-julgamento"])
        assert "nenhum relatório de cobertura" in capsys.readouterr().out


class TestAjuda:
    def test_help_sai_zero_e_documenta_os_codigos(self, capsys):
        with pytest.raises(SystemExit) as saida:
            principal(["--help"])
        assert saida.value.code == 0
        texto = capsys.readouterr().out
        assert "0 aprovar, 1 revisar, 2 bloquear, 3 erro" in texto
        assert "jev-crap-mcp" in texto


class TestSaidaCompletaDeUmaFuncaoJulgada:
    """Exercita o caminho de impressão inteiro, com nota, gate e dúvida.

    Este bloco existe porque a própria ferramenta o apontou: rodando
    `jev-crap src --cobertura coverage.xml` sobre este repositório, `_imprimir`
    saiu com 6% de cobertura de linha e nota 30 — a função que desenha o
    relatório era a menos verificada do projeto. Ignorar o achado para publicar
    um número melhor é exatamente o atalho que a skill manda não tomar.
    """

    @pytest.fixture
    def com_julgamento(self, monkeypatch, projeto):
        from tests.conftest import RESPOSTAS_BOAS, noul, score

        from jev_crap import cli
        from jev_crap.julgamento.jev import JulgadorFake

        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_cognitiva": score(0.4, confianca=0.2),  # nota baixa e dispersa
            "caso_limite_nao_tratado": noul(0.9),                 # dúvida
        }
        monkeypatch.setattr(cli, "obter_julgador", lambda: JulgadorFake(respostas))
        return projeto

    def test_imprime_nota_faixa_conselho_e_duvidas(self, com_julgamento, capsys):
        codigo = principal(
            [str(com_julgamento / "src"), "--limiar", "0",
             "--cobertura", str(com_julgamento / "lcov.info"),
             "--testes", str(com_julgamento / "tests")]
        )
        saida = capsys.readouterr().out
        assert "julgado    " in saida          # a linha das dimensões
        assert "/100 (" in saida               # nota com a faixa junto
        assert "→ " in saida                   # o conselho
        assert "revisar por:" in saida         # as dúvidas
        assert "disperso" in saida             # confiança baixa aparece
        assert codigo == 1                     # revisar

    def test_gate_grave_aparece_como_barrado_e_sai_dois(self, monkeypatch, projeto, capsys):
        from tests.conftest import RESPOSTAS_BOAS, noul

        from jev_crap import cli
        from jev_crap.julgamento.jev import JulgadorFake

        monkeypatch.setattr(
            cli,
            "obter_julgador",
            lambda: JulgadorFake({**RESPOSTAS_BOAS, "injecao": noul(0.97)}),
        )
        codigo = principal([str(projeto / "src"), "--limiar", "0"])
        saida = capsys.readouterr().out
        assert "barrado por:" in saida
        assert "BLOQUEAR" in saida
        assert codigo == 2

    def test_cobertura_sem_dados_aparece_como_nd_e_nao_como_zero(
        self, com_julgamento, capsys
    ):
        """0% diria "nada coberto"; n/d diz "o relatório não alcançou"."""
        principal([str(com_julgamento / "src"), "--limiar", "0"])
        assert "linha n/d" in capsys.readouterr().out

    def test_o_custo_em_tokens_e_reportado(self, monkeypatch, projeto, capsys):
        """O `usage` vinha na resposta e era descartado; sem ele não há como
        responder "quanto custou esta varredura" senão por estimativa."""
        from tests.conftest import RESPOSTAS_BOAS

        from jev_crap import cli
        from jev_crap.julgamento.jev import JulgadorFake

        monkeypatch.setattr(
            cli,
            "obter_julgador",
            lambda: JulgadorFake(RESPOSTAS_BOAS, usage={"input_tokens": 900, "output_tokens": 80}),
        )
        principal([str(projeto / "src"), "--limiar", "0"])
        saida = capsys.readouterr().out
        assert "custo:" in saida
        assert "1800 tokens de entrada" in saida  # duas funções × 900


class TestPct:
    """`_pct` é a última coisa entre um sentinela e a tela de quem lê."""

    def test_pct_formata_fracao_como_porcentagem(self):
        assert _pct(0.2) == "20%"

    def test_pct_arredonda_para_inteiro(self):
        assert _pct(0.156) == "16%"

    def test_pct_devolve_nd_para_none(self):
        assert _pct(None) == "n/d"

    def test_pct_devolve_nd_para_a_sentinela_sem_dados(self):
        assert _pct(-1.0) == "n/d"

    def test_pct_devolve_nd_para_nan(self):
        assert _pct(float("nan")) == "n/d"

    def test_pct_devolve_nd_para_texto(self):
        assert _pct("80%") == "n/d"

    def test_pct_devolve_nd_para_booleano(self):
        assert _pct(True) == "n/d"

    def test_pct_nao_esconde_valor_acima_de_cem_por_cento(self):
        assert _pct(1.4) == "140%"

    def test_pct_aceita_zero(self):
        assert _pct(0.0) == "0%"


class TestLinhasDaFuncao:
    def test_linhas_da_funcao_desempacota_o_par(self):
        assert _linhas_da_funcao({"linhas": [3, 9]}) == (3, 9)

    def test_linhas_da_funcao_aceita_tupla(self):
        assert _linhas_da_funcao({"linhas": (1, 2)}) == (1, 2)

    def test_linhas_da_funcao_marca_ausencia(self):
        assert _linhas_da_funcao({}) == ("?", "?")

    def test_linhas_da_funcao_marca_tamanho_errado(self):
        assert _linhas_da_funcao({"linhas": [1, 2, 3]}) == ("?", "?")

    def test_linhas_da_funcao_marca_tipo_errado(self):
        assert _linhas_da_funcao({"linhas": "3-9"}) == ("?", "?")


class TestImprimirTolerante:
    """Uma chave faltando não pode levar junto o relatório inteiro."""

    def test_imprimir_nao_levanta_com_dicionario_vazio(self, capsys):
        _imprimir({})
        assert capsys.readouterr().out

    def test_imprimir_marca_campos_ausentes(self, capsys):
        _imprimir({})
        assert "?" in capsys.readouterr().out

    def test_imprimir_usa_sem_julgamento_como_veredito_padrao(self, capsys):
        _imprimir({})
        assert "SEM_JULGAMENTO" in capsys.readouterr().out

    def test_imprimir_omite_conselho_quando_nao_houve_julgamento(self, capsys):
        _imprimir({"veredito": "sem_julgamento", "conselho": "não deveria aparecer"})
        assert "não deveria aparecer" not in capsys.readouterr().out

    def test_imprimir_mostra_barrado_por_quando_ha_grave(self, capsys):
        _imprimir({"graves": ["injecao"]})
        assert "barrado por: injecao" in capsys.readouterr().out

    def test_imprimir_mostra_sem_nota_quando_nao_ha_nota(self, capsys):
        _imprimir({"nota": None})
        assert "sem nota" in capsys.readouterr().out

    def test_imprimir_nao_levanta_com_nota_sentinela(self, capsys):
        _imprimir({"notas": {"teste_verifica": -1.0}})
        assert "teste n/d" in capsys.readouterr().out


class TestSemChave:
    def test_sem_chave_diz_qual_env_foi_lido(self, tmp_path):
        assert "nao define a variável" in _sem_chave(tmp_path / ".env").replace("ã", "a")

    def test_sem_chave_diz_quando_nao_houve_env(self):
        assert "nenhum .env" in _sem_chave(None)

    def test_sem_chave_sempre_oferece_a_saida_sem_julgamento(self):
        assert "--sem-julgamento" in _sem_chave(None)

    def test_sem_chave_nomeia_a_variavel(self):
        assert "TYPESAFE_API_KEY" in _sem_chave(None)

    def test_sem_chave_nao_levanta_sem_diretorio_de_trabalho(self, monkeypatch):
        def explode():
            raise FileNotFoundError("workspace apagado")

        monkeypatch.setattr(Path, "cwd", explode)
        assert "indisponível" in _sem_chave(None)


class TestDiretorioAtual:
    def test_diretorio_atual_devolve_o_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _diretorio_atual() == str(Path.cwd())

    def test_diretorio_atual_marca_quando_o_cwd_sumiu(self, monkeypatch):
        def explode():
            raise OSError("sumiu")

        monkeypatch.setattr(Path, "cwd", explode)
        assert "indisponível" in _diretorio_atual()


class TestLimiar:
    def test_limiar_aceita_numero_positivo(self):
        assert _limiar("12.5") == 12.5

    def test_limiar_aceita_zero(self):
        assert _limiar("0") == 0.0

    def test_limiar_recusa_negativo(self):
        with pytest.raises(argparse.ArgumentTypeError, match="não pode ser negativo"):
            _limiar("-1")

    def test_limiar_recusa_nan(self):
        with pytest.raises(argparse.ArgumentTypeError, match="nan"):
            _limiar("nan")

    def test_limiar_recusa_texto(self):
        with pytest.raises(argparse.ArgumentTypeError, match="precisa ser um número"):
            _limiar("alto")


class TestArgumentos:
    def test_argumentos_recolhe_varios_alvos(self):
        assert _argumentos(["src", "lib"]).alvos == ["src", "lib"]

    def test_argumentos_aceita_nenhum_alvo_para_diagnostico(self):
        assert _argumentos(["--diagnostico"]).alvos == []

    def test_argumentos_guarda_o_limiar_conferido(self):
        assert _argumentos(["src", "--limiar", "30"]).limiar == 30.0

    def test_argumentos_recusa_limiar_negativo_saindo_dois(self):
        with pytest.raises(SystemExit) as saida:
            _argumentos(["src", "--limiar", "-3"])
        assert saida.value.code == 2

    def test_argumentos_tem_sem_julgamento_desligado_por_padrao(self):
        assert _argumentos(["src"]).sem_julgamento is False

    def test_argumentos_liga_sem_julgamento_com_a_flag(self):
        assert _argumentos(["src", "--sem-julgamento"]).sem_julgamento is True


class TestDiagnosticoNaCli:
    def test_diagnostico_sai_zero_sem_alvo(self, capsys):
        assert principal(["--diagnostico"]) == 0
        assert "jev_crap:" in capsys.readouterr().out

    def test_diagnostico_nao_exige_chave(self, capsys):
        assert principal(["--diagnostico"]) == 0

    def test_sem_alvo_e_sem_diagnostico_sai_erro_de_uso(self, capsys):
        assert principal([]) == 3
        assert "ALVO" in capsys.readouterr().err
