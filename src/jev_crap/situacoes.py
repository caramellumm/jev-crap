"""Falha prevista vira situação com nome, não exceção de biblioteca.

Quem lê o erro de uma tool MCP é um modelo decidindo o próximo passo, do outro
lado de um transporte stdio. ``FileNotFoundError: coverage.xml`` não diz o que
fazer; ``cobertura_inexistente`` mais "rode ``pytest --cov`` e aponte o arquivo
gerado" diz. A diferença entre as duas é a diferença entre uma ferramenta que
"não funciona" e uma que ensina como usá-la.

A distinção que este módulo sustenta, e que o servidor aplica:

- **situação previsível e recuperável** vira erro nomeado com ``como_resolver``.
  O cliente MCP a recebe como erro de verdade (``isError``), porque foi isso que
  aconteceu — devolver um dicionário de sucesso contendo uma falha faria o
  agente tratar o fracasso como resultado;
- **estado normal que muda a leitura** (não há cobertura de branch, o eixo
  semântico está desligado) **não** é erro: vira campo no relatório, porque a
  resposta continua válida e apenas vale menos.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SituacaoConhecida"]


class SituacaoConhecida(Exception):
    """Falha que a ferramenta previu e sabe explicar.

    ``como_resolver`` não é opcional por desenho: uma situação que ninguém sabe
    resolver não merece nome próprio — ela é um defeito, e defeito vai para o
    log com traceback, não para a resposta da tool.
    """

    def __init__(
        self,
        situacao: str,
        explicacao: str,
        como_resolver: str,
        **detalhes: Any,
    ) -> None:
        super().__init__(f"{situacao}: {explicacao}")
        self.situacao = situacao
        self.explicacao = explicacao
        self.como_resolver = como_resolver
        self.detalhes = detalhes

    def para_dict(self) -> dict[str, Any]:
        return {
            "situacao": self.situacao,
            "explicacao": self.explicacao,
            "como_resolver": self.como_resolver,
            **self.detalhes,
        }

    def para_texto(self) -> str:
        """Uma linha por parte, que é como a mensagem chega ao cliente MCP."""
        partes = [f"{self.situacao}: {self.explicacao}", f"Como resolver: {self.como_resolver}"]
        partes.extend(f"{chave}: {valor}" for chave, valor in self.detalhes.items())
        return "\n".join(partes)
