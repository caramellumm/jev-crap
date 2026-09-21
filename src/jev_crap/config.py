"""O que muda de projeto para projeto, sem precisar editar código.

Um servidor MCP não tem linha de comando: ele é iniciado por um cliente que
passa, no máximo, variáveis de ambiente. Então toda escolha ajustável vira
variável de ambiente, e este módulo é o único lugar que as lê — o resto do
código recebe um :class:`Config` pronto e não sabe de onde os valores vieram.
É isso que permite ao teste montar uma configuração na mão, sem mexer no
ambiente do processo.

Duas decisões guiam o módulo:

1. **Variável mal escrita não derruba o servidor.** ``JEV_CRAP_LIMIAR=muito``
   não é motivo para o servidor não subir: o valor é descartado, o padrão entra
   no lugar e a explicação vai para :attr:`Config.avisos`, que aparece na
   resposta das tools. Um servidor que recusa iniciar por causa de uma variável
   torta deixa a pessoa sem ferramenta e sem mensagem, porque a saída de erro de
   um processo stdio some dentro do cliente MCP.

2. **A chave do Jev não passa por aqui.** Ela é lida por
   ``julgamento.obter_julgador`` direto de ``TYPESAFE_API_KEY``. Guardar segredo
   num objeto que é serializado em relatório e em log é como vazamento costuma
   começar.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jev_crap.aprendizado.episodio import Repositorio
from jev_crap.metrica.risco import Formula, formulas_disponiveis, obter_formula

__all__ = ["Config", "VARIAVEIS"]

VARIAVEL_FORMULA = "JEV_CRAP_FORMULA"
VARIAVEL_LIMIAR = "JEV_CRAP_LIMIAR"
VARIAVEL_RAIZ = "JEV_CRAP_RAIZ"
VARIAVEL_EXCLUIR = "JEV_CRAP_EXCLUIR"
VARIAVEL_MAX_JULGAMENTOS = "JEV_CRAP_MAX_JULGAMENTOS"
VARIAVEL_MAX_NO_RELATORIO = "JEV_CRAP_MAX_NO_RELATORIO"
VARIAVEL_MAX_LINHAS = "JEV_CRAP_MAX_LINHAS"
VARIAVEL_CONCORRENCIA = "JEV_CRAP_CONCORRENCIA"
VARIAVEL_BLOQUEIO = "JEV_CRAP_BLOQUEIO"
VARIAVEL_SUSPEITA = "JEV_CRAP_SUSPEITA"
VARIAVEL_LIMITE_TAMANHO = "JEV_CRAP_LIMITE_TAMANHO"
VARIAVEL_LIMITE_CCN = "JEV_CRAP_LIMITE_CCN"
VARIAVEL_NOTA_MINIMA = "JEV_CRAP_NOTA_MINIMA"
VARIAVEL_CUSTO = "JEV_CRAP_CUSTO_POR_JULGAMENTO"
VARIAVEL_MOEDA = "JEV_CRAP_MOEDA"
VARIAVEL_EPISODIOS = "JEV_CRAP_EPISODIOS"

FORMULA_PADRAO = "crap"

#: Teto de funções enviadas ao Jev numa avaliação. Existe porque o eixo
#: semântico é o único que custa dinheiro e tempo: medir mil funções é grátis,
#: julgar mil funções não. Vinte é o que costuma caber numa revisão humana de
#: uma sentada — acima disso a lista deixa de ser acionável.
MAX_JULGAMENTOS_PADRAO = 20

#: Teto de funções detalhadas na resposta. Relatório com milhares de itens
#: estoura a janela de contexto de quem o lê (um modelo) e esconde justamente
#: as poucas linhas que importam.
MAX_NO_RELATORIO_PADRAO = 50

#: Teto de linhas de código enviadas por função. Função gigante não fica mais
#: bem julgada por ir inteira: a documentação do modelo é explícita sobre
#: acurácia cair com estado grande, e o trecho que decide o julgamento está no
#: começo. O que passar disso é truncado **com aviso no estado**.
MAX_LINHAS_PADRAO = 400

#: Requisições simultâneas ao Jev. O teto real é o rate limit da conta; oito é
#: o que mantém a batelada rápida sem transformar cada rodada numa rajada que
#: provoca o próprio 429.
CONCORRENCIA_PADRAO = 8

#: Probabilidade a partir da qual um gate grave barra o código. Alto de
#: propósito: bloqueio automático só se sustenta enquanto for raro e certo.
BLOQUEIO_PADRAO = 0.80

#: Probabilidade a partir da qual um risco vira dúvida e vai para olho humano.
SUSPEITA_PADRAO = 0.50

#: Acima deste tamanho a função é reprovada por tamanho, e só. Tamanho é
#: contável, então é gate e não opinião.
LIMITE_TAMANHO_PADRAO = 2000

#: Acima desta complexidade, cobrir todos os caminhos deixa de ser viável — a
#: função entra na lista de revisão mesmo com nota boa.
LIMITE_CCN_PADRAO = 10

#: Abaixo desta nota a função vai para revisão mesmo sem dúvida nenhuma.
NOTA_MINIMA_PADRAO = 60.0

MOEDA_PADRAO = "BRL"

#: Divisor grosseiro de caracteres por token. Não é a tokenização real de
#: nenhum modelo; serve para dar ordem de grandeza a quem decide se roda a
#: avaliação num repositório inteiro ou só no que mudou.
CARACTERES_POR_TOKEN = 4

#: Todas as variáveis que a ferramenta lê. Inclui a que é lida noutro módulo
#: (``JEV_CRAP_MODELO``, em ``julgamento.jev``): uma lista que se anuncia como
#: "os ajustes" e omite um deles manda a documentação mentir por omissão.
#: ``TYPESAFE_API_KEY`` de propósito não entra — segredo não passa por aqui.
VARIAVEIS: tuple[str, ...] = (
    VARIAVEL_FORMULA,
    VARIAVEL_LIMIAR,
    VARIAVEL_RAIZ,
    VARIAVEL_EXCLUIR,
    VARIAVEL_MAX_JULGAMENTOS,
    VARIAVEL_MAX_NO_RELATORIO,
    VARIAVEL_MAX_LINHAS,
    VARIAVEL_CONCORRENCIA,
    VARIAVEL_BLOQUEIO,
    VARIAVEL_SUSPEITA,
    VARIAVEL_LIMITE_TAMANHO,
    VARIAVEL_LIMITE_CCN,
    VARIAVEL_NOTA_MINIMA,
    VARIAVEL_CUSTO,
    VARIAVEL_MOEDA,
    VARIAVEL_EPISODIOS,
    "JEV_CRAP_MODELO",
)


def _texto(ambiente: Mapping[str, str], variavel: str) -> str:
    return (ambiente.get(variavel) or "").strip()


def _numero(
    ambiente: Mapping[str, str],
    variavel: str,
    padrao: float | None,
    avisos: list[str],
    *,
    minimo: float | None = None,
    maximo: float | None = None,
) -> float | None:
    bruto = _texto(ambiente, variavel)
    if not bruto:
        return padrao
    try:
        valor = float(bruto.replace(",", "."))
    except ValueError:
        avisos.append(f"{variavel}={bruto!r} não é um número; usando {padrao!r}")
        return padrao
    if minimo is not None and valor < minimo:
        avisos.append(f"{variavel}={bruto!r} é menor que {minimo}; usando {padrao!r}")
        return padrao
    if maximo is not None and valor > maximo:
        avisos.append(f"{variavel}={bruto!r} é maior que {maximo}; usando {padrao!r}")
        return padrao
    return valor


def _inteiro(
    ambiente: Mapping[str, str],
    variavel: str,
    padrao: int,
    avisos: list[str],
    *,
    minimo: int = 1,
) -> int:
    valor = _numero(ambiente, variavel, float(padrao), avisos, minimo=float(minimo))
    return int(valor) if valor is not None else padrao


@dataclass(frozen=True)
class Config:
    """Tudo que o servidor precisa saber antes de olhar para o código analisado.

    Congelado porque uma única avaliação atravessa vários passos (medir, filtrar,
    julgar, decidir, compor relatório) e nenhum deles pode mudar a régua no meio
    do caminho — um relatório em que metade das funções foi comparada com um
    limiar e a outra metade com outro não descreve nada.
    """

    raiz: Path = field(default_factory=Path.cwd)
    """Raiz do projeto analisado. É contra ela que os caminhos do relatório de
    cobertura são normalizados e é dentro dela que o histórico de episódios
    mora — nunca o diretório do pacote instalado, que é comum a todos os
    projetos da máquina."""

    formula: str = FORMULA_PADRAO
    limiar: float | None = None
    """``None`` significa "use o limiar que a própria fórmula recomenda". Guardar
    ``None`` em vez de copiar o número mantém as duas coisas ligadas: trocar de
    fórmula troca o limiar junto, sem configuração órfã apontando para uma
    escala que não existe mais."""

    excluir: tuple[str, ...] = ()
    max_julgamentos: int = MAX_JULGAMENTOS_PADRAO
    max_no_relatorio: int = MAX_NO_RELATORIO_PADRAO
    max_linhas: int = MAX_LINHAS_PADRAO
    concorrencia: int = CONCORRENCIA_PADRAO
    bloqueio: float = BLOQUEIO_PADRAO
    suspeita: float = SUSPEITA_PADRAO
    limite_tamanho: int = LIMITE_TAMANHO_PADRAO
    limite_ccn: int = LIMITE_CCN_PADRAO
    nota_minima: float = NOTA_MINIMA_PADRAO

    custo_por_julgamento: float | None = None
    """Preço de uma chamada ao Jev, se a pessoa souber o dela. Não há tabela
    embutida: preço de API muda sem avisar, e um número desatualizado no código
    é pior do que nenhum, porque parece autoridade."""

    moeda: str = MOEDA_PADRAO
    caminho_episodios: Path | None = None
    avisos: tuple[str, ...] = ()
    """Variáveis de ambiente que foram ignoradas e por quê (ver o cabeçalho)."""

    @classmethod
    def do_ambiente(
        cls,
        ambiente: Mapping[str, str] | None = None,
        *,
        raiz: Path | str | None = None,
    ) -> Config:
        """Monta a configuração a partir das variáveis de ambiente.

        ``ambiente`` entra por parâmetro (e não é lido de ``os.environ`` lá
        dentro) para que o teste descreva um cenário sem mexer no processo —
        variável de ambiente é estado global, e teste que a altera passa a
        depender da ordem em que roda.
        """
        fonte: Mapping[str, str] = os.environ if ambiente is None else ambiente
        avisos: list[str] = []

        return cls(
            raiz=cls._raiz(fonte, raiz),
            formula=cls._formula(fonte, avisos),
            limiar=_numero(fonte, VARIAVEL_LIMIAR, None, avisos, minimo=0.0),
            excluir=cls._lista(fonte, VARIAVEL_EXCLUIR),
            max_julgamentos=_inteiro(
                fonte, VARIAVEL_MAX_JULGAMENTOS, MAX_JULGAMENTOS_PADRAO, avisos
            ),
            max_no_relatorio=_inteiro(
                fonte, VARIAVEL_MAX_NO_RELATORIO, MAX_NO_RELATORIO_PADRAO, avisos
            ),
            max_linhas=_inteiro(fonte, VARIAVEL_MAX_LINHAS, MAX_LINHAS_PADRAO, avisos, minimo=20),
            concorrencia=_inteiro(fonte, VARIAVEL_CONCORRENCIA, CONCORRENCIA_PADRAO, avisos),
            bloqueio=_fracao(fonte, VARIAVEL_BLOQUEIO, BLOQUEIO_PADRAO, avisos),
            suspeita=_fracao(fonte, VARIAVEL_SUSPEITA, SUSPEITA_PADRAO, avisos),
            limite_tamanho=_inteiro(
                fonte, VARIAVEL_LIMITE_TAMANHO, LIMITE_TAMANHO_PADRAO, avisos, minimo=10
            ),
            limite_ccn=_inteiro(fonte, VARIAVEL_LIMITE_CCN, LIMITE_CCN_PADRAO, avisos),
            nota_minima=_numero(
                fonte, VARIAVEL_NOTA_MINIMA, NOTA_MINIMA_PADRAO, avisos, minimo=0.0, maximo=100.0
            )
            or 0.0,
            custo_por_julgamento=_numero(fonte, VARIAVEL_CUSTO, None, avisos, minimo=0.0),
            moeda=_texto(fonte, VARIAVEL_MOEDA) or MOEDA_PADRAO,
            caminho_episodios=cls._episodios(fonte),
            avisos=tuple(avisos),
        )

    @staticmethod
    def _raiz(fonte: Mapping[str, str], raiz: Path | str | None) -> Path:
        if raiz is not None:
            return Path(raiz).expanduser()
        declarada = _texto(fonte, VARIAVEL_RAIZ)
        return Path(declarada).expanduser() if declarada else Path.cwd()

    @staticmethod
    def _episodios(fonte: Mapping[str, str]) -> Path | None:
        declarado = _texto(fonte, VARIAVEL_EPISODIOS)
        return Path(declarado).expanduser() if declarado else None

    @staticmethod
    def _formula(fonte: Mapping[str, str], avisos: list[str]) -> str:
        nome = _texto(fonte, VARIAVEL_FORMULA)
        if not nome:
            return FORMULA_PADRAO
        if nome not in formulas_disponiveis():
            disponiveis = ", ".join(formulas_disponiveis())
            avisos.append(
                f"{VARIAVEL_FORMULA}={nome!r} não é uma fórmula registrada "
                f"(disponíveis: {disponiveis}); usando {FORMULA_PADRAO!r}"
            )
            return FORMULA_PADRAO
        return nome

    @staticmethod
    def _lista(fonte: Mapping[str, str], variavel: str) -> tuple[str, ...]:
        bruto = _texto(fonte, variavel)
        return tuple(item.strip() for item in bruto.split(",") if item.strip())

    def obter_formula(self) -> Formula:
        """A fórmula de risco configurada, já instanciada."""
        return obter_formula(self.formula)

    def limiar_efetivo(self, formula: Formula, pedido: float | None = None) -> float:
        """O limiar que vale nesta chamada.

        Precedência: o que a tool recebeu, depois o da configuração, depois o
        que a fórmula recomenda. O parâmetro da tool ganha porque limiar é
        decisão de quem está olhando o relatório agora — é ele que sabe se está
        varrendo um módulo crítico ou um script descartável.
        """
        if pedido is not None:
            return float(pedido)
        if self.limiar is not None:
            return float(self.limiar)
        return float(formula.limiar_padrao())

    def repositorio(self) -> Repositorio:
        """O histórico de episódios deste projeto."""
        return Repositorio(self.caminho_episodios, raiz_projeto=self.raiz)

    def exclusoes(self, extras: Sequence[str] = ()) -> tuple[str, ...]:
        """Exclusões da configuração somadas às que a chamada trouxe.

        As exclusões embutidas no analisador de complexidade não aparecem aqui:
        elas são sempre aplicadas por ele, e repeti-las daria a impressão de que
        removê-las desta lista as desliga.
        """
        return (*self.excluir, *(item for item in extras if item))

    def para_regua(self) -> dict[str, object]:
        """Versão serializável, para o relatório dizer sob qual régua ele foi feito."""
        return {
            "raiz": str(self.raiz),
            "formula": self.formula,
            "limiar_configurado": self.limiar,
            "excluir": list(self.excluir),
            "max_julgamentos": self.max_julgamentos,
            "max_no_relatorio": self.max_no_relatorio,
            "max_linhas": self.max_linhas,
            "bloqueio": self.bloqueio,
            "suspeita": self.suspeita,
            "limite_tamanho": self.limite_tamanho,
            "limite_ccn": self.limite_ccn,
            "nota_minima": self.nota_minima,
            "moeda": self.moeda,
            "avisos_de_configuracao": list(self.avisos),
        }


def _fracao(
    ambiente: Mapping[str, str], variavel: str, padrao: float, avisos: list[str]
) -> float:
    valor = _numero(ambiente, variavel, padrao, avisos, minimo=0.0, maximo=1.0)
    return padrao if valor is None else valor
