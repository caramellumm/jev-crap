"""O laço de aprendizado: o que o histórico diz e o que ele autoriza propor.

Duas funções, com responsabilidades deliberadamente separadas:

- :func:`agregar` descreve o histórico. Só conta o que aconteceu; não sugere
  nada, não opina, não decide.
- :func:`propor` lê essa descrição e devolve **propostas** de ajuste. Nada é
  aplicado: a função não escreve arquivo, não altera configuração e não tem
  efeito colateral nenhum. Quem muda o limiar é uma pessoa, olhando a evidência
  — uma ferramenta que recalibra sozinha os próprios critérios acaba provando
  que está certa contra um alvo que ela mesma moveu.

Três regras estão embutidas no código e valem a pena explicitar:

1. **Abaixo de :data:`MINIMO_EPISODIOS` episódios, `propor` devolve lista
   vazia.** Com 8 avaliações, "60% das sugestões na faixa foram ignoradas"
   significa "3 de 5", e 3 de 5 é ruído. Dizer "ainda não há base" é a resposta
   correta, não uma limitação a ser contornada com heurística.

2. **Toda proposta carrega o número que a sustenta** (chave `evidencia`).
   Proposta sem evidência é palpite com aparência de método, e quem vai aplicar
   precisa poder discordar do número, não da conclusão.

3. **Episódios de fórmulas diferentes não se misturam.** `risco` só é comparável
   dentro da mesma implementação de fórmula; `agregar` reporta as fórmulas
   presentes e `propor` trabalha apenas sobre a fórmula dominante do histórico,
   porque um limiar calibrado sobre duas escalas somadas não serve a nenhuma
   das duas.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from jev_crap.aprendizado.episodio import Episodio

__all__ = [
    "CONFIG_PADRAO",
    "MINIMO_EPISODIOS",
    "MOTIVO_SEM_BASE",
    "Propostas",
    "agregar",
    "propor",
]

MINIMO_EPISODIOS: int = 30
MOTIVO_SEM_BASE: str = "ainda não há base"

CONFIG_PADRAO: dict[str, Any] = {
    "minimo_episodios": MINIMO_EPISODIOS,
    # Limiar em vigor; quando None, sai do episódio mais recente do histórico.
    "limiar": None,
    # Tamanho da faixa logo acima do limiar, como fração dele.
    "faixa_acima": 0.25,
    # Quanto da faixa precisa ter sido ignorado para virar "maioria".
    "proporcao_ignorada": 0.6,
    # Quantas sugestões a faixa precisa ter para que a proporção signifique algo.
    "minimo_na_faixa": 5,
    # Amplitude até a qual uma dimensão é considerada parada. O valor supõe a
    # escala normalizada em que as notas são gravadas (0..1, tanto para score
    # quanto para noul); numa escala diferente o número precisa mudar junto, e é
    # por isso que ele é configuração e não constante.
    "amplitude_minima": 0.05,
    # Quantas notas uma dimensão precisa ter antes de se afirmar que não varia.
    "minimo_notas_dimensao": 10,
    # Margem abaixo do menor risco que apresentou defeito, ao propor baixar.
    "folga_ao_baixar": 0.1,
}

#: Ordem de busca do número dentro de uma nota gravada. Importa: `confianca`
#: mede o quanto o modelo se decidiu, não a posição na escala, e pegá-la por
#: engano produziria uma série histórica que parece certa e mede outra coisa.
CHAVES_DE_NOTA: tuple[str, ...] = ("normalizado", "valor", "nota", "probabilidade", "score")
SEM_SUGESTAO: str = "sem_sugestao"


class Propostas(list):
    """Lista de propostas que também sabe dizer por que está vazia.

    Uma lista vazia sem explicação é indistinguível de "não achei nada" — e as
    duas situações pedem reações opostas: "ainda não há base" quer mais uso, "o
    histórico não indica ajuste" quer que se deixe como está. Herdar de `list`
    mantém o valor de retorno utilizável como qualquer lista e acrescenta
    `motivo` para quem perguntar.
    """

    def __init__(self, itens: list[dict] | None = None, motivo: str = "") -> None:
        super().__init__(itens or [])
        self.motivo: str = motivo


def _numero(valor: Any) -> float | None:
    """Converte para float o que for número de verdade. `bool` não é.

    `isinstance(True, int)` é verdadeiro em Python, e um campo booleano
    escorregando para dentro de uma série de notas viraria 1.0 sem aviso.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def _valor_da_nota(nota: Any) -> float | None:
    """Extrai o número de uma nota, que vem acompanhada de outros campos.

    Três formatos são aceitos porque três são os que aparecem na prática: a
    ``Resposta`` do módulo de julgamento (objeto com `.normalizado`), o mesmo
    objeto já serializado como dicionário (é assim que ele chega do arquivo
    JSONL) e um número solto.
    """
    direto = _numero(nota)
    if direto is not None:
        return direto
    if isinstance(nota, dict):
        for chave in CHAVES_DE_NOTA:
            valor = _numero(nota.get(chave))
            if valor is not None:
                return valor
        for bruto in nota.values():
            valor = _numero(bruto)
            if valor is not None:
                return valor
        return None
    for chave in CHAVES_DE_NOTA:
        valor = _numero(getattr(nota, chave, None))
        if valor is not None:
            return valor
    return None


