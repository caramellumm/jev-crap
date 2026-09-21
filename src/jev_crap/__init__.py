"""jev-crap: avalia qualidade de código cruzando métrica contável com julgamento do Jev.

O pacote raiz não re-exporta nada de propósito: as três camadas
(``metrica``, ``julgamento``, ``avaliacao``) têm entradas próprias, e uma
fachada aqui só criaria um segundo caminho para as mesmas funções.

O que mora aqui é a versão e o ``diagnostico()`` que a acompanha — as duas
informações que todo relato de problema precisa trazer e que ninguém lembra de
copiar.
"""

from __future__ import annotations

import os
import platform
from importlib import metadata

__all__ = ["__version__", "diagnostico"]

__version__ = "0.2.0"

#: As três dependências declaradas no ``pyproject.toml``. A lista é repetida
#: aqui porque ``diagnostico()`` precisa saber o que *deveria* estar instalado
#: para poder dizer que algo falta — ler o próprio metadado só conta o que
#: existe, que é exatamente a metade inútil da resposta.
DEPENDENCIAS = ("httpx", "lizard", "fastmcp")

#: A variável de onde a chave do Jev sai. Repetida de ``julgamento.jev`` porque
#: importá-la aqui puxaria o ``httpx`` para dentro de ``import jev_crap``, que é
#: o custo que o re-export preguiçoso dos subpacotes existe para evitar.
VARIAVEL_DA_CHAVE = "TYPESAFE_API_KEY"

#: O que aparece no lugar da versão quando a dependência não está instalada.
#: Texto e não ``None`` porque o diagnóstico é colado num relato de bug: um
#: campo ausente some do JSON, e "ausente" é justamente o achado.
AUSENTE = "ausente"


def diagnostico() -> dict[str, str]:
    """Versão, ambiente e dependências — o cabeçalho de um relato de problema.

    Nunca levanta. É chamado justamente quando alguma coisa já deu errado, e um
    diagnóstico que falha ao diagnosticar não serve para nada: cada dependência
    é consultada em separado, e a que não responder entra como ``AUSENTE`` sem
    interromper as outras.

    A chave nunca aparece, nem mascarada: o valor de um diagnóstico está em ser
    colável num issue público, e um prefixo de segredo colado num issue público
    continua sendo um segredo vazado. O que se responde é apenas se ela existe.
    """
    relatorio = {
        "jev_crap": __version__,
        "python": platform.python_version(),
        "plataforma": platform.platform(),
        "chave_definida": "sim" if os.environ.get(VARIAVEL_DA_CHAVE) else "nao",
    }
    for nome in DEPENDENCIAS:
        relatorio[nome] = _versao_instalada(nome)
    return relatorio


def _versao_instalada(distribuicao: str) -> str:
    """A versão instalada de um pacote, ou ``AUSENTE``.

    ``PackageNotFoundError`` é o caso esperado — rodar a partir do repositório
    sem instalar as três dependências é normal em desenvolvimento. O ``except``
    largo cobre o resto porque ``importlib.metadata`` lê disco: metadado
    corrompido de uma instalação pela metade levantaria aqui, e derrubar o
    diagnóstico por causa disso esconderia justamente a instalação quebrada que
    ele deveria revelar.
    """
    try:
        return metadata.version(distribuicao)
    except metadata.PackageNotFoundError:
        return AUSENTE
    except Exception:  # noqa: BLE001 - diagnóstico nunca pode ser a causa do erro
        return f"{AUSENTE} (metadado ilegível)"
