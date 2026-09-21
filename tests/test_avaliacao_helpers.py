"""Os auxiliares de `jev_crap.avaliacao`, exercitados um a um.

Eles se dividem em três grupos, e o que os une é serem invisíveis quando erram:
o cruzamento de caminhos atribui cobertura ao arquivo errado, a formatação
publica sentinela como número, e o resumo conta funções que não foram julgadas.
Nenhum desses erros aparece como erro — todos aparecem como relatório.
"""

from __future__ import annotations

import logging

import pytest
from tests.conftest import RESPOSTAS_BOAS, noul, score

from jev_crap.avaliacao import (
    FAIXAS,
    ORDEM_DOS_VEREDITOS,
    SEM_CONTAGEM,
    FuncaoMedida,
    Medicao,
    _avisos_da_medicao,
    _avisos_de_cruzamento,
    _casar_arquivo,
    _cobertura_da_funcao,
    _componentes,
    _conselho,
    _Cruzamento,
    _escolher_funcao,
    _estado_do_eixo,
    _estimar_custo,
    _gates,
    _insumos_da_funcao,
    _interpretar,
    _julgar_lote,
    _julgar_uma,
    _medir_uma,
    _nota_ponderada,
    _ou_nulo,
    _pior_veredito,
    _prioridade,
    _resumo,
    _somar_tokens,
    _sufixo_comum,
    _trecho_do_arquivo,
    decidir,
    faixa_da_nota,
    ler_cobertura,
    relatorio_contavel,
)
from jev_crap.config import Config
from jev_crap.julgamento.jev import JulgadorDesligado, JulgadorFake
from jev_crap.metrica.cobertura import SEM_DADOS, CoberturaArquivo
from jev_crap.metrica.risco import obter_formula
from jev_crap.situacoes import SituacaoConhecida


def medida(**ajustes) -> FuncaoMedida:
    campos = dict(
        arquivo="src/a.py", nome="f", linha_inicio=1, linha_fim=10, complexidade=3,
        linhas_logicas=6, linguagem="python", cobertura_linha=0.5,
        cobertura_branch=0.4, risco=12.0,
    )
    return FuncaoMedida(**{**campos, **ajustes})


def cobertura(arquivo: str) -> CoberturaArquivo:
    return CoberturaArquivo(
        arquivo=arquivo, linhas_cobertas={1}, linhas_totais={1, 2},
        branches_cobertos=SEM_DADOS, branches_totais=SEM_DADOS,
    )


class TestFaixaDaNota:
    """A faixa é a unidade real da resposta; o decimal é ruído com cara de precisão."""

    def test_faixa_da_nota_chama_oitenta_de_solido(self):
        assert faixa_da_nota(80.0) == "sólido"

    def test_faixa_da_nota_chama_setenta_de_aceitavel(self):
        assert faixa_da_nota(70.0) == "aceitável"

    def test_faixa_da_nota_chama_cinquenta_de_fragil(self):
        assert faixa_da_nota(50.0) == "frágil"

    def test_faixa_da_nota_chama_dez_de_ruim(self):
        assert faixa_da_nota(10.0) == "ruim"

    def test_faixa_da_nota_sem_nota_nao_e_ruim(self):
        """Não julgada e julgada mal são coisas diferentes."""
        assert faixa_da_nota(None) == "sem nota"

    def test_faixa_da_nota_trata_as_pontas_como_a_faixa_de_cima(self):
        assert faixa_da_nota(60.0) == "aceitável"

    def test_faixa_da_nota_cobre_toda_a_escala(self):
        for nota in (0.0, 39.9, 40.0, 59.9, 60.0, 79.9, 80.0, 100.0):
            assert faixa_da_nota(nota) in {n for _, n in FAIXAS}


class TestIdentificador:
    """A chave identifica a função no relatório, nas falhas e no histórico."""

    def test_identificador_junta_arquivo_e_linha(self):
        assert medida().identificador == "src/a.py:1"

    def test_identificador_distingue_homonimas_no_mesmo_arquivo(self):
        assert medida(linha_inicio=1).identificador != medida(linha_inicio=40).identificador

    def test_identificador_recusa_arquivo_vazio(self):
        with pytest.raises(ValueError, match="sem arquivo"):
            _ = medida(arquivo="").identificador

    def test_identificador_recusa_linha_zero(self):
        with pytest.raises(ValueError, match="linhas começam em 1"):
            _ = medida(linha_inicio=0).identificador

    def test_identificador_cita_o_nome_da_funcao_no_erro(self):
        with pytest.raises(ValueError, match="orfa"):
            _ = medida(arquivo="  ", nome="orfa").identificador


class TestTamanho:
    """Tamanho vira gate; negativo passaria por qualquer comparação `>`."""

    def test_tamanho_conta_as_pontas(self):
        assert medida(linha_inicio=3, linha_fim=5).tamanho == 3

    def test_tamanho_de_uma_linha_e_um(self):
        assert medida(linha_inicio=7, linha_fim=7).tamanho == 1

    def test_tamanho_recusa_faixa_invertida(self):
        with pytest.raises(ValueError, match="faixa da função está invertida"):
            _ = medida(linha_inicio=10, linha_fim=3).tamanho

    def test_tamanho_nunca_e_negativo(self):
        assert medida(linha_inicio=1, linha_fim=1).tamanho > 0


class TestOuNulo:
    """`-1.0` numa coluna de porcentagem vira conclusão errada com cara de dado."""

    def test_ou_nulo_traduz_a_sentinela(self):
        assert _ou_nulo(float(SEM_DADOS)) is None

    def test_ou_nulo_preserva_valor_de_verdade(self):
        assert _ou_nulo(0.5) == 0.5

    def test_ou_nulo_preserva_zero(self):
        """Zero é dado: nada coberto. Só a sentinela é ausência."""
        assert _ou_nulo(0.0) == 0.0

    def test_ou_nulo_arredonda_para_quatro_casas(self):
        assert _ou_nulo(0.123456789) == 0.1235


class TestAcimaDoLimiar:
    """O recorte que mantém o custo baixo: medir é grátis, julgar não."""

    def medicao(self, limiar: float) -> Medicao:
        return Medicao(
            funcoes=(medida(risco=5.0), medida(risco=40.0)),
            limiar=limiar,
            formula=obter_formula(),
        )

    def test_acima_do_limiar_separa_pelo_risco(self):
        assert len(self.medicao(30.0).acima_do_limiar) == 1

    def test_acima_do_limiar_inclui_quem_esta_exatamente_nele(self):
        assert len(self.medicao(40.0).acima_do_limiar) == 1

    def test_acima_do_limiar_com_zero_pega_todas(self):
        assert len(self.medicao(0.0).acima_do_limiar) == 2

    def test_acima_do_limiar_recusa_limiar_nan(self):
        """nan faz toda comparação dar falso: a varredura sairia vazia sem erro."""
        with pytest.raises(ValueError, match="não é finito"):
            _ = self.medicao(float("nan")).acima_do_limiar

    def test_acima_do_limiar_recusa_limiar_infinito(self):
        with pytest.raises(ValueError, match="não é finito"):
            _ = self.medicao(float("inf")).acima_do_limiar


