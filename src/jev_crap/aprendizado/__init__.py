"""Aprendizado: o histórico do que a ferramenta disse e do que aconteceu depois.

O módulo registra episódios (``episodio``) e transforma histórico em números e
propostas (``laco``). A separação existe porque persistir e concluir falham por
motivos diferentes: gravação quebra por disco e formato, conclusão quebra por
amostra pequena e comparação indevida.

Os nomes são re-exportados sob demanda (ver ``jev_crap._reexport``): gravar um
episódio não precisa carregar o laço de propostas, e vice-versa.
"""

from jev_crap._reexport import conferir_mapa, importar_publicado

#: Onde cada nome publicado mora de fato. Conferido contra ``__all__`` na
#: importação do pacote.
_ORIGEM = {
    "NOME_ARQUIVO": "episodio",
    "NOME_DIRETORIO": "episodio",
    "VARIAVEL_CAMINHO": "episodio",
    "Episodio": "episodio",
    "Repositorio": "episodio",
    "agora_iso": "episodio",
    "caminho_padrao": "episodio",
    "CONFIG_PADRAO": "laco",
    "MINIMO_EPISODIOS": "laco",
    "MOTIVO_SEM_BASE": "laco",
    "Propostas": "laco",
    "agregar": "laco",
    "propor": "laco",
}

__all__ = [
    "CONFIG_PADRAO",
    "MINIMO_EPISODIOS",
    "MOTIVO_SEM_BASE",
    "NOME_ARQUIVO",
    "NOME_DIRETORIO",
    "VARIAVEL_CAMINHO",
    "Episodio",
    "Propostas",
    "Repositorio",
    "agora_iso",
    "agregar",
    "caminho_padrao",
    "propor",
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
