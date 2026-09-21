"""Leitura de cobertura em formatos universais (LCOV e Cobertura XML).

Por que formatos de relatório e não uma biblioteca de cobertura: o jev-crap é
multilinguagem. Amarrar a leitura ao ``coverage.py`` prenderia o projeto ao
Python, enquanto LCOV e Cobertura XML são emitidos por praticamente todo
ecossistema (pytest-cov, jest/istanbul, go test, cargo-llvm-cov, JaCoCo,
SimpleCov). Ambos carregam cobertura de branch, que é o que a fórmula de risco
precisa e que formatos mais simples não trazem.

O parse é manual e usa só a biblioteca padrão: LCOV é um formato de linhas
``CHAVE:valor`` e Cobertura é XML comum. Uma dependência a mais aqui custaria
mais do que resolve.

Convenção de caminhos
---------------------
As chaves do dicionário devolvido são caminhos **relativos à raiz do projeto**,
com barra normal (``/``) mesmo quando o relatório foi gerado no Windows. Isso
existe porque a cobertura precisa ser cruzada com a saída do analisador de
complexidade, e relatórios gravam o mesmo arquivo de formas diferentes
(``/ci/build/src/a.py``, ``./src/a.py``, ``src\\a.py``). Sem normalizar, o
cruzamento falha em silêncio e o arquivo aparece como "sem cobertura".

A raiz vem do parâmetro ``raiz``; quando ele é omitido, usa-se o diretório de
trabalho atual. Caminhos absolutos fora da raiz são mantidos absolutos (em vez
de virar uma pilha de ``../``), porque um caminho fora da raiz normalmente
indica raiz errada, e um valor obviamente estranho é mais fácil de diagnosticar.

Ausência de dado de branch
--------------------------
Nem todo gerador emite branch. Quando o dado não existe, os campos de branch
valem :data:`SEM_DADOS` (``-1``) em vez de zero. Zero significaria "existem
branches e nenhum foi coberto" — o pior caso possível — e inflaria o risco de
código que talvez esteja bem testado. Quem chama decide o que fazer com a
ausência (cair para cobertura de linha, avisar, ignorar).
"""

from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "SEM_DADOS",
    "CoberturaArquivo",
    "FormatoDeCoberturaDesconhecido",
    "cobertura_de_faixa",
    "ler",
    "ler_cobertura_xml",
    "ler_lcov",
]

#: Sentinela de "o relatório não trouxe esse dado". Ver a nota do módulo sobre
#: por que ausência não pode ser representada como zero.
SEM_DADOS: Final[int] = -1


class FormatoDeCoberturaDesconhecido(ValueError):
    """O arquivo não se parece com LCOV nem com Cobertura XML."""


