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

import logging
import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

_log = logging.getLogger(__name__)

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
        """Se o relatório trouxe informação de branch para este arquivo.

        A pergunta que este método responde é a diferença entre **zero branch
        medido** e **nenhuma informação de branch**, e ela vale muito: no
        primeiro caso a função não tem desvio, no segundo o gerador não emitiu
        o dado. Confundi-los faz um arquivo sem informação aparecer como 0% de
        branch — código bem testado descrito como descoberto.

        É por isso que a ausência é :data:`SEM_DADOS` (-1) e não 0, e por isso
        um ``branches_totais`` negativo diferente da sentinela é recusado: ele
        só pode vir de aritmética errada em quem montou este objeto, e passaria
        adiante como "sem dados" escondendo o defeito.

        A recusa custa uma leitura, não a execução: ``jev_crap.avaliacao``
        captura falha por função medida e a registra como sem dados de
        cobertura. Nada é escrito, nada é enviado, e o único efeito é uma
        função entrar no relatório sem o dado de branch — que é exatamente o
        que ela teria se o gerador não o tivesse emitido.
        """
        if self.branches_totais < 0 and self.branches_totais != SEM_DADOS:
            raise ValueError(
                f"{self.arquivo}: branches_totais {self.branches_totais} é negativo sem ser "
                f"a sentinela {SEM_DADOS}; ausência de dado e contagem errada não são a "
                "mesma coisa"
            )
        return self.branches_totais >= 0

    @property
    def cobertura_de_linha(self) -> float:
        """Fração de linhas executáveis cobertas, ou :data:`SEM_DADOS` se não há linha.

        A interseção com ``linhas_totais`` não é decoração: relatórios trazem
        linha coberta que não consta como executável — concatenação de execuções
        de versões diferentes do arquivo é a causa comum. Sem a interseção a
        divisão passaria de 1.0, e uma cobertura de 130% atravessaria o
        relatório inteiro parecendo excelente.

        Arquivo sem linha executável devolve :data:`SEM_DADOS`, não 0.0: um
        módulo só de constantes não é um módulo descoberto.
        """
        if not self.linhas_totais:
            return float(SEM_DADOS)
        return len(self.linhas_cobertas & self.linhas_totais) / len(self.linhas_totais)

    @property
    def cobertura_de_branch(self) -> float:
        """Fração de branches cobertos, ou :data:`SEM_DADOS` se não há branch medível.

        Zero total cai em :data:`SEM_DADOS` junto com a sentinela, e é o caso
        honesto: arquivo sem desvio nenhum não tem cobertura de branch a
        reportar — dizer 0% o descreveria como totalmente descoberto.

        Mais cobertos que totais é contradição do relatório (mesma causa da
        interseção em :attr:`cobertura_de_linha`) e é cortado em 1.0 em vez de
        virar 1.4: o número segue para um cálculo de risco que supõe uma
        fração, e acima de 1 ele produziria risco negativo.
        """
        if self.branches_totais <= 0:
            return float(SEM_DADOS)
        return min(1.0, self.branches_cobertos / self.branches_totais)


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
        """Abre o acumulador de um arquivo, com todas as estruturas vazias.

        ``arquivo`` é a chave sob a qual tudo isto será guardado e depois
        cruzado com a medição por sufixo de caminho. Vazio, o acumulador
        recolheria dados de cobertura que nunca casariam com função nenhuma —
        e o relatório sairia com todo mundo em 0%, que parece um projeto ruim
        em vez de um relatório mal lido.
        """
        if not isinstance(arquivo, str) or not arquivo.strip():
            raise ValueError(
                "um registro de cobertura sem nome de arquivo não pode ser cruzado "
                "com nenhuma função medida"
            )
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
        """Soma execuções de uma linha, acumulando entre registros repetidos.

        Somar, e não substituir, é o que torna correto concatenar execuções: o
        mesmo arquivo em dois registros traz as linhas de cada uma, e ficar com
        a última perderia a cobertura da primeira.

        Linha fora de faixa é ignorada em vez de registrada. Numeração começa
        em 1, e um ``0`` (ou negativo) vem de relatório com deslocamento errado;
        aceitá-lo criaria uma linha executável que não existe no arquivo, o que
        baixa a cobertura de toda função que a contiver — uma punição por um
        defeito do gerador.

        ``hits`` negativo vira 0: no LCOV ele significa "bloco não alcançado",
        e somá-lo reduziria a contagem de uma linha que outro registro viu
        executar.
        """
        if linha < 1:
            return
        self.hits_por_linha[linha] = self.hits_por_linha.get(linha, 0) + max(0, hits)

    def registrar_branch_lcov(self, linha: int, bloco: str, branch: str, vezes: int) -> None:
        """Registra um branch do LCOV, guardando o melhor resultado observado.

        A chave ``(linha, bloco, branch)`` é o que evita contar o mesmo branch
        duas vezes quando o arquivo aparece em registros repetidos — e ``max``,
        em vez de soma, porque o que interessa é se aquele branch chegou a ser
        exercitado alguma vez, não quantas.

        Linha fora de faixa é ignorada pelo mesmo motivo de
        :meth:`registrar_linha`: ela inventaria um desvio inexistente.
        """
        if linha < 1:
            return
        chave = (linha, str(bloco), str(branch))
        self.branches[chave] = max(self.branches.get(chave, 0), max(0, vezes))

    def registrar_branch_resumido(self, linha: int, cobertos: int, totais: int) -> None:
        """Registra o resumo por linha que o Cobertura XML dá no lugar do detalhe.

        Valores incoerentes são normalizados aqui, não adiante: negativos viram
        zero e ``cobertos`` acima de ``totais`` é cortado. Um par ``(3, 2)``
        atravessaria a soma de :meth:`congelar` e sairia como cobertura de
        branch acima de 100%, que o cálculo de risco leria como fração e
        transformaria em risco negativo.
        """
        if linha < 1:
            return
        totais = max(0, totais)
        cobertos = min(max(0, cobertos), totais)
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
        """Fecha o acumulador num :class:`CoberturaArquivo` imutável.

        A ordem de precedência do branch é o ponto: o detalhe (``BRDA`` do
        LCOV, ``<condition>`` do XML) sempre vence o resumo (``BRF``/``BRH``),
        e o resumo só entra quando não houve detalhe nenhum. A razão é que o
        resumo é por registro: num arquivo que aparece em vários registros,
        somá-los conta o mesmo branch mais de uma vez, enquanto o detalhe é
        deduplicável pela chave.

        Sem nenhum dos dois, os contadores de branch saem como
        :data:`SEM_DADOS` — nunca zero, que seria lido como "nenhum branch
        coberto" para um arquivo cujo relatório não mediu branch.
        """
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
    """Fecha todos os acumuladores, deixando de fora o que não fecha.

    Um arquivo cujo congelamento falha — contadores incoerentes que escaparam
    das normalizações, ou um caminho que virou chave inválida — é registrado no
    log e descartado. Deixar a exceção subir perderia a cobertura do
    repositório inteiro por causa de um arquivo, e a consequência disso não é
    um erro visível: é um relatório em que **todas** as funções aparecem como
    descobertas, o que se lê como projeto ruim e não como relatório ilegível.
    """
    congelados: dict[str, CoberturaArquivo] = {}
    for arquivo, acc in acumuladores.items():
        try:
            congelados[arquivo] = acc.congelar()
        except Exception:  # noqa: BLE001 - um arquivo torto não zera o relatório
            _log.warning("cobertura de %s ilegível; arquivo ignorado", arquivo)
    return congelados


