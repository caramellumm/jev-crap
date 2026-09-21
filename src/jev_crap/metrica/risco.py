"""Cálculo do risco de uma função, com a fórmula trocável.

Este módulo separa duas coisas que costumam vir grudadas: os *insumos* medidos
(complexidade, cobertura, tamanho) e a *fórmula* que transforma esses insumos
em um número. A fórmula é um `Protocol` porque *qual* fórmula está certa ainda
é pergunta em aberto: a clássica do CRAP entra como padrão por ser a mais
conhecida, e uma fórmula melhor entra depois como mais uma entrada no registro,
sem obrigar nenhum chamador a mudar.

Sobre cobertura, vale registrar qual insumo é o mais correto: é
`cobertura_branch`, porque ela conta caminhos — a mesma unidade da complexidade
ciclomática. `cobertura_linha` existe aqui por dois motivos práticos: a fórmula
clássica foi definida sobre ela, e nem todo gerador de relatório emite branch
coverage. Uma fórmula futura tende a preferir branch e usar linha só como
último recurso; `Insumos.cobertura_preferida` já implementa essa escolha.

**O risco não é a nota.** São duas escalas com propósitos diferentes e o
projeto as mantém separadas de propósito: o risco ordena o que olhar primeiro
(é contável, determinístico e não custa nada), a nota de qualidade diz o quão
bom é o que se olhou (é julgamento, custa uma chamada de API). Somar as duas
produziria um número que não responde nenhuma das duas perguntas.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "COMPLEXIDADE_DE_REFERENCIA",
    "CrapClassico",
    "Formula",
    "Insumos",
    "SEM_DADOS",
    "formulas_disponiveis",
    "obter_formula",
    "registrar_formula",
]

#: A complexidade cujo risco, sem teste nenhum, define a linha de corte. Cinco
#: é o ponto que dá exatamente 30 na fórmula clássica — o limiar da ferramenta
#: original. Ele mora aqui, e não dentro de `limiar_padrao`, porque é a única
#: escolha arbitrária da linha de corte: tudo mais é consequência da fórmula.
COMPLEXIDADE_DE_REFERENCIA = 5

#: Ausência de dado. Repetido aqui (também existe em `cobertura`) porque este
#: módulo precisa reconhecê-lo sem importar o de cobertura — a dependência na
#: direção contrária é que faria sentido, e nenhuma das duas precisa da outra.
SEM_DADOS: float = -1.0


def _validar_fracao(valor: float, campo: str) -> None:
    """Recusa o que não é fração de 0 a 1, dizendo qual campo e qual valor.

    A comparação encadeada rejeita ``nan`` de graça — toda comparação com
    ``nan`` é falsa —, mas a mensagem sairia dizendo "é uma fração de 0 a 1",
    que manda procurar o valor fora da faixa. ``nan`` não está fora da faixa:
    ele não está em faixa nenhuma, e vem de uma divisão 0/0 na leitura do
    relatório. Nomear o caso aponta o defeito certo.

    O tipo é conferido antes da faixa porque ``"0.5" <= 1.0`` levanta
    ``TypeError`` cru, sem dizer qual campo veio como texto — e cobertura
    chegando como texto é exatamente o que acontece quando alguém monta os
    insumos a partir de um JSON sem converter.

    O alcance da recusa é uma função, não a execução: ``jev_crap.avaliacao``
    captura este ``ValueError`` por função medida, registra a função como sem
    dados de cobertura e segue. Um arquivo com relatório defeituoso custa o
    dado daquele arquivo, nunca a varredura inteira.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError(f"{campo} precisa ser número; recebi {valor!r}")
    if valor != valor:
        raise ValueError(
            f"{campo} veio nan — provável divisão 0/0 na leitura do relatório de cobertura"
        )
    if not 0.0 <= valor <= 1.0:
        raise ValueError(f"{campo} é uma fração de 0 a 1; recebi {valor!r}")


