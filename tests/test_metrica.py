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
from jev_crap.metrica.risco import (
    Insumos,
    formulas_disponiveis,
    obter_formula,
    registrar_formula,
)


@pytest.fixture(params=formulas_disponiveis())
def formula(request):
    return obter_formula(request.param)


class TestPropriedadesDeQualquerFormula:
    def test_mais_cobertura_nunca_aumenta_o_risco(self, formula):
        anterior = None
        for cobertura in (0.0, 0.25, 0.5, 0.75, 1.0):
            valor = formula.calcular(Insumos(10, cobertura, None, 30))
            if anterior is not None:
                assert valor <= anterior
            anterior = valor

    def test_mais_complexidade_nunca_diminui_o_risco(self, formula):
        anterior = None
        for complexidade in (1, 2, 5, 10, 25):
            valor = formula.calcular(Insumos(complexidade, 0.5, None, 30))
            if anterior is not None:
                assert valor >= anterior
            anterior = valor

    def test_mesma_entrada_mesma_saida(self, formula):
        insumos = Insumos(7, 0.3, 0.2, 20)
        assert formula.calcular(insumos) == formula.calcular(insumos)

    def test_interpreta_o_numero_em_vez_de_so_devolve_lo(self, formula):
        """Número sem interpretação vira meta de planilha."""
        texto = formula.interpretar(formula.limiar_padrao() + 1)
        assert len(texto) > 40
        assert str(int(formula.limiar_padrao())) in texto

    def test_cobertura_ausente_e_tratada_como_o_pior_caso(self, formula):
        """Um número otimista esconderia exatamente o que se quer achar."""
        assert formula.calcular(Insumos(5, SEM_DADOS, None, 10)) == formula.calcular(
            Insumos(5, 0.0, None, 10)
        )


class TestCrapClassico:
    @pytest.mark.parametrize(
        "complexidade,cobertura,esperado",
        [(5, 0.0, 30.0), (5, 1.0, 5.0), (30, 1.0, 30.0), (1, 0.0, 2.0), (10, 0.5, 22.5)],
    )
    def test_os_numeros_da_formula_publicada(self, complexidade, cobertura, esperado):
        """Os dois 30 não são coincidência: é onde a convenção do limiar mora."""
        assert obter_formula("crap").calcular(
            Insumos(complexidade, cobertura, None, 10)
        ) == pytest.approx(esperado)


class TestRegistroDeFormulas:
    def test_nome_repetido_e_recusado(self):
        """Duas fórmulas com o mesmo nome fariam relatórios antigos mudarem de
        significado sem aviso."""
        with pytest.raises(ValueError, match="já existe"):
            registrar_formula("crap", lambda: None)

    def test_nome_desconhecido_lista_os_disponiveis(self):
        with pytest.raises(ValueError, match="disponíveis"):
            obter_formula("inventada")


class TestInsumos:
    @pytest.mark.parametrize("cobertura", [-0.5, 1.5])
    def test_cobertura_fora_da_faixa_e_erro_e_nao_corte_silencioso(self, cobertura):
        """Cobertura acima de 1 quase sempre é bug de leitura do relatório;
        cortar em silêncio esconderia o defeito que interessa descobrir."""
        with pytest.raises(ValueError, match="fração"):
            Insumos(5, cobertura, None, 10)

    def test_complexidade_zero_e_erro(self):
        with pytest.raises(ValueError, match="começa em 1"):
            Insumos(0, 0.5, None, 10)

    def test_branch_e_preferida_quando_existe(self):
        """Branch está na mesma unidade da complexidade: caminhos."""
        assert Insumos(5, 0.9, 0.3, 10).cobertura_preferida == 0.3
        assert Insumos(5, 0.9, None, 10).cobertura_preferida == 0.9


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
