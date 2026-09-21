"""Eixo semântico: a régua (``rubrica``) e a conversa com o modelo (``jev``).

A separação existe porque as duas falham por motivos diferentes: a régua quebra
por texto mal escrito e peso que não fecha, o transporte quebra por rede, chave
e contrato de resposta. Juntá-las faria um erro de redação parecer falha de API.
"""

from jev_crap.julgamento.jev import (
    CONFIANCA_MINIMA,
    Julgador,
    JulgadorDesligado,
    JulgadorFake,
    JulgadorJev,
    Resposta,
    extrair_respostas,
    montar_estado,
    obter_julgador,
)
from jev_crap.julgamento.rubrica import (
    GRUPOS,
    Dimensao,
    Rubrica,
    RubricaInvalida,
    carregar_rubrica,
)

__all__ = [
    "CONFIANCA_MINIMA",
    "Dimensao",
    "GRUPOS",
    "Julgador",
    "JulgadorDesligado",
    "JulgadorFake",
    "JulgadorJev",
    "Resposta",
    "Rubrica",
    "RubricaInvalida",
    "carregar_rubrica",
    "extrair_respostas",
    "montar_estado",
    "obter_julgador",
]
