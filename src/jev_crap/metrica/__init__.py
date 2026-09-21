"""Eixo contável: complexidade, cobertura e o risco que sai do cruzamento dos dois.

Os três submódulos não se conhecem de propósito — ``risco`` repete a sentinela
``SEM_DADOS`` em vez de importá-la de ``cobertura``, porque nenhum dos dois
precisa do outro para existir. Quem cruza os eixos é ``jev_crap.avaliacao``.

O pacote publica os nomes dos três sob demanda (ver ``jev_crap._reexport``).
``SEM_DADOS`` vem de ``cobertura``: os dois valores são iguais por contrato, e
escolher uma origem evita que o pacote publique dois nomes com o mesmo rótulo.
"""

from jev_crap._reexport import conferir_mapa, importar_publicado

#: Onde cada nome publicado mora de fato. Conferido contra ``__all__`` na
#: importação do pacote.
_ORIGEM = {
    "SEM_DADOS": "cobertura",
    "CoberturaArquivo": "cobertura",
    "FormatoDeCoberturaDesconhecido": "cobertura",
    "cobertura_de_faixa": "cobertura",
    "ler": "cobertura",
    "ler_cobertura_xml": "cobertura",
    "ler_lcov": "cobertura",
    "EXCLUSOES_PADRAO": "complexidade",
    "Funcao": "complexidade",
    "analisar": "complexidade",
    "medir_fonte": "complexidade",
    "CrapClassico": "risco",
    "Formula": "risco",
    "Insumos": "risco",
    "formulas_disponiveis": "risco",
    "obter_formula": "risco",
    "registrar_formula": "risco",
}

__all__ = [
    "CoberturaArquivo",
    "CrapClassico",
    "EXCLUSOES_PADRAO",
    "Formula",
    "FormatoDeCoberturaDesconhecido",
    "Funcao",
    "Insumos",
    "SEM_DADOS",
    "analisar",
    "cobertura_de_faixa",
    "formulas_disponiveis",
    "ler",
    "ler_cobertura_xml",
    "ler_lcov",
    "medir_fonte",
    "obter_formula",
    "registrar_formula",
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
