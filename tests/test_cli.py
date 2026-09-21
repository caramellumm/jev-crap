"""A linha de comando: principalmente os códigos de saída.

Os códigos são o contrato com o CI, e é a parte que ninguém percebe estar
quebrada — um pipeline que sai 0 por engano passa meses "avaliando".
"""

from __future__ import annotations

import pytest

from jev_crap.cli import principal


@pytest.fixture(autouse=True)
def sem_chave_no_ambiente(monkeypatch, tmp_path):
    """Isola do `.env` do repositório e da variável da máquina.

    Sem isto, a suíte passaria ou falharia conforme a máquina em que roda — e a
    versão que falha é a do CI de outra pessoa, seis meses depois.
    """
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


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
