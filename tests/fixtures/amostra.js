// Amostra em JavaScript com complexidade ciclomática conhecida.
//
// Não é código de produção: cada função existe para fixar um número de
// caminhos que o teste confere. O arquivo espelha `amostra.py` de propósito —
// as duas linguagens têm as mesmas três funções, com os mesmos números, para
// que o teste prove que a contagem não depende da linguagem.

function soma(a, b) {
  // Um caminho só: complexidade 1.
  return a + b;
}

function classifica(nota) {
  // Três desvios encadeados: complexidade 4 (1 + if + else if + else if).
  if (nota >= 90) {
    return "A";
  } else if (nota >= 80) {
    return "B";
  } else if (nota >= 70) {
    return "C";
  }
  return "F";
}

class Carrinho {
  // Existe para provar que método dentro de classe também é contado.
  total(itens) {
    // Laço mais condição composta: complexidade 4 (1 + for + if + &&).
    let acumulado = 0;
    for (const item of itens) {
      if (item.preco > 0 && item.quantidade > 0) {
        acumulado += item.preco * item.quantidade;
      }
    }
    return acumulado;
  }
}
