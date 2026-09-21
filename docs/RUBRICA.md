# A régua

O que a ferramenta pergunta ao Jev, o que ela faz com cada resposta, e por que
os grupos têm papéis diferentes.

A régua mora em [`perguntas.json`](../src/jev_crap/julgamento/perguntas.json) —
mexer nela é revisão de texto, não de código. A tool `explicar_criterios`
devolve a régua inteira em vigor, sem custo e sem rede.

---

## Onze perguntas, quatro papéis

| grupo | dimensões | o que acontece com o resultado |
|---|---|---|
| **qualidade** | `complexidade_cognitiva` 0.30 · `teste_verifica` 0.25 · `manutenibilidade` 0.25 · `tratamento_de_erros` 0.20 | formam a **nota** (são compensáveis entre si) |
| **contexto** | `consequencia_de_falha` · `complexidade_essencial` | decidem o **conselho** e a **prioridade** |
| **risco grave** | `exec_dinamica` · `injecao` | **barram**, e ficam fora da nota |
| **risco atenção** | `entrada_nao_validada` · `retorno_inconsistente` · `caso_limite_nao_tratado` | mandam para revisão, **nunca** barram |

### Por que os gates ficam fora da nota

Nota é compensável por definição: é média ponderada. Uma função com injeção de
SQL e nota 95 continua sendo uma função com injeção de SQL. Se o risco entrasse
como peso, legibilidade boa compensaria vulnerabilidade — e o número deixaria de
significar o que parece significar.

Medido neste repositório: os gates graves marcaram **0.02 a 0.08** em 8 funções
reais e **0.98/0.99** num trecho com `os.system` concatenado e `eval`. É essa
distância que torna o bloqueio automático confiável: ele só se sustenta enquanto
for raro e certo, e por isso o limiar padrão é alto (0.80).

### Por que as de atenção nunca barram

"Existe entrada plausível que quebraria isto?" é verdade para quase toda função
escrita em linguagem dinâmica. Medido na geração anterior da ferramenta: 125 de
145 funções passavam de 0.50 e 32 passavam de 0.80 — entre elas uma função de 5
linhas com 100% de cobertura. Como gate, isso não separa nada; como lista de
revisão, é informação legítima.

### `teste_verifica` e a regra do "sem dado"

A pergunta sobre teste só é feita quando há trecho de teste para mostrar
(`exige: testes`). Sem evidência, o peso dela é **redistribuído** entre as
outras três.

Perguntar sem material devolveria "não há teste" para uma função testada
indiretamente — e cobraria por isso um quarto da nota. A mesma regra vale para a
cobertura: ausência de dado é `SEM_DADOS`, nunca 0.0.

---

## Como a resposta vira veredito

| veredito | quando | exit code da CLI |
|---|---|---|
| `aprovar` | nenhum gate, nenhuma dúvida, nota ≥ `JEV_CRAP_NOTA_MINIMA` | 0 |
| `revisar` | alguma dúvida, ou nota abaixo do mínimo | 1 |
| `bloquear` | algum gate grave ≥ `JEV_CRAP_BLOQUEIO`, ou tamanho acima do limite | 2 |
| `sem_julgamento` | o eixo semântico não rodou. **Não é aprovação** | 1 |

A **faixa** resume a nota em quatro degraus — `sólido` (≥80) · `aceitável`
(≥60) · `frágil` (≥40) · `ruim` — e é o que você deve usar quando precisa de um
segundo dado. O decimal não: 72.4 e 75.1 são a mesma coisa, e tratá-los como
diferentes é ler ruído.

`graves` barra, `duvidas` não. Os dois campos vêm com o motivo escrito, para que
você discorde olhando a evidência e não a conclusão.

---

## As seis tools

| tool | o que faz | rede | custa |
|---|---|---|---|
| `avaliar_arquivos` | mede tudo, filtra pelo limiar, julga o recorte, decide | sim | 1 chamada por função julgada |
| `avaliar_trecho` | julga código que você tem em mãos e ainda não salvou | sim | 1 chamada |
| `medir_risco` | só o eixo contável, sem julgamento | não | nada |
| `explicar_criterios` | a régua em vigor: perguntas, níveis, pesos, limiares | não | nada |
| `registrar_episodio` | guarda o que a avaliação disse e o que foi feito depois | não | nada |
| `consultar_aprendizado` | o que o histórico mostra e o que ele autoriza mudar | não | nada |

Todas declaram `readOnlyHint` e `openWorldHint`, para o cliente saber quais só
leem e quais custam dinheiro. Só `registrar_episodio` grava — num JSONL local.

Os nomes são em português porque quem mantém o projeto é brasileiro e o idioma
do código é o mesmo da documentação.

---

## O funil, e por que ele é barato

1. **Mede tudo.** O [lizard](https://github.com/terryyin/lizard) extrai a
   complexidade ciclomática de cada função. É local, grátis e determinístico.
2. **Cruza com a cobertura.** O relatório LCOV ou Cobertura XML é lido e
   recortado por faixa de linhas de cada função.
3. **Calcula o risco.** `cc² × (1 − cobertura)³ + cc`, limiar 30 — a fórmula
   clássica do CRAP, que é trocável (ver [FORMULA.md](FORMULA.md)).
4. **Recorta.** Só as funções acima do limiar seguem adiante, no máximo 20 por
   avaliação. É esse recorte que mantém o custo baixo o bastante para rodar num
   projeto de verdade.
5. **Julga o recorte.** Uma requisição por função, com todas as perguntas juntas.
6. **Devolve a lista ordenada** por risco, com veredito, nota, prioridade e o
   conselho — sempre com os números que sustentam a conclusão.

O que dá para contar não vira pergunta. Perguntar trocaria certeza por
distribuição de probabilidade, e custaria dinheiro para isso.
