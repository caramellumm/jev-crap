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

As três partes obrigatórias são conferidas na construção, não confiadas ao
chamador. Uma situação sem ``como_resolver`` atravessaria o transporte inteiro
e só apareceria como ``"Como resolver: "`` na tela do agente — tarde demais
para descobrir quem a levantou. Falhar na linha do ``raise`` aponta o culpado.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SituacaoConhecida"]

#: As chaves que ``para_corpo_de_erro`` sempre escreve. ``**detalhes`` não consegue
#: capturá-las — são parâmetros nomeados de ``__init__`` —, mas a lista existe
#: para que ``_detalhe_serializavel`` não precise adivinhar o contrato.
PARTES_OBRIGATORIAS = ("situacao", "explicacao", "como_resolver")

#: O que o ``json.dumps`` do cliente MCP aceita sem conversão. Qualquer outra
#: coisa vira texto na fronteira: um ``Path`` em ``detalhes`` derrubaria a
#: serialização da resposta *inteira*, trocando um erro explicado por um erro
#: de protocolo — e o agente perderia justamente a instrução de como resolver.
TIPOS_JSON = (str, int, float, bool, type(None))

#: Até onde ``_detalhe_serializavel`` desce numa estrutura aninhada. O limite
#: existe contra o caso patológico — uma lista que contém a si mesma —, que
#: estouraria a pilha justamente enquanto a ferramenta tenta explicar outro
#: erro. Cinco níveis cobrem qualquer `detalhes` que este projeto produz.
PROFUNDIDADE_MAXIMA = 5


def _parte_valida(nome: str, valor: Any) -> str:
    """Texto não vazio, ou ``ValueError`` dizendo qual parte falta.

    Conferir aqui é o que torna ``como_resolver`` obrigatório de fato. Sem isso
    a única consequência de esquecê-lo seria uma linha vazia no meio da
    mensagem, e ninguém liga uma linha vazia a um ``raise`` três módulos acima.
    """
    if not isinstance(valor, str):
        raise ValueError(
            f"{nome} de SituacaoConhecida precisa ser texto, veio {type(valor).__name__}"
        )
    limpo = valor.strip()
    if not limpo:
        raise ValueError(f"{nome} de SituacaoConhecida não pode ser vazio")
    return limpo


def _detalhe_serializavel(valor: Any, profundidade: int = 0) -> Any:
    """O valor como veio, se o JSON o aceita; senão, o texto dele.

    Converter é melhor que recusar: ``detalhes`` é contexto extra, e perder a
    resposta inteira porque alguém anexou um ``Path`` seria trocar um problema
    pequeno por um grande.

    Duas falhas são tratadas aqui porque acontecem no caminho de erro, onde
    levantar de novo apagaria a situação original:

    - **estrutura funda ou cíclica.** ``detalhes`` vem de quem chamou; uma
      lista que contém a si mesma faria a recursão estourar a pilha. Acima de
      ``PROFUNDIDADE_MAXIMA`` o valor vira texto e a descida para;
    - **``__str__`` que levanta.** Objeto de terceiro pode falhar ao virar
      texto. O ``repr`` do tipo ainda diz o que era, e é melhor que nada.
    """
    if isinstance(valor, TIPOS_JSON):
        return valor
    if profundidade >= PROFUNDIDADE_MAXIMA:
        return _texto_seguro(valor)
    if isinstance(valor, (list, tuple)):
        return [_detalhe_serializavel(item, profundidade + 1) for item in valor]
    if isinstance(valor, dict):
        return {
            _texto_seguro(chave): _detalhe_serializavel(item, profundidade + 1)
            for chave, item in valor.items()
        }
    return _texto_seguro(valor)


