"""Testes do módulo de cobertura.

Os testes leem fixtures reais em ``tests/fixtures/`` (um ``lcov.info`` e um
``coverage.xml``, ambos com branch) em vez de só montar strings inline: o
objetivo é garantir que o parse aguenta o formato como os geradores realmente
escrevem, com DOCTYPE, tabulação, registros ``FN`` que ignoramos e caminho
absoluto de máquina de CI. Casos de borda que nenhum gerador emite junto no
mesmo arquivo ficam em strings inline gravadas em ``tmp_path``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from jev_crap.metrica.cobertura import (
    SEM_DADOS,
    CoberturaArquivo,
    FormatoDeCoberturaDesconhecido,
    _Acumulador,
    _cheirar_formato,
    _congelar_todos,
    _cwd,
    _dentro,
    _inteiro_lcov,
    _limpar,
    _normalizar_caminho,
    _parse_cobertura_xml,
    _parse_lcov,
    cobertura_de_faixa,
    ler,
    ler_cobertura_xml,
    ler_lcov,
)

FIXTURES = Path(__file__).parent / "fixtures"
LCOV = str(FIXTURES / "lcov.info")
COBERTURA_XML = str(FIXTURES / "coverage.xml")


def escrever(tmp_path: Path, nome: str, conteudo: str) -> str:
    caminho = tmp_path / nome
    caminho.write_text(conteudo, encoding="utf-8")
    return str(caminho)


# --------------------------------------------------------------------------- #
# LCOV
# --------------------------------------------------------------------------- #


def test_lcov_encontra_todos_os_arquivos_do_relatorio() -> None:
    cobertura = ler_lcov(LCOV, raiz="/home/projeto")
    assert set(cobertura) == {"src/calculadora.py", "src/util.js", "src/legado.rb"}


def test_lcov_le_linhas_executaveis_e_cobertas() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    assert cob.linhas_totais == {1, 3, 4, 5, 7, 8, 9}
    # A linha 9 tem DA:9,0 — executável e nunca executada.
    assert cob.linhas_cobertas == {1, 3, 4, 5, 7, 8}
    assert cob.cobertura_de_linha == pytest.approx(6 / 7)


def test_lcov_le_branches_do_brda_com_traco_valendo_nao_coberto() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    # BRDA:8,0,1,- significa bloco nunca alcançado, não "zero execuções conhecidas".
    assert cob.branches_por_linha == {4: (1, 2), 8: (1, 2)}
    assert (cob.branches_cobertos, cob.branches_totais) == (2, 4)
    assert cob.cobertura_de_branch == pytest.approx(0.5)


def test_lcov_sem_registro_de_branch_sinaliza_ausencia_em_vez_de_zero() -> None:
    cob = ler_lcov(LCOV, raiz="/home/projeto")["src/util.js"]
    assert cob.branches_totais == SEM_DADOS
    assert cob.branches_cobertos == SEM_DADOS
    assert cob.tem_dados_de_branch is False
    assert cob.cobertura_de_branch == SEM_DADOS


def test_lcov_usa_brf_brh_quando_nao_ha_detalhe_por_branch() -> None:
    cob = ler_lcov(LCOV)["src/legado.rb"]
    assert (cob.branches_cobertos, cob.branches_totais) == (1, 2)
    assert cob.tem_dados_de_branch is True
    # O resumo diz quanto, não onde: não dá para recortar por função.
    assert cob.branches_por_linha == {}


def test_lcov_ignora_brf_brh_quando_ha_brda(tmp_path: Path) -> None:
    # Resumo e detalhe discordam de propósito: o detalhe é quem manda, porque
    # BRF/BRH são somatórios por registro e contam duplicado ao concatenar runs.
    caminho = escrever(
        tmp_path,
        "lcov.info",
        "SF:src/a.py\nDA:1,1\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nBRF:99\nBRH:99\nend_of_record\n",
    )
    cob = ler_lcov(caminho)["src/a.py"]
    assert (cob.branches_cobertos, cob.branches_totais) == (1, 2)


def test_lcov_soma_registros_repetidos_do_mesmo_arquivo(tmp_path: Path) -> None:
    # Dois runs concatenados: a linha 2 só foi coberta no segundo.
    caminho = escrever(
        tmp_path,
        "lcov.info",
        "SF:src/a.py\nDA:1,1\nDA:2,0\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nend_of_record\n"
        "SF:src/a.py\nDA:1,3\nDA:2,4\nBRDA:1,0,0,0\nBRDA:1,0,1,2\nend_of_record\n",
    )
    cobertura = ler_lcov(caminho)
    assert len(cobertura) == 1
    cob = cobertura["src/a.py"]
    assert cob.linhas_cobertas == {1, 2}
    # Os mesmos dois branches, agora ambos cobertos — e não quatro branches.
    assert (cob.branches_cobertos, cob.branches_totais) == (2, 2)


def test_lcov_ignora_registros_fora_de_um_bloco_sf(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, "lcov.info", "TN:\nDA:1,1\nSF:src/a.py\nDA:2,1\nend_of_record\n")
    cob = ler_lcov(caminho)["src/a.py"]
    assert cob.linhas_totais == {2}


# --------------------------------------------------------------------------- #
# Cobertura XML
# --------------------------------------------------------------------------- #


def test_xml_encontra_todos_os_arquivos_do_relatorio() -> None:
    cobertura = ler_cobertura_xml(COBERTURA_XML)
    assert set(cobertura) == {"src/pedido.py", "src/relatorio.py"}


def test_xml_le_linhas_executaveis_e_cobertas() -> None:
    cob = ler_cobertura_xml(COBERTURA_XML)["src/pedido.py"]
    assert cob.linhas_totais == {1, 4, 5, 6, 9, 10, 11}
    assert cob.linhas_cobertas == {1, 4, 5, 6, 9, 10}


def test_xml_le_branch_do_condition_coverage() -> None:
    cob = ler_cobertura_xml(COBERTURA_XML)["src/pedido.py"]
    assert cob.branches_por_linha == {5: (2, 2), 10: (1, 2)}
    assert (cob.branches_cobertos, cob.branches_totais) == (3, 4)
    assert cob.cobertura_de_branch == pytest.approx(0.75)


def test_xml_sem_branch_sinaliza_ausencia() -> None:
    cob = ler_cobertura_xml(COBERTURA_XML)["src/relatorio.py"]
    assert cob.branches_totais == SEM_DADOS
    assert cob.tem_dados_de_branch is False


def test_xml_ignora_branch_sem_condition_coverage(tmp_path: Path) -> None:
    # Sem os números não há o que contar; inventar "2 condições" seria fabricar dado.
    caminho = escrever(
        tmp_path,
        "coverage.xml",
        "<coverage><packages><package><classes>"
        '<class filename="src/a.py"><lines>'
        '<line number="1" hits="1" branch="true"/>'
        "</lines></class></classes></package></packages></coverage>",
    )
    cob = ler_cobertura_xml(caminho)["src/a.py"]
    assert cob.linhas_totais == {1}
    assert cob.branches_totais == SEM_DADOS


def test_xml_nao_conta_em_dobro_as_linhas_repetidas_em_methods(tmp_path: Path) -> None:
    caminho = escrever(
        tmp_path,
        "coverage.xml",
        "<coverage><packages><package><classes>"
        '<class filename="src/a.py">'
        '<methods><method name="f"><lines><line number="2" hits="1"/></lines></method></methods>'
        '<lines><line number="2" hits="1"/></lines>'
        "</class></classes></package></packages></coverage>",
    )
    cob = ler_cobertura_xml(caminho)["src/a.py"]
    assert cob.linhas_totais == {2}
    assert cob.linhas_cobertas == {2}


def test_xml_junta_classes_que_apontam_para_o_mesmo_arquivo(tmp_path: Path) -> None:
    # Java/Kotlin e conversores para Cobertura emitem uma <class> por classe.
    caminho = escrever(
        tmp_path,
        "coverage.xml",
        "<coverage><packages><package><classes>"
        '<class name="A" filename="src/a.java"><lines><line number="3" hits="1"/></lines></class>'
        '<class name="B" filename="src/a.java"><lines><line number="9" hits="0"/></lines></class>'
        "</classes></package></packages></coverage>",
    )
    cobertura = ler_cobertura_xml(caminho)
    assert list(cobertura) == ["src/a.java"]
    assert cobertura["src/a.java"].linhas_totais == {3, 9}
    assert cobertura["src/a.java"].linhas_cobertas == {3}


def test_xml_com_outra_tag_raiz_e_recusado(tmp_path: Path) -> None:
    # "raiz" aqui é a tag raiz do XML, não a raiz do projeto: um relatório
    # JaCoCo nativo não é Cobertura XML e não pode ser lido como se fosse.
    caminho = escrever(tmp_path, "relatorio.xml", "<jacoco><package/></jacoco>")
    with pytest.raises(FormatoDeCoberturaDesconhecido):
        ler_cobertura_xml(caminho)


# --------------------------------------------------------------------------- #
# Normalização de caminhos
# --------------------------------------------------------------------------- #


def test_caminho_absoluto_dentro_da_raiz_vira_relativo() -> None:
    cobertura = ler_lcov(LCOV, raiz="/home/projeto")
    assert "src/util.js" in cobertura
    assert cobertura["src/util.js"].arquivo == "src/util.js"


def test_caminho_absoluto_fora_da_raiz_continua_absoluto() -> None:
    # Melhor um caminho visivelmente estranho do que uma pilha de "../" que
    # parece relativa ao projeto e cruza errado com a complexidade.
    cobertura = ler_lcov(LCOV, raiz="/outro/lugar")
    assert "/home/projeto/src/util.js" in cobertura


def test_caminho_de_windows_vira_barra_normal(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, "lcov.info", "SF:src\\pacote\\a.py\nDA:1,1\nend_of_record\n")
    assert "src/pacote/a.py" in ler_lcov(caminho)


def test_prefixo_file_e_ponto_barra_sao_removidos(tmp_path: Path) -> None:
    caminho = escrever(
        tmp_path,
        "lcov.info",
        "SF:file:///home/projeto/src/a.py\nDA:1,1\nend_of_record\n"
        "SF:./src/b.py\nDA:1,1\nend_of_record\n",
    )
    cobertura = ler_lcov(caminho, raiz="/home/projeto")
    assert set(cobertura) == {"src/a.py", "src/b.py"}


def test_xml_resolve_relativo_contra_sources_quando_a_raiz_e_um_subdiretorio(
    tmp_path: Path,
) -> None:
    # <source> diz /home/projeto, filename diz src/a.py, e o projeto analisado
    # é /home/projeto/src: o arquivo precisa virar "a.py", não "src/a.py".
    caminho = escrever(
        tmp_path,
        "coverage.xml",
        "<coverage><sources><source>/home/projeto</source></sources>"
        "<packages><package><classes>"
        '<class filename="src/a.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>",
    )
    assert "a.py" in ler_cobertura_xml(caminho, raiz="/home/projeto/src")


def test_xml_sem_raiz_declarada_preserva_o_relativo_do_relatorio(tmp_path: Path) -> None:
    # Sem raiz explícita, ancorar em <source> produziria o caminho absoluto da
    # máquina que gerou o relatório — inútil para cruzar com o código local.
    caminho = escrever(
        tmp_path,
        "coverage.xml",
        "<coverage><sources><source>/maquina/de/ci</source></sources>"
        "<packages><package><classes>"
        '<class filename="src/a.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>",
    )
    assert "src/a.py" in ler_cobertura_xml(caminho)


# --------------------------------------------------------------------------- #
# Detecção de formato
# --------------------------------------------------------------------------- #


def test_ler_detecta_lcov_pela_fixture() -> None:
    assert ler(LCOV, raiz="/home/projeto") == ler_lcov(LCOV, raiz="/home/projeto")


def test_ler_detecta_cobertura_xml_pela_fixture() -> None:
    assert ler(COBERTURA_XML) == ler_cobertura_xml(COBERTURA_XML)


def test_ler_detecta_pelo_conteudo_mesmo_com_extensao_enganosa(tmp_path: Path) -> None:
    lcov_disfarcado = escrever(
        tmp_path, "cobertura.xml", "TN:\nSF:src/a.py\nDA:1,1\nend_of_record\n"
    )
    assert "src/a.py" in ler(lcov_disfarcado)

    xml_disfarcado = escrever(
        tmp_path,
        "relatorio.info",
        "<coverage><packages><package><classes>"
        '<class filename="src/b.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>",
    )
    assert "src/b.py" in ler(xml_disfarcado)


def test_ler_aceita_extensao_info_com_conteudo_inconclusivo(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, "lcov.info", "DA:1,1\n")
    assert ler(caminho) == {}


def test_ler_recusa_formato_estranho(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, "cobertura.json", '{"total": {"lines": {"pct": 90}}}')
    with pytest.raises(FormatoDeCoberturaDesconhecido):
        ler(caminho)


def test_ler_recusa_xml_de_outro_formato(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, "jacoco.xml", '<?xml version="1.0"?><report name="x"/>')
    with pytest.raises(FormatoDeCoberturaDesconhecido):
        ler(caminho)


# --------------------------------------------------------------------------- #
# Cobertura por faixa de linhas
# --------------------------------------------------------------------------- #


def test_faixa_recorta_linhas_e_branches_de_uma_funcao() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]

    # soma(): linhas 3-5, todas cobertas; o único branch está na linha 4.
    assert cobertura_de_faixa(cob, 3, 5) == (pytest.approx(1.0), pytest.approx(0.5))

    # divide(): linhas 7-9, a 9 nunca executou.
    linha, branch = cobertura_de_faixa(cob, 7, 9)
    assert linha == pytest.approx(2 / 3)
    assert branch == pytest.approx(0.5)


def test_faixa_conta_so_linhas_executaveis_e_nao_o_tamanho_da_faixa() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    # 1 a 5 tem 5 linhas no arquivo, mas só 4 executáveis (a 2 é branco/comentário).
    linha, _ = cobertura_de_faixa(cob, 1, 5)
    assert linha == pytest.approx(1.0)


def test_faixa_sem_linha_executavel_sinaliza_ausencia() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    assert cobertura_de_faixa(cob, 100, 200) == (SEM_DADOS, SEM_DADOS)


def test_faixa_sem_branch_sinaliza_ausencia_em_vez_de_fingir_cobertura_total() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    linha, branch = cobertura_de_faixa(cob, 1, 3)
    assert linha == pytest.approx(1.0)
    # Nenhum branch entre as linhas 1 e 3: não é 1.0 ("tudo coberto") nem 0.0.
    assert branch == SEM_DADOS


def test_faixa_em_arquivo_sem_dado_de_branch_sinaliza_ausencia() -> None:
    cob = ler_lcov(LCOV, raiz="/home/projeto")["src/util.js"]
    linha, branch = cobertura_de_faixa(cob, 1, 3)
    assert linha == pytest.approx(2 / 3)
    assert branch == SEM_DADOS


def test_faixa_em_arquivo_com_branch_so_no_resumo_sinaliza_ausencia() -> None:
    # O arquivo tem 1/2 branches cobertos, mas BRF/BRH não dizem em que linha:
    # por faixa, o dado não existe.
    cob = ler_lcov(LCOV)["src/legado.rb"]
    assert cob.tem_dados_de_branch is True
    assert cobertura_de_faixa(cob, 1, 2)[1] == SEM_DADOS


def test_faixa_com_pontas_invertidas_nao_quebra() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    assert cobertura_de_faixa(cob, 5, 3) == cobertura_de_faixa(cob, 3, 5)


def test_faixa_de_uma_linha_so() -> None:
    cob = ler_lcov(LCOV)["src/calculadora.py"]
    assert cobertura_de_faixa(cob, 9, 9)[0] == pytest.approx(0.0)


def test_faixa_funciona_com_cobertura_montada_a_mao() -> None:
    # A interface precisa ser usável sem passar por um parser — os outros
    # módulos e os testes deles montam CoberturaArquivo direto.
    cob = CoberturaArquivo(
        arquivo="src/a.py",
        linhas_cobertas={10, 11},
        linhas_totais={10, 11, 12, 13},
        branches_cobertos=1,
        branches_totais=2,
        branches_por_linha={11: (1, 2)},
    )
    assert cobertura_de_faixa(cob, 10, 13) == (pytest.approx(0.5), pytest.approx(0.5))


def test_cobertura_arquivo_sem_branches_por_linha_e_construivel_posicionalmente() -> None:
    cob = CoberturaArquivo("src/a.py", {1}, {1, 2}, SEM_DADOS, SEM_DADOS)
    assert cob.cobertura_de_linha == pytest.approx(0.5)
    assert cob.tem_dados_de_branch is False


def _acc(arquivo: str = "src/a.py") -> _Acumulador:
    return _Acumulador(arquivo)


class TestTemDadosDeBranch:
    """A diferença entre 'zero branch' e 'nenhuma informação de branch'."""

    def cob(self, **ajustes) -> CoberturaArquivo:
        campos = dict(
            arquivo="src/a.py", linhas_cobertas={1}, linhas_totais={1, 2},
            branches_cobertos=1, branches_totais=2,
        )
        return CoberturaArquivo(**{**campos, **ajustes})

    def test_tem_dados_de_branch_com_contagem(self):
        assert self.cob().tem_dados_de_branch is True

    def test_tem_dados_de_branch_com_zero_branches_medidos(self):
        """Zero é dado: o arquivo não tem desvio."""
        assert self.cob(branches_cobertos=0, branches_totais=0).tem_dados_de_branch is True

    def test_tem_dados_de_branch_falso_para_a_sentinela(self):
        cob = self.cob(branches_cobertos=SEM_DADOS, branches_totais=SEM_DADOS)
        assert cob.tem_dados_de_branch is False

    def test_tem_dados_de_branch_recusa_negativo_que_nao_e_sentinela(self):
        with pytest.raises(ValueError, match="negativo sem ser a sentinela"):
            _ = self.cob(branches_totais=-7).tem_dados_de_branch

    def test_tem_dados_de_branch_nomeia_o_arquivo_no_erro(self):
        with pytest.raises(ValueError, match="src/a.py"):
            _ = self.cob(branches_totais=-7).tem_dados_de_branch


class TestCoberturaDeLinha:
    def cob(self, **ajustes) -> CoberturaArquivo:
        campos = dict(
            arquivo="src/a.py", linhas_cobertas={1, 2}, linhas_totais={1, 2, 3, 4},
            branches_cobertos=SEM_DADOS, branches_totais=SEM_DADOS,
        )
        return CoberturaArquivo(**{**campos, **ajustes})

    def test_cobertura_de_linha_e_a_fracao_coberta(self):
        assert self.cob().cobertura_de_linha == 0.5

    def test_cobertura_de_linha_sem_linha_executavel_e_sem_dados(self):
        """Módulo só de constantes não é módulo descoberto."""
        assert self.cob(linhas_totais=set()).cobertura_de_linha == SEM_DADOS

    def test_cobertura_de_linha_nunca_passa_de_um(self):
        """Linha coberta que não consta como executável não pode dar 130%."""
        cob = self.cob(linhas_cobertas={1, 2, 3, 4, 90, 91}, linhas_totais={1, 2})
        assert cob.cobertura_de_linha == 1.0

    def test_cobertura_de_linha_zero_quando_nada_foi_coberto(self):
        assert self.cob(linhas_cobertas=set()).cobertura_de_linha == 0.0


class TestCoberturaDeBranch:
    def cob(self, **ajustes) -> CoberturaArquivo:
        campos = dict(
            arquivo="src/a.py", linhas_cobertas={1}, linhas_totais={1},
            branches_cobertos=1, branches_totais=4,
        )
        return CoberturaArquivo(**{**campos, **ajustes})

    def test_cobertura_de_branch_e_a_fracao_coberta(self):
        assert self.cob().cobertura_de_branch == 0.25

    def test_cobertura_de_branch_sem_branch_medivel_e_sem_dados(self):
        assert self.cob(branches_totais=0).cobertura_de_branch == SEM_DADOS

    def test_cobertura_de_branch_da_sentinela_e_sem_dados(self):
        cob = self.cob(branches_cobertos=SEM_DADOS, branches_totais=SEM_DADOS)
        assert cob.cobertura_de_branch == SEM_DADOS

    def test_cobertura_de_branch_nunca_passa_de_um(self):
        """Acima de 1 viraria risco negativo no cálculo que a consome."""
        assert self.cob(branches_cobertos=9, branches_totais=4).cobertura_de_branch == 1.0


class TestRegistrarLinha:
    def test_registrar_linha_guarda_as_execucoes(self):
        acc = _acc()
        acc.registrar_linha(3, 2)
        assert acc.hits_por_linha == {3: 2}

    def test_registrar_linha_acumula_entre_registros(self):
        """Ficar com a última perderia a cobertura da primeira execução."""
        acc = _acc()
        acc.registrar_linha(3, 1)
        acc.registrar_linha(3, 2)
        assert acc.hits_por_linha == {3: 3}

    def test_registrar_linha_ignora_linha_zero(self):
        acc = _acc()
        acc.registrar_linha(0, 5)
        assert acc.hits_por_linha == {}

    def test_registrar_linha_ignora_linha_negativa(self):
        acc = _acc()
        acc.registrar_linha(-2, 5)
        assert acc.hits_por_linha == {}

    def test_registrar_linha_trata_hits_negativo_como_zero(self):
        acc = _acc()
        acc.registrar_linha(3, -4)
        assert acc.hits_por_linha == {3: 0}

    def test_registrar_linha_preserva_linha_executada_zero_vezes(self):
        acc = _acc()
        acc.registrar_linha(7, 0)
        assert 7 in acc.hits_por_linha


class TestRegistrarBranchLcov:
    def test_registrar_branch_lcov_guarda_por_chave(self):
        acc = _acc()
        acc.registrar_branch_lcov(3, "0", "0", 1)
        assert acc.branches == {(3, "0", "0"): 1}

    def test_registrar_branch_lcov_nao_conta_o_mesmo_branch_duas_vezes(self):
        acc = _acc()
        acc.registrar_branch_lcov(3, "0", "0", 1)
        acc.registrar_branch_lcov(3, "0", "0", 1)
        assert len(acc.branches) == 1

    def test_registrar_branch_lcov_fica_com_o_melhor_resultado(self):
        acc = _acc()
        acc.registrar_branch_lcov(3, "0", "0", 0)
        acc.registrar_branch_lcov(3, "0", "0", 5)
        assert acc.branches[(3, "0", "0")] == 5

    def test_registrar_branch_lcov_ignora_linha_fora_de_faixa(self):
        acc = _acc()
        acc.registrar_branch_lcov(0, "0", "0", 1)
        assert acc.branches == {}

    def test_registrar_branch_lcov_trata_vezes_negativo_como_zero(self):
        acc = _acc()
        acc.registrar_branch_lcov(3, "0", "0", -1)
        assert acc.branches[(3, "0", "0")] == 0


class TestRegistrarBranchResumido:
    def test_registrar_branch_resumido_guarda_o_par(self):
        acc = _acc()
        acc.registrar_branch_resumido(3, 1, 2)
        assert acc.branches_por_linha == {3: (1, 2)}

    def test_registrar_branch_resumido_fica_com_o_maior_de_cada(self):
        acc = _acc()
        acc.registrar_branch_resumido(3, 1, 2)
        acc.registrar_branch_resumido(3, 2, 2)
        assert acc.branches_por_linha[3] == (2, 2)

    def test_registrar_branch_resumido_corta_cobertos_acima_de_totais(self):
        """(3, 2) sairia como cobertura de branch acima de 100%."""
        acc = _acc()
        acc.registrar_branch_resumido(3, 3, 2)
        assert acc.branches_por_linha[3] == (2, 2)

    def test_registrar_branch_resumido_zera_negativos(self):
        acc = _acc()
        acc.registrar_branch_resumido(3, -1, -2)
        assert acc.branches_por_linha[3] == (0, 0)

    def test_registrar_branch_resumido_ignora_linha_fora_de_faixa(self):
        acc = _acc()
        acc.registrar_branch_resumido(0, 1, 2)
        assert acc.branches_por_linha == {}


class TestCongelar:
    def test_congelar_devolve_cobertura_imutavel(self):
        acc = _acc()
        acc.registrar_linha(1, 1)
        assert acc.congelar().arquivo == "src/a.py"

    def test_congelar_separa_linhas_cobertas_de_totais(self):
        acc = _acc()
        acc.registrar_linha(1, 1)
        acc.registrar_linha(2, 0)
        congelada = acc.congelar()
        assert congelada.linhas_cobertas == {1}
        assert congelada.linhas_totais == {1, 2}

    def test_congelar_sem_branch_usa_a_sentinela_e_nao_zero(self):
        acc = _acc()
        acc.registrar_linha(1, 1)
        assert acc.congelar().branches_totais == SEM_DADOS

    def test_congelar_prefere_o_detalhe_ao_resumo(self):
        """O resumo é por registro e contaria o mesmo branch duas vezes."""
        acc = _acc()
        acc.registrar_branch_lcov(1, "0", "0", 1)
        acc.resumo_branch = (99, 99)
        assert acc.congelar().branches_totais == 1

    def test_congelar_usa_o_resumo_quando_nao_ha_detalhe(self):
        acc = _acc()
        acc.resumo_branch = (3, 4)
        congelada = acc.congelar()
        assert (congelada.branches_cobertos, congelada.branches_totais) == (3, 4)


class TestCongelarTodos:
    def test_congelar_todos_fecha_cada_acumulador(self):
        acc = _acc()
        acc.registrar_linha(1, 1)
        assert set(_congelar_todos({"src/a.py": acc})) == {"src/a.py"}

    def test_congelar_todos_descarta_o_que_nao_fecha(self, caplog):
        quebrado = _acc("src/b.py")
        quebrado.hits_por_linha = "não é dicionário"
        with caplog.at_level(logging.WARNING, logger="jev_crap.metrica.cobertura"):
            resultado = _congelar_todos({"src/b.py": quebrado})
        assert resultado == {}
        assert "src/b.py" in caplog.text

    def test_congelar_todos_preserva_os_outros_arquivos(self):
        bom = _acc("src/a.py")
        bom.registrar_linha(1, 1)
        quebrado = _acc("src/b.py")
        quebrado.hits_por_linha = "não é dicionário"
        assert set(_congelar_todos({"a": bom, "b": quebrado})) == {"a"}

    def test_congelar_todos_de_nada_e_vazio(self):
        assert _congelar_todos({}) == {}


class TestLimpar:
    def test_limpar_tira_espacos(self):
        assert _limpar("  src/a.py  ") == "src/a.py"

    def test_limpar_troca_barra_invertida(self):
        assert _limpar("src\\a.py") == "src/a.py"

    def test_limpar_tira_o_esquema_de_arquivo(self):
        assert _limpar("file:///tmp/a.py") == "/tmp/a.py"

    def test_limpar_devolve_vazio_para_o_que_nao_e_texto(self):
        assert _limpar(None) == ""

    def test_limpar_de_vazio_e_vazio(self):
        assert _limpar("   ") == ""


class TestDentro:
    def test_dentro_aceita_o_proprio_caminho(self):
        assert _dentro("/proj", "/proj")

    def test_dentro_aceita_subcaminho(self):
        assert _dentro("/proj/src/a.py", "/proj")

    def test_dentro_recusa_irmao_com_prefixo_parecido(self):
        """Sem a barra, /proj-antigo contaria como dentro de /proj."""
        assert not _dentro("/proj-antigo/a.py", "/proj")

    def test_dentro_aceita_raiz_com_barra_no_fim(self):
        assert _dentro("/proj/a.py", "/proj/")

    def test_dentro_recusa_raiz_vazia(self):
        assert not _dentro("/proj/a.py", "")

    def test_dentro_recusa_caminho_vazio(self):
        assert not _dentro("", "/proj")


class TestInteiroLcov:
    def test_inteiro_lcov_le_um_numero(self):
        assert _inteiro_lcov("42") == 42

    def test_inteiro_lcov_trata_traco_como_zero(self):
        assert _inteiro_lcov("-") == 0

    def test_inteiro_lcov_trata_vazio_como_zero(self):
        assert _inteiro_lcov("") == 0

    def test_inteiro_lcov_trata_ilegivel_como_zero(self):
        assert _inteiro_lcov("12abc") == 0

    def test_inteiro_lcov_nunca_devolve_negativo(self):
        assert _inteiro_lcov("-5") == 0

    def test_inteiro_lcov_aceita_espacos_em_volta(self):
        assert _inteiro_lcov("  7 ") == 7

    def test_inteiro_lcov_nunca_levanta(self):
        for bruto in ("", "-", "x", None, "999999999999999999999999"):
            assert _inteiro_lcov(bruto) >= 0


class TestCwd:
    def test_cwd_devolve_o_diretorio_atual(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _cwd() == os.getcwd()

    def test_cwd_cai_no_ponto_quando_o_diretorio_sumiu(self, monkeypatch):
        def explode():
            raise FileNotFoundError("workspace apagado")

        monkeypatch.setattr(os, "getcwd", explode)
        assert _cwd() == "."



class TestNormalizarCaminho:
    """O caminho do relatório precisa casar com o do analisador de complexidade."""

    def test_normalizar_caminho_relativiza_absoluto_dentro_da_raiz(self):
        assert _normalizar_caminho("/proj/src/a.py", "/proj") == "src/a.py"

    def test_normalizar_caminho_preserva_absoluto_fora_da_raiz(self):
        assert _normalizar_caminho("/outro/a.py", "/proj") == "/outro/a.py"

    def test_normalizar_caminho_sem_raiz_devolve_o_relativo_como_veio(self):
        """Ancorar numa base qualquer produziria um absoluto de outra máquina."""
        assert _normalizar_caminho("src/a.py", None) == "src/a.py"

    def test_normalizar_caminho_usa_a_base_declarada_pelo_relatorio(self):
        assert _normalizar_caminho("src/a.py", "/proj", ["/proj"]) == "src/a.py"

    def test_normalizar_caminho_ignora_base_que_sai_da_raiz(self):
        assert _normalizar_caminho("src/a.py", "/proj", ["/outro"]) == "src/a.py"

    def test_normalizar_caminho_limpa_o_esquema_de_arquivo(self):
        assert _normalizar_caminho("file:///proj/src/a.py", "/proj") == "src/a.py"

    def test_normalizar_caminho_de_vazio_e_vazio(self):
        assert _normalizar_caminho("   ", "/proj") == ""

    def test_normalizar_caminho_nao_levanta_sem_diretorio_de_trabalho(self, monkeypatch):
        def explode():
            raise OSError("workspace apagado")

        monkeypatch.setattr(os, "getcwd", explode)
        assert _normalizar_caminho("src/a.py", None) == "src/a.py"


class TestParseLcov:
    def test_parse_lcov_le_um_registro_simples(self):
        linhas = ["SF:src/a.py", "DA:1,1", "DA:2,0", "end_of_record"]
        cob = _parse_lcov(linhas, None)["src/a.py"]
        assert cob.linhas_cobertas == {1}
        assert cob.linhas_totais == {1, 2}

    def test_parse_lcov_junta_o_mesmo_arquivo_em_dois_registros(self):
        linhas = ["SF:src/a.py", "DA:1,1", "end_of_record",
                  "SF:src/a.py", "DA:2,1", "end_of_record"]
        assert _parse_lcov(linhas, None)["src/a.py"].linhas_cobertas == {1, 2}

    def test_parse_lcov_le_o_detalhe_de_branch(self):
        linhas = ["SF:src/a.py", "DA:1,1", "BRDA:1,0,0,1", "BRDA:1,0,1,-", "end_of_record"]
        cob = _parse_lcov(linhas, None)["src/a.py"]
        assert (cob.branches_cobertos, cob.branches_totais) == (1, 2)

    def test_parse_lcov_usa_o_resumo_so_sem_detalhe(self):
        linhas = ["SF:src/a.py", "DA:1,1", "BRF:4", "BRH:2", "end_of_record"]
        cob = _parse_lcov(linhas, None)["src/a.py"]
        assert (cob.branches_cobertos, cob.branches_totais) == (2, 4)

    def test_parse_lcov_ignora_registro_desconhecido(self):
        linhas = ["SF:src/a.py", "FN:1,f", "FNDA:3,f", "DA:1,1", "end_of_record"]
        assert _parse_lcov(linhas, None)["src/a.py"].linhas_totais == {1}

    def test_parse_lcov_ignora_linha_malformada(self):
        linhas = ["SF:src/a.py", "DA:isto,nao,e,numero", "DA:1,1", "end_of_record"]
        assert _parse_lcov(linhas, None)["src/a.py"].linhas_totais == {1}

    def test_parse_lcov_de_nada_e_vazio(self):
        assert _parse_lcov([], None) == {}

    def test_parse_lcov_sem_branch_usa_a_sentinela(self):
        linhas = ["SF:src/a.py", "DA:1,1", "end_of_record"]
        assert _parse_lcov(linhas, None)["src/a.py"].branches_totais == SEM_DADOS


class TestParseCoberturaXml:
    def arvore(self, corpo: str):
        import xml.etree.ElementTree as ET

        return ET.fromstring(corpo)

    def test_parse_cobertura_xml_le_linhas(self):
        arvore = self.arvore(
            '<coverage><packages><package><classes>'
            '<class filename="src/a.py"><lines>'
            '<line number="1" hits="1"/><line number="2" hits="0"/>'
            "</lines></class></classes></package></packages></coverage>"
        )
        cob = _parse_cobertura_xml(arvore, None)["src/a.py"]
        assert cob.linhas_cobertas == {1}

    def test_parse_cobertura_xml_recusa_raiz_errada(self):
        with pytest.raises(FormatoDeCoberturaDesconhecido, match="esperada <coverage>"):
            _parse_cobertura_xml(self.arvore("<testsuite/>"), None)

    def test_parse_cobertura_xml_junta_classes_do_mesmo_arquivo(self):
        arvore = self.arvore(
            '<coverage><class filename="src/a.py"><lines><line number="1" hits="1"/>'
            '</lines></class><class filename="src/a.py"><lines>'
            '<line number="2" hits="1"/></lines></class></coverage>'
        )
        assert _parse_cobertura_xml(arvore, None)["src/a.py"].linhas_cobertas == {1, 2}

    def test_parse_cobertura_xml_pula_classe_sem_filename(self):
        arvore = self.arvore('<coverage><class><lines><line number="1" hits="1"/>'
                             "</lines></class></coverage>")
        assert _parse_cobertura_xml(arvore, None) == {}

    def test_parse_cobertura_xml_pula_linha_sem_numero(self):
        arvore = self.arvore('<coverage><class filename="src/a.py"><lines>'
                             '<line hits="1"/></lines></class></coverage>')
        assert _parse_cobertura_xml(arvore, None)["src/a.py"].linhas_totais == set()

    def test_parse_cobertura_xml_le_condition_coverage(self):
        arvore = self.arvore(
            '<coverage><class filename="src/a.py"><lines>'
            '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
            "</lines></class></coverage>"
        )
        cob = _parse_cobertura_xml(arvore, None)["src/a.py"]
        assert (cob.branches_cobertos, cob.branches_totais) == (1, 2)

    def test_parse_cobertura_xml_ignora_condition_coverage_torto(self):
        arvore = self.arvore(
            '<coverage><class filename="src/a.py"><lines>'
            '<line number="1" hits="1" branch="true" condition-coverage="parcial"/>'
            "</lines></class></coverage>"
        )
        assert _parse_cobertura_xml(arvore, None)["src/a.py"].branches_totais == SEM_DADOS


class TestCheirarFormato:
    def escrever(self, tmp_path, nome, conteudo):
        alvo = tmp_path / nome
        alvo.write_text(conteudo, encoding="utf-8")
        return str(alvo)

    def test_cheirar_formato_reconhece_lcov_por_sf(self, tmp_path):
        assert _cheirar_formato(self.escrever(tmp_path, "x.dat", "SF:a.py\n")) == "lcov"

    def test_cheirar_formato_reconhece_lcov_por_tn(self, tmp_path):
        assert _cheirar_formato(self.escrever(tmp_path, "x.dat", "TN:\nSF:a.py\n")) == "lcov"

    def test_cheirar_formato_reconhece_xml_pela_raiz(self, tmp_path):
        caminho = self.escrever(tmp_path, "x.dat", '<?xml version="1.0"?><coverage/>')
        assert _cheirar_formato(caminho) == "cobertura-xml"

    def test_cheirar_formato_recusa_xml_de_outra_raiz(self, tmp_path):
        caminho = self.escrever(tmp_path, "x.xml", "<testsuite/>")
        with pytest.raises(FormatoDeCoberturaDesconhecido, match="esperado <coverage>"):
            _cheirar_formato(caminho)

    def test_cheirar_formato_usa_a_extensao_como_desempate(self, tmp_path):
        assert _cheirar_formato(self.escrever(tmp_path, "x.info", "")) == "lcov"

    def test_cheirar_formato_reconhece_lcov_que_comeca_por_da(self, tmp_path):
        assert _cheirar_formato(self.escrever(tmp_path, "x.dat", "DA:1,1\n")) == "lcov"

    def test_cheirar_formato_recusa_o_que_nao_e_nenhum_dos_dois(self, tmp_path):
        caminho = self.escrever(tmp_path, "x.dat", "isto é um texto qualquer")
        with pytest.raises(FormatoDeCoberturaDesconhecido, match="não parece LCOV"):
            _cheirar_formato(caminho)


class TestLerDetectaOFormato:
    """`ler` decide pelo conteúdo: nome de arquivo é convenção, conteúdo é fato."""

    def test_ler_reconhece_lcov_com_extensao_inesperada(self, tmp_path):
        alvo = tmp_path / "cobertura.dat"
        alvo.write_text("SF:src/a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
        assert set(ler(str(alvo))) == {"src/a.py"}

    def test_ler_reconhece_xml_com_extensao_inesperada(self, tmp_path):
        alvo = tmp_path / "cobertura.dat"
        alvo.write_text(
            '<coverage><class filename="src/a.py"><lines>'
            '<line number="1" hits="1"/></lines></class></coverage>',
            encoding="utf-8",
        )
        assert set(ler(str(alvo))) == {"src/a.py"}

    def test_ler_recusa_arquivo_que_nao_e_relatorio(self, tmp_path):
        alvo = tmp_path / "leia.txt"
        alvo.write_text("um texto qualquer", encoding="utf-8")
        with pytest.raises(FormatoDeCoberturaDesconhecido):
            ler(str(alvo))

    def test_ler_propaga_arquivo_inexistente(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ler(str(tmp_path / "nao_existe.info"))

    def test_ler_repassa_a_raiz_para_a_normalizacao(self, tmp_path):
        alvo = tmp_path / "cobertura.info"
        alvo.write_text("SF:/proj/src/a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
        assert set(ler(str(alvo), raiz="/proj")) == {"src/a.py"}


class TestLerLcovAvisa:
    def test_ler_lcov_le_um_relatorio_real(self, tmp_path):
        alvo = tmp_path / "lcov.info"
        alvo.write_text("SF:src/a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
        assert set(ler_lcov(str(alvo))) == {"src/a.py"}

    def test_ler_lcov_registra_quando_o_relatorio_esta_vazio(self, tmp_path, caplog):
        """'Relatório vazio' e 'nenhum caminho casou' pedem correções opostas."""
        alvo = tmp_path / "lcov.info"
        alvo.write_text("", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="jev_crap.metrica.cobertura"):
            assert ler_lcov(str(alvo)) == {}
        assert "sem nenhum registro SF" in caplog.text

    def test_ler_lcov_nao_avisa_quando_ha_registro(self, tmp_path, caplog):
        alvo = tmp_path / "lcov.info"
        alvo.write_text("SF:src/a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="jev_crap.metrica.cobertura"):
            ler_lcov(str(alvo))
        assert caplog.text == ""

    def test_ler_lcov_aguenta_bom_de_ferramenta_windows(self, tmp_path):
        alvo = tmp_path / "lcov.info"
        alvo.write_bytes(b"\xef\xbb\xbfSF:src/a.py\nDA:1,1\nend_of_record\n")
        assert set(ler_lcov(str(alvo))) == {"src/a.py"}

    def test_ler_lcov_aguenta_byte_invalido(self, tmp_path):
        alvo = tmp_path / "lcov.info"
        alvo.write_bytes(b"SF:src/\xff.py\nDA:1,1\nend_of_record\n")
        assert ler_lcov(str(alvo))

    def test_ler_lcov_propaga_caminho_inexistente(self, tmp_path):
        with pytest.raises(OSError):
            ler_lcov(str(tmp_path / "nao_existe.info"))