class TestComponentes:
    def test_componentes_quebra_o_caminho(self):
        assert _componentes("src/app/a.py") == ("src", "app", "a.py")

    def test_componentes_normaliza_barra_do_windows(self):
        """Sem a troca, `src\\a.py` seria um componente só e não casaria."""
        assert _componentes("src\\app\\a.py") == ("src", "app", "a.py")

    def test_componentes_descarta_a_raiz(self):
        assert _componentes("/src/a.py") == ("src", "a.py")

    def test_componentes_de_vazio_e_vazio(self):
        assert _componentes("") == ()

    def test_componentes_de_um_nome_simples(self):
        assert _componentes("a.py") == ("a.py",)


class TestSufixoComum:
    def test_sufixo_comum_conta_componentes_iguais_no_fim(self):
        assert _sufixo_comum("/build/src/a.py", "src/a.py") == 2

    def test_sufixo_comum_nao_casa_por_texto(self):
        """Por texto puro `cobertura.py` é sufixo de `xcobertura.py`."""
        assert _sufixo_comum("src/xcobertura.py", "src/cobertura.py") == 0

    def test_sufixo_comum_de_caminhos_iguais_conta_tudo(self):
        assert _sufixo_comum("src/a.py", "src/a.py") == 2

    def test_sufixo_comum_sem_nada_em_comum_e_zero(self):
        assert _sufixo_comum("lib/b.py", "src/a.py") == 0

    def test_sufixo_comum_com_caminho_vazio_e_zero(self):
        assert _sufixo_comum("", "src/a.py") == 0

    def test_sufixo_comum_nao_levanta_com_os_dois_vazios(self):
        assert _sufixo_comum("", "") == 0


class TestCasarArquivo:
    """Chute silencioso aqui vira cobertura atribuída ao arquivo errado."""

    def test_casar_arquivo_acha_pelo_sufixo(self):
        relatorio = {"a": cobertura("/build/src/a.py")}
        achado, empate = _casar_arquivo("src/a.py", relatorio)
        assert achado.arquivo == "/build/src/a.py"
        assert empate is False

    def test_casar_arquivo_prefere_o_maior_sufixo_comum(self):
        relatorio = {
            "a": cobertura("/build/outro/a.py"),
            "b": cobertura("/build/src/app/a.py"),
        }
        achado, _ = _casar_arquivo("src/app/a.py", relatorio)
        assert achado.arquivo == "/build/src/app/a.py"

    def test_casar_arquivo_marca_empate_em_vez_de_chutar(self):
        relatorio = {"a": cobertura("x/a.py"), "b": cobertura("y/a.py")}
        _, empate = _casar_arquivo("src/a.py", relatorio)
        assert empate is True

    def test_casar_arquivo_sem_candidato_devolve_none(self):
        assert _casar_arquivo("src/a.py", {"b": cobertura("lib/b.py")}) == (None, False)

    def test_casar_arquivo_com_relatorio_vazio_devolve_none(self):
        assert _casar_arquivo("src/a.py", {}) == (None, False)

    def test_casar_arquivo_nao_marca_empate_com_um_candidato_so(self):
        relatorio = {"a": cobertura("/build/src/a.py")}
        assert _casar_arquivo("src/a.py", relatorio)[1] is False


class TestPiorVeredito:
    """Este valor é o exit code que o CI lê."""

    class Fingida:
        def __init__(self, veredito: str) -> None:
            self.veredito = veredito

    def test_pior_veredito_de_nada_e_aprovar(self):
        """Nada acima do limiar é resultado completo, não ausência de resposta."""
        assert _pior_veredito([]) == "aprovar"

    def test_pior_veredito_escolhe_o_mais_grave(self):
        avaliadas = [self.Fingida("aprovar"), self.Fingida("bloquear")]
        assert _pior_veredito(avaliadas) == "bloquear"

    def test_pior_veredito_trata_sem_julgamento_como_pior_que_aprovar(self):
        avaliadas = [self.Fingida("aprovar"), self.Fingida("sem_julgamento")]
        assert _pior_veredito(avaliadas) == "sem_julgamento"

    def test_pior_veredito_trata_revisar_como_pior_que_sem_julgamento(self):
        avaliadas = [self.Fingida("sem_julgamento"), self.Fingida("revisar")]
        assert _pior_veredito(avaliadas) == "revisar"

    def test_pior_veredito_desconhecido_vira_revisar(self):
        """Nem levantar (esconde o relatório) nem aprovar (sinal verde falso)."""
        assert _pior_veredito([self.Fingida("inventado")]) == "revisar"

    def test_pior_veredito_registra_o_desconhecido_no_log(self, caplog):
        with caplog.at_level(logging.WARNING, logger="jev_crap.avaliacao"):
            _pior_veredito([self.Fingida("inventado")])
        assert "veredito desconhecido" in caplog.text

    def test_pior_veredito_conhece_todos_os_vereditos_declarados(self):
        for veredito in ORDEM_DOS_VEREDITOS:
            assert _pior_veredito([self.Fingida(veredito)]) == veredito


class TestEstimarCusto:
    """A estimativa acompanha um relatório que já custou tempo: não pode derrubá-lo."""

    def test_estimar_custo_conta_as_chamadas(self):
        assert _estimar_custo([medida(), medida()], Config())["chamadas"] == 2

    def test_estimar_custo_estima_tokens_pelo_tamanho(self):
        com_codigo = medida(codigo="x" * 400)
        assert _estimar_custo([com_codigo], Config())["tokens_de_entrada_estimados"] == 100

    def test_estimar_custo_soma_os_trechos_de_teste(self):
        com_teste = medida(codigo="", testes=("y" * 400,))
        assert _estimar_custo([com_teste], Config())["tokens_de_entrada_estimados"] == 100

    def test_estimar_custo_declara_a_base_da_estimativa(self):
        """Número sem a ressalva parece autoridade."""
        assert "ordem de grandeza" in _estimar_custo([medida()], Config())["base_da_estimativa"]

    def test_estimar_custo_omite_preco_sem_configuracao(self):
        assert "custo" not in _estimar_custo([medida()], Config())

    def test_estimar_custo_inclui_preco_quando_configurado(self):
        config = Config(custo_por_julgamento=0.01, moeda="BRL")
        assert _estimar_custo([medida(), medida()], config)["custo"] == 0.02

    def test_estimar_custo_de_nada_e_zero(self):
        assert _estimar_custo([], Config())["chamadas"] == 0

    def test_estimar_custo_nao_levanta_com_codigo_vazio(self):
        assert _estimar_custo([medida(codigo="", testes=())], Config())


