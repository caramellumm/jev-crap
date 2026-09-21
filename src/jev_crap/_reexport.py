"""Peças do re-export preguiçoso dos subpacotes (PEP 562).

Os pacotes deste projeto publicam nomes que moram nos submódulos. Importá-los
de forma ansiosa no ``__init__`` obriga quem toca o pacote a pagar por tudo:
``import jev_crap.julgamento`` puxa ``httpx`` mesmo quando quem chamou só
queria a régua, e ``import jev_crap.aprendizado`` carrega o laço de propostas
mesmo quando só se vai gravar um episódio.

O ganho não é só tempo. É também o diagnóstico: com importação ansiosa, um
defeito de sintaxe em ``laco.py`` quebra ``import jev_crap.aprendizado`` com um
traceback que aponta para o ``__init__``, e quem lê procura o erro no arquivo
errado.

Cada pacote define o próprio ``__getattr__`` em vez de receber um pronto daqui.
É de propósito: o ``__getattr__`` de um pacote é parte da interface dele, e
quem abre o ``__init__.py`` para descobrir o que ele publica precisa ver a
regra ali, não uma atribuição que aponta para outro módulo. O que este módulo
guarda são as duas peças que seriam idênticas nos três: a conferência do mapa e
a importação em si.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["MapaDeReexportInconsistente", "conferir_mapa", "importar_publicado"]


class MapaDeReexportInconsistente(UserWarning):
    """``__all__`` de um pacote não bate com o mapa de origens dele.

    Categoria própria, e não ``UserWarning`` solto, para que a suíte possa
    transformar só este aviso em erro sem calar os outros — e para que quem
    embute a ferramenta consiga filtrá-lo se souber o que está fazendo.
    """


def conferir_mapa(
    pacote: str,
    origem_por_nome: Mapping[str, str],
    publicados: Sequence[str],
) -> tuple[str, ...]:
    """As divergências entre ``__all__`` e o mapa de origens. Nunca levanta.

    Roda na importação do pacote e **avisa** em vez de barrar. A diferença é
    deliberada e custou pensar: levantar aqui transforma um erro de digitação
    no mapa em ``import jev_crap.metrica`` impossível, ou seja, derruba a
    ferramenta inteira por causa de um nome publicado a mais. O aviso mostra o
    mesmo problema sem tirar do ar o que continua funcionando — todos os outros
    nomes resolvem normalmente.

    Quem barra de verdade é a suíte, que afirma que esta lista vem vazia para
    os três pacotes. É o lugar certo: a coerência entre ``__all__`` e o mapa é
    invariante de empacotamento, conferível sem rodar nada em produção.

    Confere nos dois sentidos porque os dois erros acontecem e pedem correções
    opostas: nome publicado sem origem é ``__all__`` adiantado, origem sem
    publicação é ``__all__`` esquecido.
    """
    problemas: list[str] = []
    faltam_origem = sorted(set(publicados) - set(origem_por_nome))
    if faltam_origem:
        problemas.append(
            f"{pacote}: {faltam_origem} está em __all__ mas não tem submódulo de origem"
        )
    sobram_origem = sorted(set(origem_por_nome) - set(publicados))
    if sobram_origem:
        problemas.append(
            f"{pacote}: {sobram_origem} tem origem declarada mas não está em __all__"
        )
    for problema in problemas:
        warnings.warn(problema, MapaDeReexportInconsistente, stacklevel=2)
    return tuple(problemas)


def importar_publicado(pacote: str, submodulo: str, nome: str) -> Any:
    """O objeto ``nome``, importando ``pacote.submodulo`` só agora.

    O ``AttributeError`` é traduzido antes de subir. Sem a tradução ele diria
    que *o submódulo* não tem o atributo, e quem lê iria procurar o defeito no
    submódulo — quando o defeito está no mapa do ``__init__``, que ficou para
    trás de um renome.
    """
    modulo = importlib.import_module(f"{pacote}.{submodulo}")
    try:
        return getattr(modulo, nome)
    except AttributeError as erro:
        raise AttributeError(
            f"{pacote} publica {nome!r} vindo de {submodulo!r}, "
            f"mas {pacote}.{submodulo} não define esse nome"
        ) from erro