@dataclass(frozen=True)
class Insumos:
    """Tudo que se mediu sobre uma função, antes de virar um número de risco.

    É congelado de propósito: o mesmo conjunto de insumos alimenta várias
    fórmulas na mesma execução (para comparar uma com a outra), e nenhuma delas
    pode editar o que as outras vão ler.

    A validação é estrita — cobertura fora de 0..1 vira erro em vez de ser
    silenciosamente cortada — porque cobertura acima de 1 quase sempre é bug de
    leitura do relatório (somar execuções em vez de linhas distintas, por
    exemplo), e cortar em silêncio esconderia exatamente o defeito que interessa
    descobrir.

    Note que **nenhuma nota do Jev entra aqui**. Na primeira geração deste
    projeto elas entravam, para que uma fórmula futura pudesse usá-las; a
    consequência prática foi um insumo que só podia ser preenchido depois de
    pagar a chamada de API, dentro de uma estrutura cujo propósito é ser
    calculável de graça. O julgamento é cruzado com o risco na camada de
    avaliação, onde as duas escalas continuam distinguíveis.
    """

    complexidade: int
    cobertura_linha: float
    """Fração de 0 a 1 das linhas executadas pelos testes, ou :data:`SEM_DADOS`."""

    cobertura_branch: float | None
    """Fração de 0 a 1 dos ramos executados; `None` ou :data:`SEM_DADOS` quando
    o gerador não emitiu."""

    linhas_logicas: int

    def __post_init__(self) -> None:
        """Confere os quatro campos assim que o objeto nasce.

        Validar aqui, e não em quem calcula, é o que faz um ``Insumos`` que
        existe ser um ``Insumos` válido: as fórmulas registradas de fora não
        precisam repetir a conferência, e nenhuma delas pode esquecê-la.

        ``complexidade`` começa em 1 porque uma função sem desvio nenhum já tem
        um caminho. Zero indicaria que o analisador não leu a função — e o CRAP
        de zero dá zero, ou seja, a função não medida sairia como a mais segura
        do relatório.

        Recusar aqui custa uma função, não a execução: quem mede captura este
        ``ValueError`` por função, marca aquela como sem dados e continua. Ver
        ``jev_crap.avaliacao.medir``.
        """
        if not isinstance(self.complexidade, int) or isinstance(self.complexidade, bool):
            raise ValueError(
                f"complexidade precisa ser inteiro; recebi {self.complexidade!r}"
            )
        if self.complexidade < 1:
            raise ValueError(
                f"complexidade ciclomática começa em 1 (um caminho); recebi {self.complexidade!r}"
            )
        if self.linhas_logicas < 0:
            raise ValueError(
                f"linhas_logicas não pode ser negativo; recebi {self.linhas_logicas!r}"
            )
        if self.cobertura_linha != SEM_DADOS:
            _validar_fracao(self.cobertura_linha, "cobertura_linha")
        if self.cobertura_branch is not None and self.cobertura_branch != SEM_DADOS:
            _validar_fracao(self.cobertura_branch, "cobertura_branch")

    @property
    def cobertura_preferida(self) -> float:
        """A cobertura mais adequada disponível: branch quando existe, linha quando não.

        Branch é preferida porque está na mesma unidade da complexidade
        ciclomática (caminhos). Uma função com `if` sem `else` chega fácil a
        100% de linha com 50% de branch — e é justamente o ramo não exercitado
        que carrega o risco.
        """
        if self.cobertura_branch is not None and self.cobertura_branch != SEM_DADOS:
            return self.cobertura_branch
        return self.cobertura_linha