# --------------------------------------------------------------------------- #
# Normalização de caminhos
# --------------------------------------------------------------------------- #


def _limpar(bruto: str) -> str:
    """Tira ruído de transporte: espaços, ``file://`` e barra invertida do Windows.

    Tudo que entra aqui veio de um arquivo de relatório gerado por outra
    ferramenta, possivelmente em outro sistema operacional. Os três ruídos
    tratados são os que aparecem na prática, e cada um deles impediria o
    caminho de casar com o do analisador de complexidade — o que faz a função
    sair como "sem cobertura" em vez de "não casou".

    Valor que não é texto vira ``""`` em vez de levantar: o chamador já trata
    caminho vazio como "não dá para casar", e derrubar a leitura do relatório
    inteiro por um registro torto custaria a cobertura de todos os outros.
    """
    if not isinstance(bruto, str):
        return ""
    caminho = bruto.strip().replace("\\", "/")
    if caminho.startswith("file://"):
        caminho = caminho[len("file://") :]
    return caminho


def _dentro(caminho: str, raiz: str) -> bool:
    """Se ``caminho`` é a raiz ou está abaixo dela.

    A barra no prefixo é o detalhe que decide: sem ela, ``/proj-antigo``
    contaria como dentro de ``/proj``, e caminhos de outro checkout seriam
    relativizados contra uma raiz que não é a deles — produzindo cobertura
    atribuída ao arquivo errado, que é pior que cobertura não atribuída.

    Raiz vazia devolve ``False`` para tudo, inclusive para caminho vazio: sem
    raiz não há "dentro", e responder ``True`` faria o chamador relativizar
    contra nada.
    """
    if not raiz or not caminho:
        return False
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
    raiz_norm = posixpath.normpath(_limpar(raiz if raiz is not None else _cwd()))

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


