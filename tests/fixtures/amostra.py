"""Amostra em Python com complexidade ciclomática conhecida.

Não é código de produção: cada função existe para fixar um número de caminhos
que o teste confere. O arquivo espelha `amostra.js` de propósito — as duas
linguagens têm as mesmas três funções, com os mesmos números, para que o teste
prove que a contagem não depende da linguagem.
"""


def soma(a, b):
    """Um caminho só: complexidade 1."""
    return a + b


def classifica(nota):
    """Três desvios encadeados: complexidade 4 (1 + if + elif + elif)."""
    if nota >= 90:
        return "A"
    elif nota >= 80:
        return "B"
    elif nota >= 70:
        return "C"
    return "F"


class Carrinho:
    """Existe para provar que método dentro de classe também é contado."""

    def total(self, itens):
        """Laço mais condição composta: complexidade 4 (1 + for + if + and)."""
        acumulado = 0
        for item in itens:
            if item.preco > 0 and item.quantidade > 0:
                acumulado += item.preco * item.quantidade
        return acumulado