@runtime_checkable
class Formula(Protocol):
    """Contrato de qualquer fórmula de risco.

    Quatro obrigações, e a terceira é a que costuma faltar em ferramenta de
    métrica: além de produzir o número e dizer a partir de onde ele incomoda, a
    fórmula precisa explicar o que o número significa. Número sem interpretação
    vira meta de planilha, e meta de planilha vira teste escrito para subir
    cobertura em vez de verificar comportamento.

    Propriedades que qualquer implementação respeita, e que a suíte cobre para
    todas as fórmulas registradas de uma vez:

    - mais cobertura nunca aumenta o risco;
    - mais complexidade nunca diminui o risco;
    - com cobertura total, o valor não depende de qual era a cobertura antes;
    - mesma entrada, mesma saída (sem estado guardado entre chamadas).

    Os corpos levantam ``NotImplementedError`` em vez de serem ``...``. A
    diferença aparece quando alguém herda do protocolo e implementa só parte
    dele: com ``...`` o método devolve ``None`` em silêncio, e ``None`` vira
    risco ``None`` que atravessa o relatório inteiro até estourar na formatação,
    longe da classe incompleta. Com o ``raise``, estoura no método que falta.
    """

    nome: str

    def calcular(self, i: Insumos) -> float:
        """O número de risco. Maior é pior. **O único método obrigatório.**

        Quem implementa precisa respeitar as quatro propriedades listadas no
        topo da classe — a suíte as verifica para toda fórmula registrada, sem
        que a fórmula nova precise escrever teste para elas.

        O corpo levanta em vez de ser ``...`` porque a alternativa é pior do
        que parece: com ``...`` a chamada devolve ``None``, e ``None`` vira o
        campo ``risco`` do relatório, atravessa o cruzamento com cobertura, a
        ordenação e o filtro por limiar, e só estoura na formatação — a dezenas
        de linhas e uma camada de distância da classe que esqueceu o método.

        Este corpo não roda em produção. Ele só é alcançado por uma classe que
        herda o protocolo e não implementa o método, o que estoura na primeira
        chamada — em desenvolvimento, antes de qualquer arquivo ser medido.
        ``CrapClassico`` implementa o método, ``obter_formula`` confere a
        instância contra o protocolo antes de devolvê-la, e a suíte cobre as
        duas coisas. Nada é lido, escrito ou enviado aqui.
        """
        raise NotImplementedError(
            f"{type(self).__name__} não implementa calcular(Insumos) -> float; "
            "é o único método obrigatório do protocolo, porque limiar_padrao e "
            "interpretar têm implementação padrão derivada dele"
        )

    def limiar_padrao(self) -> float:
        """A partir de qual valor a função merece atenção.

        O padrão é o risco de uma função de complexidade
        :data:`COMPLEXIDADE_DE_REFERENCIA` sem teste nenhum, calculado pela
        própria fórmula. Assim toda fórmula nova ganha uma linha de corte
        coerente com a curva dela, em vez de herdar um número que só fazia
        sentido para outra — que é como um limiar vira superstição.

        Construir o ``Insumos`` faz a validação dele rodar aqui, e não seis
        camadas adiante como um limiar estranho no meio de um relatório.

        O resultado é conferido antes de sair. ``calcular`` veio de uma fórmula
        registrada de fora e pode devolver ``nan``, infinito ou um não-número:
        qualquer um dos três faz toda comparação ``risco >= limiar`` dar falso,
        e a varredura termina dizendo "0 função acima do limiar" — uma saída
        cara, plausível e completamente errada, sem mensagem de erro nenhuma.
        Recusar aqui nomeia a fórmula culpada.

        O que sobe daqui é erro de configuração, não falha em produção: o único
        chamador é ``Config.limiar_efetivo``, que roda na montagem — antes de
        qualquer arquivo ser medido — e as duas entradas o traduzem em erro de
        uso (saída 3 na CLI, situação conhecida no MCP). Nenhum dado é escrito
        nem perdido no caminho, e a fórmula que falha é sempre uma registrada
        de fora: a que vem no pacote é coberta pela suíte.
        """
        sem_teste_nenhum = Insumos(
            complexidade=COMPLEXIDADE_DE_REFERENCIA,
            cobertura_linha=0.0,
            cobertura_branch=None,
            linhas_logicas=0,
        )
        limiar = self.calcular(sem_teste_nenhum)
        if isinstance(limiar, bool) or not isinstance(limiar, (int, float)):
            raise TypeError(
                f"{self.nome}.calcular devolveu {type(limiar).__name__}; o limiar precisa "
                "ser número, senão nenhuma função fica acima dele"
            )
        if not math.isfinite(limiar):
            raise ValueError(
                f"{self.nome}.calcular devolveu {limiar} no ponto de referência; "
                "um limiar não finito faz toda comparação de risco dar falso"
            )
        return float(limiar)

    def interpretar(self, valor: float) -> str:
        """O que esse número significa, em uma frase, para quem vai decidir o que fazer.

        O padrão compara com ``limiar_padrao()`` e nada mais — genérico de
        propósito. Uma fórmula que sobrescreve isto pode explicar *por que* o
        número subiu, que é o que separa uma métrica acionável de uma meta de
        planilha; mas nenhuma fórmula fica sem frase nenhuma por esquecimento.

        ``valor`` é conferido antes de virar texto: ``nan`` ou um não-número
        formatados direto produzem uma recomendação com cara de confiança em
        cima de um número que não existe.
        """
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return f"risco não numérico ({valor!r}): a medição desta função não pode ser lida."
        if valor != valor:
            return "risco nan: a medição desta função não pode ser lida — trate como não medida."
        limiar = self.limiar_padrao()
        posicao = "acima" if valor >= limiar else "abaixo"
        return (
            f"{self.nome} {valor:.1f}: {posicao} do limiar {limiar:.0f}. "
            "Esta fórmula não explica o número; veja a documentação dela."
        )


