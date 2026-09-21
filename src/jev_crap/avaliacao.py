"""Onde os dois eixos se cruzam e viram decisão.

O eixo contável (complexidade × cobertura) é barato, determinístico e conta
caminhos e linhas. O eixo semântico (o Jev) responde o que o número não sabe.
Este módulo mede o primeiro, pede o segundo só sobre o que vale a pena, e
combina os dois em algo acionável. As regras abaixo são o projeto inteiro:

1. **O que dá para contar, conta-se — não vira pergunta.** Complexidade,
   cobertura, tamanho. Perguntar trocaria certeza por distribuição de
   probabilidade.

2. **O estado só leva o que o modelo não vê no texto.** Cobertura e trechos de
   teste sim, complexidade ciclomática não. Mandar o ccn ancoraria o julgamento
   no número que nós mesmos enviamos.

3. **Risco vira gate e fica fora da nota.** Vulnerabilidade não se compensa com
   legibilidade boa. Tamanho também: função gigante é fato, não opinião. Mas só
   barra o que é proposição verificável e de consequência alta — dimensão
   gradual ("dá para quebrar com entrada esquisita?") é quase sempre verdadeira
   em qualquer código real e barraria o repositório inteiro.

4. **Ausência de dado é ausência, nunca zero.** Zero diria "nada coberto" e
   puniria função sem desvio nenhum.

5. **A regra 4 vale também para o julgamento.** Se não há trecho de teste para
   mostrar, a pergunta sobre teste não é feita e o peso dela é redistribuído.
   Perguntar sem evidência devolveria "não há teste" para função testada
   indiretamente — e cobraria por isso um quarto da nota.

E uma regra sobre como o resultado se apresenta: **a nota ordena, não mede.** A
documentação do modelo é explícita quanto à calibração numérica fraca de um
score ("use score outputs only to check threshold passage, not for precise
magnitude reconstruction"). Por isso a nota sai acompanhada de uma faixa
nomeada: 72.4 e 75.1 são o mesmo "aceitável", e tratar a diferença entre eles
como informação é ler precisão que o modelo não tem.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_crap.config import CARACTERES_POR_TOKEN, Config
from jev_crap.evidencia import PADROES_DE_TESTE, testes_de
from jev_crap.julgamento.jev import Julgador, Resposta, montar_estado
from jev_crap.julgamento.rubrica import Rubrica, carregar_rubrica
from jev_crap.metrica import cobertura as mod_cobertura
from jev_crap.metrica import complexidade as mod_complexidade
from jev_crap.metrica.cobertura import SEM_DADOS, CoberturaArquivo
from jev_crap.metrica.risco import Formula, Insumos
from jev_crap.situacoes import SituacaoConhecida

__all__ = [
    "FAIXAS",
    "FuncaoAvaliada",
    "FuncaoMedida",
    "Medicao",
    "avaliar",
    "decidir",
    "faixa_da_nota",
    "julgar_trecho",
    "medir",
    "relatorio_contavel",
]

_log = logging.getLogger(__name__)

VEREDITOS = ("aprovar", "revisar", "bloquear", "sem_julgamento")

#: Gravidade crescente. `sem_julgamento` fica entre aprovar e revisar porque
#: não é aprovação — é ausência de informação —, mas também não é uma dúvida
#: levantada por evidência.
ORDEM_DOS_VEREDITOS = ("aprovar", "sem_julgamento", "revisar", "bloquear")

#: O que entra no lugar de uma contagem que não pôde ser feita. Negativo de
#: propósito: zero seria lido como "nenhuma função acima do limiar", que é o
#: oposto de "não consegui contar".
SEM_CONTAGEM = -1

#: O que a nota sustenta, em palavras. Ver a nota sobre calibração no topo do
#: módulo: a faixa é a unidade real da resposta; o decimal é ruído com aparência
#: de precisão.
FAIXAS: tuple[tuple[float, str], ...] = (
    (80.0, "sólido"),
    (60.0, "aceitável"),
    (40.0, "frágil"),
    (0.0, "ruim"),
)


def faixa_da_nota(nota: float | None) -> str:
    """A faixa em que a nota cai, ou ``"sem nota"`` quando não houve julgamento."""
    if nota is None:
        return "sem nota"
    for piso, nome in FAIXAS:
        if nota >= piso:
            return nome
    return "ruim"


# --------------------------------------------------------------------------- #
# Estruturas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FuncaoMedida:
    """O que se sabe de uma função sem gastar um token sequer."""

    arquivo: str
    nome: str
    linha_inicio: int
    linha_fim: int
    complexidade: int
    linhas_logicas: int
    linguagem: str
    cobertura_linha: float
    cobertura_branch: float
    risco: float
    codigo: str = ""
    testes: tuple[str, ...] = ()

    @property
    def identificador(self) -> str:
        """Identificador estável: ``arquivo:linha_inicio``.

        Nome não serve de chave — há homônimos no mesmo arquivo e, em
        JavaScript, um monte de `(anonymous)`.

        O nome é ``identificador`` e não ``chave`` de propósito: neste projeto
        "chave" é a credencial da API, e os dois sob a mesma palavra tornam
        impossível procurar por um sem achar o outro.

        Os dois campos são conferidos antes de virar identificador. Esta string
        identifica a função no relatório, no registro de falhas e no episódio
        de histórico; duas funções com a mesma chave viram uma só na contagem
        de julgadas, e o relatório sai com menos funções do que foram medidas
        sem dizer por quê.
        """
        if not self.arquivo.strip():
            raise ValueError(f"função sem arquivo não tem chave estável (nome={self.nome!r})")
        if self.linha_inicio < 1:
            raise ValueError(
                f"{self.arquivo}: linha_inicio é {self.linha_inicio}; linhas começam em 1"
            )
        return f"{self.arquivo}:{self.linha_inicio}"

    @property
    def tamanho(self) -> int:
        """Linhas físicas que a função ocupa, pontas incluídas.

        Faixa invertida é recusada em vez de devolver número negativo. Este
        valor vira gate: acima de ``limite_tamanho`` a função é reprovada por
        tamanho, e tamanho negativo passa por qualquer comparação ``>`` — uma
        função gigante com as linhas trocadas escaparia do gate justamente por
        estar malformada.
        """
        if self.linha_fim < self.linha_inicio:
            raise ValueError(
                f"{self.arquivo}:{self.linha_inicio}: linha_fim {self.linha_fim} vem antes do "
                "início; a faixa da função está invertida"
            )
        return self.linha_fim - self.linha_inicio + 1

    def para_medicao(self) -> dict[str, Any]:
        """A medição em formato de resposta, com as ausências como ``null``.

        ``_ou_nulo`` traduz a sentinela ``SEM_DADOS`` (-1) em ``None`` para que
        o JSON traga ``null`` e não ``-1.0``: do outro lado há um modelo lendo
        uma coluna de porcentagem, e cobertura negativa é exatamente o tipo de
        dado que vira conclusão errada com cara de fato.

        ``trechos_de_teste`` sai como **contagem**, não como os trechos: eles já
        foram enviados ao modelo dentro do estado e repeti-los na resposta
        multiplicaria o tamanho do relatório sem acrescentar decisão nenhuma.

        Falhar aqui não corrompe nada — é uma leitura de campos já congelados —,
        mas ``identificador`` e ``tamanho`` conferem os próprios invariantes, então uma
        função malformada é recusada aqui em vez de virar uma linha torta do
        relatório.
        """
        return {
            "chave": self.identificador,
            "arquivo": self.arquivo,
            "funcao": self.nome,
            "linhas": [self.linha_inicio, self.linha_fim],
            "tamanho": self.tamanho,
            "linguagem": self.linguagem,
            "complexidade": self.complexidade,
            "linhas_logicas": self.linhas_logicas,
            "cobertura_linha": _ou_nulo(self.cobertura_linha),
            "cobertura_branch": _ou_nulo(self.cobertura_branch),
            "risco": self.risco,
            "trechos_de_teste": len(self.testes),
        }


@dataclass(frozen=True)
class FuncaoAvaliada:
    """A função medida, julgada e decidida."""

    medida: FuncaoMedida
    respostas: Mapping[str, Resposta]
    notas: Mapping[str, float]
    """Dimensões de qualidade observadas, já normalizadas em 0..1."""

    nao_observadas: tuple[str, ...]
    nota: float | None
    graves: tuple[str, ...]
    duvidas: tuple[str, ...]
    conselho: str
    prioridade: str
    veredito: str
    modelo: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)

    @property
    def faixa(self) -> str:
        """O nome da faixa em que a nota cai — a unidade real da resposta.

        Delega a :func:`faixa_da_nota`, que trata ``None`` como "sem nota" em
        vez de "ruim": função sem julgamento e função julgada mal são coisas
        diferentes, e o relatório precisa mostrar a diferença.

        Existe como propriedade, e não como campo, justamente para não poder
        divergir de ``nota``: guardar as duas deixaria alguém atualizar uma e
        esquecer a outra, e o relatório sairia com nota 85 na faixa "frágil".
        """
        return faixa_da_nota(self.nota)

    def para_avaliacao(self, *, com_respostas: bool = True) -> dict[str, Any]:
        """A função medida, julgada e decidida, em formato de resposta.

        Estende :meth:`FuncaoMedida.para_medicao`, e a ordem importa: os fatos
        contáveis primeiro, depois o julgamento. Quem lê o relatório precisa
        conseguir separar o que foi medido do que foi opinado, e um dicionário
        que mistura os dois convida a tratar nota como medida.

        ``com_respostas=False`` existe para o histórico, que guarda as notas
        normalizadas e não o bruto da API: o bruto só faz sentido junto da
        versão da régua que o produziu, e guardá-lo sem ela criaria uma série
        que parece comparável e não é.

        Os números são arredondados aqui, na saída, e nunca antes: arredondar
        na hora de calcular propaga o erro para a média ponderada.
        """
        corpo: dict[str, Any] = {
            **self.medida.para_medicao(),
            "nota": self.nota,
            "faixa": self.faixa,
            "notas": {n: round(v, 3) for n, v in self.notas.items()},
            "nao_observadas": list(self.nao_observadas),
            "graves": list(self.graves),
            "duvidas": list(self.duvidas),
            "conselho": self.conselho,
            "prioridade": self.prioridade,
            "veredito": self.veredito,
        }
        if com_respostas and self.respostas:
            corpo["respostas"] = {
                nome: {
                    "tipo": r.tipo,
                    "bruto": round(r.bruto, 4),
                    "normalizado": round(r.normalizado, 4),
                    "confianca": None if r.confianca is None else round(r.confianca, 4),
                }
                for nome, r in self.respostas.items()
            }
        if self.modelo:
            corpo["modelo"] = self.modelo
        return corpo


@dataclass(frozen=True)
class Medicao:
    """O resultado do eixo contável sobre um conjunto de caminhos."""

    funcoes: tuple[FuncaoMedida, ...]
    limiar: float
    formula: Formula
    avisos: tuple[str, ...] = ()

    @property
    def acima_do_limiar(self) -> tuple[FuncaoMedida, ...]:
        """As funções que passaram da linha de corte — as únicas que serão julgadas.

        Este recorte é o que mantém o custo baixo: medir mil funções é grátis,
        julgar mil não. Por isso o limiar é conferido aqui em vez de confiado:
        ``nan`` faz **toda** comparação ``>=`` dar falso, e a varredura
        terminaria dizendo "nenhuma função acima do limiar" — resposta cara,
        plausível e completamente errada, sem erro nenhum na tela.

        A recusa é de leitura pura: nada é escrito, nada é enviado, nenhum
        relatório anterior muda. Ela acontece no recorte, **antes** de qualquer
        requisição paga, e as duas entradas a traduzem em erro de uso — saída 3
        na CLI, situação nomeada no MCP. O estrago de falhar aqui é uma
        varredura que não começa, com a mensagem dizendo qual valor consertar.
        """
        if not math.isfinite(self.limiar):
            raise ValueError(
                f"limiar {self.limiar} não é finito; nenhuma função ficaria acima dele e a "
                "varredura sairia vazia sem dizer por quê"
            )
        return tuple(f for f in self.funcoes if f.risco >= self.limiar)


def _ou_nulo(valor: float) -> float | None:
    """Traduz o sentinela de ausência para ``null`` no JSON.

    Do outro lado há um modelo lendo o relatório. ``-1.0`` numa coluna de
    porcentagem seria lido como um número, e um número negativo de cobertura é
    exatamente o tipo de coisa que vira conclusão errada com cara de dado.

    É uma conversão pura, chamada na formatação da resposta: não lê disco, não
    escreve, não envia nada e não participa de nenhuma decisão — os vereditos
    já foram tomados sobre o valor bruto quando esta função é chamada. O pior
    desfecho possível aqui é um campo do relatório sair com o número errado,
    enquanto o veredito ao lado continua correto.
    """
    return None if valor == SEM_DADOS else round(valor, 4)


# --------------------------------------------------------------------------- #
# Cruzamento com o relatório de cobertura
# --------------------------------------------------------------------------- #


def _componentes(caminho: str) -> tuple[str, ...]:
    """O caminho quebrado em componentes, sem raiz nem vazios.

    A barra invertida vira barra antes da quebra porque o relatório de
    cobertura pode ter sido gerado no Windows enquanto a medição roda no Linux:
    sem a troca, ``src\\a.py`` seria **um** componente e não casaria com
    ``src/a.py`` — e o arquivo apareceria como sem cobertura.

    A raiz e os vazios saem porque a comparação é por sufixo: mantê-los faria
    ``/src/a.py`` e ``src/a.py`` terem contagens diferentes para o mesmo
    caminho relativo.
    """
    if not caminho:
        return ()
    return tuple(p for p in Path(str(caminho).replace("\\", "/")).parts if p not in ("/", ""))


def _sufixo_comum(a: str, b: str) -> int:
    """Quantos componentes finais de caminho os dois têm em comum.

    Comparar componentes inteiros, e não texto, é o que impede `cobertura.py` de
    casar com `xcobertura.py`: por texto puro um é sufixo do outro.

    Caminho vazio devolve 0, e não levanta: o relatório pode trazer um registro
    sem nome de arquivo, e nesse caso a resposta certa é "não casa com nada" —
    não uma exceção que derruba o cruzamento de todos os outros arquivos.
    """
    pa, pb = _componentes(a), _componentes(b)
    if not pa or not pb:
        return 0
    n = 0
    while n < min(len(pa), len(pb)) and pa[-1 - n] == pb[-1 - n]:
        n += 1
    return n


def _casar_arquivo(
    arquivo: str, cobertura: Mapping[str, CoberturaArquivo]
) -> tuple[CoberturaArquivo | None, bool]:
    """A entrada do relatório que corresponde a ``arquivo``, e se houve empate.

    O relatório grava o caminho do CI (``/build/src/a.js``); casar por sufixo
    evita que o arquivo apareça como "sem cobertura" por diferença de prefixo.
    Entre vários candidatos vence o de maior sufixo comum — pegar o primeiro que
    serve atribuiria a cobertura do arquivo errado quando dois módulos têm o
    mesmo nome de base em pastas diferentes.

    O empate é devolvido em vez de resolvido: quando dois candidatos casam
    igualmente bem, qualquer escolha é chute, e um chute silencioso vira
    cobertura atribuída ao arquivo errado. O chamador transforma isso em aviso.
    """
    melhor: CoberturaArquivo | None = None
    melhor_n = 0
    empate = False
    for candidato in cobertura.values():
        n = _sufixo_comum(candidato.arquivo, arquivo)
        if n > melhor_n:
            melhor, melhor_n, empate = candidato, n, False
        elif n == melhor_n and n > 0 and candidato is not melhor:
            empate = True
    return melhor, empate


# --------------------------------------------------------------------------- #
# Medir
# --------------------------------------------------------------------------- #


def ler_cobertura(caminho: str, raiz: Path) -> dict[str, CoberturaArquivo]:
    """Lê o relatório de cobertura, traduzindo as falhas previsíveis."""
    try:
        return mod_cobertura.ler(caminho, raiz=str(raiz))
    except FileNotFoundError as erro:
        raise SituacaoConhecida(
            "cobertura_inexistente",
            f"não há relatório de cobertura em {caminho!r}",
            "Rode a suíte gerando cobertura e aponte o arquivo: "
            "`pytest --cov=src --cov-branch --cov-report=xml` (Python) ou "
            "`jest --coverage --coverageReporters=lcov` (JS/TS). Sem relatório, use "
            "`com_julgamento=false` sabendo que toda função entra como 0% e tudo vira risco alto.",
            caminho=caminho,
        ) from erro
    except mod_cobertura.FormatoDeCoberturaDesconhecido as erro:
        raise SituacaoConhecida(
            "cobertura_ilegivel",
            str(erro),
            "O arquivo precisa ser LCOV (começa com `TN:` ou `SF:`) ou Cobertura XML "
            "(raiz `<coverage>`). Relatórios em HTML ou JSON do coverage.py não servem — "
            "gere de novo com `--cov-report=xml`.",
            caminho=caminho,
        ) from erro
    except OSError as erro:
        raise SituacaoConhecida(
            "cobertura_ilegivel",
            f"não consegui ler {caminho!r}: {erro}",
            "Confira permissão e se o caminho aponta para um arquivo, não para um diretório.",
            caminho=caminho,
        ) from erro


def medir(
    caminhos: Sequence[str],
    cobertura: str | None = None,
    *,
    config: Config,
    limiar: float | None = None,
    pasta_testes: str | None = None,
    com_codigo: bool = False,
) -> Medicao:
    """Mede o eixo contável de todas as funções encontradas em ``caminhos``.

    Arquivos de teste são podados na descida: avaliar o próprio teste polui o
    relatório e gasta token sem responder nada.

    ``com_codigo`` controla se o texto de cada função e os trechos de teste são
    carregados. Sai desligado porque a medição é usada também em varredura
    ampla, onde carregar o corpo de milhares de funções custa memória para nada;
    a avaliação com julgamento liga a chave só para o recorte que vai ao modelo.
    """
    if not caminhos:
        raise SituacaoConhecida(
            "sem_caminhos",
            "nenhum caminho foi informado",
            'Passe ao menos um arquivo ou diretório, por exemplo ["src"].',
        )

    formula = config.obter_formula()
    limiar_efetivo = config.limiar_efetivo(formula, limiar)
    avisos: list[str] = list(config.avisos)

    relatorio: dict[str, CoberturaArquivo] = {}
    if cobertura:
        relatorio = ler_cobertura(cobertura, config.raiz)
        if not relatorio:
            avisos.append(
                f"{cobertura} foi lido mas não descreve nenhum arquivo; "
                "toda função entra sem dados de cobertura"
            )
    else:
        avisos.append(
            "nenhum relatório de cobertura informado: o risco é calculado como se nada "
            "estivesse coberto, o que sobe o número de todas as funções por igual"
        )

    try:
        brutas = mod_complexidade.analisar(
            list(caminhos), excluir=config.exclusoes(PADROES_DE_TESTE)
        )
    except FileNotFoundError as erro:
        raise SituacaoConhecida(
            "caminho_inexistente",
            str(erro),
            "Confira o caminho. Ele é resolvido a partir do diretório em que o servidor "
            f"está rodando ({Path.cwd()}); use caminho absoluto se houver dúvida.",
        ) from erro

    cruzamento = _Cruzamento()
    textos: dict[str, list[str]] = {}
    medidas = [
        _medir_uma(
            bruta,
            relatorio=relatorio,
            formula=formula,
            cruzamento=cruzamento,
            com_codigo=com_codigo,
            pasta_testes=pasta_testes,
            textos=textos,
        )
        for bruta in brutas
    ]

    avisos.extend(
        _avisos_da_medicao(
            relatorio=relatorio,
            arquivos=cruzamento.arquivos,
            nao_casados=cruzamento.nao_casados,
            ambiguos=cruzamento.ambiguos,
            sem_branch=cruzamento.sem_branch,
            insumos_invalidos=cruzamento.insumos_invalidos,
            encontrou_funcao=bool(medidas),
        )
    )

    medidas.sort(key=lambda f: (-f.risco, f.arquivo, f.linha_inicio))
    return Medicao(
        funcoes=tuple(medidas),
        limiar=limiar_efetivo,
        formula=formula,
        avisos=tuple(avisos),
    )


@dataclass
class _Cruzamento:
    """O que a medição acumula **por arquivo** enquanto percorre as funções.

    Existe para que :func:`_medir_uma` possa registrar o que descobriu sem
    devolver cinco valores a mais por função: os contadores aqui têm
    cardinalidade de arquivo e de varredura, enquanto o retorno daquela função
    tem cardinalidade de função. Passá-los pelo retorno obrigaria ``medir`` a
    reagregar, a cada função, o que já estava agregado.
    """

    arquivos: set[str] = field(default_factory=set)
    nao_casados: set[str] = field(default_factory=set)
    """Arquivos que não casaram com nenhuma entrada do relatório."""

    ambiguos: set[str] = field(default_factory=set)
    """Arquivos que casaram igualmente bem com mais de uma entrada."""

    sem_branch: int = 0
    """Funções cujo arquivo não trouxe cobertura de branch."""

    insumos_invalidos: list[str] = field(default_factory=list)
    """Funções cujo insumo de cobertura foi recusado, com o motivo."""


def _medir_uma(
    bruta: Any,
    *,
    relatorio: Mapping[str, CoberturaArquivo],
    formula: Formula,
    cruzamento: _Cruzamento,
    com_codigo: bool,
    pasta_testes: str | None,
    textos: dict[str, list[str]],
) -> FuncaoMedida:
    """Uma função bruta do analisador, cruzada com cobertura e pontuada.

    Reúne os quatro passos que só fazem sentido juntos — casar com o relatório,
    montar os insumos, buscar código e teste, calcular o risco — e registra em
    ``cruzamento`` o que precisa virar aviso depois.

    ``com_codigo`` decide se o texto da função e os trechos de teste são
    carregados. ``medir_risco`` não precisa deles: nada vai ao modelo, e ler o
    disco por função custaria tempo para produzir campos que ninguém lê. O
    ``textos`` é o cache que faz um arquivo com trinta funções ser lido uma vez
    e não trinta.

    Nada aqui levanta por causa dos dados: cobertura que não casa vira ausência
    registrada, insumo inválido vira ausência mais um aviso, arquivo ilegível
    vira trecho vazio. Uma função problemática custa a própria precisão, nunca
    a varredura.
    """
    cruzamento.arquivos.add(bruta.arquivo)
    cobertura_linha, cobertura_branch = _cobertura_da_funcao(
        bruta, relatorio, cruzamento.nao_casados, cruzamento.ambiguos
    )
    if cobertura_branch == SEM_DADOS and cobertura_linha != SEM_DADOS:
        cruzamento.sem_branch += 1

    insumos, cobertura_linha, cobertura_branch = _insumos_da_funcao(
        bruta, cobertura_linha, cobertura_branch, cruzamento.insumos_invalidos
    )

    codigo = ""
    testes: tuple[str, ...] = ()
    if com_codigo:
        codigo = _trecho_do_arquivo(bruta.arquivo, bruta.linha_inicio, bruta.linha_fim, textos)
        testes = tuple(testes_de(bruta.nome, pasta_testes))

    return FuncaoMedida(
        arquivo=bruta.arquivo,
        nome=bruta.nome,
        linha_inicio=bruta.linha_inicio,
        linha_fim=bruta.linha_fim,
        complexidade=insumos.complexidade,
        linhas_logicas=insumos.linhas_logicas,
        linguagem=bruta.linguagem,
        cobertura_linha=cobertura_linha,
        cobertura_branch=cobertura_branch,
        risco=formula.calcular(insumos),
        codigo=codigo,
        testes=testes,
    )


def _cobertura_da_funcao(
    bruta: Any,
    relatorio: Mapping[str, CoberturaArquivo],
    nao_casados: set[str],
    ambiguos: set[str],
) -> tuple[float, float]:
    """A cobertura da faixa de linhas da função, e o registro do que não casou.

    Devolve :data:`SEM_DADOS` nos dois valores quando não há relatório ou quando
    o arquivo não casou com nenhuma entrada dele — e a diferença entre esses
    dois casos vai para ``nao_casados``, que vira aviso. Sem essa distinção, um
    relatório gerado noutra raiz produziria um projeto inteiro em 0% e seria
    lido como falta de teste, que é o diagnóstico errado.

    ``ambiguos`` recebe o arquivo que casou igualmente bem com mais de uma
    entrada: a cobertura escolhida pode ser de outro módulo de mesmo nome, e
    quem lê precisa saber disso antes de agir sobre o número.

    Os dois conjuntos são mutados de propósito, em vez de devolvidos: eles
    acumulam por arquivo ao longo de toda a varredura, enquanto o retorno é por
    função. Misturar as duas cardinalidades no retorno faria o chamador
    reagregar o que já estava agregado.
    """
    if not relatorio:
        return float(SEM_DADOS), float(SEM_DADOS)

    cob, empate = _casar_arquivo(bruta.arquivo, relatorio)
    if empate:
        ambiguos.add(bruta.arquivo)
    if cob is None:
        nao_casados.add(bruta.arquivo)
        return float(SEM_DADOS), float(SEM_DADOS)

    cobertura_linha, cobertura_branch = mod_cobertura.cobertura_de_faixa(
        cob, bruta.linha_inicio, bruta.linha_fim
    )
    if not cob.tem_dados_de_branch:
        cobertura_branch = float(SEM_DADOS)
    return cobertura_linha, cobertura_branch


def _insumos_da_funcao(
    bruta: Any,
    cobertura_linha: float,
    cobertura_branch: float,
    invalidos: list[str],
) -> tuple[Insumos, float, float]:
    """Os insumos de risco de uma função, sem deixar um arquivo torto derrubar todos.

    Cobertura acima de 1 num único arquivo — LCOV somando execuções em vez de
    linhas distintas é a causa comum — abortaria a medição do repositório
    inteiro. A função entra como sem dados de cobertura, o caso é registrado em
    ``invalidos`` para virar aviso, e a varredura segue.

    Devolve também a cobertura efetivamente usada, que pode ser diferente da
    recebida: sem isso o relatório mostraria o valor recusado ao lado de um
    risco calculado sem ele, e os dois números não se explicariam.
    """
    campos = {
        "complexidade": max(1, bruta.complexidade),
        "linhas_logicas": max(0, bruta.linhas_logicas),
    }
    try:
        insumos = Insumos(
            cobertura_linha=cobertura_linha,
            cobertura_branch=None if cobertura_branch == SEM_DADOS else cobertura_branch,
            **campos,
        )
    except ValueError as erro:
        invalidos.append(f"{bruta.arquivo}:{bruta.linha_inicio} ({erro})")
        cobertura_linha = cobertura_branch = float(SEM_DADOS)
        insumos = Insumos(
            cobertura_linha=cobertura_linha, cobertura_branch=None, **campos
        )
    return insumos, cobertura_linha, cobertura_branch


def _avisos_da_medicao(
    *,
    relatorio: Mapping[str, CoberturaArquivo],
    arquivos: set[str],
    nao_casados: set[str],
    ambiguos: set[str],
    sem_branch: int,
    insumos_invalidos: Sequence[str],
    encontrou_funcao: bool,
) -> list[str]:
    """Tudo que muda a leitura da medição, numa lista só.

    Os avisos moram juntos porque são lidos juntos, e porque a ordem entre eles
    importa: o de cruzamento vem primeiro por ser o que mais vezes explica um
    relatório inteiro em vermelho. Espalhá-los pelo corpo de ``medir`` fazia
    cada um ser acrescentado longe dos outros, e o segundo a ser escrito não
    tinha como saber que já havia um primeiro.

    Nenhum deles é erro: a medição aconteceu e o resultado vale. Eles existem
    para que o número não seja lido como o que não é — "sem cobertura" quando o
    relatório não casou, "projeto ruim" quando faltou o branch.
    """
    avisos = list(
        _avisos_de_cruzamento(relatorio, arquivos, nao_casados, ambiguos, sem_branch)
    )
    if insumos_invalidos:
        amostra = ", ".join(insumos_invalidos[:3])
        avisos.append(
            f"{len(insumos_invalidos)} função(ões) tiveram insumo de cobertura inválido e "
            f"entraram como não medidas: {amostra}. Isso costuma ser defeito do relatório "
            "(cobertura acima de 100% sai de somar execuções em vez de linhas distintas)"
        )
    if not encontrou_funcao:
        avisos.append(
            "nenhuma função foi encontrada — confira se o caminho tem código em linguagem "
            "que o lizard lê e se ele não está inteiro dentro de uma exclusão"
        )
    return avisos


def _trecho_do_arquivo(
    arquivo: str, inicio: int, fim: int, cache: dict[str, list[str]]
) -> str:
    """O texto de uma função. O arquivo é lido uma vez por varredura, não por função."""
    if arquivo not in cache:
        try:
            cache[arquivo] = Path(arquivo).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            cache[arquivo] = []
    return "\n".join(cache[arquivo][inicio - 1 : fim])


def _avisos_de_cruzamento(
    relatorio: Mapping[str, CoberturaArquivo],
    arquivos: set[str],
    nao_casados: set[str],
    ambiguos: set[str],
    sem_branch: int,
) -> list[str]:
    """Avisos que mudam a leitura do relatório — e que por isso não podem ficar calados.

    O caso mais perigoso é o primeiro: quando *nenhum* arquivo casa, o relatório
    sai inteiro em vermelho e parece dizer que o projeto é péssimo, quando na
    verdade diz que o caminho do relatório não bate com o caminho analisado.
    """
    avisos: list[str] = []
    if not relatorio:
        return avisos

    if arquivos and nao_casados >= arquivos:
        avisos.append(
            "nenhum arquivo analisado casou com o relatório de cobertura — quase sempre é "
            "diferença de raiz entre o caminho do CI e o caminho local, não ausência de teste. "
            "Confira o prefixo dos caminhos dentro do relatório antes de ler o risco."
        )
    elif nao_casados:
        exemplos = ", ".join(sorted(nao_casados)[:3])
        avisos.append(
            f"{len(nao_casados)} arquivo(s) não foram encontrados no relatório de cobertura "
            f"e entraram sem dados (ex.: {exemplos})"
        )
    if ambiguos:
        exemplos = ", ".join(sorted(ambiguos)[:3])
        avisos.append(
            f"{len(ambiguos)} arquivo(s) casaram com mais de uma entrada do relatório com a "
            f"mesma profundidade (ex.: {exemplos}); a cobertura deles pode ser de outro módulo "
            "de mesmo nome"
        )
    if sem_branch:
        avisos.append(
            f"{sem_branch} função(ões) vieram de arquivos sem cobertura de branch no relatório. "
            "Cobertura de linha declara coberto um `if` sem `else` cujo ramo nunca rodou — "
            "gere com `--cov-branch` (ou equivalente) para o número valer mais."
        )
    return avisos


def relatorio_contavel(medicao: Medicao, config: Config) -> dict[str, Any]:
    """O eixo contável em formato de resposta, sem nenhuma chamada de rede.

    É o que ``medir_risco`` devolve: grátis, determinístico e utilizável em CI
    sem chave de API. O campo ``o_que_isto_nao_responde`` vai junto de propósito
    — sem ele o número parece um veredito, e o erro mais caro que esta
    ferramenta pode induzir é alguém refatorar código cuja complexidade é
    essencial ao domínio porque um número mandou.

    Só as funções acima do limiar são detalhadas, e ``max_no_relatorio`` corta o
    resto: relatório com milhares de itens estoura a janela de contexto de quem
    o lê (um modelo) e esconde justamente as poucas linhas que importam. Os
    cortes são declarados em ``omitidas_do_relatorio``, nunca silenciosos.

    Nada é escrito e nada é enviado: uma falha aqui custa a formatação de uma
    resposta cujo cálculo já terminou, sem alterar dado nenhum. Por isso a
    interpretação de cada número é protegida: uma fórmula registrada de fora
    pode falhar ao explicar um valor, e a explicação de uma função não pode
    levar junto o relatório das outras.
    """
    acima = medicao.acima_do_limiar
    # O teto pode vir de configuração do usuário. Negativo fatiaria a lista
    # pelo fim (`lista[:-3]` devolve tudo menos os três últimos), entregando um
    # relatório que parece completo e esconde justamente as funções de maior
    # risco — que vêm primeiro na ordenação.
    teto = max(0, config.max_no_relatorio)
    mostradas = acima[:teto] or medicao.funcoes[:teto]
    return {
        "resumo": {
            "funcoes_medidas": len(medicao.funcoes),
            "acima_do_limiar": len(acima),
            "limiar": medicao.limiar,
            "formula": medicao.formula.nome,
            "detalhadas_no_relatorio": len(mostradas),
            "omitidas_do_relatorio": max(0, len(acima) - len(mostradas)),
        },
        "funcoes": [
            {**f.para_medicao(), "interpretacao": _interpretar(medicao.formula, f.risco)}
            for f in mostradas
        ],
        "avisos": list(medicao.avisos),
        "o_que_isto_nao_responde": (
            "Se a complexidade é essencial ao domínio ou acidental, e se os testes verificam "
            "comportamento ou só executam linhas. Essas duas decidem entre escrever teste e "
            "refatorar — só avaliar_arquivos as responde."
        ),
        "regua": config.para_regua(),
    }


# --------------------------------------------------------------------------- #
# Decidir
# --------------------------------------------------------------------------- #


def _interpretar(formula: Formula, risco: float) -> str:
    """A frase da fórmula sobre um número, ou um aviso no lugar dela.

    A fórmula pode ter sido registrada de fora e ``interpretar`` é o método
    mais fácil de implementar errado — ele formata texto sobre um número que
    pode ser qualquer coisa. Uma exceção aqui derrubaria o relatório de todas
    as funções por causa da legenda de uma.
    """
    try:
        return str(formula.interpretar(risco))
    except Exception:  # noqa: BLE001 - legenda de uma função não derruba as outras
        _log.warning("a fórmula %r não soube interpretar o risco %r", formula.nome, risco)
        return f"risco {risco}: a fórmula em uso não soube explicar este número."


def _nota_ponderada(
    respostas: Mapping[str, Resposta], rubrica: Rubrica
) -> tuple[dict[str, float], tuple[str, ...], float | None]:
    """A nota, as dimensões que a formaram e as que não foram observadas.

    Acesso via ``pesos_observados`` de propósito: o peso de uma dimensão que não
    veio é redistribuído entre as que vieram, em vez de entrar como zero. Nota
    calculada sobre três dimensões é honesta; nota calculada sobre quatro com
    uma delas inventada, não.
    """
    pesos = rubrica.pesos_observados(respostas)
    notas = {nome: respostas[nome].normalizado for nome in pesos}
    nao_observadas = tuple(nome for nome in rubrica.pesos if nome not in respostas)
    if not pesos:
        return notas, nao_observadas, None
    return notas, nao_observadas, round(100 * sum(pesos[n] * notas[n] for n in pesos), 1)


def _gates(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    rubrica: Rubrica,
    config: Config,
) -> tuple[list[str], list[str]]:
    """Os bloqueios e as dúvidas. Nenhum dos dois entra na nota.

    Um gate que não foi respondido vira dúvida e **nunca** passa em silêncio:
    "a pergunta não foi feita" e "a resposta foi não" são coisas diferentes, e
    tratá-las igual é exatamente como um gate de segurança deixa de valer.
    """
    graves: list[str] = []
    duvidas: list[str] = []
    julgou = bool(respostas)

    for nome in rubrica.risco_grave:
        resposta = respostas.get(nome)
        if resposta is None:
            if julgou:
                duvidas.append(f"{nome}: gate não respondido, não vale como 'não'")
            continue
        if resposta.normalizado >= config.bloqueio:
            graves.append(f"{nome} {resposta.normalizado:.2f}")

    barrados = {g.split()[0] for g in graves}
    for nome, resposta in respostas.items():
        if nome not in rubrica.dimensoes_de_risco or nome in barrados:
            continue
        if resposta.normalizado >= config.suspeita:
            duvidas.append(f"{nome} {resposta.normalizado:.2f}")

    # Confiança baixa não invalida a nota; transforma o veredito em dúvida. A
    # distribuição espalhada é informação real sobre um caso ambíguo, e apagá-la
    # transferiria a quem lê uma certeza que ninguém teve.
    duvidas.extend(
        f"{nome} disperso (confiança {resposta.confianca:.2f})"
        for nome, resposta in respostas.items()
        if nome in rubrica.pesos and resposta.dispersa
    )

    if medida.complexidade > config.limite_ccn:
        duvidas.append(
            f"complexidade {medida.complexidade}: acima de {config.limite_ccn}, "
            "cobrir todos os caminhos deixa de ser viável"
        )
    if medida.tamanho > config.max_linhas and julgou:
        # A nota vale sobre um pedaço. Isso não barra — barrar aqui puniria
        # tamanho duas vezes —, mas precisa aparecer, senão a nota se apresenta
        # como se o modelo tivesse visto a função toda.
        duvidas.append(
            f"julgado sobre as {config.max_linhas} primeiras de {medida.tamanho} linhas"
        )
    return graves, duvidas


def _conselho(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    notas: Mapping[str, float],
) -> str:
    """O que fazer com esta função, em uma frase.

    Aqui mora a decisão que a métrica sozinha não consegue tomar: o número não
    distingue complexidade que veio do domínio de complexidade que veio da
    escrita, e a ação muda por completo. Testar antes de refatorar congela
    justamente o desenho que se quer trocar; simplificar complexidade essencial
    apaga casos reais do domínio — e o número melhora, porque a métrica não sabe
    a diferença.
    """
    if not respostas:
        return (
            "sem julgamento: com só o eixo contável, 'falta teste' e 'precisa refatorar' "
            "produzem o mesmo número, e a escolha entre os dois volta a ser de quem lê o código"
        )

    cognitiva = notas.get("complexidade_cognitiva")
    essencial_resposta = respostas.get("complexidade_essencial")
    if cognitiva is None or essencial_resposta is None:
        return (
            "conselho indisponível: sem complexidade_cognitiva e complexidade_essencial não dá "
            "para separar forma acidental de complexidade que o domínio impõe"
        )

    essencial = essencial_resposta.normalizado >= 0.5
    forma_fraca = cognitiva < 0.5 and not essencial

    if "teste_verifica" in notas:
        teste_fraco = notas["teste_verifica"] < 0.5
    else:
        # Sem julgamento sobre teste, quem responde é o fato contável — que é a
        # regra 1 do módulo aplicada onde ela sempre deveria ter valido.
        cobertura = medida.cobertura_linha
        teste_fraco = cobertura != SEM_DADOS and cobertura < 0.5

    if forma_fraca and teste_fraco:
        return "refatorar e só depois testar: testar agora congela o desenho que se quer trocar"
    if forma_fraca:
        return "simplificar a forma: os caminhos vieram da escrita, não do domínio"
    if teste_fraco:
        return "escrever teste, não refatorar: a complexidade vem do domínio"
    return "nada obrigatório"


def _prioridade(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    limiar: float,
    graves: Sequence[str],
    duvidas: Sequence[str],
) -> str:
    """Com que pressa. O risco diz o tamanho do problema; a consequência, se vale a pressa.

    Consequência de falha é exatamente o que a fórmula CRAP ignora: um
    formatador de log e um parser de boot com o mesmo risco não merecem a mesma
    fila. Ela entra **normalizada** e na direção declarada pela régua
    (``maior_mais_em_jogo``): comparar o score cru com 0.5 mediria metade de uma
    escala que vai até 2, e a condição quase nunca dispararia.
    """
    consequencia = respostas.get("consequencia_de_falha")
    muito_em_jogo = consequencia is not None and consequencia.normalizado >= 0.5
    if graves or (medida.risco >= limiar and muito_em_jogo):
        return "alta"
    if medida.risco >= limiar or duvidas:
        return "media"
    return "baixa"


def decidir(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    *,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
    modelo: str = "",
    usage: Mapping[str, int] | None = None,
) -> FuncaoAvaliada:
    """Cruza os dois eixos e produz o veredito de uma função."""
    notas, nao_observadas, nota = _nota_ponderada(respostas, rubrica)
    graves, duvidas = _gates(medida, respostas, rubrica, config)

    if nao_observadas and respostas:
        duvidas.append(
            "teste não localizado: "
            + ", ".join(nao_observadas)
            + " não foi perguntado porque não havia trecho de teste para mostrar"
        )

    # Tamanho é contável, então é gate e não opinião: acima do limite a nota vai
    # a zero independentemente do que o modelo tenha achado do trecho que viu.
    if medida.tamanho > config.limite_tamanho:
        nota = 0.0
        graves.insert(0, f"{medida.tamanho} linhas (limite {config.limite_tamanho})")

    if graves:
        veredito = "bloquear"
    elif not respostas:
        veredito = "sem_julgamento"
    elif duvidas or (nota is not None and nota < config.nota_minima):
        veredito = "revisar"
    else:
        veredito = "aprovar"

    return FuncaoAvaliada(
        medida=medida,
        respostas=dict(respostas),
        notas=notas,
        nao_observadas=nao_observadas,
        nota=nota,
        graves=tuple(graves),
        duvidas=tuple(duvidas),
        conselho=_conselho(medida, respostas, notas),
        prioridade=_prioridade(medida, respostas, limiar, graves, duvidas),
        veredito=veredito,
        modelo=modelo,
        usage=dict(usage or {}),
    )


# --------------------------------------------------------------------------- #
# Orquestrar
# --------------------------------------------------------------------------- #


def _julgar_uma(
    medida: FuncaoMedida,
    *,
    julgador: Julgador,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
) -> FuncaoAvaliada:
    estado = montar_estado(
        codigo=medida.codigo,
        linguagem=medida.linguagem,
        testes=list(medida.testes),
        cobertura_branch=(
            None if medida.cobertura_branch == SEM_DADOS else medida.cobertura_branch
        ),
        max_linhas=config.max_linhas,
    )
    resultado = julgador.julgar(estado, rubrica)
    return decidir(
        medida,
        resultado.get("respostas", {}),
        rubrica=rubrica,
        config=config,
        limiar=limiar,
        modelo=str(resultado.get("modelo", "")),
        usage=resultado.get("usage", {}),
    )


def _julgar_lote(
    medidas: Sequence[FuncaoMedida],
    *,
    julgador: Julgador,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
) -> tuple[list[FuncaoAvaliada], list[dict[str, str]]]:
    """Julga em paralelo, e uma falha não derruba a batelada.

    ``as_completed`` no lugar de ``map``: com ``map``, uma exceção descartava
    tudo que já tinha sido pago. A função que falhou vira linha de relatório; as
    outras continuam valendo.
    """
    if not medidas:
        return [], []

    avaliadas: list[FuncaoAvaliada] = []
    falhas: list[dict[str, str]] = []
    trabalhadores = max(1, min(config.concorrencia, len(medidas)))
    with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
        tarefas = {
            pool.submit(
                _julgar_uma,
                medida,
                julgador=julgador,
                rubrica=rubrica,
                config=config,
                limiar=limiar,
            ): medida
            for medida in medidas
        }
        for tarefa in as_completed(tarefas):
            medida = tarefas[tarefa]
            try:
                avaliadas.append(tarefa.result())
            except Exception as erro:  # noqa: BLE001 - uma função não derruba o resto
                _log.exception("falha ao julgar %s", medida.identificador)
                falhas.append(
                    {
                        "funcao": f"{medida.identificador} ({medida.nome})",
                        "erro": f"{type(erro).__name__}: {erro}",
                    }
                )
    return avaliadas, falhas


def _estimar_custo(medidas: Sequence[FuncaoMedida], config: Config) -> dict[str, Any]:
    """Ordem de grandeza do que a rodada custa, antes de ela acontecer.

    Deliberadamente grosseiro e dito como tal: não há tabela de preço embutida,
    porque preço de API muda sem avisar e um número desatualizado no código é
    pior do que nenhum — parece autoridade.

    Nunca levanta: esta estimativa acompanha um relatório que já custou tempo,
    e nenhum número aproximado vale derrubar o resultado de verdade. Campo
    ausente ou vazio entra como zero, que é o que uma estimativa grosseira deve
    fazer com o que não consegue medir.
    """
    caracteres = sum(
        len(m.codigo or "") + sum(len(t or "") for t in (m.testes or ())) for m in medidas
    )
    estimativa: dict[str, Any] = {
        "chamadas": len(medidas),
        "tokens_de_entrada_estimados": caracteres // CARACTERES_POR_TOKEN,
        "base_da_estimativa": (
            f"{CARACTERES_POR_TOKEN} caracteres por token; é ordem de grandeza, "
            "não a tokenização real do modelo"
        ),
    }
    if config.custo_por_julgamento is not None:
        estimativa["custo"] = round(len(medidas) * config.custo_por_julgamento, 4)
        estimativa["moeda"] = config.moeda
    return estimativa


def _estado_do_eixo(julgador: Julgador, com_julgamento: bool, julgadas: int) -> dict[str, Any]:
    """Diz se o eixo semântico respondeu, e o que muda quando não respondeu.

    Sai no topo do relatório porque, sem ele, um relatório inteiro de
    ``sem_julgamento`` é indistinguível de um relatório de código ruim. Os dois
    motivos de desligamento são separados de propósito — escolha de quem chamou
    (``com_julgamento=false``) e ausência de chave — porque pedem ações opostas:
    um é intencional, o outro é configuração faltando.

    ``consequencia`` acompanha o motivo em vez de ficar implícita: quem lê
    precisa saber que "falta teste" e "precisa refatorar" produzem o mesmo
    número no eixo contável e deixam de ser distinguíveis.

    Nunca levanta: ``getattr`` com padrão cobre o julgador que não declara
    ``ativo`` ou ``motivo``, porque este bloco explica um relatório que já foi
    calculado — falhar aqui trocaria o relatório inteiro pela ausência da
    legenda dele.
    """
    if not com_julgamento:
        return {
            "ligado": False,
            "motivo": "com_julgamento=false nesta chamada",
            "consequencia": "todas as funções saem como 'sem_julgamento'",
        }
    if not getattr(julgador, "ativo", False):
        return {
            "ligado": False,
            "motivo": getattr(julgador, "motivo", "eixo semântico indisponível"),
            "consequencia": (
                "o eixo contável vale sozinho; 'falta teste' e 'precisa refatorar' produzem "
                "o mesmo número e não são distinguíveis"
            ),
        }
    return {"ligado": True, "funcoes_julgadas": max(0, julgadas)}


def avaliar(
    caminhos: Sequence[str],
    cobertura: str | None = None,
    *,
    config: Config,
    julgador: Julgador,
    rubrica: Rubrica | None = None,
    limiar: float | None = None,
    pasta_testes: str | None = None,
    com_julgamento: bool = True,
) -> dict[str, Any]:
    """Mede tudo, julga o recorte que vale a pena, cruza os eixos e decide.

    O recorte é o que torna a ferramenta usável num projeto de verdade: medir
    mil funções é grátis, julgar mil funções não. Inverter a ordem (julgar tudo
    e depois medir) daria o mesmo relatório por um preço proporcional ao tamanho
    do repositório.

    O preço desse recorte está dito no relatório e não escondido: filtrar por
    risco cega o sistema justamente no caso mais interessante — complexidade
    baixa com código ilegível, que o número não pega. Quem quiser julgar tudo
    passa ``limiar=0``.
    """
    regua = rubrica if rubrica is not None else carregar_rubrica()
    ligado = com_julgamento and getattr(julgador, "ativo", False)

    medicao = medir(
        caminhos,
        cobertura,
        config=config,
        limiar=limiar,
        pasta_testes=pasta_testes,
        com_codigo=ligado,
    )

    acima = list(medicao.acima_do_limiar)
    a_julgar = acima[: config.max_julgamentos] if ligado else []
    cortadas = len(acima) - len(a_julgar) if ligado else 0

    avaliadas, falhas = _julgar_lote(
        a_julgar, julgador=julgador, rubrica=regua, config=config, limiar=medicao.limiar
    )

    julgadas = {f.medida.identificador for f in avaliadas}
    for medida in medicao.funcoes:
        if medida.identificador not in julgadas:
            avaliadas.append(
                decidir(
                    medida, {}, rubrica=regua, config=config, limiar=medicao.limiar
                )
            )
    avaliadas.sort(key=lambda f: (-f.medida.risco, f.medida.arquivo, f.medida.linha_inicio))

    avisos = list(medicao.avisos)
    if cortadas:
        avisos.append(
            f"{cortadas} função(ões) acima do limiar não foram julgadas porque o teto de "
            f"{config.max_julgamentos} julgamentos por rodada foi atingido; as de maior risco "
            "tiveram prioridade. Suba JEV_CRAP_MAX_JULGAMENTOS ou rode sobre menos caminhos."
        )
    abaixo = len(medicao.funcoes) - len(acima)
    if ligado and abaixo:
        avisos.append(
            f"{abaixo} função(ões) abaixo do limiar não foram julgadas. O filtro economiza "
            "chamada mas cega o caso mais interessante — complexidade baixa com código "
            "ilegível, que o número não pega. Passe limiar=0 para julgar tudo."
        )
    if falhas:
        avisos.append(
            f"{len(falhas)} função(ões) não obtiveram julgamento por falha na chamada; "
            "elas aparecem como 'sem_julgamento' e não como aprovadas"
        )

    mostradas = avaliadas[: max(0, config.max_no_relatorio)]
    return {
        "resumo": _resumo(medicao, avaliadas, mostradas, a_julgar),
        "eixo_semantico": _estado_do_eixo(julgador, com_julgamento, len(a_julgar)),
        "custo": _estimar_custo(a_julgar, config),
        "funcoes": [f.para_avaliacao() for f in mostradas],
        "falhas": falhas,
        "avisos": avisos,
        "regua": config.para_regua(),
        "como_ler": COMO_LER,
    }


def _resumo(
    medicao: Medicao,
    avaliadas: Sequence[FuncaoAvaliada],
    mostradas: Sequence[FuncaoAvaliada],
    a_julgar: Sequence[FuncaoMedida],
) -> dict[str, Any]:
    """O cabeçalho do relatório: quantas, quais vereditos, quanto custou.

    A contagem começa com **todos** os vereditos em zero, e não só com os que
    apareceram. Um dicionário sem a chave ``bloquear`` obriga quem lê a
    distinguir "nenhuma função bloqueada" de "este relatório não reporta
    bloqueio", e é justamente aí que um CI erra para o lado errado.

    ``tokens_usados`` vira ``None`` quando nada foi gasto, em vez de zerado:
    zero diria "a chamada aconteceu e custou nada", e o fato é que não houve
    chamada — a diferença é o que separa eixo desligado de eixo caro.

    As contagens vêm de fontes diferentes de propósito: ``funcoes_medidas`` e
    ``acima_do_limiar`` da medição, ``julgadas`` do recorte que foi realmente
    enviado. Derivar uma da outra esconderia o teto de julgamentos, que é onde
    o relatório e a medição deixam de bater.
    """
    contagem: dict[str, int] = {v: 0 for v in VEREDITOS}
    for f in avaliadas:
        if f.veredito not in contagem:
            # Veredito fora do vocabulário só pode vir de uma versão que
            # introduziu um valor novo. Contá-lo à parte é melhor que somá-lo a
            # um veredito conhecido: a soma bateria e a diferença sumiria.
            _log.warning("veredito desconhecido %r no resumo", f.veredito)
        contagem[f.veredito] = contagem.get(f.veredito, 0) + 1
    entrada = _somar_tokens(avaliadas, "input_tokens")
    saida = _somar_tokens(avaliadas, "output_tokens")
    try:
        acima = len(medicao.acima_do_limiar)
    except ValueError:
        # `acima_do_limiar` recusa limiar não finito. Aqui isso já não decide
        # nada — a varredura terminou —, e derrubar o resumo esconderia o
        # relatório inteiro por causa de um número do cabeçalho.
        _log.warning(
            "limiar %r não é finito; a contagem de 'acima' sai indisponível", medicao.limiar
        )
        acima = SEM_CONTAGEM
    return {
        "funcoes_medidas": len(medicao.funcoes),
        "acima_do_limiar": acima,
        "julgadas": len(a_julgar),
        "limiar": medicao.limiar,
        "formula": medicao.formula.nome,
        "por_veredito": contagem,
        "resultado": _pior_veredito(avaliadas),
        "detalhadas_no_relatorio": len(mostradas),
        "omitidas_do_relatorio": max(0, len(avaliadas) - len(mostradas)),
        "tokens_usados": {"entrada": entrada, "saida": saida} if entrada or saida else None,
    }


def _somar_tokens(avaliadas: Iterable[FuncaoAvaliada], campo: str) -> int:
    """Soma um campo de ``usage``, ignorando o que não é número.

    ``usage`` vem da resposta da API e é repassado como veio. Um campo ausente,
    nulo ou em texto derrubaria o resumo inteiro numa soma — e o resumo é o
    cabeçalho de um relatório que já foi pago. Contagem de custo errada por
    falta é melhor que relatório nenhum, e o valor é declarado como estimativa.
    """
    total = 0
    for f in avaliadas:
        bruto = (f.usage or {}).get(campo, 0)
        if isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
            continue
        if math.isfinite(bruto) and bruto > 0:
            total += int(bruto)
    return total


def _pior_veredito(avaliadas: Iterable[FuncaoAvaliada]) -> str:
    """O veredito da rodada é o pior de suas funções.

    ``sem_julgamento`` conta como pior que ``aprovar``: o que não foi julgado
    não foi aprovado, e quem lê o resultado agregado (um CI, por exemplo)
    precisa que o silêncio não se apresente como sinal verde.

    Lista vazia devolve ``aprovar``, e é a resposta certa: nenhuma função acima
    do limiar é resultado completo ("nada a fazer agora"), não ausência de
    resposta. Devolver ``sem_julgamento`` aqui faria todo projeto saudável sair
    com código 1 no CI.

    Veredito desconhecido vira ``revisar`` em vez de levantar. Ele só pode vir
    de uma régua ou versão que introduziu um veredito novo, e **este valor é o
    exit code que o CI lê**: derrubar a rodada esconderia o relatório inteiro,
    e assumir ``aprovar`` transformaria o desconhecido em sinal verde.
    """
    pior = "aprovar"
    for f in avaliadas:
        if f.veredito not in ORDEM_DOS_VEREDITOS:
            _log.warning("veredito desconhecido %r; tratado como 'revisar'", f.veredito)
            return "revisar"
        if ORDEM_DOS_VEREDITOS.index(f.veredito) > ORDEM_DOS_VEREDITOS.index(pior):
            pior = f.veredito
    return pior


COMO_LER: dict[str, str] = {
    "risco": (
        "Eixo contável: complexidade ciclomática cruzada com cobertura. Ordena o que olhar "
        "primeiro. Não sabe se a complexidade é do domínio nem se os testes verificam algo."
    ),
    "nota": (
        "Eixo semântico: média ponderada das dimensões de qualidade, 0 a 100. A nota ORDENA, "
        "não mede — a calibração numérica de um score é fraca, então leia a 'faixa' e trate "
        "diferenças de poucos pontos como empate."
    ),
    "graves": (
        "Gates que barram. Ficam fora da nota porque risco não se compensa com legibilidade "
        "boa: uma função com injeção e nota 95 continua sendo uma função com injeção."
    ),
    "duvidas": (
        "Vão para olho humano, nunca barram. Incluem riscos graduais, confiança baixa, "
        "complexidade acima do viável e o que não foi possível perguntar."
    ),
    "conselho": (
        "A decisão que a métrica não consegue tomar: refatorar apaga casos reais quando a "
        "complexidade é essencial; testar antes de refatorar congela o desenho a trocar."
    ),
    "confianca": (
        "Mede o quanto o modelo se decidiu, não se acertou. Abaixo de ~0.45 a nota vira "
        "'vale um olhar humano' em vez de veredito. Respostas do tipo noul não têm confiança: "
        "a própria probabilidade já descreve a distribuição inteira."
    ),
}


def julgar_trecho(
    codigo: str,
    *,
    config: Config,
    julgador: Julgador,
    rubrica: Rubrica | None = None,
    arquivo: str = "trecho.py",
    funcao: str = "",
    testes: Sequence[str] = (),
    cobertura_branch: float | None = None,
    limiar: float | None = None,
) -> dict[str, Any]:
    """Avalia um trecho que já está em mãos, sem tocar no disco.

    É o caminho para o código que um agente acabou de escrever e ainda não
    salvou, para um pedaço de diff, e para quando o número e a impressão de quem
    leu o código discordam — essa discordância costuma ser informação, não erro.

    A complexidade é medida do próprio texto, então o eixo contável continua
    valendo: ``arquivo`` existe para dar a extensão certa ao analisador (é ela
    que escolhe a linguagem) e para identificar o trecho no relatório; ele não é
    lido do disco.

    A cobertura entra só se quem chamou souber dizer: não há de onde inferi-la
    para um texto solto, e inventar 0% transformaria todo trecho colado em risco
    alto por construção.
    """
    if not codigo.strip():
        raise SituacaoConhecida(
            "trecho_vazio",
            "não veio código para julgar",
            "Passe o texto da função em `codigo`. O caminho do arquivo não serve: "
            "esta tool julga o texto que recebe, não lê disco — para isso use avaliar_arquivos.",
        )

    regua = rubrica if rubrica is not None else carregar_rubrica()
    formula = config.obter_formula()
    limiar_efetivo = config.limiar_efetivo(formula, limiar)

    medidas = mod_complexidade.medir_fonte(arquivo, codigo)
    escolhida = _escolher_funcao(medidas, funcao)

    linhas = codigo.splitlines()
    if escolhida is None:
        # Nenhuma função reconhecida: pode ser um trecho solto, um arquivo de
        # script ou uma linguagem que o lizard não lê. O julgamento semântico
        # continua valendo sobre o texto; o que não existe é complexidade por
        # função, e dizer complexidade 1 aqui seria inventar um fato.
        avisos = [
            f"nenhuma função foi reconhecida em {arquivo!r}: o eixo contável vale pouco "
            "(complexidade tratada como 1). Confira se a extensão do nome em `arquivo` "
            "corresponde à linguagem do trecho."
        ]
        complexidade, nloc, inicio, fim = 1, len(linhas), 1, max(1, len(linhas))
        linguagem = Path(arquivo).suffix.lstrip(".") or "desconhecida"
        nome = funcao or "(trecho)"
    else:
        avisos = []
        complexidade = max(1, escolhida.complexidade)
        nloc = max(0, escolhida.linhas_logicas)
        inicio, fim = escolhida.linha_inicio, escolhida.linha_fim
        linguagem = escolhida.linguagem
        nome = escolhida.nome
        if len(medidas) > 1 and not funcao:
            avisos.append(
                f"o trecho tem {len(medidas)} funções; foi julgada a de maior complexidade "
                f"({nome}). Informe `funcao` para escolher outra."
            )

    insumos = Insumos(
        complexidade=complexidade,
        cobertura_linha=float(SEM_DADOS) if cobertura_branch is None else cobertura_branch,
        cobertura_branch=cobertura_branch,
        linhas_logicas=nloc,
    )
    medida = FuncaoMedida(
        arquivo=arquivo,
        nome=nome,
        linha_inicio=inicio,
        linha_fim=fim,
        complexidade=complexidade,
        linhas_logicas=nloc,
        linguagem=linguagem,
        cobertura_linha=insumos.cobertura_linha,
        cobertura_branch=float(SEM_DADOS) if cobertura_branch is None else cobertura_branch,
        risco=formula.calcular(insumos),
        codigo=codigo,
        testes=tuple(t for t in testes if t and t.strip()),
    )

    if not getattr(julgador, "ativo", False):
        raise SituacaoConhecida(
            "eixo_semantico_desligado",
            getattr(julgador, "motivo", "o julgamento do Jev não está disponível"),
            "Defina TYPESAFE_API_KEY no ambiente do servidor MCP. Sem ela, esta tool não tem "
            "o que fazer — use medir_risco, que funciona sem chave e sem rede.",
        )

    avaliada = _julgar_uma(
        medida, julgador=julgador, rubrica=regua, config=config, limiar=limiar_efetivo
    )
    if not avaliada.respostas:
        raise SituacaoConhecida(
            "julgamento_indisponivel",
            "a chamada ao Jev não devolveu nenhuma resposta utilizável",
            "Costuma ser rede, chave inválida ou a API fora do ar. O log do servidor MCP tem o "
            "motivo. O eixo contável continua disponível em medir_risco.",
        )

    if not medida.testes:
        avisos.append(
            "nenhum trecho de teste foi enviado: a pergunta sobre teste não foi feita e o peso "
            "dela foi redistribuído. Isso não é 'os testes são ruins' — é 'não havia teste para "
            "olhar'. Mande os testes em `testes` se existirem."
        )
    if cobertura_branch is None:
        avisos.append(
            "sem cobertura informada: o risco foi calculado como se nada estivesse coberto. "
            "Para um trecho solto isso é o padrão seguro, mas o número não é comparável com o "
            "de avaliar_arquivos, que lê cobertura de verdade."
        )

    return {
        "funcao": avaliada.para_avaliacao(),
        "custo": _estimar_custo([medida], config),
        "avisos": avisos,
        "regua": config.para_regua(),
        "como_ler": COMO_LER,
    }


def _escolher_funcao(medidas: Sequence[Any], pedida: str) -> Any | None:
    """A função a julgar dentro do trecho: a pedida pelo nome, ou a mais complexa.

    A mais complexa e não a primeira: quem cola um trecho com uma função
    auxiliar de duas linhas no topo quer a resposta sobre a outra, e julgar a
    primeira devolveria uma nota excelente sobre o pedaço que ninguém perguntou.
    """
    if not medidas:
        return None
    if pedida:
        exatas = [m for m in medidas if m.nome == pedida]
        if exatas:
            return exatas[0]
        parciais = [m for m in medidas if pedida in m.nome]
        if parciais:
            return parciais[0]
    return max(medidas, key=lambda m: (m.complexidade, m.linhas_logicas))