def _texto_seguro(valor: Any) -> str:
    """``str(valor)``, e o nome do tipo quando nem isso funciona.

    Existe separado porque é chamado de três lugares e porque o ``except`` aqui
    é largo de propósito: qualquer coisa que ``__str__`` levante — inclusive
    ``RecursionError`` — precisa virar texto, já que quem espera esta resposta
    está tratando outro erro.
    """
    try:
        return str(valor)
    except Exception:  # noqa: BLE001 - formatar detalhe nunca pode derrubar o erro real
        return f"<{type(valor).__name__} não textualizável>"


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
        """Valida as três partes antes de existir como exceção.

        ``ValueError`` e não ``SituacaoConhecida``: construir mal esta classe é
        defeito de quem chamou, não situação que o usuário da ferramenta possa
        resolver. Tratá-lo como situação conhecida o mandaria para o agente
        como se fosse problema dele.
        """
        self.situacao = _parte_valida("situacao", situacao)
        self.explicacao = _parte_valida("explicacao", explicacao)
        self.como_resolver = _parte_valida("como_resolver", como_resolver)
        self.detalhes: dict[str, Any] = {
            str(chave): _detalhe_serializavel(valor)
            for chave, valor in detalhes.items()
            if chave not in PARTES_OBRIGATORIAS
        }
        super().__init__(f"{self.situacao}: {self.explicacao}")

    def _parte(self, nome: str) -> str:
        """A parte pedida, ou um marcador dizendo que ela falta.

        ``getattr`` com padrão, e não ``self.<nome>``, por causa da subclasse
        que esquece o ``super().__init__()``: o atributo simplesmente não
        existe, e um ``AttributeError`` aqui trocaria a situação que a
        ferramenta sabe explicar por uma que ela não sabe.
        """
        valor = getattr(self, nome, None)
        if isinstance(valor, str) and valor.strip():
            return valor
        return f"<{nome} ausente: SituacaoConhecida construída sem super().__init__()>"

    def _detalhes_seguros(self) -> dict[str, Any]:
        """``self.detalhes`` quando é dicionário; vazio quando não é.

        Mesma razão de ``_parte``: subclasse que não chamou o construtor, ou
        que sobrescreveu ``detalhes`` com outra coisa, não pode fazer a
        formatação do erro estourar.
        """
        detalhes = getattr(self, "detalhes", None)
        if not isinstance(detalhes, dict):
            return {}
        return {
            chave: valor
            for chave, valor in detalhes.items()
            if chave not in PARTES_OBRIGATORIAS
        }

    def para_corpo_de_erro(self) -> dict[str, Any]:
        """O corpo que o servidor MCP serializa. Sempre com as três partes.

        A montagem é em duas etapas e nesta ordem — detalhes primeiro, partes
        obrigatórias por cima — justamente para que nenhum caminho consiga
        apagar a instrução de como resolver, que é a única parte da resposta
        que serve para agir. Fazer o contrário (``{**fixas, **detalhes}``)
        dependeria de ``_detalhes_seguros`` nunca falhar no filtro; assim a
        garantia é da própria ordem e não precisa de confiança.

        O ``except`` largo cobre o detalhe hostil que escapou da construção:
        um dicionário cuja iteração levanta deixaria a tool devolver erro de
        protocolo em vez do erro que ela sabe explicar.
        """
        corpo: dict[str, Any] = {}
        try:
            corpo.update(self._detalhes_seguros())
        except Exception:  # noqa: BLE001 - detalhe ruim não pode apagar a situação
            corpo = {"detalhes_ilegiveis": True}
        corpo["situacao"] = self._parte("situacao")
        corpo["explicacao"] = self._parte("explicacao")
        corpo["como_resolver"] = self._parte("como_resolver")
        return corpo

    def para_texto(self) -> str:
        """Uma linha por parte, que é como a mensagem chega ao cliente MCP.

        Sem detalhes saem duas linhas, não uma lista vazia pendurada no fim: o
        texto é lido por um modelo decidindo o próximo passo, e um rodapé em
        branco é ruído que ele precisa interpretar.

        Nenhum valor é interpolado direto: ``_texto_seguro`` cobre o objeto
        cujo ``__str__`` levanta. Um formatador de erro que falha ao formatar
        destrói o diagnóstico original, que é o pior desfecho possível aqui.
        """
        partes = [
            f"{self._parte('situacao')}: {self._parte('explicacao')}",
            f"Como resolver: {self._parte('como_resolver')}",
        ]
        partes.extend(
            f"{_texto_seguro(chave)}: {_texto_seguro(valor)}"
            for chave, valor in self._detalhes_seguros().items()
        )
        return "\n".join(partes)