class TestParaMedicao:
    """A ausência sai como `null`, nunca como `-1.0`."""

    def test_para_medicao_traz_os_fatos_contaveis(self):
        corpo = medida().para_medicao()
        assert corpo["complexidade"] == 3
        assert corpo["risco"] == 12.0
        assert corpo["linhas_logicas"] == 6
        assert corpo["linguagem"] == "python"
        assert corpo["tamanho"] == 10
        assert corpo["cobertura_linha"] == 0.5
        assert corpo["cobertura_branch"] == 0.4

    def test_para_medicao_traduz_cobertura_ausente_em_null(self):
        corpo = medida(
            cobertura_linha=float(SEM_DADOS), cobertura_branch=float(SEM_DADOS)
        ).para_medicao()
        assert corpo["cobertura_linha"] is None
        assert corpo["cobertura_branch"] is None
        assert corpo["risco"] == 12.0

    def test_para_medicao_conta_os_trechos_em_vez_de_repeti_los(self):
        corpo = medida(testes=("def test_x(): ...", "def test_y(): ...")).para_medicao()
        assert corpo["trechos_de_teste"] == 2
        assert "def test_x" not in str(corpo)
        assert corpo["chave"] == "src/a.py:1"

    def test_para_medicao_traz_a_chave_e_a_faixa_de_linhas(self):
        corpo = medida().para_medicao()
        assert corpo["chave"] == "src/a.py:1"
        assert corpo["linhas"] == [1, 10]

    def test_para_medicao_e_serializavel(self):
        import json

        assert json.dumps(medida().para_medicao())


