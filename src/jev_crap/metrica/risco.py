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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "CrapClassico",
    "Formula",
    "Insumos",
    "SEM_DADOS",
    "formulas_disponiveis",
    "obter_formula",
    "registrar_formula",
]

#: Ausência de dado. Repetido aqui (também existe em `cobertura`) porque este
#: módulo precisa reconhecê-lo sem importar o de cobertura — a dependência na
#: direção contrária é que faria sentido, e nenhuma das duas precisa da outra.
SEM_DADOS: float = -1.0


def _validar_fracao(valor: float, campo: str) -> None:
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
    """

    nome: str

    def calcular(self, i: Insumos) -> float:
        """O número de risco. Maior é pior."""
        ...

    def interpretar(self, valor: float) -> str:
        """O que esse número significa, em uma frase, para quem vai decidir o que fazer."""
        ...

    def limiar_padrao(self) -> float:
        """A partir de qual valor a função merece atenção."""
        ...


class CrapClassico:
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

    def limiar_padrao(self) -> float:
        """30, o valor da ferramenta original.

        É convenção, não medida: 30 é onde caem, por exemplo, complexidade 5 sem
        teste nenhum (30.0) e complexidade 30 com cobertura total (30.0). O
        módulo de aprendizado registra os episódios de uso justamente para que
        esse limiar passe a sair de evidência em vez de herança.
        """
        return 30.0


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
    """
    if nome in _REGISTRO:
        raise ValueError(f"já existe fórmula registrada como {nome!r}")
    _REGISTRO[nome] = fabrica


def formulas_disponiveis() -> tuple[str, ...]:
    """Nomes registrados, em ordem alfabética."""
    return tuple(sorted(_REGISTRO))


def obter_formula(nome: str = "crap") -> Formula:
    """Instancia a fórmula pelo nome. Sem argumento, a clássica do CRAP."""
    fabrica = _REGISTRO.get(nome)
    if fabrica is None:
        disponiveis = ", ".join(formulas_disponiveis())
        raise ValueError(f"fórmula de risco desconhecida: {nome!r}; disponíveis: {disponiveis}")
    return fabrica()