@dataclass(frozen=True)
class CoberturaArquivo:
    """Cobertura de um único arquivo de código.

    ``linhas_totais`` são as linhas **executáveis** que o instrumentador
    enxergou, não todas as linhas do arquivo: linhas em branco, comentários e
    declarações não entram. É por isso que a cobertura de uma faixa se calcula
    contra esse conjunto, e não contra ``fim - inicio + 1``.

    ``branches_por_linha`` mapeia linha -> ``(cobertos, totais)`` e não estava
    no desenho original, mas sem ele não há como recortar cobertura de branch
    por função: os totais do arquivo não dizem *onde* os branches estão. Vem por
    último e com default para manter a construção posicional já existente.
    """

    arquivo: str
    linhas_cobertas: set[int]
    linhas_totais: set[int]
    branches_cobertos: int
    branches_totais: int
    branches_por_linha: Mapping[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def tem_dados_de_branch(self) -> bool:
        """Se o relatório trouxe informação de branch para este arquivo."""
        return self.branches_totais >= 0

    @property
    def cobertura_de_linha(self) -> float:
        """Fração de linhas executáveis cobertas, ou :data:`SEM_DADOS` se não há linha."""
        if not self.linhas_totais:
            return float(SEM_DADOS)
        return len(self.linhas_cobertas & self.linhas_totais) / len(self.linhas_totais)

    @property
    def cobertura_de_branch(self) -> float:
        """Fração de branches cobertos, ou :data:`SEM_DADOS` se não há branch medível."""
        if self.branches_totais <= 0:
            return float(SEM_DADOS)
        return self.branches_cobertos / self.branches_totais


def cobertura_de_faixa(cob: CoberturaArquivo, inicio: int, fim: int) -> tuple[float, float]:
    """Cobertura de linha e de branch dentro de uma faixa de linhas, inclusive nas pontas.

    A faixa costuma vir do analisador de complexidade (primeira e última linha
    de uma função). Devolve ``(cobertura_de_linha, cobertura_de_branch)``, cada
    uma entre 0.0 e 1.0 ou :data:`SEM_DADOS` quando não há o que medir.

    Faixa sem linha executável devolve :data:`SEM_DADOS` em vez de 0.0 porque
    0.0 seria lido como "nada coberto" e puniria, por exemplo, uma função só de
    docstring. O mesmo vale para branch: função sem desvio não é função com
    desvios descobertos.
    """
    if inicio > fim:
        inicio, fim = fim, inicio

    na_faixa = {linha for linha in cob.linhas_totais if inicio <= linha <= fim}
    if na_faixa:
        cobertas = len(na_faixa & cob.linhas_cobertas)
        cobertura_linha = cobertas / len(na_faixa)
    else:
        cobertura_linha = float(SEM_DADOS)

    if not cob.tem_dados_de_branch:
        return cobertura_linha, float(SEM_DADOS)

    cobertos = 0
    totais = 0
    for linha, (br_cobertos, br_totais) in cob.branches_por_linha.items():
        if inicio <= linha <= fim:
            cobertos += br_cobertos
            totais += br_totais

    cobertura_branch = cobertos / totais if totais else float(SEM_DADOS)
    return cobertura_linha, cobertura_branch


# --------------------------------------------------------------------------- #
# Acumulador interno
# --------------------------------------------------------------------------- #


class _Acumulador:
    """Estado mutável de um arquivo enquanto o relatório é lido.

    Existe porque o mesmo arquivo pode aparecer em vários registros: LCOV
    repete blocos ``SF:``/``end_of_record`` quando se concatenam execuções, e
    Cobertura XML emite um ``<class>`` por classe, vários por arquivo em
    linguagens que permitem mais de uma. Juntar tudo antes de congelar evita
    que o último registro apague os anteriores.
    """

    def __init__(self, arquivo: str) -> None:
        self.arquivo = arquivo
        self.hits_por_linha: dict[int, int] = {}
        # (linha, bloco, branch) -> vezes executado; a chave evita contar duas
        # vezes o mesmo branch quando o arquivo aparece em registros repetidos.
        self.branches: dict[tuple[int, str, str], int] = {}
        # Usado só pelo Cobertura XML, que dá o resumo por linha e não por branch.
        self.branches_por_linha: dict[int, tuple[int, int]] = {}
        # Resumo LCOV (BRF/BRH), aproveitado apenas quando não há detalhe BRDA.
        self.resumo_branch: tuple[int, int] | None = None

    def registrar_linha(self, linha: int, hits: int) -> None:
        self.hits_por_linha[linha] = self.hits_por_linha.get(linha, 0) + hits

    def registrar_branch_lcov(self, linha: int, bloco: str, branch: str, vezes: int) -> None:
        chave = (linha, bloco, branch)
        self.branches[chave] = max(self.branches.get(chave, 0), vezes)

    def registrar_branch_resumido(self, linha: int, cobertos: int, totais: int) -> None:
        anterior = self.branches_por_linha.get(linha)
        if anterior is None:
            self.branches_por_linha[linha] = (cobertos, totais)
            return
        # Mesma linha em dois registros: fica o melhor resultado observado. Somar
        # inventaria branches que não existem no código.
        self.branches_por_linha[linha] = (
            max(anterior[0], cobertos),
            max(anterior[1], totais),
        )

    def congelar(self) -> CoberturaArquivo:
        totais = set(self.hits_por_linha)
        cobertas = {linha for linha, hits in self.hits_por_linha.items() if hits > 0}

        por_linha = dict(self.branches_por_linha)
        for (linha, _bloco, _branch), vezes in self.branches.items():
            cobertos, total = por_linha.get(linha, (0, 0))
            por_linha[linha] = (cobertos + (1 if vezes > 0 else 0), total + 1)

        if por_linha:
            branches_cobertos = sum(c for c, _ in por_linha.values())
            branches_totais = sum(t for _, t in por_linha.values())
        elif self.resumo_branch is not None:
            branches_cobertos, branches_totais = self.resumo_branch
        else:
            branches_cobertos = branches_totais = SEM_DADOS

        return CoberturaArquivo(
            arquivo=self.arquivo,
            linhas_cobertas=cobertas,
            linhas_totais=totais,
            branches_cobertos=branches_cobertos,
            branches_totais=branches_totais,
            branches_por_linha=por_linha,
        )


def _congelar_todos(acumuladores: Mapping[str, _Acumulador]) -> dict[str, CoberturaArquivo]:
    return {arquivo: acc.congelar() for arquivo, acc in acumuladores.items()}


# --------------------------------------------------------------------------- #
# Normalização de caminhos
# --------------------------------------------------------------------------- #


def _limpar(bruto: str) -> str:
    """Tira ruído de transporte: espaços, ``file://`` e barra invertida do Windows."""
    caminho = bruto.strip().replace("\\", "/")
    if caminho.startswith("file://"):
        caminho = caminho[len("file://") :]
    return caminho


def _dentro(caminho: str, raiz: str) -> bool:
    if caminho == raiz:
        return True
    prefixo = raiz if raiz.endswith("/") else raiz + "/"
    return caminho.startswith(prefixo)


def _normalizar_caminho(
    bruto: str,
    raiz: str | None,
    bases: Sequence[str] = (),
) -> str:
    """Converte o caminho de um relatório no caminho relativo à raiz do projeto.

    ``bases`` são os diretórios que o próprio relatório declara como origem
    (``<sources>`` do Cobertura XML). Só são usados quando ``raiz`` foi passada
    explicitamente: sem raiz declarada, ancorar um caminho relativo numa base
    qualquer transformaria ``src/a.py`` num absoluto de outra máquina, que é
    pior do que o relativo que já veio pronto.
    """
    caminho = _limpar(bruto)
    if not caminho:
        return caminho

    raiz_explicita = raiz is not None
    raiz_norm = posixpath.normpath(_limpar(raiz if raiz is not None else os.getcwd()))

    if posixpath.isabs(caminho):
        absoluto = posixpath.normpath(caminho)
    elif raiz_explicita:
        absoluto = None
        for base in bases:
            tentativa = posixpath.normpath(posixpath.join(_limpar(base), caminho))
            if _dentro(tentativa, raiz_norm):
                absoluto = tentativa
                break
        if absoluto is None:
            return posixpath.normpath(caminho)
    else:
        return posixpath.normpath(caminho)

    if _dentro(absoluto, raiz_norm):
        return posixpath.relpath(absoluto, raiz_norm)
    return absoluto


# --------------------------------------------------------------------------- #
# LCOV
# --------------------------------------------------------------------------- #


def _inteiro_lcov(valor: str) -> int:
    """Lê um contador do LCOV, onde ``-`` significa "bloco nunca alcançado"."""
    valor = valor.strip()
    if valor in {"", "-"}:
        return 0
    try:
        return int(valor)
    except ValueError:
        # gcov às vezes emite contadores gigantes com sufixo ou notação estranha;
        # um valor ilegível vale mais como "não sei" do que como exceção.
        return 0


def ler_lcov(caminho: str, *, raiz: str | None = None) -> dict[str, CoberturaArquivo]:
    """Lê um relatório LCOV (``lcov.info``) e devolve a cobertura por arquivo.

    Registros considerados: ``SF`` (arquivo), ``DA`` (linha,execuções),
    ``BRDA`` (linha,bloco,branch,vezes) e ``BRF``/``BRH`` (resumo de branch).
    ``FN``/``FNDA`` são ignorados de propósito: a lista de funções vem do
    analisador de complexidade, que é multilinguagem e concorda com as linhas
    reais do arquivo, enquanto o LCOV registra só a linha de declaração.

    ``BRF``/``BRH`` só entram quando não houve nenhum ``BRDA``: são somatórios
    por registro e, num arquivo que aparece em vários registros, somá-los conta
    o mesmo branch mais de uma vez. O detalhe do ``BRDA`` é deduplicável.
    """
    with open(caminho, encoding="utf-8-sig", errors="replace") as arquivo:
        return _parse_lcov(arquivo, raiz)


def _parse_lcov(linhas: Iterable[str], raiz: str | None) -> dict[str, CoberturaArquivo]:
    acumuladores: dict[str, _Acumulador] = {}
    atual: _Acumulador | None = None
    branch_found: int | None = None
    branch_hit: int | None = None

    def fechar() -> None:
        nonlocal atual, branch_found, branch_hit
        if atual is not None and branch_found is not None:
            atual.resumo_branch = (branch_hit or 0, branch_found)
        atual = None
        branch_found = None
        branch_hit = None

    for linha_bruta in linhas:
        linha = linha_bruta.strip()
        if not linha or ":" not in linha:
            if linha == "end_of_record":
                fechar()
            continue

        chave, _, valor = linha.partition(":")
        chave = chave.strip().upper()

        if chave == "SF":
            fechar()
            arquivo = _normalizar_caminho(valor, raiz)
            if not arquivo:
                continue
            atual = acumuladores.setdefault(arquivo, _Acumulador(arquivo))
        elif atual is None:
            continue
        elif chave == "DA":
            partes = valor.split(",")
            if len(partes) >= 2:
                atual.registrar_linha(_inteiro_lcov(partes[0]), _inteiro_lcov(partes[1]))
        elif chave == "BRDA":
            partes = valor.split(",")
            if len(partes) >= 4:
                atual.registrar_branch_lcov(
                    _inteiro_lcov(partes[0]),
                    partes[1].strip(),
                    partes[2].strip(),
                    _inteiro_lcov(partes[3]),
                )
        elif chave == "BRF":
            branch_found = _inteiro_lcov(valor)
        elif chave == "BRH":
            branch_hit = _inteiro_lcov(valor)

    fechar()
    return _congelar_todos(acumuladores)


# --------------------------------------------------------------------------- #
# Cobertura XML
# --------------------------------------------------------------------------- #

# "50% (1/2)" -> (1, 2). O percentual é redundante e às vezes arredondado, então
# só os inteiros entre parênteses são confiáveis.
_CONDICOES = re.compile(r"\((\d+)\s*/\s*(\d+)\)")


def ler_cobertura_xml(caminho: str, *, raiz: str | None = None) -> dict[str, CoberturaArquivo]:
    """Lê um relatório no formato Cobertura XML e devolve a cobertura por arquivo.

    Usa o ``filename`` de cada ``<class>`` como identidade do arquivo, com os
    diretórios de ``<sources>`` como base para resolvê-lo quando ``raiz`` é
    informada. Várias ``<class>`` podem apontar para o mesmo ``filename`` (uma
    por classe do arquivo) e são somadas.

    Só as linhas filhas diretas de ``<class><lines>`` são lidas. As linhas que
    aparecem dentro de ``<methods>`` repetem essas mesmas linhas, e varrer as
    duas contaria tudo em dobro.

    Branch vem de ``condition-coverage`` (``"50% (1/2)"``). Uma linha marcada
    ``branch="true"`` sem esse atributo é ignorada para efeito de branch: não há
    número para usar, e chutar 2 condições inventaria dado que o relatório não
    deu.
    """
    # ElementTree sem defusedxml: a entrada é um relatório gerado pelo próprio
    # build da pessoa que roda a ferramenta, não conteúdo de terceiro.
    arvore = ET.parse(caminho)
    return _parse_cobertura_xml(arvore.getroot(), raiz)


def _parse_cobertura_xml(raiz_xml: ET.Element, raiz: str | None) -> dict[str, CoberturaArquivo]:
    if raiz_xml.tag != "coverage":
        raise FormatoDeCoberturaDesconhecido(
            f"raiz XML esperada <coverage>, encontrada <{raiz_xml.tag}>"
        )

    bases = [
        fonte.text.strip()
        for fonte in raiz_xml.findall("./sources/source")
        if fonte.text and fonte.text.strip()
    ]

    acumuladores: dict[str, _Acumulador] = {}
    for classe in raiz_xml.iter("class"):
        bruto = classe.get("filename")
        if not bruto:
            continue
        arquivo = _normalizar_caminho(bruto, raiz, bases)
        acc = acumuladores.setdefault(arquivo, _Acumulador(arquivo))

        for linha in classe.findall("./lines/line"):
            numero = linha.get("number")
            if numero is None or not numero.strip().isdigit():
                continue
            acc.registrar_linha(int(numero), _inteiro_lcov(linha.get("hits", "0")))

            if linha.get("branch", "").strip().lower() != "true":
                continue
            achado = _CONDICOES.search(linha.get("condition-coverage", ""))
            if achado is not None:
                acc.registrar_branch_resumido(
                    int(numero), int(achado.group(1)), int(achado.group(2))
                )

    return _congelar_todos(acumuladores)


# --------------------------------------------------------------------------- #
# Detecção de formato
# --------------------------------------------------------------------------- #

# Primeira tag de verdade do XML, pulando declaração (<?xml?>), doctype e comentário.
_PRIMEIRA_TAG = re.compile(r"<\s*([A-Za-z_][\w.:-]*)")


def _cheirar_formato(caminho: str) -> str:
    """Decide entre ``"lcov"`` e ``"cobertura-xml"`` olhando o conteúdo.

    O conteúdo decide antes da extensão porque nome de arquivo é convenção e
    conteúdo é fato: relatórios chegam como ``coverage.dat``, ``lcov.txt`` ou
    de um pipe de CI sem extensão nenhuma. A extensão entra só como desempate
    quando o começo do arquivo não é conclusivo.
    """
    with open(caminho, encoding="utf-8-sig", errors="replace") as arquivo:
        inicio = arquivo.read(8192)

    despido = inicio.lstrip()
    if despido.startswith(("TN:", "SF:")):
        return "lcov"

    sem_prologo = re.sub(r"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", "", despido, flags=re.DOTALL)
    achado = _PRIMEIRA_TAG.search(sem_prologo)
    if achado is not None:
        if achado.group(1) == "coverage":
            return "cobertura-xml"
        raise FormatoDeCoberturaDesconhecido(
            f"{caminho}: XML com raiz <{achado.group(1)}>; esperado <coverage>"
        )

    # Conteúdo inconclusivo (relatório vazio, ou LCOV que começa por outro
    # registro): a extensão é a última pista disponível.
    extensao = os.path.splitext(caminho)[1].lower()
    if extensao == ".info":
        return "lcov"
    if extensao == ".xml":
        return "cobertura-xml"
    if any(
        despido.startswith(prefixo)
        for prefixo in ("DA:", "BRDA:", "LF:", "LH:", "BRF:", "BRH:", "FN:", "FNF:")
    ):
        return "lcov"

    raise FormatoDeCoberturaDesconhecido(
        f"{caminho}: não parece LCOV (TN:/SF:) nem Cobertura XML (<coverage>)"
    )


def ler(caminho: str, *, raiz: str | None = None) -> dict[str, CoberturaArquivo]:
    """Lê um relatório de cobertura detectando o formato pelo conteúdo.

    Aceita LCOV e Cobertura XML. Levanta
    :class:`FormatoDeCoberturaDesconhecido` quando o arquivo não é nenhum dos
    dois — em vez de devolver dicionário vazio, que o chamador leria como
    "projeto sem cobertura" e transformaria um erro de configuração num
    relatório de risco falso.
    """
    formato = _cheirar_formato(caminho)
    if formato == "lcov":
        return ler_lcov(caminho, raiz=raiz)
    return ler_cobertura_xml(caminho, raiz=raiz)