def _estatisticas(valores: list[float]) -> dict[str, float | int]:
    return {
        "n": len(valores),
        "media": round(statistics.fmean(valores), 4),
        "desvio": round(statistics.pstdev(valores), 4) if len(valores) > 1 else 0.0,
        "minimo": min(valores),
        "maximo": max(valores),
        "amplitude": round(max(valores) - min(valores), 4),
        "distintos": len({round(valor, 3) for valor in valores}),
    }


def _limiar_vigente(eps: list[Episodio], config: dict[str, Any]) -> float | None:
    """Limiar a calibrar: o informado na config ou o do episódio mais recente.

    O mais recente e não a média: o limiar é um valor único em vigor, e média de
    limiares antigos descreve a história da configuração, não a configuração.
    """
    informado = config.get("limiar")
    if informado is not None:
        return float(informado)
    if not eps:
        return None
    return max(eps, key=lambda ep: (ep.em, ep.id)).limiar_vigente


def agregar(eps: list[Episodio]) -> dict:
    """Descreve o histórico em números, sem interpretar nenhum deles.

    Métricas ausentes viram ``None`` com um aviso em `avisos`, nunca ``0``:
    "nenhuma sugestão foi aceita" e "nenhuma sugestão teve decisão registrada"
    são fatos diferentes, e confundi-los produz exatamente o tipo de conclusão
    errada que este módulo existe para evitar.
    """
    avisos: list[str] = []

    com_decisao = [ep for ep in eps if ep.aceita is not None]
    aceitas = [ep for ep in com_decisao if ep.aceita]
    taxa_aceitacao = round(len(aceitas) / len(com_decisao), 4) if com_decisao else None
    if taxa_aceitacao is None:
        avisos.append("taxa_aceitacao indisponível: nenhuma sugestão teve decisão registrada")

    # Cobertura de risco é o inverso do falso negativo: entre os trechos que
    # depois deram problema, quantos a ferramenta já apontava como arriscados. É
    # o número mais honesto do conjunto porque só pode ser calculado com
    # informação que chegou depois, fora do controle de quem escreveu a régua.
    com_defeito = [ep for ep in eps if ep.defeito]
    pegos = [ep for ep in com_defeito if ep.risco >= ep.limiar_vigente]
    cobertura_de_risco = round(len(pegos) / len(com_defeito), 4) if com_defeito else None
    if cobertura_de_risco is None:
        avisos.append("cobertura_de_risco indisponível: nenhum defeito posterior foi registrado")

    por_dimensao: dict[str, list[float]] = {}
    for ep in eps:
        for dimensao, nota in (ep.notas or {}).items():
            valor = _valor_da_nota(nota)
            if valor is not None:
                por_dimensao.setdefault(dimensao, []).append(valor)

    variacao_dimensoes = {
        dimensao: _estatisticas(valores) for dimensao, valores in sorted(por_dimensao.items())
    }

    notas = [ep.nota for ep in eps if ep.nota is not None]

    formulas = Counter(ep.formula for ep in eps)
    if len(formulas) > 1:
        avisos.append(
            "há mais de uma fórmula no histórico "
            f"({', '.join(sorted(formulas))}): riscos de fórmulas diferentes não são comparáveis"
        )

    return {
        "total": len(eps),
        "com_decisao": len(com_decisao),
        "aceitas": len(aceitas),
        "ignoradas": len(com_decisao) - len(aceitas),
        "taxa_aceitacao": taxa_aceitacao,
        "com_defeito": len(com_defeito),
        "defeitos_acima_do_limiar": len(pegos),
        "cobertura_de_risco": cobertura_de_risco,
        "por_conselho": dict(sorted(Counter(ep.conselho for ep in eps).items())),
        "por_veredito": dict(sorted(Counter(ep.veredito for ep in eps).items())),
        "por_acao": dict(sorted(Counter(ep.acao or SEM_SUGESTAO for ep in eps).items())),
        "nota_media": round(statistics.fmean(notas), 2) if notas else None,
        "episodios_com_nota": len(notas),
        "variacao_dimensoes": variacao_dimensoes,
        "por_formula": dict(sorted(formulas.items())),
        "avisos": avisos,
    }