def _cwd() -> str:
    """O diretório de trabalho, ou ``.`` quando ele não existe mais.

    O processo pode rodar num diretório apagado — CI que limpa o workspace
    entre passos faz isso. Levantar aqui derrubaria a leitura do relatório por
    causa de um caminho que só seria usado como âncora.
    """
    try:
        return os.getcwd()
    except OSError:
        return "."


def _inteiro_lcov(valor: str) -> int:
    """Lê um contador do LCOV, onde ``-`` significa "bloco nunca alcançado".

    Nunca levanta e nunca devolve negativo. Um relatório LCOV tem uma linha por
    registro e é gerado por dezenas de ferramentas diferentes; recusar o arquivo
    inteiro por causa de um contador estranho custaria a cobertura de tudo que
    veio depois dele.
    """
    if not isinstance(valor, str):
        return 0
    valor = valor.strip()
    if valor in {"", "-"}:
        return 0
    try:
        return max(0, int(valor))
    except ValueError:
        # gcov às vezes emite contadores gigantes com sufixo ou notação estranha;
        # um valor ilegível vale mais como "não sei" do que como exceção. Zero é
        # o "não sei" certo aqui: ele não inventa execução que não houve, e a
        # linha continua contando como executável.
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

    ``errors="replace"`` e ``utf-8-sig`` são sobre o que os geradores emitem na
    prática: BOM vindo de ferramenta Windows e byte inválido num nome de
    arquivo com acento. Recusar o relatório por causa de um byte custaria a
    cobertura de tudo que veio depois dele.

    Relatório sem nenhum arquivo é registrado no log em vez de passar calado.
    O dicionário vazio é a resposta honesta — não há o que cruzar —, mas quem
    o recebe não consegue distinguir "o relatório estava vazio" de "nenhum
    caminho casou", e as duas pedem correções opostas.

    A leitura não escreve nada. O que sobe daqui é erro de configuração
    (``OSError``: caminho que virou diretório, permissão negada), traduzido em
    situação conhecida por ``jev_crap.avaliacao.ler_cobertura``.
    """
    with open(caminho, encoding="utf-8-sig", errors="replace") as arquivo:
        cobertura = _parse_lcov(arquivo, raiz)
    if not cobertura:
        _log.warning("%s: LCOV sem nenhum registro SF; nenhum arquivo para cruzar", caminho)
    return cobertura


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
    """Converte a árvore de um Cobertura XML em cobertura por arquivo.

    Percorre ``<class>`` e não ``<package>`` porque linguagens com mais de uma
    classe por arquivo emitem vários ``<class>`` com o mesmo ``filename``; o
    acumulador por arquivo junta tudo antes de congelar, e olhar só o último
    apagaria os anteriores.

    Registro incompleto é pulado, nunca rejeitado: ``<class>`` sem
    ``filename``, ``<line>`` sem ``number`` ou com número não numérico, e
    ``condition-coverage`` fora do formato esperado. Um relatório de projeto
    grande quase sempre tem alguma linha assim, e recusar o arquivo inteiro
    trocaria a cobertura de milhares de linhas pela ausência de todas.

    A única coisa recusada é a raiz errada: um XML que não é ``<coverage>`` não
    é um relatório incompleto, é outro arquivo — e seguir produziria zero
    arquivo sem dizer por quê.
    """
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
            achado = _CONDICOES.search(linha.get("condition-coverage") or "")
            if achado is None:
                continue
            try:
                cobertos, totais = int(achado.group(1)), int(achado.group(2))
            except ValueError:
                # O padrão já garante dígitos, mas um número absurdamente longo
                # vindo de gerador quebrado ainda pode falhar na conversão. A
                # linha continua contando como executável; só o branch se perde.
                continue
            acc.registrar_branch_resumido(int(numero), cobertos, totais)

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

    Esta é uma leitura: nada é escrito, nada é enviado, nenhum relatório
    anterior muda. O que sobe daqui é sempre erro de **configuração** — arquivo
    que não existe, que não dá para ler, ou que não é relatório de cobertura —
    e acontece na montagem, antes de qualquer função ser medida ou julgada.
    ``jev_crap.avaliacao.ler_cobertura`` traduz cada um deles numa situação
    conhecida com instrução de como resolver, que as duas entradas devolvem
    como erro de uso (saída 3 na CLI, situação nomeada no MCP).
    """
    formato = _cheirar_formato(caminho)
    if formato == "lcov":
        return ler_lcov(caminho, raiz=raiz)
    return ler_cobertura_xml(caminho, raiz=raiz)