class CrapClassico(Formula):
    """CRAP original: ``cc² × (1 − cobertura_linha)³ + cc``, limiar 30.

    É o padrão por ser a fórmula que as pessoas conhecem e conseguem conferir
    na mão — não por ser a melhor. Os defeitos abaixo são conhecidos e são
    exatamente a razão de a fórmula ser trocável; quem for implementar a
    segunda precisa saber o que está corrigindo:

    1. **Mistura de unidades.** A complexidade ciclomática conta *caminhos*; a
       cobertura de linha conta *linhas*. Multiplicar uma pela outra trata
       grandezas diferentes como se fossem comparáveis. Uma função com `if` sem
       `else` atinge 100% de linha com metade dos caminhos nunca executada, e a
       fórmula a declara sem risco algum.

    2. **Expoentes escolhidos por intuição.** O 2 e o 3 não vêm de dado sobre
       densidade de defeito: vêm da vontade de que a curva subisse "bem rápido"
       quando falta cobertura. Não há derivação por trás deles, nem calibração
       contra bugs reais.

    3. **Sem limite superior.** O valor cresce com o quadrado da complexidade e
       não satura: complexidade 50 sem teste dá 2550, complexidade 100 dá
       10100. Como não existe teto, não existe escala — a diferença de 4x entre
       eles não corresponde a 4x de nada observável, e ordenar funções por esse
       número dá peso desproporcional às poucas mais extremas.

    O limiar não é redefinido aqui: o padrão do protocolo já dá 30 para esta
    fórmula, porque 30 é exatamente o risco de uma função de complexidade
    :data:`COMPLEXIDADE_DE_REFERENCIA` sem teste nenhum. Escrever ``30.0`` num
    método próprio desligaria a linha de corte da curva — trocar os expoentes
    mudaria todos os números do relatório e deixaria o limiar parado num valor
    que não significa mais nada.

    4. **Ignora consequência de falha.** Um parser de configuração usado no boot
       e um formatador de mensagem de log com a mesma complexidade e a mesma
       cobertura recebem o mesmo número. É justamente o que a pergunta
       `consequencia_de_falha` do Jev mede — e que esta fórmula, por definição,
       não lê. A camada de avaliação cruza as duas coisas na hora de priorizar.
    """

    nome: str = "crap"

    def calcular(self, i: Insumos) -> float:
        """Usa `cobertura_linha` porque é como o CRAP foi definido.

        Ler `cobertura_preferida` aqui daria um número melhor, mas com o mesmo
        nome de uma fórmula publicada — dois resultados diferentes chamados de
        "CRAP" confundem mais do que ajudam. A correção é assunto da próxima
        fórmula, que entra com nome próprio.

        Cobertura ausente é tratada como zero, isto é, como o pior caso. Um
        número otimista aqui esconderia exatamente o que se quer achar: função
        que o relatório não alcançou é função sobre a qual não se sabe nada, e
        "não sei" não pode sair mais barato do que "sei que está descoberta".
        """
        cc = float(i.complexidade)
        cobertura = 0.0 if i.cobertura_linha == SEM_DADOS else i.cobertura_linha
        descoberto = 1.0 - cobertura
        return round(cc**2 * descoberto**3 + cc, 1)

    def interpretar(self, valor: float) -> str:
        """Uma frase sobre o número, para quem vai decidir o que fazer com ele.

        ``valor`` é conferido antes de virar texto. Esta frase é a única parte
        do relatório que alguém lê sem saber o que é CRAP, e ``nan`` ou texto
        formatados direto produzem "CRAP nan: risco baixo" — uma recomendação
        com aparência de confiança em cima de um número que não existe.
        """
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return f"risco não numérico ({valor!r}): a medição desta função não pode ser lida."
        if valor != valor:
            return "risco nan: a medição desta função não pode ser lida — trate como não medida."
        limiar = self.limiar_padrao()
        if valor < 10.0:
            return (
                f"CRAP {valor:.1f}: risco baixo — ou a função é simples, ou os testes "
                "percorrem quase tudo que ela faz. Mexer nela é barato."
            )
        if valor < limiar:
            return (
                f"CRAP {valor:.1f}: risco moderado, ainda abaixo do limiar {limiar:.0f}. "
                "Costuma ser complexidade real com cobertura parcial; vale um teste a mais "
                "antes da próxima mudança."
            )
        return (
            f"CRAP {valor:.1f}: acima do limiar {limiar:.0f} — complexidade alta sem teste "
            "que a acompanhe. Cobrir os caminhos que faltam derruba o número bem mais rápido "
            "do que simplificar o código, porque a cobertura entra ao cubo."
        )