def _proposta(
    tipo: str,
    alvo: str,
    de: Any,
    para: Any,
    motivo: str,
    evidencia: dict[str, Any],
) -> dict:
    return {
        "tipo": tipo,
        "alvo": alvo,
        "de": de,
        "para": para,
        "motivo": motivo,
        "evidencia": evidencia,
    }


def _subir_limiar(eps: list[Episodio], limiar: float, cfg: dict[str, Any]) -> dict | None:
    """Sugestões na faixa logo acima do limiar que as pessoas vêm ignorando.

    A faixa é estreita de propósito: ignorar um alerta de risco 12 quando o
    limiar é 10 sugere que o limiar está baixo; ignorar um de risco 90 sugere
    outra coisa (falta de tempo, código legado intocável) e não deveria mexer na
    régua. Por isso só a vizinhança imediata do limiar entra na conta.
    """
    topo = limiar * (1 + float(cfg["faixa_acima"]))
    na_faixa = [
        ep for ep in eps if ep.aceita is not None and ep.acao and limiar <= ep.risco <= topo
    ]
    if len(na_faixa) < int(cfg["minimo_na_faixa"]):
        return None

    ignorados = [ep for ep in na_faixa if not ep.aceita]
    proporcao = len(ignorados) / len(na_faixa)
    if proporcao < float(cfg["proporcao_ignorada"]):
        return None

    # Um defeito dentro da faixa derruba a proposta: subir o limiar esconderia
    # justamente o caso que a ferramenta acertou, e falso negativo custa mais
    # caro que incômodo.
    if any(ep.defeito for ep in na_faixa):
        return None

    return _proposta(
        tipo="subir_limiar",
        alvo="limiar",
        de=round(limiar, 4),
        para=round(topo, 4),
        motivo=(
            f"{len(ignorados)} de {len(na_faixa)} sugestões com risco entre "
            f"{round(limiar, 2)} e {round(topo, 2)} foram ignoradas "
            f"({round(proporcao * 100)}%), e nenhuma delas apresentou defeito depois"
        ),
        evidencia={
            "sugestoes_na_faixa": len(na_faixa),
            "ignoradas_na_faixa": len(ignorados),
            "proporcao_ignorada": round(proporcao, 4),
            "faixa": [round(limiar, 4), round(topo, 4)],
            "defeitos_na_faixa": 0,
        },
    )


def _baixar_limiar(eps: list[Episodio], limiar: float, cfg: dict[str, Any]) -> dict | None:
    """Defeitos que apareceram abaixo do limiar — falso negativo com prova.

    Basta um para propor: diferente do incômodo, que precisa de repetição para
    virar sinal, o defeito que escapou já é a evidência completa de que a régua
    estava alta demais para aquele código.
    """
    escaparam = [ep for ep in eps if ep.defeito and ep.risco < limiar]
    if not escaparam:
        return None

    menor = min(ep.risco for ep in escaparam)
    # A folga existe porque o risco medido tem ruído (cobertura muda com o
    # tempo, complexidade muda com refatoração): colocar o limiar exatamente em
    # cima do defeito que escapou deixaria o próximo passar por milésimos.
    para = round(menor * (1 - float(cfg["folga_ao_baixar"])), 4)
    return _proposta(
        tipo="baixar_limiar",
        alvo="limiar",
        de=round(limiar, 4),
        para=para,
        motivo=(
            f"{len(escaparam)} episódio(s) apresentaram defeito com risco abaixo do limiar "
            f"{round(limiar, 2)}; o menor deles marcava {round(menor, 2)}"
        ),
        evidencia={
            "defeitos_abaixo_do_limiar": len(escaparam),
            "menor_risco_com_defeito": round(menor, 4),
            "riscos": sorted(round(ep.risco, 4) for ep in escaparam),
            "folga_aplicada": float(cfg["folga_ao_baixar"]),
        },
    )


