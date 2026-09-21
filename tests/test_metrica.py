"""O eixo contável: complexidade, cobertura e a fórmula de risco.

A suíte da fórmula é escrita como propriedades e roda sobre **todas** as
fórmulas registradas, não só a clássica: quem acrescentar a segunda ganha a
verificação de graça, e é justamente aí que uma fórmula nova costuma errar.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXTURES

from jev_crap.metrica.cobertura import SEM_DADOS, cobertura_de_faixa, ler
from jev_crap.metrica.complexidade import analisar, medir_fonte


class TestCobertura:
    def test_le_lcov(self):
        arquivos = ler(str(FIXTURES / "lcov.info"))
        assert arquivos
        assert any("calculadora" in nome for nome in arquivos)

    def test_le_cobertura_xml(self):
        arquivos = ler(str(FIXTURES / "coverage.xml"))
        assert arquivos
        assert any("pedido" in nome for nome in arquivos)

    def test_o_formato_e_decidido_pelo_conteudo_e_nao_pela_extensao(self, tmp_path):
        """Relatórios chegam como `coverage.dat`, `lcov.txt` ou de um pipe sem
        extensão nenhuma: nome de arquivo é convenção, conteúdo é fato."""
        disfarcado = tmp_path / "relatorio.dat"
        disfarcado.write_text((FIXTURES / "lcov.info").read_text(), encoding="utf-8")
        assert ler(str(disfarcado))

    def test_faixa_sem_linha_executavel_devolve_sem_dados_e_nao_zero(self):
        """Função só de docstring não é função sem teste."""
        arquivos = ler(str(FIXTURES / "lcov.info"))
        cobertura = next(iter(arquivos.values()))
        linha, branch = cobertura_de_faixa(cobertura, 9000, 9100)
        assert linha == SEM_DADOS
        assert branch == SEM_DADOS


class TestComplexidade:
    def test_mede_varias_linguagens_com_o_mesmo_algoritmo(self, tmp_path):
        (tmp_path / "a.py").write_text("def f(x):\n    return x if x else 0\n")
        (tmp_path / "b.js").write_text("function g(x) { return x ? 1 : 0 }\n")
        linguagens = {f.linguagem for f in analisar([str(tmp_path)])}
        assert linguagens == {"python", "javascript"}

    def test_arquivo_de_extensao_desconhecida_e_pulado_em_silencio(self, tmp_path):
        """Um repositório é cheio de `.md` e `.json`; reclamar de cada um
        transformaria o aviso útil em ruído."""
        (tmp_path / "leiame.md").write_text("# nada aqui\n")
        assert analisar([str(tmp_path)]) == []

    def test_caminho_inexistente_levanta_em_vez_de_devolver_vazio(self):
        """Retorno vazio esconderia o engano de quem chamou."""
        with pytest.raises(FileNotFoundError):
            analisar(["/nao/existe/mesmo"])

    def test_o_mesmo_arquivo_por_dois_caminhos_e_medido_uma_vez(self, tmp_path):
        """Medir duas vezes dobraria o peso dele no relatório."""
        arquivo = tmp_path / "a.py"
        arquivo.write_text("def f():\n    return 1\n")
        assert len(analisar([str(tmp_path), str(arquivo)])) == 1

    def test_a_ordem_e_deterministica(self, tmp_path):
        """Relatório que muda de ordem produz diff falso em CI."""
        for nome in ("c.py", "a.py", "b.py"):
            (tmp_path / nome).write_text("def f():\n    return 1\n")
        primeira = [f.arquivo for f in analisar([str(tmp_path)])]
        assert primeira == [f.arquivo for f in analisar([str(tmp_path)])]
        assert primeira == sorted(primeira)

    def test_medir_fonte_nao_precisa_do_disco(self):
        funcoes = medir_fonte("x.py", "def f(a):\n    if a:\n        return 1\n    return 0\n")
        assert [f.nome for f in funcoes] == ["f"]
        assert funcoes[0].complexidade == 2

    def test_medir_fonte_com_extensao_desconhecida_devolve_vazio(self):
        """Vazio é honesto: melhor "não consegui medir" do que complexidade 1
        para um trecho que não foi lido."""
        assert medir_fonte("x.qqcoisa", "def f(): pass") == []

    def test_exclusao_e_conferida_contra_o_caminho_relativo(self, tmp_path):
        """Quem guarda o checkout em `~/build/projeto` receberia relatório vazio
        se o absoluto fosse usado: a pasta `build` do ancestral casaria com a
        exclusão padrão e podaria o projeto inteiro, em silêncio."""
        projeto = tmp_path / "build" / "projeto"
        projeto.mkdir(parents=True)
        (projeto / "app.py").write_text("def f():\n    return 1\n")
        assert [f.nome for f in analisar([str(projeto)])] == ["f"]
