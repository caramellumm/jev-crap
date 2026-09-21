"""Aprendizado: o histórico do que a ferramenta disse e do que aconteceu depois.

O módulo registra episódios (``episodio``) e transforma histórico em números e
propostas (``laco``). A separação existe porque persistir e concluir falham por
motivos diferentes: gravação quebra por disco e formato, conclusão quebra por
amostra pequena e comparação indevida.
"""

from jev_crap.aprendizado.episodio import (
    NOME_ARQUIVO,
    NOME_DIRETORIO,
    VARIAVEL_CAMINHO,
    Episodio,
    Repositorio,
    agora_iso,
    caminho_padrao,
)
from jev_crap.aprendizado.laco import (
    CONFIG_PADRAO,
    MINIMO_EPISODIOS,
    MOTIVO_SEM_BASE,
    Propostas,
    agregar,
    propor,
)

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