def _remover_dimensoes(resumo: dict, cfg: dict[str, Any]) -> list[dict]:
    """Dimensões do Jev cuja nota quase não varia.

    Uma dimensão que responde quase sempre o mesmo não separa código bom de
    código ruim: ela só acrescenta latência, custo de chamada e uma coluna a
    mais no relatório. Remover é a proposta; a decisão pode ser outra (talvez a
    pergunta esteja mal formulada e valha reescrevê-la em vez de descartá-la), e
    por isso a evidência vem junto.
    """
    propostas: list[dict] = []
    for dimensao, estatistica in resumo["variacao_dimensoes"].items():
        if estatistica["n"] < int(cfg["minimo_notas_dimensao"]):
            continue
        if estatistica["amplitude"] > float(cfg["amplitude_minima"]):
            continue
        propostas.append(
            _proposta(
                tipo="remover_dimensao",
                alvo=dimensao,
                de="ativa",
                para="removida",
                motivo=(
                    f"em {estatistica['n']} avaliações a nota de '{dimensao}' variou apenas "
                    f"{estatistica['amplitude']} (desvio {estatistica['desvio']}), "
                    f"com {estatistica['distintos']} valor(es) distinto(s)"
                ),
                evidencia={
                    "avaliacoes": estatistica["n"],
                    "amplitude": estatistica["amplitude"],
                    "desvio": estatistica["desvio"],
                    "media": estatistica["media"],
                    "valores_distintos": estatistica["distintos"],
                },
            )
        )
    return propostas


def propor(eps: list[Episodio], config: dict | None = None) -> Propostas:
    """Propõe ajustes sustentados pelo histórico. Não aplica nenhum deles.

    A ordem do retorno é por gravidade: `baixar_limiar` (falso negativo, já
    custou defeito) vem antes de `subir_limiar` (falso positivo, custou
    incômodo). As duas podem aparecer juntas quando o histórico mostra defeito
    abaixo do limiar e irritação acima dele — sinal de que a fórmula, e não o
    limiar, está ordenando mal os casos. O conflito é informação, então as duas
    vão para a pessoa em vez de uma ser suprimida em silêncio.
    """
    cfg = {**CONFIG_PADRAO, **(config or {})}

    minimo = int(cfg["minimo_episodios"])
    if len(eps) < minimo:
        return Propostas(
            motivo=(
                f"{MOTIVO_SEM_BASE}: {len(eps)} episódio(s) registrados, "
                f"{minimo} é o mínimo para que qualquer proporção signifique algo"
            )
        )

    # Só a fórmula dominante entra: riscos de fórmulas diferentes são escalas
    # diferentes, e um limiar calibrado sobre a mistura não vale para nenhuma.
    formulas = Counter(ep.formula for ep in eps)
    formula_dominante = max(sorted(formulas.items()), key=lambda item: item[1])[0]
    do_escopo = [ep for ep in eps if ep.formula == formula_dominante]
    if len(do_escopo) < minimo:
        return Propostas(
            motivo=(
                f"{MOTIVO_SEM_BASE}: a fórmula com mais histórico ('{formula_dominante}') tem "
                f"{len(do_escopo)} episódio(s) de {minimo} necessários; riscos de fórmulas "
                "diferentes não podem ser somados"
            )
        )

    limiar = _limiar_vigente(do_escopo, cfg)
    if limiar is None:
        return Propostas(motivo=f"{MOTIVO_SEM_BASE}: nenhum limiar vigente identificado")

    resumo_escopo = agregar(do_escopo)
    encontradas: list[dict] = []
    for candidata in (
        _baixar_limiar(do_escopo, limiar, cfg),
        _subir_limiar(do_escopo, limiar, cfg),
    ):
        if candidata is not None:
            encontradas.append(candidata)
    encontradas.extend(_remover_dimensoes(resumo_escopo, cfg))

    if not encontradas:
        return Propostas(
            motivo=(
                f"histórico de {len(do_escopo)} episódio(s) não indica ajuste: "
                "nenhum defeito abaixo do limiar, nenhuma faixa majoritariamente "
                "ignorada e nenhuma dimensão parada"
            )
        )
    return Propostas(
        encontradas,
        motivo=f"{len(encontradas)} proposta(s) a partir de {len(do_escopo)} episódio(s)",
    )
