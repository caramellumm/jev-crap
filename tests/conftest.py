"""Peças que quase todo teste precisa, montadas sem ambiente global nem rede.

O projeto inteiro foi desenhado para ser testável assim: `Config` recebe o
ambiente por parâmetro, `criar_servidor` recebe configuração, julgador e régua
por injeção, e o julgador tem uma implementação falsa. Nenhum teste desta suíte
toca a API do Jev, escreve fora de um diretório temporário ou depende da ordem
em que roda.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_crap.avaliacao import FuncaoMedida
from jev_crap.config import Config
from jev_crap.julgamento.jev import JulgadorFake, Resposta
from jev_crap.julgamento.rubrica import carregar_rubrica

FIXTURES = Path(__file__).parent / "fixtures"


def score(bruto: float, confianca: float = 0.9, niveis: int = 3) -> Resposta:
    """Uma resposta de `score` como o extrator a produziria."""
    return Resposta("score", bruto, bruto / (niveis - 1), confianca)


def noul(probabilidade: float) -> Resposta:
    """Uma resposta de `noul`. Sem confiança — a API não manda, e inventar mente."""
    return Resposta("noul", probabilidade, probabilidade, None)


#: Julgamento em que tudo vai bem: qualidade alta, nenhum risco, domínio complexo.
RESPOSTAS_BOAS: dict[str, Resposta] = {
    "complexidade_cognitiva": score(1.8),
    "teste_verifica": score(1.8),
    "manutenibilidade": score(1.8),
    "tratamento_de_erros": score(1.8),
    "consequencia_de_falha": score(0.2),
    "complexidade_essencial": noul(0.9),
    "exec_dinamica": noul(0.01),
    "injecao": noul(0.01),
    "entrada_nao_validada": noul(0.1),
    "retorno_inconsistente": noul(0.1),
    "caso_limite_nao_tratado": noul(0.1),
}

#: Resposta real do jev-1.13.0 para a função `agregar`, capturada e congelada.
#: Vale a pena ser real porque a escala do Score é a armadilha fácil deste
#: código — `score` volta de 0 a (níveis−1), não de 0 a 1 — e um exemplo escrito
#: na escala errada faria a suíte confirmar o erro em vez de pegá-lo.
#:
#: Uma ressalva honesta sobre `consequencia_de_falha`: a captura foi feita sob
#: a régua do protótipo, em que o nível 0 era "corrompe dado" e o 2 "efeito
#: cosmético" — a ordem oposta à de hoje. Como os três níveis descrevem as
#: mesmas três situações, a tradução é exata (`2 − 0.88 = 1.12`) e é ela que
#: está abaixo. O valor bruto original, 0.88, significaria "quase cosmético" na
#: régua atual, isto é, o oposto do que o modelo respondeu.
#:
#: O que a tradução **não** captura: os níveis de hoje trazem exemplos que o
#: protótipo não tinha, e exemplos mudam a resposta. Então 1.12 é o sentido
#: certo, não a resposta que a régua atual devolveria. Nenhum teste afirma nada
#: sobre esta dimensão por causa disso.
RESPOSTAS_REAIS: dict[str, Resposta] = {
    "complexidade_cognitiva": score(1.07, 0.64),
    "teste_verifica": score(0.22, 0.67),
    "manutenibilidade": score(1.01, 0.44),
    "tratamento_de_erros": score(1.43, 0.33),
    "consequencia_de_falha": score(1.12, 0.59),
    "complexidade_essencial": noul(0.63),
    "exec_dinamica": noul(0.02),
    "injecao": noul(0.02),
    "entrada_nao_validada": noul(0.37),
    "retorno_inconsistente": noul(0.21),
    "caso_limite_nao_tratado": noul(0.69),
}


@pytest.fixture
def rubrica():
    return carregar_rubrica()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Configuração padrão com o histórico num diretório temporário.

    O caminho do histórico é redirecionado sempre: um teste que grave episódio
    no repositório de verdade contamina o próximo `consultar_aprendizado` — e o
    defeito aparece longe daqui, num teste que não mexeu em nada.
    """
    return Config(raiz=tmp_path, caminho_episodios=tmp_path / "episodios.jsonl")


@pytest.fixture
def julgador() -> JulgadorFake:
    return JulgadorFake(RESPOSTAS_BOAS)


@pytest.fixture
def medida() -> FuncaoMedida:
    return FuncaoMedida(
        arquivo="src/pagamento.py",
        nome="conciliar",
        linha_inicio=10,
        linha_fim=40,
        complexidade=8,
        linhas_logicas=28,
        linguagem="python",
        cobertura_linha=0.6,
        cobertura_branch=0.5,
        risco=45.0,
        codigo="def conciliar(lote):\n    return lote\n",
        testes=("def test_conciliar():\n    assert conciliar([]) == []\n",),
    )


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    """Um projetinho em disco: código, teste e relatório de cobertura coerentes.

    Existe porque medir e cruzar cobertura são justamente os passos que só
    quebram com arquivo de verdade — caminho, extensão e número de linha são o
    que o cruzamento usa, e nada disso se reproduz com objeto em memória.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calculo.py").write_text(
        "def soma(a, b):\n"
        "    return a + b\n"
        "\n"
        "\n"
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ValueError('divisão por zero')\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_calculo.py").write_text(
        "from src.calculo import divide, soma\n"
        "\n"
        "\n"
        "def test_soma_devolve_a_soma():\n"
        "    assert soma(2, 3) == 5\n"
        "\n"
        "\n"
        "def test_divide_rejeita_zero():\n"
        "    with pytest.raises(ValueError):\n"
        "        divide(1, 0)\n",
        encoding="utf-8",
    )
    (tmp_path / "lcov.info").write_text(
        "TN:teste\n"
        "SF:src/calculo.py\n"
        "DA:1,1\nDA:2,3\nDA:5,1\nDA:6,2\nDA:7,1\nDA:8,1\n"
        "BRDA:6,0,0,1\nBRDA:6,0,1,1\n"
        "BRF:2\nBRH:2\nLF:6\nLH:6\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    return tmp_path
