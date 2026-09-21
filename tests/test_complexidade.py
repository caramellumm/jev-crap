"""Testes do módulo de complexidade.

As amostras em `tests/fixtures/` são arquivos reais de duas linguagens com
complexidade calculada à mão (o cálculo está no comentário de cada função).
Se o lizard mudar de contagem numa atualização, estes testes quebram — que é
exatamente o aviso que queremos, porque a nota de risco do projeto inteiro é
construída em cima desses números.
"""

from __future__ import annotations

import logging
from pathlib import Path

import lizard
import pytest

from jev_crap.metrica.complexidade import (
    NOME_DESCONHECIDO,
    Funcao,
    _arquivos_candidatos,
    _excluido,
    _funcoes_do_arquivo,
    _linguagem,
    _nome,
    _pular_pasta_ilegivel,
    analisar,
)

FIXTURES = Path(__file__).parent / "fixtures"
AMOSTRA_PY = FIXTURES / "amostra.py"
AMOSTRA_JS = FIXTURES / "amostra.js"


def por_nome(funcoes: list[Funcao]) -> dict[str, Funcao]:
    return {f.nome: f for f in funcoes}


# --- a contagem em si -------------------------------------------------------


def test_python_complexidade_por_funcao() -> None:
    funcoes = por_nome(analisar([str(AMOSTRA_PY)]))

    assert set(funcoes) == {"soma", "classifica", "total"}
    assert funcoes["soma"].complexidade == 1
    assert funcoes["classifica"].complexidade == 4
    assert funcoes["total"].complexidade == 4


def test_javascript_complexidade_por_funcao() -> None:
    funcoes = por_nome(analisar([str(AMOSTRA_JS)]))

    assert set(funcoes) == {"soma", "classifica", "total"}
    assert funcoes["soma"].complexidade == 1
    assert funcoes["classifica"].complexidade == 4
    assert funcoes["total"].complexidade == 4


def test_as_duas_linguagens_dao_o_mesmo_numero() -> None:
    """A métrica precisa ser comparável entre linguagens para o relatório somar."""
    python = {f.nome: f.complexidade for f in analisar([str(AMOSTRA_PY)])}
    javascript = {f.nome: f.complexidade for f in analisar([str(AMOSTRA_JS)])}

    assert python == javascript


# --- os demais campos -------------------------------------------------------


def test_campos_da_funcao_python() -> None:
    soma = por_nome(analisar([str(AMOSTRA_PY)]))["soma"]

    assert soma.arquivo.endswith("amostra.py")
    assert soma.linguagem == "python"
    assert soma.parametros == 2
    assert soma.linha_inicio < soma.linha_fim
    assert soma.linhas_logicas == 2  # def + return; docstring não é linha lógica


def test_campos_da_funcao_javascript() -> None:
    total = por_nome(analisar([str(AMOSTRA_JS)]))["total"]

    assert total.arquivo.endswith("amostra.js")
    assert total.linguagem == "javascript"
    assert total.parametros == 1
    assert total.linha_inicio < total.linha_fim


def test_linhas_logicas_ignoram_documentacao(tmp_path: Path) -> None:
    """Documentar uma função não pode fazer ela parecer maior do que é."""
    (tmp_path / "com.py").write_text(
        'def f(a, b):\n    """Explica.\n\n    Em várias linhas.\n    """\n    return a + b\n',
        encoding="utf-8",
    )
    (tmp_path / "sem.py").write_text("def f(a, b):\n    return a + b\n", encoding="utf-8")

    com, sem = analisar([str(tmp_path / "com.py")]), analisar([str(tmp_path / "sem.py")])

    assert com[0].linhas_logicas == sem[0].linhas_logicas == 2


def test_linhas_da_funcao_apontam_para_o_corpo_certo() -> None:
    """O cruzamento com cobertura usa essa faixa; se ela mentir, tudo depois mente."""
    classifica = por_nome(analisar([str(AMOSTRA_PY)]))["classifica"]
    linhas = AMOSTRA_PY.read_text(encoding="utf-8").splitlines()

    assert linhas[classifica.linha_inicio - 1].startswith("def classifica(")
    assert 'return "F"' in linhas[classifica.linha_fim - 1]


