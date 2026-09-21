"""Lê um `.env` para dentro do ambiente do processo, sem sobrescrever nada.

Feito à mão, sem python-dotenv: a única coisa que este projeto precisa do
arquivo é uma linha ``NOME=valor``, e uma dependência a mais é uma a mais para
instalar no CI antes que a primeira avaliação rode.

Três decisões que mudam o resultado:

- **o ambiente vence o arquivo** (``setdefault``). No CI a chave chega por
  secret; se o arquivo sobrescrevesse, um `.env` esquecido no disco trocaria
  silenciosamente a chave da organização pela de alguém. É também o que torna
  seguro chamar isto dentro do servidor MCP: o que o cliente passou por
  configuração continua valendo;
- **a procura sobe os diretórios pais**, porque rodar de dentro de ``src/`` é
  comum e o `.env` mora na raiz — olhar só o diretório atual falharia
  exatamente aí, e falharia dizendo "defina a variável", que é o diagnóstico
  errado;
- **aspas em volta do valor são do formato do arquivo, não do segredo.** Sem
  tirá-las, a chave vai para o cabeçalho com as aspas juntas e a API responde
  401 sem dizer por quê. Já ``#`` no fim da linha **não** vira comentário: é
  caractere válido dentro de uma chave, e cortar ali corromperia o valor.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ARQUIVO_ENV", "carregar_env"]

ARQUIVO_ENV = ".env"


def carregar_env(inicio: Path | str | None = None) -> Path | None:
    """Exporta para o ambiente o que estiver num `.env`. Devolve o arquivo lido.

    Devolve ``None`` quando não achou nenhum — e essa diferença importa na hora
    de explicar a falta da chave: "não existe `.env` nenhum" e "existe um e ele
    não tem a chave" produzem a mesma tela em branco e pedem ações opostas.
    """
    partida = Path(inicio or Path.cwd()).resolve()
    for pasta in (partida, *partida.parents):
        arquivo = pasta / ARQUIVO_ENV
        if arquivo.is_file():
            break
    else:
        return None

    try:
        texto = arquivo.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for linha in texto.splitlines():
        linha = linha.strip().removeprefix("export ").lstrip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        nome, valor = nome.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if nome:
            os.environ.setdefault(nome, valor)
    return arquivo
