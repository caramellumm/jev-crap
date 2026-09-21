"""Complexidade ciclomática por função, em qualquer linguagem, via lizard.

Por que lizard e não radon: o radon só lê Python. O lizard lê 27 linguagens
com o mesmo algoritmo e, em Python, devolve o mesmo número que o radon — o que
permite ao relatório somar maçãs com maçãs num repositório com backend Python e
frontend JavaScript.

## A escolha de nome de função (leia antes de mexer)

O lizard não qualifica nomes do mesmo jeito em toda linguagem:

- Java e C/C++ devolvem `Classe::metodo` e `namespace::Classe::metodo`;
- Python, Go e JavaScript devolvem só `metodo`, sem a classe em volta;
- funções anônimas de JavaScript chegam todas como `(anonymous)`.

Aqui os separadores `::` viram `.`, para que o relatório leia igual em toda
linguagem. O que este módulo **não** faz é inventar qualificação onde o lizard
não dá: deduzir a classe de um método Python pelo nível de aninhamento daria um
nome que muda quando o lizard muda de parser, e o erro apareceria lá na frente,
como cobertura atribuída à função errada.

Por isso `nome` serve para o humano ler, e **não** é chave. A chave estável é
`Funcao.identidade` — `arquivo:linha_inicio` — porque é o único identificador que
também existe do outro lado do cruzamento: LCOV e Cobertura XML reportam
arquivo e linha. (Os registros `FN` do LCOV até trazem nome, mas com a
decoração de cada compilador, que não bate com a do lizard.)

O campo `arquivo` sai normalizado mas preserva a forma do caminho que o
chamador passou: quem analisa `src/` recebe `src/app/x.py`, que é como os
relatórios de cobertura costumam escrever. Quem for cruzar os dois lados deve
comparar por sufixo de caminho, não por igualdade crua — os dois lados podem ter
raízes diferentes (o CI gera cobertura de um checkout, o desenvolvedor analisa
outro).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import lizard

_log = logging.getLogger(__name__)

#: O que entra no lugar de um nome que o lizard não soube dar. Texto e não
#: vazio: uma linha de relatório sem nome nenhum não dá para localizar, e o
#: rótulo entre colchetes não se confunde com identificador de verdade.
NOME_DESCONHECIDO = "[sem nome]"

EXCLUSOES_PADRAO: tuple[str, ...] = (
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
)
"""Pastas que quase nunca são código do projeto: dependência de terceiro, cache
ou saída de build. Medir isso infla o relatório com código que ninguém vai
corrigir, e o custo não é só cosmético: medido no protótipo que deu origem a
este pacote, um alvo `.` distraído varria 3156 arquivos no lugar de 14 — o
site-packages inteiro do virtualenv, cada arquivo virando candidato a
requisição paga. O chamador acrescenta as dele pelo parâmetro `excluir`."""

_LINGUAGEM_POR_EXTENSAO: dict[str, str] = {
    "c": "c",
    "h": "c",
    "jsx": "jsx",
}
"""Desempate onde um leitor do lizard atende mais de uma linguagem e anuncia a
errada primeiro: o leitor de C/C++ se apresenta como `cpp` mesmo lendo um `.c`,
e o de TSX se apresenta como `tsx` mesmo lendo um `.jsx`. O relatório agrupa por
esse campo, então a etiqueta precisa ser a da linguagem do arquivo."""


@dataclass(frozen=True)
class Funcao:
    """Uma função medida — a unidade que o resto do projeto pontua.

    Imutável porque atravessa o programa inteiro (cruzamento com cobertura,
    cálculo de risco, relatório) e nenhum desses passos tem o direito de
    reescrever a medição.
    """

    arquivo: str
    nome: str
    linha_inicio: int
    linha_fim: int
    complexidade: int
    linhas_logicas: int
    parametros: int
    linguagem: str

    @property
    def identidade(self) -> str:
        """Identificador estável da função: `arquivo:linha_inicio`.

        Nome não serve de chave (veja o cabeçalho do módulo): há homônimos no
        mesmo arquivo e, em JavaScript, um monte de `(anonymous)`.

        Os dois campos são conferidos antes de virar identidade, e aqui a
        exceção é preferível ao valor torto: esta string é o que cruza a
        medição com o relatório de cobertura. Uma identidade malformada não
        casa com nada e a função aparece como 0% coberta — resposta errada,
        plausível e silenciosa, que é a pior combinação das três. Falhar alto
        transforma isso num defeito que alguém conserta.
        """
        if not self.arquivo.strip():
            raise ValueError(
                f"Funcao sem arquivo não tem identidade estável (nome={self.nome!r})"
            )
        if self.linha_inicio < 1:
            raise ValueError(
                f"linha_inicio de {self.arquivo} é {self.linha_inicio}; "
                "linhas começam em 1 e o cruzamento com cobertura depende disso"
            )
        return f"{self.arquivo}:{self.linha_inicio}"


def analisar(caminhos: Sequence[str], excluir: Sequence[str] = ()) -> list[Funcao]:
    """Mede a complexidade de todas as funções encontradas em `caminhos`.

    Cada item de `caminhos` pode ser um arquivo ou um diretório; diretório é
    varrido recursivamente. Arquivo de extensão que o lizard não conhece é
    pulado em silêncio — um repositório é cheio de `.md`, `.json` e `.svg`, e
    reclamar de cada um deles transformaria o aviso útil em ruído. Caminho que
    não existe, ao contrário, levanta `FileNotFoundError`: aí é erro de quem
    chamou, e um retorno vazio esconderia o engano.

    `excluir` soma padrões aos de `EXCLUSOES_PADRAO`. A regra de casamento:

    - padrão sem barra casa com qualquer componente do caminho — `node_modules`
      poda a pasta onde ela estiver, `*.min.js` derruba o arquivo minificado;
    - padrão com barra casa com o caminho inteiro ou com um sufixo dele —
      `src/legado` pega tanto `src/legado` quanto `app/src/legado`.

    Os padrões são conferidos contra o caminho **relativo ao ponto analisado**,
    nunca contra o absoluto. Do contrário quem guarda o checkout em
    `~/build/projeto` receberia um relatório vazio — a pasta `build` do ancestral
    casaria com a exclusão padrão e podaria o projeto inteiro, em silêncio.

    Um arquivo nomeado diretamente em `caminhos` é analisado mesmo que case com
    uma exclusão: quem aponta o dedo para um arquivo quer aquele arquivo (é como
    o ripgrep trata um caminho explícito). As exclusões existem para podar
    varredura, não para vetar pedido.

    A ordem da lista é determinística — diretórios e arquivos são visitados em
    ordem alfabética — porque relatório que muda de ordem a cada execução
    produz diff falso em CI.
    """
    padroes = (*EXCLUSOES_PADRAO, *excluir)
    vistos: set[str] = set()
    funcoes: list[Funcao] = []
    for caminho in caminhos:
        for arquivo in _arquivos_candidatos(caminho, padroes):
            # O mesmo arquivo pode chegar por dois caminhos (`["src", "src/a.py"]`,
            # ou um link simbólico): medir duas vezes dobraria o peso dele na nota.
            identidade = os.path.realpath(arquivo)
            if identidade in vistos:
                continue
            vistos.add(identidade)
            funcoes.extend(_funcoes_do_arquivo(arquivo))
    return funcoes


def _arquivos_candidatos(caminho: str, padroes: Sequence[str]) -> Iterator[str]:
    """Arquivos sob ``caminho`` que sobrevivem às exclusões, em ordem estável.

    Três falhas são tratadas, e a diferença entre elas é o que esta função
    decide:

    - **caminho inexistente levanta.** É erro de quem chamou, e devolver vazio
      esconderia um alvo digitado errado atrás de um relatório de zero função;
    - **caminho ilegível levanta também**, traduzido: ``exists()`` devolve
      ``False`` para um diretório sem permissão de execução, o que faria a
      mensagem dizer "inexistente" sobre um caminho que existe e manda procurar
      o erro de digitação que não há;
    - **subpasta ilegível no meio da descida é pulada.** ``os.walk`` engole isso
      em silêncio por padrão; o ``onerror`` aqui existe para que esse silêncio
      seja uma escolha registrada, e não um padrão herdado sem querer.
    """
    alvo = Path(caminho)
    try:
        existe = alvo.exists()
    except OSError as erro:
        raise FileNotFoundError(f"caminho ilegível: {caminho} ({erro})") from erro
    if not existe:
        raise FileNotFoundError(f"caminho inexistente: {caminho}")
    if alvo.is_file():
        yield str(alvo)
        return
    for raiz, subpastas, nomes in os.walk(alvo, onerror=_pular_pasta_ilegivel):
        # Atribuir em fatia é o que faz o os.walk realmente não descer na pasta;
        # filtrar uma cópia só esconderia os arquivos, depois de já tê-los lido.
        subpastas[:] = sorted(
            pasta
            for pasta in subpastas
            if not _excluido(os.path.relpath(os.path.join(raiz, pasta), alvo), padroes)
        )
        for nome in sorted(nomes):
            completo = os.path.join(raiz, nome)
            if not _excluido(os.path.relpath(completo, alvo), padroes):
                yield completo


def _pular_pasta_ilegivel(erro: OSError) -> None:
    """Callback de ``os.walk`` para diretório que não deu para ler.

    O padrão de ``os.walk`` é engolir o erro sem deixar rastro. Registrar em
    ``debug`` mantém a varredura viva — uma pasta sem permissão não pode zerar
    o relatório do repositório — e ainda assim deixa o motivo disponível para
    quem for investigar por que uma função esperada não apareceu.

    ``filename`` é atributo de ``OSError`` que existe sempre, mas vale ``None``
    quando ninguém o preencheu — então o padrão do ``getattr`` nunca entraria e
    a linha de log diria "pulando None", que não ajuda ninguém a achar a pasta.
    """
    _log.debug("pulando %s: %s", getattr(erro, "filename", None) or "?", erro)


def _excluido(caminho: str, padroes: Sequence[str]) -> bool:
    """Se ``caminho`` casa com algum padrão de exclusão.

    Padrão vazio é descartado em vez de aplicado: ``fnmatch(x, "")`` é falso
    para tudo, então ele não faria mal — mas um ``--excluir ""`` vindo de um
    shell que expandiu uma variável vazia é engano de quem chamou, e ignorá-lo
    explicitamente é mais honesto que deixá-lo passar por acaso.

    Padrão sintaticamente inválido (um ``[`` sem fechar, que vem de um glob
    escrito à mão) faz ``fnmatch`` levantar ``re.error``. Aqui ele é tratado
    como "não casa": uma exclusão que não compila deve deixar o arquivo passar
    e ser medido, nunca abortar a varredura do repositório inteiro.
    """
    partes = Path(caminho).parts
    inteiro = Path(caminho).as_posix()
    for padrao in padroes:
        if not padrao:
            continue
        try:
            if "/" in padrao:
                if fnmatch(inteiro, padrao) or fnmatch(inteiro, f"*/{padrao}"):
                    return True
            elif any(fnmatch(parte, padrao) for parte in partes):
                return True
        except re.error:
            continue
    return False


def _funcoes_do_arquivo(caminho: str) -> list[Funcao]:
    # `get_reader_for` é o registro oficial de extensões do lizard; perguntar a ele
    # evita manter aqui uma lista que envelheceria a cada linguagem nova do upstream.
    # None significa extensão que o lizard não lê — o arquivo é pulado em silêncio.
    leitor = lizard.get_reader_for(caminho)
    if leitor is None:
        return []
    try:
        analise = lizard.analyze_file(caminho)
    except Exception:
        # O lizard tokeniza texto bruto; arquivo com bytes inválidos ou sintaxe
        # torta pode estourar de várias formas. Um arquivo problemático não pode
        # zerar o relatório do repositório inteiro, então ele é pulado.
        return []
    arquivo = os.path.normpath(caminho)
    linguagem = _linguagem(caminho, leitor)
    return [
        Funcao(
            arquivo=arquivo,
            nome=_nome(funcao.name),
            linha_inicio=funcao.start_line,
            linha_fim=funcao.end_line,
            complexidade=funcao.cyclomatic_complexity,
            linhas_logicas=funcao.nloc,
            parametros=funcao.parameter_count,
            linguagem=linguagem,
        )
        for funcao in analise.function_list
    ]


def _linguagem(caminho: str, leitor: type) -> str:
    """O nome da linguagem, preferindo o mapa próprio ao rótulo do lizard.

    Nunca levanta, e nunca devolve vazio: o valor vai para o `state` que o
    modelo lê, e uma linguagem em branco ali faz o julgamento ser feito sem
    saber de que linguagem se trata — pior que o palpite "desconhecida", que
    pelo menos é legível como o que é.

    ``language_names`` é atributo de classe do leitor do lizard, não contrato
    estável: versão nova pode não trazê-lo, trazê-lo vazio ou trazer algo que
    não é sequência de texto. Os três casos caem no mesmo lugar.
    """
    extensao = Path(caminho).suffix.lstrip(".").lower()
    if extensao in _LINGUAGEM_POR_EXTENSAO:
        return _LINGUAGEM_POR_EXTENSAO[extensao]
    nomes = getattr(leitor, "language_names", ())
    if isinstance(nomes, (list, tuple)) and nomes and isinstance(nomes[0], str):
        return nomes[0]
    return extensao or "desconhecida"


def _nome(bruto: str) -> str:
    """Normaliza o nome vindo do lizard (veja a explicação no topo do módulo).

    ``bruto`` vem de biblioteca de terceiro, então o tipo é conferido em vez de
    presumido: um ``None`` levantaria ``AttributeError`` dentro da medição, e o
    relatório perderia o arquivo inteiro por causa de uma função. Nome ausente
    vira ``NOME_DESCONHECIDO``, que é legível e não se confunde com nome real.

    Nome só de espaços cairia em string vazia depois do ``split`` — e função
    sem nome nenhum no relatório é linha que ninguém consegue localizar.
    """
    if not isinstance(bruto, str):
        return NOME_DESCONHECIDO
    limpo = " ".join(bruto.replace("::", ".").split())
    return limpo or NOME_DESCONHECIDO


def medir_fonte(nome_do_arquivo: str, codigo: str) -> list[Funcao]:
    """Mede um texto que não está (necessariamente) em disco.

    Serve ao caso em que alguém cola um trecho na conversa: dá para saber a
    complexidade dele sem escrever arquivo temporário. O ``nome_do_arquivo``
    não precisa existir — o lizard só o usa para escolher o leitor pela
    extensão —, mas a extensão precisa estar certa, senão a linguagem é outra e
    o número também.

    Devolve lista vazia quando a extensão é desconhecida ou o texto não
    tokeniza. Vazio é a resposta honesta: melhor dizer "não consegui medir" do
    que devolver complexidade 1 para um trecho que não foi lido.
    """
    if lizard.get_reader_for(nome_do_arquivo) is None:
        return []
    try:
        analise = lizard.analyze_file.analyze_source_code(nome_do_arquivo, codigo)
    except Exception:
        return []
    leitor = lizard.get_reader_for(nome_do_arquivo)
    linguagem = _linguagem(nome_do_arquivo, leitor)
    return [
        Funcao(
            arquivo=nome_do_arquivo,
            nome=_nome(funcao.name),
            linha_inicio=funcao.start_line,
            linha_fim=funcao.end_line,
            complexidade=funcao.cyclomatic_complexity,
            linhas_logicas=funcao.nloc,
            parametros=funcao.parameter_count,
            linguagem=linguagem,
        )
        for funcao in analise.function_list
    ]