# --- varredura de diretório -------------------------------------------------


def test_diretorio_varre_recursivamente_as_duas_linguagens() -> None:
    funcoes = analisar([str(FIXTURES)])

    assert {f.linguagem for f in funcoes} == {"python", "javascript"}
    assert len(funcoes) == 6


def test_subdiretorio_e_alcancado(tmp_path: Path) -> None:
    fundo = tmp_path / "a" / "b" / "c"
    fundo.mkdir(parents=True)
    (fundo / "fundo.py").write_text("def f(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)])

    assert [f.nome for f in funcoes] == ["f"]


def test_varredura_e_deterministica(tmp_path: Path) -> None:
    for nome in ("z.py", "a.py", "m.py"):
        (tmp_path / nome).write_text("def f(x):\n    return x\n", encoding="utf-8")

    primeira = [f.arquivo for f in analisar([str(tmp_path)])]
    segunda = [f.arquivo for f in analisar([str(tmp_path)])]

    assert primeira == segunda
    assert primeira == sorted(primeira)


def test_arquivo_repetido_nao_duplica(tmp_path: Path) -> None:
    arquivo = tmp_path / "unico.py"
    arquivo.write_text("def f(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path), str(arquivo)])

    assert len(funcoes) == 1


# --- exclusões --------------------------------------------------------------


@pytest.mark.parametrize("lixo", ["node_modules", ".venv", "__pycache__", "dist", "build", ".git"])
def test_ignora_diretorios_de_terceiros_por_padrao(tmp_path: Path, lixo: str) -> None:
    (tmp_path / "meu.py").write_text("def meu(x):\n    return x\n", encoding="utf-8")
    poluido = tmp_path / lixo
    poluido.mkdir()
    (poluido / "alheio.py").write_text("def alheio(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)])

    assert [f.nome for f in funcoes] == ["meu"]


def test_exclusao_adicional_por_nome_de_pasta(tmp_path: Path) -> None:
    (tmp_path / "meu.py").write_text("def meu(x):\n    return x\n", encoding="utf-8")
    gerado = tmp_path / "gerado"
    gerado.mkdir()
    (gerado / "proto.py").write_text("def proto(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)], excluir=["gerado"])

    assert [f.nome for f in funcoes] == ["meu"]


def test_exclusao_adicional_por_glob_de_arquivo(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("function app(x) { return x; }\n", encoding="utf-8")
    (tmp_path / "app.min.js").write_text("function apx(x) { return x; }\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)], excluir=["*.min.js"])

    assert [f.nome for f in funcoes] == ["app"]


def test_exclusao_com_barra_casa_subcaminho(tmp_path: Path) -> None:
    antigo = tmp_path / "src" / "legado"
    antigo.mkdir(parents=True)
    (antigo / "velho.py").write_text("def velho(x):\n    return x\n", encoding="utf-8")
    novo = tmp_path / "src" / "novo.py"
    novo.write_text("def novo(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)], excluir=["src/legado"])

    assert [f.nome for f in funcoes] == ["novo"]


def test_pasta_acima_da_raiz_analisada_nao_exclui_nada(tmp_path: Path) -> None:
    """Regressão: o projeto pode morar dentro de uma pasta chamada `build`.

    As exclusões valem do ponto analisado para baixo. Se elas olhassem o caminho
    absoluto inteiro, quem guarda o checkout em `~/build/projeto` receberia um
    relatório vazio sem nenhum aviso — o pior tipo de falha para esta ferramenta.
    """
    dentro = tmp_path / "build" / "meuprojeto" / "src"
    dentro.mkdir(parents=True)
    (dentro / "app.py").write_text("def app(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path / "build" / "meuprojeto")])

    assert [f.nome for f in funcoes] == ["app"]


def test_raiz_analisada_com_nome_excluido_ainda_e_varrida(tmp_path: Path) -> None:
    """Apontar para `dist/` é pedido explícito, igual a apontar para um arquivo."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "empacotado.js").write_text("function emp(x) { return x; }\n", encoding="utf-8")

    funcoes = analisar([str(dist)])

    assert [f.nome for f in funcoes] == ["emp"]


def test_arquivo_apontado_explicitamente_vence_a_exclusao(tmp_path: Path) -> None:
    """Quem aponta o dedo para um arquivo quer aquele arquivo (regra do ripgrep)."""
    pasta = tmp_path / "node_modules"
    pasta.mkdir()
    alvo = pasta / "alheio.js"
    alvo.write_text("function alheio(x) { return x; }\n", encoding="utf-8")

    funcoes = analisar([str(alvo)])

    assert [f.nome for f in funcoes] == ["alheio"]


# --- linguagens fora do alcance do lizard -----------------------------------


def test_extensao_sem_suporte_e_pulada_em_silencio(tmp_path: Path) -> None:
    (tmp_path / "LEIAME.md").write_text("# nada de código aqui\n", encoding="utf-8")
    (tmp_path / "dados.json").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "notas.txt").write_text("texto solto\n", encoding="utf-8")
    (tmp_path / "bom.py").write_text("def bom(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)])

    assert [f.nome for f in funcoes] == ["bom"]


def test_arquivo_ilegivel_nao_derruba_a_varredura(tmp_path: Path) -> None:
    """Um arquivo torto não pode zerar o relatório do repositório inteiro."""
    (tmp_path / "quebrado.py").write_bytes(b"\x00\x01\x02 n\xe3o \xff decodifica")
    (tmp_path / "bom.py").write_text("def bom(x):\n    return x\n", encoding="utf-8")

    funcoes = analisar([str(tmp_path)])

    assert [f.nome for f in funcoes] == ["bom"]


def test_caminho_inexistente_levanta_erro(tmp_path: Path) -> None:
    """Erro de digitação do usuário não pode virar 'nenhuma função encontrada'."""
    with pytest.raises(FileNotFoundError):
        analisar([str(tmp_path / "nao-existe")])


# --- nome e chave -----------------------------------------------------------


def test_nome_qualificado_vira_ponto(tmp_path: Path) -> None:
    """Java/C++ vêm com `Classe::metodo`; o relatório usa ponto em toda linguagem."""
    (tmp_path / "Caixa.java").write_text(
        "public class Caixa {\n"
        "    public int soma(int a, int b) {\n"
        "        if (a > b) { return a; }\n"
        "        return b;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    funcoes = analisar([str(tmp_path)])

    assert [f.nome for f in funcoes] == ["Caixa.soma"]
    assert funcoes[0].linguagem == "java"


def test_chave_separa_funcoes_de_mesmo_nome(tmp_path: Path) -> None:
    """Nome não é chave: anônimas e homônimas existem. Arquivo + linha é."""
    (tmp_path / "duplo.js").write_text(
        "const a = [1].map((x) => x + 1);\nconst b = [2].map((x) => x + 2);\n",
        encoding="utf-8",
    )

    funcoes = analisar([str(tmp_path)])

    assert len({f.nome for f in funcoes}) < len(funcoes)
    assert len({f.identidade for f in funcoes}) == len(funcoes)


def test_chave_repete_entre_execucoes() -> None:
    """A chave precisa sobreviver entre rodadas para cruzar com a cobertura."""
    primeira = sorted(f.identidade for f in analisar([str(AMOSTRA_PY)]))
    segunda = sorted(f.identidade for f in analisar([str(AMOSTRA_PY)]))

    assert primeira == segunda
    assert all(chave.startswith(str(AMOSTRA_PY)) for chave in primeira)


# --- contrato do dataclass --------------------------------------------------


def test_funcao_e_imutavel_e_hashavel() -> None:
    funcao = analisar([str(AMOSTRA_PY)])[0]

    with pytest.raises(Exception):  # noqa: B017 - frozen levanta FrozenInstanceError
        funcao.complexidade = 99  # type: ignore[misc]
    assert len({funcao, funcao}) == 1


def test_lista_vazia_de_caminhos_nao_reclama() -> None:
    assert analisar([]) == []


class TestIdentidade:
    """`Funcao.identidade` é o que cruza a medição com o relatório de cobertura."""

    def uma(self, **ajustes):
        campos = dict(
            arquivo="src/a.py", nome="f", linha_inicio=3, linha_fim=9,
            complexidade=1, linhas_logicas=4, parametros=0, linguagem="python",
        )
        return Funcao(**{**campos, **ajustes})

    def test_identidade_junta_arquivo_e_linha_inicial(self):
        assert self.uma().identidade == "src/a.py:3"

    def test_identidade_distingue_homonimas_no_mesmo_arquivo(self):
        assert self.uma(linha_inicio=3).identidade != self.uma(linha_inicio=40).identidade

    def test_identidade_recusa_arquivo_vazio(self):
        with pytest.raises(ValueError, match="sem arquivo não tem identidade"):
            _ = self.uma(arquivo="").identidade

    def test_identidade_recusa_arquivo_so_de_espacos(self):
        with pytest.raises(ValueError, match="sem arquivo"):
            _ = self.uma(arquivo="   ").identidade

    def test_identidade_recusa_linha_zero(self):
        with pytest.raises(ValueError, match="linhas começam em 1"):
            _ = self.uma(linha_inicio=0).identidade

    def test_identidade_recusa_linha_negativa(self):
        with pytest.raises(ValueError, match="linha_inicio"):
            _ = self.uma(linha_inicio=-2).identidade

    def test_identidade_cita_o_nome_da_funcao_no_erro(self):
        with pytest.raises(ValueError, match="orfa"):
            _ = self.uma(arquivo="", nome="orfa").identidade


class TestExcluido:
    def test_excluido_casa_padrao_sem_barra_em_qualquer_componente(self):
        assert _excluido("a/node_modules/x.js", ["node_modules"])

    def test_excluido_casa_glob_de_arquivo(self):
        assert _excluido("a/app.min.js", ["*.min.js"])

    def test_excluido_casa_padrao_com_barra_como_sufixo(self):
        assert _excluido("app/src/legado", ["src/legado"])

    def test_excluido_casa_padrao_com_barra_no_caminho_inteiro(self):
        assert _excluido("src/legado", ["src/legado"])

    def test_excluido_recusa_o_que_nao_casa(self):
        assert not _excluido("src/app.py", ["node_modules", "*.min.js"])

    def test_excluido_ignora_padrao_vazio(self):
        assert not _excluido("src/app.py", [""])

    def test_excluido_trata_padrao_invalido_como_nao_casa(self):
        assert not _excluido("src/app.py", ["[nao-fecha"])

    def test_excluido_segue_avaliando_os_padroes_depois_de_um_invalido(self):
        assert _excluido("src/app.py", ["[nao-fecha", "src/app.py"])

    def test_excluido_sem_padrao_nenhum_nao_exclui(self):
        assert not _excluido("src/app.py", [])


class TestNome:
    def test_nome_troca_separador_de_escopo_por_ponto(self):
        assert _nome("Classe::metodo") == "Classe.metodo"

    def test_nome_colapsa_espacos_internos(self):
        assert _nome("  a   b  ") == "a b"

    def test_nome_preserva_nome_simples(self):
        assert _nome("rotina_qualquer") == "rotina_qualquer"

    def test_nome_devolve_marcador_para_texto_vazio(self):
        assert _nome("") == NOME_DESCONHECIDO

    def test_nome_devolve_marcador_para_texto_so_de_espacos(self):
        assert _nome("   \t ") == NOME_DESCONHECIDO

    def test_nome_devolve_marcador_para_valor_que_nao_e_texto(self):
        assert _nome(None) == NOME_DESCONHECIDO

    def test_nome_nunca_devolve_vazio(self):
        for bruto in ("", "  ", None, 7, "::"):
            assert _nome(bruto)


class TestLinguagem:
    class LeitorSemNomes:
        pass

    class LeitorComNomes:
        language_names = ("rust",)

    class LeitorComLixo:
        language_names = 123

    def test_linguagem_usa_o_mapa_proprio_quando_conhece_a_extensao(self):
        assert _linguagem("a/b.h", self.LeitorComNomes) == "c"

    def test_linguagem_cai_no_rotulo_do_lizard_para_extensao_de_fora(self):
        assert _linguagem("a/b.zig", self.LeitorComNomes) == "rust"

    def test_linguagem_usa_a_extensao_quando_o_leitor_nao_diz(self):
        assert _linguagem("a/b.zig", self.LeitorSemNomes) == "zig"

    def test_linguagem_ignora_language_names_de_tipo_errado(self):
        assert _linguagem("a/b.zig", self.LeitorComLixo) == "zig"

    def test_linguagem_devolve_desconhecida_sem_extensao(self):
        assert _linguagem("Makefile", self.LeitorSemNomes) == "desconhecida"

    def test_linguagem_nunca_devolve_vazio(self):
        for caminho in ("a/b.py", "a/b.zig", "Makefile", ""):
            assert _linguagem(caminho, self.LeitorSemNomes)


class TestArquivosCandidatos:
    def test_arquivos_candidatos_rende_o_arquivo_apontado_diretamente(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("x", encoding="utf-8")
        assert list(_arquivos_candidatos(str(alvo), ())) == [str(alvo)]

    def test_arquivos_candidatos_desce_em_diretorio(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.py").write_text("x", encoding="utf-8")
        assert len(list(_arquivos_candidatos(str(tmp_path), ()))) == 1

    def test_arquivos_candidatos_poda_pasta_excluida(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "a.py").write_text("x", encoding="utf-8")
        assert list(_arquivos_candidatos(str(tmp_path), ("node_modules",))) == []

    def test_arquivos_candidatos_levanta_para_caminho_inexistente(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="inexistente"):
            list(_arquivos_candidatos(str(tmp_path / "nada"), ()))

    def test_arquivos_candidatos_distingue_ilegivel_de_inexistente(self, tmp_path, monkeypatch):
        def explode(_self):
            raise PermissionError("sem execução")

        monkeypatch.setattr(Path, "exists", explode)
        with pytest.raises(FileNotFoundError, match="ilegível"):
            list(_arquivos_candidatos(str(tmp_path), ()))

    def test_arquivos_candidatos_visita_em_ordem_alfabetica(self, tmp_path):
        for nome in ("c.py", "a.py", "b.py"):
            (tmp_path / nome).write_text("x", encoding="utf-8")
        achados = [Path(c).name for c in _arquivos_candidatos(str(tmp_path), ())]
        assert achados == ["a.py", "b.py", "c.py"]


class TestPularPastaIlegivel:
    def test_pular_pasta_ilegivel_nao_levanta(self):
        assert _pular_pasta_ilegivel(PermissionError("x")) is None

    def test_pular_pasta_ilegivel_registra_o_caminho(self, caplog):
        erro = PermissionError("negado")
        erro.filename = "/proibido"
        with caplog.at_level(logging.DEBUG, logger="jev_crap.metrica.complexidade"):
            _pular_pasta_ilegivel(erro)
        assert "/proibido" in caplog.text

    def test_pular_pasta_ilegivel_aceita_erro_sem_filename(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="jev_crap.metrica.complexidade"):
            _pular_pasta_ilegivel(OSError("sem nome"))
        assert "?" in caplog.text


class TestFuncoesDoArquivo:
    def test_funcoes_do_arquivo_mede_um_python_real(self):
        assert _funcoes_do_arquivo(str(AMOSTRA_PY))

    def test_funcoes_do_arquivo_pula_extensao_que_o_lizard_nao_le(self, tmp_path):
        alvo = tmp_path / "leia.md"
        alvo.write_text("# título", encoding="utf-8")
        assert _funcoes_do_arquivo(str(alvo)) == []

    def test_funcoes_do_arquivo_devolve_vazio_quando_a_analise_explode(self, monkeypatch):
        def explode(_caminho):
            raise RuntimeError("tokenizador")

        monkeypatch.setattr(lizard, "analyze_file", explode)
        assert _funcoes_do_arquivo(str(AMOSTRA_PY)) == []

    def test_funcoes_do_arquivo_nao_levanta_com_arquivo_inexistente(self, tmp_path):
        assert _funcoes_do_arquivo(str(tmp_path / "nao_existe.py")) == []

    def test_funcoes_do_arquivo_normaliza_o_caminho(self, tmp_path):
        alvo = tmp_path / "a.py"
        alvo.write_text("def f():\n    return 1\n", encoding="utf-8")
        medidas = _funcoes_do_arquivo(f"{tmp_path}/./a.py")
        assert medidas[0].arquivo == str(alvo)
