"""Testes do módulo de cobertura.

Os testes leem fixtures reais em ``tests/fixtures/`` (um ``lcov.info`` e um
``coverage.xml``, ambos com branch) em vez de só montar strings inline: o
objetivo é garantir que o parse aguenta o formato como os geradores realmente
escrevem, com DOCTYPE, tabulação, registros ``FN`` que ignoramos e caminho
absoluto de máquina de CI. Casos de borda que nenhum gerador emite junto no
mesmo arquivo ficam em strings inline gravadas em ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_crap.metrica.cobertura import (
    SEM_DADOS,
    CoberturaArquivo,
    FormatoDeCoberturaDesconhecido,
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
