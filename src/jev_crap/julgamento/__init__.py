"""Eixo semântico: a régua (``rubrica``) e a conversa com o modelo (``jev``).

A separação existe porque as duas falham por motivos diferentes: a régua quebra
por texto mal escrito e peso que não fecha, o transporte quebra por rede, chave
e contrato de resposta. Juntá-las faria um erro de redação parecer falha de API.

Os nomes são re-exportados sob demanda (ver ``jev_crap._reexport``): quem só
precisa da régua não paga o ``httpx`` que o transporte carrega.
"""

from jev_crap._reexport import conferir_mapa, importar_publicado

#: Onde cada nome publicado mora de fato. É conferido contra ``__all__`` na
#: importação do pacote, então os dois não conseguem divergir em silêncio.
_ORIGEM = {
    "CONFIANCA_MINIMA": "jev",
    "Julgador": "jev",
    "JulgadorDesligado": "jev",
    "JulgadorFake": "jev",
    "JulgadorJev": "jev",
    "Resposta": "jev",
    "extrair_respostas": "jev",
    "montar_estado": "jev",
    "obter_julgador": "jev",
    "GRUPOS": "rubrica",
    "Dimensao": "rubrica",
    "Rubrica": "rubrica",
    "RubricaInvalida": "rubrica",
    "carregar_rubrica": "rubrica",
}

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

conferir_mapa(__name__, _ORIGEM, __all__)


def __getattr__(nome: str) -> object:
    """Resolve um nome publicado, importando o submódulo só agora (PEP 562).

    Python chama isto apenas quando o nome não está no módulo — ou seja, uma
    vez por nome: a partir da segunda vez o próprio ``import`` já achou o que
    ficou em cache. O custo do adiamento é pago uma vez e some.

    Nome fora de ``__all__`` levanta ``AttributeError`` listando o que existe.
    A mensagem padrão do Python é ``module 'x' has no attribute 'y'``, que não
    ajuda quem errou uma letra no nome; a lista ajuda.
    """
    submodulo = _ORIGEM.get(nome)
    if submodulo is None:
        raise AttributeError(f"{__name__} não publica {nome!r}; publica {sorted(__all__)}")
    return importar_publicado(__name__, submodulo, nome)
