"""A régua: quais perguntas são feitas, o que cada nível significa, quanto pesa.

A rubrica mora em ``perguntas.json`` e não no código por um motivo prático: a
redação dos níveis é o que faz o modelo distinguir um nível do outro, então
mexer nela é ajuste de conteúdo — revisado como texto, sem tocar em lógica de
transporte ou de decisão. Este módulo é quem lê esse arquivo, confere que ele
faz sentido e o entrega em pedaços que o resto do programa consegue usar.

Quatro grupos, e a diferença entre eles é a única coisa que realmente importa
entender aqui:

``qualidade``
    Dimensões **compensáveis**: legibilidade boa compensa tratamento de erro
    mediano, e a média ponderada delas é a nota. Só este grupo tem peso.

``contexto``
    Não entra na nota e não barra nada. Decide *o que fazer* e *com que pressa*
    — consequência de falha é exatamente o que a fórmula CRAP ignora, e
    complexidade essencial é o que separa "escreva teste" de "refatore antes".

``risco_grave``
    **Gates.** Ficam fora da nota porque risco não se compensa com legibilidade:
    uma função com injeção de SQL e nota 95 continua sendo uma função com
    injeção de SQL. São restritos a proposições que ou valem ou não valem e
    cuja consequência não se discute.

``risco_atencao``
    Mandam para olho humano, nunca barram. São perguntas graduais disfarçadas
    de proposição: "existe entrada plausível que quebraria isto?" é verdade
    para quase toda função escrita em linguagem dinâmica. Medido na geração
    anterior deste projeto: 125 de 145 funções acima de 0.50 e 32 acima de
    0.80 — entre elas uma função de 5 linhas, complexidade 1, com 100% de
    cobertura. Como gate de bloqueio isso não separa nada; como lista de
    revisão, é informação legítima.

A validação é feita na carga, e é deliberadamente barulhenta: um peso somando
0.99 ou um grupo escrito errado precisa quebrar aqui, no início do processo,
não virar uma nota silenciosamente errada num relatório que alguém vai ler
como se fosse medida.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ARQUIVO_PADRAO",
    "Dimensao",
    "GRUPOS",
    "Rubrica",
    "RubricaInvalida",
    "carregar_rubrica",
]

ARQUIVO_PADRAO = Path(__file__).with_name("perguntas.json")

GRUPO_QUALIDADE = "qualidade"
GRUPO_CONTEXTO = "contexto"
GRUPO_RISCO_GRAVE = "risco_grave"
GRUPO_RISCO_ATENCAO = "risco_atencao"

GRUPOS: tuple[str, ...] = (
    GRUPO_QUALIDADE,
    GRUPO_CONTEXTO,
    GRUPO_RISCO_GRAVE,
    GRUPO_RISCO_ATENCAO,
)

TIPOS = ("score", "noul")

#: Direções possíveis de uma escala. Declarar o sentido como dado, em vez de
#: deixá-lo implícito na ordem dos níveis, existe por causa de um erro
#: específico e caro: `consequencia_de_falha` é a única dimensão em que nota
#: alta é má notícia, e inverter essa leitura despriorizaria justamente o
#: código perigoso — silenciosamente, porque o número continuaria plausível.
SENTIDOS = (
    "maior_melhor",
    "maior_pior",
    "maior_mais_em_jogo",
    "maior_mais_essencial",
)


class RubricaInvalida(ValueError):
    """O arquivo de perguntas não descreve uma régua utilizável."""


@dataclass(frozen=True)
class Dimensao:
    """Uma pergunta da régua, com o que o código precisa saber sobre ela."""

    nome: str
    grupo: str
    tipo: str
    sentido: str
    peso: float
    """Participação na nota. Zero fora do grupo ``qualidade``."""

    exige: str | None
    """Campo do estado que precisa estar preenchido para a pergunta ser feita.

    Só ``teste_verifica`` usa: sem trecho de teste para olhar, a resposta seria
    "não há teste" para toda função privada exercitada indiretamente — medido
    na geração anterior: 87 de 145 funções chegavam sem trecho, e 78 delas
    tinham 80% ou mais de cobertura de linha. O programa já sabia que estavam
    testadas; perguntar assim mesmo trocava um fato por um palpite mal
    informado, e cobrava um quarto da nota por ele.
    """

    pergunta: Mapping[str, Any]
    """O objeto enviado à API, exatamente como ela o espera."""

    @property
    def niveis(self) -> int:
        """Quantidade de níveis de um ``score``; 0 para ``noul``."""
        criterios = self.pergunta.get("criteria")
        return len(criterios) if self.tipo == "score" and isinstance(criterios, list) else 0

    def descrever_niveis(self) -> list[str]:
        """Os níveis em texto, do pior (0) ao melhor, para o relatório explicar a régua."""
        criterios = self.pergunta.get("criteria")
        if self.tipo == "noul":
            if isinstance(criterios, Mapping):
                return [f"não: {criterios.get('false', '')}", f"sim: {criterios.get('true', '')}"]
            return []
        if not isinstance(criterios, list):
            return []
        return [c["what"] if isinstance(c, Mapping) else str(c) for c in criterios]


class Rubrica:
    """A régua inteira, já conferida, com as consultas que o resto do código faz."""

    def __init__(self, bruto: Mapping[str, Any]) -> None:
        self.versao: str = str(bruto.get("versao", "desconhecida"))
        self.dimensoes: dict[str, Dimensao] = _ler_dimensoes(bruto)
        _conferir_pesos(self.dimensoes)

    # -- consultas por grupo -------------------------------------------------

    def do_grupo(self, grupo: str) -> dict[str, Dimensao]:
        return {n: d for n, d in self.dimensoes.items() if d.grupo == grupo}

    @property
    def qualidade(self) -> dict[str, Dimensao]:
        """Dimensões compensáveis, as únicas que formam a nota."""
        return self.do_grupo(GRUPO_QUALIDADE)

    @property
    def contexto(self) -> dict[str, Dimensao]:
        return self.do_grupo(GRUPO_CONTEXTO)

    @property
    def risco_grave(self) -> dict[str, Dimensao]:
        """Gates: acima do limiar de bloqueio, barram o código."""
        return self.do_grupo(GRUPO_RISCO_GRAVE)

    @property
    def risco_atencao(self) -> dict[str, Dimensao]:
        """Mandam para revisão humana; nunca barram."""
        return self.do_grupo(GRUPO_RISCO_ATENCAO)

    @property
    def risco(self) -> dict[str, Dimensao]:
        return {**self.risco_grave, **self.risco_atencao}

    @property
    def pesos(self) -> dict[str, float]:
        return {n: d.peso for n, d in self.qualidade.items()}

    # -- uso ------------------------------------------------------------------

    def perguntas_para(self, estado: Mapping[str, Any]) -> dict[str, Any]:
        """As perguntas que *este* estado tem como responder.

        Uma dimensão com ``exige`` só entra quando o campo correspondente do
        estado tem conteúdo. O peso dela não vira zero: ele é redistribuído
        entre as dimensões que foram de fato observadas (ver
        :meth:`pesos_observados`). Nota calculada sobre três dimensões é
        honesta; nota calculada sobre quatro com uma delas inventada, não.
        """
        return {
            nome: dict(d.pergunta)
            for nome, d in self.dimensoes.items()
            if d.exige is None or estado.get(d.exige)
        }

    def pesos_observados(self, respondidas: Mapping[str, Any]) -> dict[str, float]:
        """Pesos renormalizados para somar 1.0 sobre o que realmente foi respondido."""
        presentes = {n: p for n, p in self.pesos.items() if n in respondidas}
        total = sum(presentes.values())
        if not total:
            return {}
        return {n: p / total for n, p in presentes.items()}

    def normalizar(self, nome: str, bruto: float) -> float:
        """Traz a resposta para 0..1, seja ela ``score`` ou ``noul``.

        A API devolve o ``score`` como média dos níveis ponderada pelas
        probabilidades, então uma escala de três níveis vai de 0 a 2 — dividir
        por 1 aqui daria nota acima de 100 e ninguém perceberia, porque o número
        continuaria parecendo plausível. ``noul`` já chega em 0..1 e passa
        inteiro.
        """
        dimensao = self.dimensoes[nome]
        if dimensao.tipo == "noul":
            return bruto
        divisor = dimensao.niveis - 1
        if divisor <= 0:
            raise RubricaInvalida(f"{nome}: um score precisa de ao menos dois níveis")
        return bruto / divisor

    def para_dict(self) -> dict[str, Any]:
        """A régua em formato serializável, para a ferramenta poder explicar a si mesma."""
        return {
            "versao": self.versao,
            "dimensoes": {
                nome: {
                    "grupo": d.grupo,
                    "tipo": d.tipo,
                    "sentido": d.sentido,
                    "peso": d.peso if d.grupo == GRUPO_QUALIDADE else None,
                    "pergunta": d.pergunta.get("instructions", ""),
                    "niveis": d.descrever_niveis(),
                    "condicionada_a": d.exige,
                }
                for nome, d in self.dimensoes.items()
            },
        }


def _ler_dimensoes(bruto: Mapping[str, Any]) -> dict[str, Dimensao]:
    cruas = bruto.get("dimensoes")
    if not isinstance(cruas, Mapping) or not cruas:
        raise RubricaInvalida("a régua precisa de um objeto 'dimensoes' não vazio")

    dimensoes: dict[str, Dimensao] = {}
    for nome, corpo in cruas.items():
        if not isinstance(corpo, Mapping):
            raise RubricaInvalida(f"{nome}: a dimensão precisa ser um objeto")
        grupo = corpo.get("grupo")
        if grupo not in GRUPOS:
            raise RubricaInvalida(
                f"{nome}: grupo {grupo!r} desconhecido; use um de {', '.join(GRUPOS)}"
            )
        sentido = corpo.get("sentido")
        if sentido not in SENTIDOS:
            raise RubricaInvalida(
                f"{nome}: sentido {sentido!r} desconhecido; use um de {', '.join(SENTIDOS)}"
            )
        pergunta = corpo.get("pergunta")
        if not isinstance(pergunta, Mapping):
            raise RubricaInvalida(f"{nome}: falta o objeto 'pergunta'")
        tipo = pergunta.get("type")
        if tipo not in TIPOS:
            raise RubricaInvalida(f"{nome}: tipo {tipo!r} desconhecido; use 'score' ou 'noul'")
        if not str(pergunta.get("instructions", "")).strip():
            raise RubricaInvalida(f"{nome}: 'instructions' não pode ser vazio")

        peso = float(corpo.get("peso", 0.0))
        if grupo == GRUPO_QUALIDADE and peso <= 0:
            raise RubricaInvalida(f"{nome}: dimensão de qualidade precisa de peso positivo")
        if grupo != GRUPO_QUALIDADE and peso:
            raise RubricaInvalida(
                f"{nome}: só dimensão do grupo '{GRUPO_QUALIDADE}' tem peso — "
                "peso fora dele daria a impressão de que a dimensão entra na nota"
            )

        dimensao = Dimensao(
            nome=str(nome),
            grupo=str(grupo),
            tipo=str(tipo),
            sentido=str(sentido),
            peso=peso,
            exige=(str(corpo["exige"]) if corpo.get("exige") else None),
            pergunta=dict(pergunta),
        )
        if dimensao.tipo == "score" and dimensao.niveis < 2:
            raise RubricaInvalida(f"{nome}: um score precisa de ao menos dois níveis")
        dimensoes[dimensao.nome] = dimensao
    return dimensoes


def _conferir_pesos(dimensoes: Mapping[str, Dimensao]) -> None:
    pesos = [d.peso for d in dimensoes.values() if d.grupo == GRUPO_QUALIDADE]
    if not pesos:
        raise RubricaInvalida(f"nenhuma dimensão no grupo '{GRUPO_QUALIDADE}': não haveria nota")
    total = round(sum(pesos), 6)
    if total != 1.0:
        raise RubricaInvalida(
            f"os pesos das dimensões de qualidade somam {total}, não 1.0 — "
            "uma nota calculada sobre pesos que não fecham não é média de nada"
        )


def carregar_rubrica(caminho: Path | str | None = None) -> Rubrica:
    """Lê e valida a régua. Sem argumento, a que vem com o pacote."""
    destino = Path(caminho) if caminho is not None else ARQUIVO_PADRAO
    try:
        bruto = json.loads(destino.read_text(encoding="utf-8"))
    except FileNotFoundError as erro:
        raise RubricaInvalida(f"régua não encontrada em {destino}") from erro
    except json.JSONDecodeError as erro:
        raise RubricaInvalida(f"{destino} não é JSON válido: {erro}") from erro
    return Rubrica(bruto)