class TestLerCobertura:
    """As falhas previsíveis do relatório viram situação com nome e instrução."""

    def test_ler_cobertura_le_um_relatorio(self, tmp_path):
        alvo = tmp_path / "lcov.info"
        alvo.write_text("SF:src/a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
        assert set(ler_cobertura(str(alvo), tmp_path)) == {"src/a.py"}

    def test_ler_cobertura_traduz_arquivo_inexistente(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="cobertura_inexistente"):
            ler_cobertura(str(tmp_path / "nao_existe.info"), tmp_path)

    def test_a_situacao_de_inexistente_ensina_a_gerar(self, tmp_path):
        with pytest.raises(SituacaoConhecida) as erro:
            ler_cobertura(str(tmp_path / "nao_existe.info"), tmp_path)
        assert "--cov" in erro.value.como_resolver

    def test_ler_cobertura_traduz_formato_desconhecido(self, tmp_path):
        alvo = tmp_path / "leia.txt"
        alvo.write_text("um texto qualquer", encoding="utf-8")
        with pytest.raises(SituacaoConhecida, match="cobertura_ilegivel"):
            ler_cobertura(str(alvo), tmp_path)

    def test_ler_cobertura_traduz_diretorio_no_lugar_de_arquivo(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="cobertura_ilegivel"):
            ler_cobertura(str(tmp_path), tmp_path)


class TestTrechoDoArquivo:
    """Lê do disco uma vez por arquivo; falha de leitura vira trecho vazio."""

    def test_trecho_do_arquivo_recorta_as_linhas_pedidas(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("um\ndois\ntres\nquatro\n", encoding="utf-8")
        assert _trecho_do_arquivo(str(alvo), 2, 3, {}) == "dois\ntres"

    def test_trecho_do_arquivo_inclui_as_pontas(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("um\ndois\n", encoding="utf-8")
        assert _trecho_do_arquivo(str(alvo), 1, 2, {}) == "um\ndois"

    def test_trecho_do_arquivo_reaproveita_o_cache(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("um\ndois\n", encoding="utf-8")
        cache: dict[str, list[str]] = {}
        _trecho_do_arquivo(str(alvo), 1, 1, cache)
        alvo.unlink()
        assert _trecho_do_arquivo(str(alvo), 2, 2, cache) == "dois"

    def test_trecho_do_arquivo_inexistente_e_vazio(self, tmp_path):
        assert _trecho_do_arquivo(str(tmp_path / "nao_existe.py"), 1, 2, {}) == ""

    def test_trecho_do_arquivo_fora_da_faixa_e_vazio(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("um\n", encoding="utf-8")
        assert _trecho_do_arquivo(str(alvo), 50, 60, {}) == ""


class TestAvisosDeCruzamento:
    """Aviso que fica calado vira relatório em vermelho lido como projeto ruim."""

    def test_avisos_de_cruzamento_sem_relatorio_nao_avisa_nada(self):
        assert _avisos_de_cruzamento({}, {"src/a.py"}, set(), set(), 0) == []

    def test_avisos_de_cruzamento_avisa_quando_nenhum_arquivo_casou(self):
        relatorio = {"a": cobertura("/build/a.py")}
        avisos = _avisos_de_cruzamento(relatorio, {"src/a.py"}, {"src/a.py"}, set(), 0)
        assert any("nenhum arquivo analisado casou" in a for a in avisos)

    def test_o_aviso_de_nenhum_casou_aponta_a_raiz_e_nao_a_falta_de_teste(self):
        relatorio = {"a": cobertura("/build/a.py")}
        avisos = _avisos_de_cruzamento(relatorio, {"src/a.py"}, {"src/a.py"}, set(), 0)
        assert "diferença de raiz" in avisos[0]

    def test_avisos_de_cruzamento_lista_exemplos_dos_nao_casados(self):
        relatorio = {"a": cobertura("/build/a.py")}
        avisos = _avisos_de_cruzamento(relatorio, {"x", "y", "z"}, {"y"}, set(), 0)
        assert any("y" in a for a in avisos)

    def test_avisos_de_cruzamento_avisa_sobre_ambiguidade(self):
        relatorio = {"a": cobertura("/build/a.py")}
        avisos = _avisos_de_cruzamento(relatorio, {"x"}, set(), {"x"}, 0)
        assert any("mais de uma entrada" in a for a in avisos)

    def test_avisos_de_cruzamento_avisa_sobre_falta_de_branch(self):
        relatorio = {"a": cobertura("/build/a.py")}
        avisos = _avisos_de_cruzamento(relatorio, {"x"}, set(), set(), 3)
        assert any("--cov-branch" in a for a in avisos)

    def test_avisos_de_cruzamento_cala_quando_esta_tudo_certo(self):
        relatorio = {"a": cobertura("/build/a.py")}
        assert _avisos_de_cruzamento(relatorio, {"x"}, set(), set(), 0) == []


class TestEstadoDoEixo:
    def test_estado_do_eixo_ligado_com_julgador_ativo(self, rubrica):
        estado = _estado_do_eixo(JulgadorFake(), True, 3)
        assert estado["ligado"] is True

    def test_estado_do_eixo_desligado_traz_o_motivo(self):
        estado = _estado_do_eixo(JulgadorDesligado(), True, 0)
        assert estado["ligado"] is False
        assert estado["motivo"]

    def test_estado_do_eixo_desligado_por_escolha(self):
        assert _estado_do_eixo(JulgadorFake(), False, 0)["ligado"] is False

    def test_estado_do_eixo_diz_a_consequencia_de_estar_desligado(self):
        estado = _estado_do_eixo(JulgadorDesligado(), True, 0)
        assert estado["consequencia"]

    def test_estado_do_eixo_conta_as_funcoes_julgadas(self):
        assert _estado_do_eixo(JulgadorFake(), True, 7)["funcoes_julgadas"] == 7

    def test_estado_do_eixo_separa_escolha_de_chave_ausente(self):
        """Um é intencional, o outro é configuração faltando: ações opostas."""
        por_escolha = _estado_do_eixo(JulgadorFake(), False, 0)["motivo"]
        por_falta = _estado_do_eixo(JulgadorDesligado(), True, 0)["motivo"]
        assert por_escolha != por_falta

    def test_estado_do_eixo_nao_levanta_com_julgador_sem_atributos(self):
        assert _estado_do_eixo(object(), True, 0)["ligado"] is False


class TestEscolherFuncao:
    """A mais complexa, e não a primeira: o auxiliar de duas linhas não é a pergunta."""

    class Fingida:
        def __init__(self, nome, complexidade, linhas_logicas=1):
            self.nome, self.complexidade, self.linhas_logicas = (
                nome, complexidade, linhas_logicas
            )

    def test_escolher_funcao_de_nada_e_none(self):
        assert _escolher_funcao([], "") is None

    def test_escolher_funcao_pega_a_mais_complexa(self):
        medidas = [self.Fingida("auxiliar", 1), self.Fingida("principal", 9)]
        assert _escolher_funcao(medidas, "").nome == "principal"

    def test_escolher_funcao_respeita_o_nome_pedido(self):
        medidas = [self.Fingida("auxiliar", 1), self.Fingida("principal", 9)]
        assert _escolher_funcao(medidas, "auxiliar").nome == "auxiliar"

    def test_escolher_funcao_aceita_nome_parcial(self):
        medidas = [self.Fingida("Classe.metodo", 1), self.Fingida("outra", 9)]
        assert _escolher_funcao(medidas, "metodo").nome == "Classe.metodo"

    def test_escolher_funcao_cai_na_mais_complexa_se_o_nome_nao_existe(self):
        medidas = [self.Fingida("a", 1), self.Fingida("b", 9)]
        assert _escolher_funcao(medidas, "inexistente").nome == "b"

    def test_escolher_funcao_desempata_por_linhas_logicas(self):
        medidas = [self.Fingida("a", 5, 2), self.Fingida("b", 5, 40)]
        assert _escolher_funcao(medidas, "").nome == "b"


class TestNotaPonderada:
    """Peso de dimensão não observada é redistribuído, não zerado."""

    def test_nota_ponderada_com_tudo_observado_soma_os_pesos(self, rubrica):
        notas, ausentes, nota = _nota_ponderada(RESPOSTAS_BOAS, rubrica)
        assert ausentes == ()
        assert 0 <= nota <= 100

    def test_nota_ponderada_sem_nenhuma_dimensao_devolve_none(self, rubrica):
        so_risco = {n: r for n, r in RESPOSTAS_BOAS.items() if n in rubrica.risco_grave}
        _, _, nota = _nota_ponderada(so_risco, rubrica)
        assert nota is None

    def test_nota_ponderada_lista_as_nao_observadas(self, rubrica):
        sem_teste = {n: r for n, r in RESPOSTAS_BOAS.items() if n != "teste_verifica"}
        _, ausentes, _ = _nota_ponderada(sem_teste, rubrica)
        assert "teste_verifica" in ausentes

    def test_nota_ponderada_redistribui_em_vez_de_zerar(self, rubrica):
        """Zerar cobraria um quarto da nota por evidência que ninguém pôde mostrar."""
        sem_teste = {n: r for n, r in RESPOSTAS_BOAS.items() if n != "teste_verifica"}
        _, _, com_tudo = _nota_ponderada(RESPOSTAS_BOAS, rubrica)
        _, _, sem_uma = _nota_ponderada(sem_teste, rubrica)
        assert sem_uma == pytest.approx(com_tudo, abs=1.0)

    def test_nota_ponderada_devolve_as_notas_por_dimensao(self, rubrica):
        notas, _, _ = _nota_ponderada(RESPOSTAS_BOAS, rubrica)
        assert "complexidade_cognitiva" in notas


class TestGates:
    """Gates barram e ficam fora da nota: risco não se compensa com legibilidade."""

    def test_gates_com_respostas_boas_nao_barra_nada(self, rubrica, config):
        graves, duvidas = _gates(medida(), RESPOSTAS_BOAS, rubrica, config)
        assert graves == []

    def test_gates_barra_injecao_acima_do_limiar(self, rubrica, config):
        respostas = {**RESPOSTAS_BOAS, "injecao": noul(0.95)}
        graves, _ = _gates(medida(), respostas, rubrica, config)
        assert "injecao" in " ".join(graves)

    def test_gates_manda_risco_de_atencao_para_duvida_sem_barrar(self, rubrica, config):
        respostas = {**RESPOSTAS_BOAS, "caso_limite_nao_tratado": noul(0.9)}
        graves, duvidas = _gates(medida(), respostas, rubrica, config)
        assert graves == []
        assert duvidas

    def test_gates_nao_barra_por_tamanho_aqui(self, rubrica, config):
        """Tamanho é gate de `decidir`, não de `_gates`: aqui só entram as respostas."""
        gigante = medida(linha_inicio=1, linha_fim=config.limite_tamanho + 100)
        graves, _ = _gates(gigante, RESPOSTAS_BOAS, rubrica, config)
        assert graves == []

    def test_gates_manda_complexidade_alta_para_duvida(self, rubrica, config):
        complexa = medida(complexidade=config.limite_ccn + 5)
        _, duvidas = _gates(complexa, RESPOSTAS_BOAS, rubrica, config)
        assert any("complexidade" in d for d in duvidas)

    def test_gates_manda_confianca_baixa_para_duvida(self, rubrica, config):
        respostas = {**RESPOSTAS_BOAS, "complexidade_cognitiva": score(1.0, confianca=0.1)}
        _, duvidas = _gates(medida(), respostas, rubrica, config)
        assert duvidas


class TestConselho:
    """A decisão que a métrica não toma: refatorar apaga casos reais do domínio."""

    def test_conselho_sem_respostas_diz_que_nao_ha_o_que_aconselhar(self):
        assert _conselho(medida(), {}, {})

    def test_conselho_manda_testar_quando_a_complexidade_e_do_dominio(self, rubrica):
        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_essencial": noul(0.95),
            "teste_verifica": score(0.1),
        }
        notas, _, _ = _nota_ponderada(respostas, rubrica)
        assert "teste" in _conselho(medida(), respostas, notas)

    def test_conselho_manda_refatorar_quando_a_complexidade_e_da_escrita(self, rubrica):
        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_essencial": noul(0.05),
            "complexidade_cognitiva": score(0.1),
        }
        notas, _, _ = _nota_ponderada(respostas, rubrica)
        assert "simplificar" in _conselho(medida(), respostas, notas).lower()

    def test_conselho_sempre_devolve_texto(self, rubrica):
        notas, _, _ = _nota_ponderada(RESPOSTAS_BOAS, rubrica)
        assert isinstance(_conselho(medida(), RESPOSTAS_BOAS, notas), str)

    def test_conselho_nunca_e_vazio(self, rubrica):
        notas, _, _ = _nota_ponderada(RESPOSTAS_BOAS, rubrica)
        assert _conselho(medida(), RESPOSTAS_BOAS, notas).strip()


class TestPrioridade:
    def test_prioridade_alta_quando_ha_gate_grave(self):
        assert _prioridade(medida(), RESPOSTAS_BOAS, 30.0, ["injecao 0.95"], []) == "alta"

    def test_prioridade_baixa_com_risco_abaixo_do_limiar(self):
        assert _prioridade(medida(risco=5.0), RESPOSTAS_BOAS, 30.0, [], []) == "baixa"

    def test_prioridade_sobe_com_risco_acima_do_limiar(self):
        prioridade = _prioridade(medida(risco=90.0), RESPOSTAS_BOAS, 30.0, [], [])
        assert prioridade in {"media", "média", "alta"}

    def test_prioridade_sempre_devolve_um_dos_rotulos(self):
        for risco in (0.0, 30.0, 999.0):
            assert _prioridade(medida(risco=risco), RESPOSTAS_BOAS, 30.0, [], []) in {
                "alta", "media", "média", "baixa"
            }

    def test_prioridade_considera_as_duvidas(self):
        com_duvida = _prioridade(medida(risco=90.0), RESPOSTAS_BOAS, 30.0, [], ["x"])
        assert com_duvida in {"alta", "media", "média"}


class TestDecidir:
    def test_decidir_aprova_codigo_bom(self, rubrica, config):
        avaliada = decidir(medida(), RESPOSTAS_BOAS, rubrica=rubrica, config=config, limiar=30.0)
        assert avaliada.veredito == "aprovar"

    def test_decidir_bloqueia_com_gate_grave(self, rubrica, config):
        respostas = {**RESPOSTAS_BOAS, "injecao": noul(0.95)}
        avaliada = decidir(medida(), respostas, rubrica=rubrica, config=config, limiar=30.0)
        assert avaliada.veredito == "bloquear"

    def test_decidir_sem_respostas_sai_como_sem_julgamento(self, rubrica, config):
        avaliada = decidir(medida(), {}, rubrica=rubrica, config=config, limiar=30.0)
        assert avaliada.veredito == "sem_julgamento"

    def test_decidir_manda_revisar_com_nota_abaixo_do_minimo(self, rubrica, config):
        ruins = {**RESPOSTAS_BOAS, **{n: score(0.1) for n in rubrica.qualidade}}
        avaliada = decidir(medida(), ruins, rubrica=rubrica, config=config, limiar=30.0)
        assert avaliada.veredito == "revisar"

    def test_decidir_guarda_o_modelo_e_o_usage(self, rubrica, config):
        avaliada = decidir(
            medida(), RESPOSTAS_BOAS, rubrica=rubrica, config=config, limiar=30.0,
            modelo="jev-x", usage={"input_tokens": 5},
        )
        assert avaliada.modelo == "jev-x"
        assert avaliada.usage["input_tokens"] == 5

    def test_decidir_sem_nota_nao_inventa_zero(self, rubrica, config):
        """Zero significaria 'péssimo'; None significa 'não sei'."""
        assert decidir(medida(), {}, rubrica=rubrica, config=config, limiar=30.0).nota is None


class TestJulgarUma:
    def test_julgar_uma_devolve_uma_funcao_avaliada(self, rubrica, config):
        com_teste = medida(codigo="def f(): return 1", testes=("def test_f(): assert f()",))
        avaliada = _julgar_uma(
            com_teste, julgador=JulgadorFake(RESPOSTAS_BOAS), rubrica=rubrica,
            config=config, limiar=30.0,
        )
        assert avaliada.veredito == "aprovar"

    def test_julgar_uma_sem_trecho_de_teste_nao_pergunta_sobre_teste(self, rubrica, config):
        """Perguntar sem evidência devolveria 'não há teste' para função testada indireta."""
        avaliada = _julgar_uma(
            medida(codigo="def f(): return 1"), julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica, config=config, limiar=30.0,
        )
        assert "teste_verifica" in avaliada.nao_observadas

    def test_julgar_uma_com_eixo_desligado_sai_sem_julgamento(self, rubrica, config):
        avaliada = _julgar_uma(
            medida(), julgador=JulgadorDesligado(), rubrica=rubrica,
            config=config, limiar=30.0,
        )
        assert avaliada.veredito == "sem_julgamento"

    def test_julgar_uma_envia_o_codigo_no_estado(self, rubrica, config):
        fake = JulgadorFake(RESPOSTAS_BOAS)
        _julgar_uma(
            medida(codigo="def f(): ..."), julgador=fake, rubrica=rubrica,
            config=config, limiar=30.0,
        )
        assert fake.chamadas[0]["codigo"]

    def test_julgar_uma_nao_envia_a_complexidade_ciclomatica(self, rubrica, config):
        """Mandar o ccn ancoraria o julgamento no número que nós mesmos enviamos."""
        fake = JulgadorFake(RESPOSTAS_BOAS)
        _julgar_uma(
            medida(codigo="def f(): ..."), julgador=fake, rubrica=rubrica,
            config=config, limiar=30.0,
        )
        assert "complexidade" not in str(fake.chamadas[0])

    def test_julgar_uma_propaga_a_falha_do_transporte(self, rubrica, config):
        com_erro = JulgadorFake(levanta=RuntimeError("caiu"))
        with pytest.raises(RuntimeError):
            _julgar_uma(
                medida(), julgador=com_erro, rubrica=rubrica, config=config, limiar=30.0
            )


class TestJulgarLote:
    """Com `map`, uma exceção descartava tudo que já tinha sido pago."""

    def test_julgar_lote_de_nada_e_vazio(self, rubrica, config):
        assert _julgar_lote(
            [], julgador=JulgadorFake(), rubrica=rubrica, config=config, limiar=30.0
        ) == ([], [])

    def test_julgar_lote_julga_todas(self, rubrica, config):
        avaliadas, falhas = _julgar_lote(
            [medida(), medida(linha_inicio=20, linha_fim=30)],
            julgador=JulgadorFake(RESPOSTAS_BOAS), rubrica=rubrica,
            config=config, limiar=30.0,
        )
        assert len(avaliadas) == 2
        assert falhas == []

    def test_julgar_lote_uma_falha_nao_derruba_as_outras(self, rubrica, config):
        avaliadas, falhas = _julgar_lote(
            [medida(), medida(linha_inicio=20, linha_fim=30)],
            julgador=JulgadorFake(RESPOSTAS_BOAS, falhar_nas=(1,)),
            rubrica=rubrica, config=config, limiar=30.0,
        )
        assert len(avaliadas) == 1
        assert len(falhas) == 1

    def test_julgar_lote_registra_a_funcao_que_falhou(self, rubrica, config):
        _, falhas = _julgar_lote(
            [medida()], julgador=JulgadorFake(levanta=RuntimeError("caiu")),
            rubrica=rubrica, config=config, limiar=30.0,
        )
        assert "src/a.py:1" in falhas[0]["funcao"]

    def test_julgar_lote_registra_o_tipo_do_erro(self, rubrica, config):
        _, falhas = _julgar_lote(
            [medida()], julgador=JulgadorFake(levanta=RuntimeError("caiu")),
            rubrica=rubrica, config=config, limiar=30.0,
        )
        assert "RuntimeError" in falhas[0]["erro"]


class TestResumo:
    """O cabeçalho do relatório: contagem por veredito e custo."""

    def medicao(self) -> Medicao:
        return Medicao(
            funcoes=(medida(risco=5.0), medida(risco=40.0)),
            limiar=30.0,
            formula=obter_formula(),
        )

    def avaliada(self, rubrica, config, **ajustes):
        return decidir(
            medida(**ajustes), RESPOSTAS_BOAS, rubrica=rubrica, config=config, limiar=30.0
        )

    def test_resumo_conta_as_funcoes_medidas(self, rubrica, config):
        resumo = _resumo(self.medicao(), [], [], [])
        assert resumo["funcoes_medidas"] == 2
        assert resumo["acima_do_limiar"] == 1
        assert resumo["julgadas"] == 0
        assert resumo["detalhadas_no_relatorio"] == 0

    def test_resumo_marca_a_contagem_como_indisponivel_com_limiar_torto(self, rubrica, config):
        """Derrubar o resumo esconderia o relatório por um número do cabeçalho."""
        torta = Medicao(funcoes=(medida(),), limiar=float("nan"), formula=obter_formula())
        assert _resumo(torta, [], [], [])["acima_do_limiar"] == SEM_CONTAGEM

    def test_resumo_traz_todos_os_vereditos_mesmo_zerados(self, rubrica, config):
        """Sem a chave, quem lê confunde 'nenhum bloqueio' com 'não reporto bloqueio'."""
        resumo = _resumo(self.medicao(), [], [], [])
        assert set(resumo["por_veredito"]) >= {"aprovar", "revisar", "bloquear"}

    def test_resumo_conta_por_veredito(self, rubrica, config):
        avaliadas = [self.avaliada(rubrica, config)]
        assert _resumo(self.medicao(), avaliadas, avaliadas, [])["por_veredito"]["aprovar"] == 1

    def test_resumo_sem_gasto_usa_none_e_nao_zero(self, rubrica, config):
        """Zero diria 'a chamada aconteceu e custou nada'."""
        assert _resumo(self.medicao(), [], [], [])["tokens_usados"] is None

    def test_resumo_conta_as_omitidas_do_relatorio(self, rubrica, config):
        avaliadas = [self.avaliada(rubrica, config) for _ in range(3)]
        resumo = _resumo(self.medicao(), avaliadas, avaliadas[:1], [])
        assert resumo["omitidas_do_relatorio"] == 2

    def test_resumo_traz_o_limiar_e_a_formula(self, rubrica, config):
        resumo = _resumo(self.medicao(), [], [], [])
        assert resumo["limiar"] == 30.0
        assert resumo["formula"] == "crap"

    def test_resumo_traz_o_resultado_da_rodada(self, rubrica, config):
        assert _resumo(self.medicao(), [], [], [])["resultado"] == "aprovar"


class TestRelatorioContavel:
    """O eixo contável sozinho: grátis, determinístico, sem chave."""

    def medicao(self) -> Medicao:
        return Medicao(
            funcoes=(medida(risco=5.0), medida(risco=40.0)),
            limiar=30.0,
            formula=obter_formula(),
        )

    def test_relatorio_contavel_traz_o_resumo(self):
        assert relatorio_contavel(self.medicao(), Config())["resumo"]["funcoes_medidas"] == 2

    def test_relatorio_contavel_detalha_so_quem_passou_do_limiar(self):
        assert len(relatorio_contavel(self.medicao(), Config())["funcoes"]) == 1

    def test_relatorio_contavel_diz_o_que_nao_responde(self):
        """Sem isso o número parece veredito, e alguém refatora domínio por ele."""
        assert relatorio_contavel(self.medicao(), Config())["o_que_isto_nao_responde"]

    def test_relatorio_contavel_traz_a_interpretacao_do_numero(self):
        funcoes = relatorio_contavel(self.medicao(), Config())["funcoes"]
        assert funcoes[0]["interpretacao"]

    def test_relatorio_contavel_traz_a_regua(self):
        assert relatorio_contavel(self.medicao(), Config())["regua"]["formula"] == "crap"

    def test_relatorio_contavel_e_serializavel(self):
        import json

        assert json.dumps(relatorio_contavel(self.medicao(), Config()))

    def test_relatorio_contavel_declara_o_que_foi_omitido(self):
        config = Config(max_no_relatorio=0)
        assert relatorio_contavel(self.medicao(), config)["resumo"]["omitidas_do_relatorio"] >= 0


class TestParaAvaliacao:
    """Os fatos contáveis primeiro, o julgamento depois: nota não é medida."""

    def avaliada(self, rubrica, config, **ajustes):
        return decidir(
            medida(**ajustes), RESPOSTAS_BOAS, rubrica=rubrica, config=config, limiar=30.0
        )

    def test_para_avaliacao_traz_os_fatos_contaveis_junto(self, rubrica, config):
        corpo = self.avaliada(rubrica, config).para_avaliacao()
        assert corpo["complexidade"] == 3
        assert corpo["risco"] == 12.0

    def test_para_avaliacao_traz_a_nota_e_a_faixa(self, rubrica, config):
        corpo = self.avaliada(rubrica, config).para_avaliacao()
        assert corpo["nota"] is not None
        assert corpo["faixa"]

    def test_para_avaliacao_traz_o_veredito_e_o_conselho(self, rubrica, config):
        corpo = self.avaliada(rubrica, config).para_avaliacao()
        assert corpo["veredito"] == "aprovar"
        assert corpo["conselho"]

    def test_para_avaliacao_inclui_as_respostas_por_padrao(self, rubrica, config):
        assert "respostas" in self.avaliada(rubrica, config).para_avaliacao()

    def test_para_avaliacao_omite_as_respostas_quando_pedido(self, rubrica, config):
        """O bruto da API só faz sentido junto da versão da régua que o produziu."""
        corpo = self.avaliada(rubrica, config).para_avaliacao(com_respostas=False)
        assert "respostas" not in corpo

    def test_para_avaliacao_lista_as_dimensoes_nao_observadas(self, rubrica, config):
        corpo = self.avaliada(rubrica, config).para_avaliacao()
        assert "nao_observadas" in corpo

    def test_para_avaliacao_e_serializavel(self, rubrica, config):
        import json

        assert json.dumps(self.avaliada(rubrica, config).para_avaliacao())

    def test_para_avaliacao_sem_julgamento_nao_inventa_nota(self, rubrica, config):
        sem = decidir(medida(), {}, rubrica=rubrica, config=config, limiar=30.0)
        assert sem.para_avaliacao()["nota"] is None


class TestSomarTokens:
    """O resumo é o cabeçalho de um relatório que já foi pago."""

    class Fingida:
        def __init__(self, usage):
            self.usage = usage

    def test_somar_tokens_soma_o_campo(self):
        avaliadas = [self.Fingida({"input_tokens": 10}), self.Fingida({"input_tokens": 5})]
        assert _somar_tokens(avaliadas, "input_tokens") == 15

    def test_somar_tokens_de_nada_e_zero(self):
        assert _somar_tokens([], "input_tokens") == 0

    def test_somar_tokens_ignora_campo_ausente(self):
        assert _somar_tokens([self.Fingida({})], "input_tokens") == 0

    def test_somar_tokens_ignora_usage_nulo(self):
        assert _somar_tokens([self.Fingida(None)], "input_tokens") == 0

    def test_somar_tokens_ignora_texto(self):
        assert _somar_tokens([self.Fingida({"input_tokens": "muitos"})], "input_tokens") == 0

    def test_somar_tokens_ignora_nan(self):
        avaliadas = [self.Fingida({"input_tokens": float("nan")})]
        assert _somar_tokens(avaliadas, "input_tokens") == 0

    def test_somar_tokens_ignora_negativo(self):
        assert _somar_tokens([self.Fingida({"input_tokens": -5})], "input_tokens") == 0

    def test_somar_tokens_nunca_levanta(self):
        for usage in (None, {}, {"input_tokens": "x"}, {"input_tokens": float("inf")}):
            assert _somar_tokens([self.Fingida(usage)], "input_tokens") >= 0


class TestInterpretar:
    """Legenda de uma função não pode derrubar o relatório das outras."""

    def test_interpretar_devolve_a_frase_da_formula(self):
        assert "CRAP" in _interpretar(obter_formula(), 12.0)

    def test_interpretar_sempre_devolve_texto(self):
        assert isinstance(_interpretar(obter_formula(), 12.0), str)

    def test_interpretar_nao_levanta_quando_a_formula_falha(self):
        class Torta:
            nome = "torta"

            def interpretar(self, valor):
                raise RuntimeError("não sei explicar")

        assert "não soube explicar" in _interpretar(Torta(), 12.0)

    def test_interpretar_registra_a_formula_culpada(self, caplog):
        class Torta:
            nome = "torta"

            def interpretar(self, valor):
                raise RuntimeError("não sei explicar")

        with caplog.at_level(logging.WARNING, logger="jev_crap.avaliacao"):
            _interpretar(Torta(), 12.0)
        assert "torta" in caplog.text


class TestCoberturaDaFuncao:
    """Sem relatório e sem casamento são o mesmo valor, mas fatos diferentes."""

    def test_cobertura_da_funcao_sem_relatorio_e_sem_dados(self):
        nao_casados, ambiguos = set(), set()
        assert _cobertura_da_funcao(medida(), {}, nao_casados, ambiguos) == (
            float(SEM_DADOS), float(SEM_DADOS)
        )

    def test_cobertura_da_funcao_sem_relatorio_nao_registra_nao_casado(self):
        """Sem relatório ninguém deixou de casar: não há com o que comparar."""
        nao_casados: set[str] = set()
        _cobertura_da_funcao(medida(), {}, nao_casados, set())
        assert nao_casados == set()

    def test_cobertura_da_funcao_registra_quem_nao_casou(self):
        nao_casados: set[str] = set()
        _cobertura_da_funcao(medida(), {"b": cobertura("lib/b.py")}, nao_casados, set())
        assert nao_casados == {"src/a.py"}

    def test_cobertura_da_funcao_le_a_faixa_quando_casa(self):
        linha, _ = _cobertura_da_funcao(
            medida(linha_inicio=1, linha_fim=2), {"a": cobertura("src/a.py")}, set(), set()
        )
        assert linha == 0.5

    def test_cobertura_da_funcao_registra_ambiguidade(self):
        ambiguos: set[str] = set()
        relatorio = {"a": cobertura("x/a.py"), "b": cobertura("y/a.py")}
        _cobertura_da_funcao(medida(), relatorio, set(), ambiguos)
        assert ambiguos == {"src/a.py"}

    def test_cobertura_da_funcao_sem_branch_devolve_a_sentinela(self):
        _, branch = _cobertura_da_funcao(
            medida(linha_inicio=1, linha_fim=2), {"a": cobertura("src/a.py")}, set(), set()
        )
        assert branch == SEM_DADOS


class TestInsumosDaFuncao:
    """Um arquivo torto custa aquele arquivo, não a varredura."""

    def test_insumos_da_funcao_monta_com_valores_bons(self):
        insumos, linha, branch = _insumos_da_funcao(medida(), 0.5, 0.4, [])
        assert insumos.complexidade == 3
        assert (linha, branch) == (0.5, 0.4)

    def test_insumos_da_funcao_eleva_complexidade_zero_a_um(self):
        insumos, _, _ = _insumos_da_funcao(medida(complexidade=0), 0.5, 0.4, [])
        assert insumos.complexidade == 1

    def test_insumos_da_funcao_trata_sentinela_de_branch_como_ausencia(self):
        insumos, _, _ = _insumos_da_funcao(medida(), 0.5, float(SEM_DADOS), [])
        assert insumos.cobertura_branch is None

    def test_insumos_da_funcao_nao_levanta_com_cobertura_invalida(self):
        insumos, linha, branch = _insumos_da_funcao(medida(), 1.4, 0.4, [])
        assert linha == SEM_DADOS
        assert branch == SEM_DADOS
        assert insumos.complexidade == 3

    def test_insumos_da_funcao_registra_o_arquivo_invalido(self):
        invalidos: list[str] = []
        _insumos_da_funcao(medida(), 1.4, 0.4, invalidos)
        assert "src/a.py:1" in invalidos[0]

    def test_insumos_da_funcao_devolve_a_cobertura_que_foi_usada(self):
        """Mostrar o valor recusado ao lado de um risco sem ele não se explicaria."""
        _, linha, _ = _insumos_da_funcao(medida(), 1.4, 0.4, [])
        assert linha == SEM_DADOS


class TestAvisosDaMedicao:
    """Nenhum deles é erro: a medição aconteceu e o resultado vale."""

    def base(self, **ajustes):
        campos = dict(
            relatorio={}, arquivos={"src/a.py"}, nao_casados=set(), ambiguos=set(),
            sem_branch=0, insumos_invalidos=(), encontrou_funcao=True,
        )
        return {**campos, **ajustes}

    def test_avisos_da_medicao_sem_problema_nao_avisa(self):
        assert _avisos_da_medicao(**self.base()) == []

    def test_avisos_da_medicao_avisa_quando_nao_achou_funcao(self):
        avisos = _avisos_da_medicao(**self.base(encontrou_funcao=False))
        assert any("nenhuma função" in a for a in avisos)

    def test_avisos_da_medicao_avisa_sobre_insumo_invalido(self):
        avisos = _avisos_da_medicao(**self.base(insumos_invalidos=["src/a.py:1 (x)"]))
        assert any("insumo de cobertura inválido" in a for a in avisos)

    def test_o_aviso_de_insumo_invalido_aponta_o_relatorio_como_causa(self):
        avisos = _avisos_da_medicao(**self.base(insumos_invalidos=["src/a.py:1 (x)"]))
        assert "defeito do relatório" in avisos[0]

    def test_avisos_da_medicao_mostra_no_maximo_tres_exemplos(self):
        muitos = [f"src/{i}.py:1 (x)" for i in range(10)]
        avisos = _avisos_da_medicao(**self.base(insumos_invalidos=muitos))
        assert avisos[0].count(".py:1") == 3

    def test_avisos_da_medicao_poe_o_cruzamento_primeiro(self):
        """É o que mais vezes explica um relatório inteiro em vermelho."""
        avisos = _avisos_da_medicao(
            **self.base(
                relatorio={"a": cobertura("/build/a.py")},
                nao_casados={"src/a.py"},
                insumos_invalidos=["src/a.py:1 (x)"],
            )
        )
        assert "casou" in avisos[0]


class TestMedirUma:
    """Uma função problemática custa a própria precisão, nunca a varredura."""

    class Bruta:
        def __init__(self, **ajustes):
            campos = dict(
                arquivo="src/a.py", nome="f", linha_inicio=1, linha_fim=2,
                complexidade=3, linhas_logicas=2, linguagem="python",
            )
            self.__dict__.update({**campos, **ajustes})

    def chamar(self, bruta=None, relatorio=None, cruzamento=None, **extras):
        return _medir_uma(
            bruta or self.Bruta(),
            relatorio=relatorio or {},
            formula=obter_formula(),
            cruzamento=cruzamento or _Cruzamento(),
            com_codigo=extras.get("com_codigo", False),
            pasta_testes=extras.get("pasta_testes"),
            textos=extras.get("textos", {}),
        )

    def test_medir_uma_devolve_a_funcao_pontuada(self):
        pontuada = _medir_uma(
            self.Bruta(), relatorio={}, formula=obter_formula(),
            cruzamento=_Cruzamento(), com_codigo=False, pasta_testes=None, textos={},
        )
        assert pontuada.arquivo == "src/a.py"
        assert pontuada.nome == "f"
        assert pontuada.complexidade == 3
        assert pontuada.linguagem == "python"
        assert pontuada.risco > 0

    def test_medir_uma_sem_relatorio_marca_cobertura_ausente(self):
        pontuada = _medir_uma(
            self.Bruta(), relatorio={}, formula=obter_formula(),
            cruzamento=_Cruzamento(), com_codigo=False, pasta_testes=None, textos={},
        )
        assert pontuada.cobertura_linha == SEM_DADOS
        assert pontuada.cobertura_branch == SEM_DADOS

    def test_medir_uma_cruza_com_o_relatorio_quando_casa(self):
        pontuada = _medir_uma(
            self.Bruta(), relatorio={"a": cobertura("src/a.py")}, formula=obter_formula(),
            cruzamento=_Cruzamento(), com_codigo=False, pasta_testes=None, textos={},
        )
        assert pontuada.cobertura_linha == 0.5

    def test_medir_uma_registra_o_arquivo_visto(self):
        cruzamento = _Cruzamento()
        self.chamar(cruzamento=cruzamento)
        assert cruzamento.arquivos == {"src/a.py"}

    def test_medir_uma_registra_quem_nao_casou(self):
        cruzamento = _Cruzamento()
        self.chamar(relatorio={"b": cobertura("lib/b.py")}, cruzamento=cruzamento)
        assert cruzamento.nao_casados == {"src/a.py"}

    def test_medir_uma_conta_quem_ficou_sem_branch(self):
        cruzamento = _Cruzamento()
        self.chamar(relatorio={"a": cobertura("src/a.py")}, cruzamento=cruzamento)
        assert cruzamento.sem_branch == 1

    def test_medir_uma_sem_com_codigo_nao_carrega_texto(self):
        """medir_risco não precisa do código: nada vai ao modelo."""
        assert self.chamar().codigo == ""

    def test_medir_uma_com_codigo_le_do_disco(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("def f():\n    return 1\n", encoding="utf-8")
        bruta = self.Bruta(arquivo=str(alvo))
        assert self.chamar(bruta=bruta, com_codigo=True).codigo.startswith("def f()")

    def test_medir_uma_com_arquivo_ilegivel_da_trecho_vazio(self, tmp_path):
        bruta = self.Bruta(arquivo=str(tmp_path / "nao_existe.py"))
        assert self.chamar(bruta=bruta, com_codigo=True).codigo == ""

    def test_medir_uma_reaproveita_o_cache_de_texto(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("def f():\n    return 1\n", encoding="utf-8")
        textos: dict[str, list[str]] = {}
        bruta = self.Bruta(arquivo=str(alvo))
        self.chamar(bruta=bruta, com_codigo=True, textos=textos)
        assert textos

    def test_medir_uma_eleva_complexidade_zero_a_um(self):
        assert self.chamar(bruta=self.Bruta(complexidade=0)).complexidade == 1