# Registro de fórmulas. Acrescentar uma fórmula é acrescentar uma entrada aqui
# (ou chamar `registrar_formula`) — nada mais no módulo precisa mudar, e a
# suíte de propriedades já passa a cobri-la automaticamente.
_REGISTRO: dict[str, Callable[[], Formula]] = {
    "crap": CrapClassico,
}


def registrar_formula(nome: str, fabrica: Callable[[], Formula]) -> None:
    """Registra uma fórmula sob um nome, para quem a implementa fora deste módulo.

    Recusa sobrescrever um nome já registrado: duas fórmulas diferentes
    respondendo pelo mesmo nome fariam relatórios antigos mudarem de significado
    sem aviso.

    Nome e fábrica são conferidos na entrada porque ``_REGISTRO`` é global e
    sobrevive à chamada: um nome vazio ou uma fábrica que não é chamável só
    falhariam na próxima execução que pedisse aquela fórmula, longe de quem
    registrou. Registro é o lugar barato de recusar.
    """
    if not isinstance(nome, str) or not nome.strip():
        raise ValueError(f"nome de fórmula precisa ser texto não vazio; recebi {nome!r}")
    if not callable(fabrica):
        raise ValueError(
            f"fábrica de {nome!r} precisa ser chamável e devolver uma Formula; "
            f"recebi {type(fabrica).__name__}"
        )
    if nome in _REGISTRO:
        raise ValueError(f"já existe fórmula registrada como {nome!r}")
    _REGISTRO[nome] = fabrica


def formulas_disponiveis() -> tuple[str, ...]:
    """Nomes registrados, em ordem alfabética.

    Esta lista entra em mensagem de erro — é o que ``obter_formula`` mostra a
    quem digitou um nome que não existe. Por isso ela não pode ser a segunda
    falha: ``_REGISTRO`` é global e mutável, e quem escreve direto nele
    (contornando ``registrar_formula``) pode deixar lá uma chave que não é
    texto, sobre a qual ``sorted`` levanta ``TypeError``. Chave assim é
    descartada aqui; a alternativa é a mensagem de erro morrer no lugar de
    explicar o erro original.
    """
    nomes = [chave for chave in _REGISTRO if isinstance(chave, str)]
    return tuple(sorted(nomes))


def obter_formula(nome: str = "crap") -> Formula:
    """Instancia a fórmula pelo nome. Sem argumento, a clássica do CRAP.

    A instância é conferida contra o protocolo antes de sair. A fábrica veio de
    fora via ``registrar_formula`` e pode devolver qualquer coisa; sem esta
    conferência, um objeto sem ``calcular`` atravessaria a medição inteira e
    estouraria como ``AttributeError`` dentro do laço de funções — com o nome
    da função medida na mensagem e nenhuma pista sobre a fórmula.

    Todo ``ValueError`` daqui é erro de configuração, não falha em produção:
    ele acontece na montagem, antes de qualquer medição, e as duas entradas
    (CLI e servidor MCP) o traduzem em "erro de uso" — saída 3 na CLI, situação
    conhecida no MCP. Nenhum dado é escrito nem perdido no caminho.
    """
    fabrica = _REGISTRO.get(nome) if isinstance(nome, str) else None
    if fabrica is None:
        disponiveis = ", ".join(formulas_disponiveis())
        raise ValueError(f"fórmula de risco desconhecida: {nome!r}; disponíveis: {disponiveis}")
    try:
        formula = fabrica()
    except Exception as erro:
        raise ValueError(f"a fábrica da fórmula {nome!r} falhou: {erro}") from erro
    if not isinstance(formula, Formula):
        raise ValueError(
            f"a fábrica de {nome!r} devolveu {type(formula).__name__}, "
            "que não cumpre o protocolo Formula (calcular, interpretar, limiar_padrao)"
        )
    return formula
