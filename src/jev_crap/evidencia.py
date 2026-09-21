"""Acha o trecho de teste que exercita uma função, para o Jev ter o que olhar.

O relatório de cobertura prova que a linha rodou; ele não mostra *como* ela foi
exercitada. A pergunta `teste_verifica` é sobre isso — se a asserção fixa um
valor esperado ou se o teste apenas chama a função — e ela precisa do texto do
teste, não de um número.

Isto é heurística e se assume como tal: teste que exercita sem citar o nome
escapa. Serve para o modelo julgar o que existe; quem prova cobertura é o
relatório. E quando nada é achado, a pergunta simplesmente não é feita — ver
``Rubrica.perguntas_para``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "MARCAS_DE_TESTE",
    "PADROES_DE_TESTE",
    "parece_teste",
    "testes_de",
]

#: Substring que denuncia um arquivo ou pasta de teste. A comparação é por
#: *substring* em cada componente, e não por igualdade, porque a convenção
#: dominante é `tests/` no plural: um teste de igualdade contra "test" não
#: pegaria nenhuma pasta real.
MARCAS_DE_TESTE: tuple[str, ...] = ("test", "spec", "fixture", "conftest", "__mocks__")

#: As mesmas marcas no formato que o analisador de complexidade entende, para
#: que arquivos de teste sejam podados na descida em vez de medidos e
#: descartados depois. Avaliar o próprio teste polui o relatório e gasta token
#: sem responder nada.
PADROES_DE_TESTE: tuple[str, ...] = tuple(f"*{marca}*" for marca in MARCAS_DE_TESTE)

#: Quantos trechos, no máximo, vão para o modelo. Três cobrem o caso feliz, uma
#: borda e um erro — que é exatamente o que a pergunta quer distinguir. Mais do
#: que isso aumenta o estado sem acrescentar dimensão nova, e a documentação do
#: modelo é explícita: acurácia cai quando o estado cresce com conteúdo que não
#: decide nada.
MAX_TRECHOS = 3

#: Quanto texto segue o ponto onde o nome foi citado. 900 caracteres costumam
#: alcançar as asserções que vêm depois da chamada.
JANELA_DEPOIS = 900

#: Distância máxima entre o cabeçalho do teste e a menção. Acima disso o
#: cabeçalho encontrado provavelmente é de outra função, e o recorte começaria
#: num teste que não tem nada a ver com a menção.
ALCANCE_DO_CABECALHO = 2000

#: Início de uma função de teste nas convenções que aparecem na prática: `def`
#: (Python), `function` (JS clássico), `it`/`test`/`describe` (jest, mocha,
#: vitest, pytest-bdd).
INICIO_DE_TESTE = re.compile(
    r"^[ \t]*(?:async\s+)?(?:def|function|it|test|describe)\b.*$", re.M
)

#: Linha que cita o nome sem exercitar a função. Import é o caso comum: um
#: `from x import (\n  nome,\n)` passa por qualquer filtro de linha, porque a
#: linha em si é só o nome.
LINHA_QUE_NAO_EXERCITA = re.compile(r"\s*(from|import|#|//|\*|@)")

#: Pastas que nunca contêm teste do projeto. Sem esta poda, procurar o nome de
#: uma função dentro de um `.venv` custa a varredura de um site-packages
#: inteiro para não achar nada de útil.
IGNORAR: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "bower_components",
        "site-packages",
        "dist",
        "build",
        ".next",
        "htmlcov",
    }
)

#: Extensões em que faz sentido procurar teste. Procurar em `.json` e `.md`
#: acharia o nome da função em changelog e fixture e mandaria isso ao modelo
#: como se fosse teste.
EXTENSOES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs",
                       ".cs", ".kt", ".swift", ".php", ".scala", ".c", ".cpp", ".m"})


def parece_teste(caminho: Path | str, raiz: Path | str | None = None) -> bool:
    """Diz se o caminho é de um arquivo de teste, por nome ou por pasta.

    Só os componentes **abaixo de ``raiz``** são olhados, e essa é a sutileza
    que custou um falso positivo inteiro: um projeto que mora em
    ``~/dev/jev-crap-test/`` teria todo o seu código classificado como teste, e
    a varredura devolveria zero função sem explicar por quê.
    """
    caminho = Path(caminho)
    if raiz is not None:
        try:
            caminho = caminho.relative_to(raiz)
        except ValueError:
            # Fora da raiz: comparar o caminho inteiro é o melhor disponível, e
            # é o comportamento seguro — inclui demais, nunca de menos.
            pass
    return any(
        marca in parte.lower() for parte in caminho.parts for marca in MARCAS_DE_TESTE
    )


def _arquivos_de_teste(raiz: Path) -> Iterable[Path]:
    """Arquivos de teste sob ``raiz``, em ordem estável.

    A comparação de ``parece_teste`` é feita contra ``raiz.parent`` de propósito:
    quem passa ``tests/`` como pasta de testes quer que o próprio nome ``tests``
    conte como marca, e relativizar contra a própria ``raiz`` o apagaria.
    """
    for arquivo in sorted(raiz.rglob("*")):
        if set(arquivo.parts) & IGNORAR:
            continue
        if arquivo.suffix not in EXTENSOES or not arquivo.is_file():
            continue
        if not parece_teste(arquivo, raiz.parent):
            continue
        yield arquivo


def _trecho_ao_redor(texto: str, posicao: int) -> str | None:
    """Recorte que começa no cabeçalho do teste que contém ``posicao``, ou ``None``.

    Voltar até o cabeçalho importa mais do que parece: sem isso o trecho começa
    no meio de uma asserção e o modelo não vê o que estava sendo montado. Medido
    na geração anterior: recortar do começo do *arquivo* em vez do começo do
    *teste* alcançava a linha de import e mais nada — o Jev respondia "não há
    teste" corretamente, sobre o material errado, e a nota da mesma função ia de
    92% para 14%.

    Não achar cabeçalho nenhum é a resposta certa para o caso chato: a menção
    está fora de qualquer função de teste. Menção fora de teste não é teste.
    """
    cabecalhos = [m.start() for m in INICIO_DE_TESTE.finditer(texto, 0, posicao)]
    if not cabecalhos or posicao - cabecalhos[-1] > ALCANCE_DO_CABECALHO:
        return None
    return texto[cabecalhos[-1] : posicao + JANELA_DEPOIS]


def testes_de(nome: str, pasta: Path | str | None, maximo: int = MAX_TRECHOS) -> list[str]:
    """Trechos de teste que citam ``nome``, prefixados pelo arquivo de origem.

    Devolve lista vazia quando não há pasta, quando o nome não aparece, ou
    quando todas as menções são import. Lista vazia é resposta legítima e o
    chamador a trata como tal: a pergunta sobre teste deixa de ser feita e o
    peso dela é redistribuído, em vez de a função levar zero por uma evidência
    que ninguém foi capaz de mostrar.
    """
    if not pasta or not nome:
        return []
    raiz = Path(pasta)
    if not raiz.exists():
        return []

    achados: list[str] = []
    padrao = re.compile(rf"\b{re.escape(nome)}\b")
    for arquivo in _arquivos_de_teste(raiz):
        try:
            texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for achado in padrao.finditer(texto):
            inicio_da_linha = texto.rfind("\n", 0, achado.start()) + 1
            fim_da_linha = texto.find("\n", achado.start())
            linha = texto[inicio_da_linha : fim_da_linha if fim_da_linha != -1 else len(texto)]
            if LINHA_QUE_NAO_EXERCITA.match(linha):
                continue
            trecho = _trecho_ao_redor(texto, achado.start())
            if trecho is None:
                continue
            achados.append(f"# {arquivo}\n{trecho}")
            if len(achados) >= maximo:
                return achados
    return achados
