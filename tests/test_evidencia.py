"""A coleta de trechos de teste: o que conta como teste e o que não conta.

Duas sutilezas custaram, cada uma, um erro real na geração anterior — o falso
positivo do projeto que mora numa pasta chamada `*test*`, e o recorte que
começava no import em vez do corpo do teste. As duas têm teste próprio aqui.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_crap.evidencia import (
    ALCANCE_DO_CABECALHO,
    JANELA_DEPOIS,
    MARCAS_DE_TESTE,
    _arquivos_de_teste,
    _trecho_ao_redor,
    parece_teste,
    testes_de,
)


class TestPareceTeste:
    @pytest.mark.parametrize(
        "caminho",
        ["tests/test_a.py", "test/a.py", "src/a_test.go", "spec/b.js", "conftest.py",
         "__mocks__/api.ts", "fixtures/dados.py"],
    )
    def test_reconhece_as_convencoes(self, caminho):
        assert parece_teste(caminho) is True

    @pytest.mark.parametrize("caminho", ["src/app.py", "lib/protesto.py", "a/contestar.py"])
    def test_substring_pega_o_plural_mas_tambem_pega_demais(self, caminho):
        """A comparação é por substring porque a convenção dominante é `tests/`
        no plural, e igualdade contra "test" não pegaria nenhuma pasta real. O
        preço é `protesto` e `contestar` — aceito, porque o erro cai para o lado
        de não avaliar um arquivo, e não para o de avaliar o teste."""
        esperado = any(m in caminho.lower() for m in MARCAS_DE_TESTE)
        assert parece_teste(caminho) is esperado

    def test_so_olha_abaixo_da_raiz(self):
        """Sem isso, um projeto em `~/dev/jev-crap-test/` teria todo o código
        classificado como teste, e a varredura devolveria zero função."""
        alvo = "/home/u/dev/jev-crap-test/src/app.py"
        assert parece_teste(alvo) is True
        assert parece_teste(alvo, "/home/u/dev/jev-crap-test") is False

    def test_fora_da_raiz_erra_para_o_lado_seguro(self):
        """Caminho que não está sob a raiz: comparar tudo inclui demais, que é
        melhor do que incluir de menos."""
        assert parece_teste("/outro/lugar/test_a.py", "/home/u/projeto") is True


class TestTechosDeTeste:
    @pytest.fixture
    def suite(self, tmp_path):
        pasta = tmp_path / "tests"
        pasta.mkdir()
        (pasta / "test_conciliacao.py").write_text(
            "from src.conciliacao import conciliar\n"
            "import pytest\n"
            "\n"
            "\n"
            "def test_conciliar_com_lote_vazio():\n"
            "    assert conciliar([]) == []\n"
            "\n"
            "\n"
            "def test_conciliar_soma_os_valores():\n"
            "    assert conciliar([1, 2]) == 3\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_recorta_a_partir_do_cabecalho_do_teste(self, suite):
        """Sem voltar ao `def`, o trecho começa no meio de uma asserção e o
        modelo não vê o que estava sendo montado."""
        trechos = testes_de("conciliar", suite / "tests")
        assert trechos
        corpo = trechos[0]
        assert "def test_conciliar_com_lote_vazio():" in corpo
        assert "assert conciliar([]) == []" in corpo

    def test_import_nao_conta_como_teste(self, suite):
        """`from x import nome` passa por qualquer filtro de linha, porque a
        linha em si é só o nome. Menção fora de teste não é teste."""
        for trecho in testes_de("conciliar", suite / "tests"):
            primeira_linha_de_codigo = trecho.splitlines()[1]
            assert not primeira_linha_de_codigo.startswith("from ")

    def test_nome_ausente_devolve_lista_vazia(self, suite):
        """Lista vazia é resposta legítima: a pergunta sobre teste deixa de ser
        feita e o peso é redistribuído, em vez de a função levar zero."""
        assert testes_de("nao_existe_em_lugar_nenhum", suite / "tests") == []

    def test_respeita_o_teto_de_trechos(self, suite):
        assert len(testes_de("conciliar", suite / "tests", maximo=1)) == 1

    def test_nome_parcial_nao_casa(self, suite):
        """A busca é por palavra inteira: `concilia` não é `conciliar`, e casar
        por prefixo mandaria o teste da função errada ao modelo."""
        assert testes_de("concilia", suite / "tests") == []

    @pytest.mark.parametrize("pasta", [None, "", "/nao/existe"])
    def test_pasta_ausente_nao_levanta(self, pasta):
        assert testes_de("f", pasta) == []

    def test_arquivo_que_nao_e_teste_e_ignorado(self, tmp_path):
        codigo = tmp_path / "src"
        codigo.mkdir()
        (codigo / "outro.py").write_text("def f():\n    conciliar()\n", encoding="utf-8")
        assert testes_de("conciliar", codigo) == []

    def test_javascript_tambem(self, tmp_path):
        pasta = tmp_path / "spec"
        pasta.mkdir()
        (pasta / "carrinho.spec.js").write_text(
            "describe('carrinho', () => {\n"
            "  it('soma os itens', () => {\n"
            "    expect(somarCarrinho([1, 2])).toBe(3)\n"
            "  })\n"
            "})\n",
            encoding="utf-8",
        )
        trechos = testes_de("somarCarrinho", pasta)
        assert trechos and "it('soma os itens'" in trechos[0]


class TestArquivosDeTeste:
    """`_arquivos_de_teste` percorre o disco: o que ele engole importa."""

    def test_arquivos_de_teste_acha_o_que_parece_teste(self, tmp_path):
        (tmp_path / "tests").mkdir()
        alvo = tmp_path / "tests" / "test_x.py"
        alvo.write_text("def test_x(): pass", encoding="utf-8")
        assert list(_arquivos_de_teste(tmp_path / "tests")) == [alvo]

    def test_arquivos_de_teste_ignora_extensao_de_fora(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.md").write_text("texto", encoding="utf-8")
        assert list(_arquivos_de_teste(tmp_path / "tests")) == []

    def test_arquivos_de_teste_ignora_pasta_podada(self, tmp_path):
        alvo = tmp_path / "tests" / "node_modules"
        alvo.mkdir(parents=True)
        (alvo / "test_x.py").write_text("x", encoding="utf-8")
        assert list(_arquivos_de_teste(tmp_path / "tests")) == []

    def test_arquivos_de_teste_devolve_vazio_para_raiz_inexistente(self, tmp_path):
        assert list(_arquivos_de_teste(tmp_path / "nao_existe")) == []

    def test_arquivos_de_teste_nao_levanta_quando_a_listagem_falha(self, tmp_path, monkeypatch):
        def explode(_self, _padrao):
            raise PermissionError("sem leitura")

        monkeypatch.setattr(Path, "rglob", explode)
        assert list(_arquivos_de_teste(tmp_path)) == []

    def test_arquivos_de_teste_pula_arquivo_cujo_is_file_falha(self, tmp_path, monkeypatch):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("x", encoding="utf-8")

        def explode(_self):
            raise OSError("link quebrado")

        monkeypatch.setattr(Path, "is_file", explode)
        assert list(_arquivos_de_teste(tmp_path / "tests")) == []

    def test_arquivos_de_teste_devolve_em_ordem_estavel(self, tmp_path):
        pasta = tmp_path / "tests"
        pasta.mkdir()
        for nome in ("test_c.py", "test_a.py", "test_b.py"):
            (pasta / nome).write_text("x", encoding="utf-8")
        nomes = [a.name for a in _arquivos_de_teste(pasta)]
        assert nomes == sorted(nomes)


class TestTrechoAoRedor:
    TEXTO = "import x\n\ndef test_soma():\n    assert soma(1, 2) == 3\n"

    def test_trecho_ao_redor_comeca_no_cabecalho_do_teste(self):
        posicao = self.TEXTO.index("soma(1, 2)")
        assert _trecho_ao_redor(self.TEXTO, posicao).startswith("def test_soma():")

    def test_trecho_ao_redor_devolve_none_sem_cabecalho_antes(self):
        assert _trecho_ao_redor("assert soma(1) == 1", 7) is None

    def test_trecho_ao_redor_devolve_none_com_cabecalho_longe_demais(self):
        texto = "def test_x():\n" + " " * (ALCANCE_DO_CABECALHO + 10) + "soma()"
        assert _trecho_ao_redor(texto, len(texto) - 3) is None

    def test_trecho_ao_redor_devolve_none_para_texto_vazio(self):
        assert _trecho_ao_redor("", 0) is None

    def test_trecho_ao_redor_devolve_none_para_posicao_negativa(self):
        posicao = self.TEXTO.index("soma(1, 2)")
        assert _trecho_ao_redor(self.TEXTO, -posicao) is None

    def test_trecho_ao_redor_devolve_none_para_posicao_alem_do_fim(self):
        assert _trecho_ao_redor(self.TEXTO, len(self.TEXTO) + 1) is None

    def test_trecho_ao_redor_aceita_posicao_no_ultimo_indice(self):
        assert _trecho_ao_redor(self.TEXTO, len(self.TEXTO)) is not None

    def test_trecho_ao_redor_nao_passa_da_janela_depois(self):
        cauda = "z" * (JANELA_DEPOIS * 2)
        texto = f"def test_x():\n    soma()\n{cauda}"
        posicao = texto.index("soma()")
        assert len(_trecho_ao_redor(texto, posicao)) <= posicao + JANELA_DEPOIS
