# O ciclo de aprendizado

A ferramenta nasce com uma régua herdada: o limiar 30 é convenção da ferramenta
original, não medida deste repositório. O ciclo descrito aqui existe para que
essa régua passe, com o uso, a sair da evidência — e, principalmente, para que a
ferramenta consiga **descobrir mais tarde que errou**.

São quatro etapas: o episódio, os sinais de verdade, as propostas com evidência
e a decisão humana. A última nunca é automática, e a seção final explica por quê.

Código correspondente: `src/jev_crap/aprendizado/`.

---

## 1. O episódio

Um episódio é a fotografia de uma avaliação: o número que saiu, a fórmula que
produziu esse número, o limiar que valia naquele instante, as notas do Jev, o
que foi sugerido e o que a pessoa fez depois.

| Campo | O que guarda |
|---|---|
| `id`, `em` | Identificador e instante (ISO 8601 UTC) |
| `arquivo`, `funcao` | Onde |
| `risco` | O número que a fórmula produziu |
| `formula` | Qual fórmula produziu esse número |
| `limiar_vigente` | A régua daquele dia |
| `complexidade`, `cobertura_linha`, `cobertura_branch` | Os insumos medidos |
| `notas` | O julgamento do Jev por dimensão, como veio |
| `veredito`, `conselho`, `acao` | O que a ferramenta decidiu, recomendou e o que foi feito |
| `nota` | A nota de qualidade 0..100, quando houve julgamento |
| `aceita` | Se a sugestão foi seguida (`None` = ninguém decidiu ainda) |
| `risco_depois` | O risco após a mudança, quando houve mudança |
| `defeito` | Se aquele trecho apresentou defeito depois |

Dois campos merecem atenção porque parecem redundantes e não são:

- **`limiar_vigente`.** Histórico que muda de valor quando o limiar é
  recalibrado não é histórico, é uma projeção do presente sobre o passado.
- **`formula`.** Risco 12 pela fórmula clássica e risco 12 por outra são escalas
  diferentes com o mesmo nome. Sem esse campo, qualquer média mistura unidades.

### Onde mora

`<raiz do projeto>/.jev-crap/episodios.jsonl`, ou o caminho declarado em
`JEV_CRAP_EPISODIOS` (útil em CI ou em sandbox).

O arquivo fica **no projeto analisado**, não no pacote instalado, porque os
episódios calibram um limiar e limiar é propriedade daquele código:
complexidade tolerável num parser de protocolo não é a mesma de um CRUD.
Misturar episódios de projetos diferentes num arquivo global produziria um
limiar médio que não serve a nenhum deles.

O formato é JSONL, uma linha por gravação, sempre em append. Nenhuma escrita
reescreve linha antiga; uma linha corrompida custa um episódio e não o histórico
inteiro; e o arquivo continua legível com `tail` e `grep`, o que importa para
quem quiser auditar o que a ferramenta aprendeu sem abrir cliente de banco.
Linhas ilegíveis são puladas e contadas — o número aparece nos avisos de
`consultar_aprendizado`.

### Como se registra

A tool `registrar_episodio` tem dois modos:

1. **Episódio novo** — `arquivo`, `funcao` e `risco` são obrigatórios; o resto
   (`veredito`, `conselho`, `nota`, `complexidade`, `cobertura_*`, `limiar`,
   `formula`, `notas`) vem copiado do relatório de `avaliar_arquivos`. Devolve
   o `id`.
2. **Desfecho** — `id_episodio` mais só os campos que mudaram (`aceita`,
   `acao`, `risco_depois`, `defeito`).

No segundo modo, os números medidos na época são preservados: medição não se
corrige retroativamente com informação que ainda não existia. Campos deixados
de fora mantêm o valor atual, e é assim que marcar um defeito meses depois não
apaga a decisão registrada na semana da avaliação.

---

## 2. Os sinais de verdade

São dois, e eles não valem a mesma coisa.

### `aceita=false` — falso positivo, barato de coletar

A sugestão foi ignorada. Ninguém precisa escrever relatório nem justificar:
basta registrar que não seguiu o conselho. É o sinal mais barato que existe, e é
o único jeito de uma ferramenta descobrir que incomoda — uma que só registra o
que deu certo nunca descobre.

Vale dizer o óbvio: ignorar uma sugestão tem muitas causas. Falta de tempo,
código legado que ninguém tem autorização para tocar, prioridade em outro lugar.
Por isso esse sinal só vira proposta quando **se repete numa faixa estreita de
risco** (ver adiante), nunca a partir de um caso.

### `defeito=true` — falso negativo, com prova

Semanas depois, aquele trecho apresentou defeito. Esta é a única evidência que
revela o erro caro: código que passou **abaixo** do limiar e quebrou mesmo
assim.

É também o número mais honesto do conjunto, porque só pode ser calculado com
informação que chegou depois, fora do controle de quem escreveu a régua. Um
defeito basta para propor baixar o limiar: diferente do incômodo, que precisa de
repetição para virar sinal, o defeito que escapou já é a evidência completa de
que a régua estava alta demais para aquele código.

---

## 3. O que o histórico mostra

`consultar_aprendizado` devolve, antes de qualquer proposta, uma descrição
numérica do histórico. Essa parte só conta o que aconteceu; não sugere nada.

| Métrica | O que é |
|---|---|
| `total`, `com_decisao`, `aceitas`, `ignoradas` | Contagens brutas |
| `taxa_aceitacao` | Aceitas ÷ episódios com decisão registrada |
| `com_defeito`, `defeitos_acima_do_limiar` | Quantos defeitos, e quantos a régua já apontava |
| `cobertura_de_risco` | Defeitos acima do limiar ÷ defeitos totais |
| `por_veredito`, `por_conselho`, `por_acao` | Distribuição das conclusões |
| `variacao_dimensoes` | Por dimensão do Jev: n, média, desvio, mínimo, máximo, amplitude, valores distintos |
| `por_formula` | Quantos episódios por fórmula (com aviso se houver mais de uma) |

**`cobertura_de_risco` é o número que interessa.** Ele é o inverso do falso
negativo: entre os trechos que depois deram problema, quantos a ferramenta já
apontava como arriscados. É por ele que duas fórmulas de risco se comparam — ver
[`FORMULA.md`](FORMULA.md).

Uma regra atravessa todas as métricas: **ausência vira `None` com aviso, nunca
`0`**. "Nenhuma sugestão foi aceita" e "nenhuma sugestão teve decisão
registrada" são fatos diferentes, e confundi-los produz exatamente o tipo de
conclusão errada que este módulo existe para evitar.

---

## 4. As propostas

Só depois da descrição vêm as propostas de ajuste. Elas têm sempre a mesma
forma:

```json
{
  "tipo": "baixar_limiar",
  "alvo": "limiar",
  "de": 30.0,
  "para": 9.72,
  "motivo": "1 episódio(s) apresentaram defeito com risco abaixo do limiar 30; o menor deles marcava 10.8",
  "evidencia": {
    "defeitos_abaixo_do_limiar": 1,
    "menor_risco_com_defeito": 10.8,
    "riscos": [10.8],
    "folga_aplicada": 0.1
  }
}
```

**Toda proposta carrega o número que a sustenta.** Proposta sem evidência é
palpite com aparência de método, e quem vai aplicar precisa poder discordar do
número, não da conclusão.

### Pré-condições

Antes de qualquer proposta, duas barreiras:

- **30 episódios, no mínimo.** Com 8 avaliações, "60% das sugestões na faixa
  foram ignoradas" significa "3 de 5", e 3 de 5 é ruído. Abaixo do mínimo,
  `consultar_aprendizado` responde `ainda_sem_base` — e devolve as métricas
  assim mesmo, que servem para acompanhar mesmo sem concluir.
- **Uma fórmula só.** As propostas são calculadas apenas sobre a fórmula
  dominante do histórico. Riscos de fórmulas diferentes são escalas diferentes,
  e um limiar calibrado sobre a mistura não vale para nenhuma das duas.

### Os três tipos

**`baixar_limiar`** — há defeito registrado com risco abaixo do limiar. Basta
um. O valor proposto é o menor risco com defeito, menos 10% de folga. A folga
existe porque o risco medido tem ruído (a cobertura muda com o tempo, a
complexidade muda com refatoração): colocar o limiar exatamente em cima do
defeito que escapou deixaria o próximo passar por milésimos.

Exemplo, com limiar 30 em vigor: um episódio que marcava risco 10,80 apresentou
defeito. A proposta é baixar o limiar para `10,80 × 0,9` = **9,72**.

**`subir_limiar`** — a maioria das sugestões na vizinhança imediata do limiar
vem sendo ignorada, e nenhuma delas deu defeito. As condições:

| Condição | Valor padrão |
|---|---|
| Faixa considerada | do limiar até o limiar + 25% |
| Sugestões com decisão na faixa | ao menos 5 |
| Proporção ignorada | ao menos 60% |
| Defeitos na faixa | nenhum |

A faixa é estreita de propósito. Ignorar um alerta de risco 32 quando o limiar é
30 sugere que o limiar está baixo; ignorar um de risco 90 sugere outra coisa
(falta de tempo, legado intocável) e não deveria mexer na régua.

Exemplo, com limiar 30: a faixa vai de 30,00 a **37,50**. Se 6 das 8 sugestões
com decisão registrada nessa faixa foram ignoradas (75%) e nenhuma apresentou
defeito depois, a proposta é subir o limiar para 37,50.

Um único defeito dentro da faixa derruba a proposta inteira: subir o limiar
esconderia justamente o caso que a ferramenta acertou, e falso negativo custa
mais caro que incômodo.

**`remover_dimensao`** — uma pergunta do Jev cuja nota quase não varia. Exige ao
menos 10 notas registradas e amplitude de no máximo 0,05 na escala 0..2. Uma
dimensão que responde quase sempre a mesma coisa não separa código bom de código
ruim: só acrescenta latência, custo de chamada e uma coluna a mais no relatório.
A proposta é remover; a decisão pode ser outra — talvez a pergunta esteja mal
formulada e valha reescrevê-la —, e é por isso que a evidência vem junto.

### Quando as duas de limiar aparecem juntas

É possível. Significa que o histórico mostra defeito abaixo do limiar **e**
irritação logo acima dele — sinal de que o problema está na fórmula, que está
ordenando mal os casos, e não no limiar, que não tem posição capaz de agradar
aos dois fatos.

O conflito é informação, então as duas propostas vão para a pessoa em vez de uma
ser suprimida em silêncio.

### Quando não há proposta nenhuma

A lista vazia sabe dizer por que está vazia, no campo `motivo_das_propostas`.
São duas situações que pedem reações opostas:

- `ainda não há base: N episódio(s) registrados, 30 é o mínimo...` — continue
  usando e registrando;
- `histórico de N episódio(s) não indica ajuste: nenhum defeito abaixo do
  limiar, nenhuma faixa majoritariamente ignorada e nenhuma dimensão parada` —
  deixe como está.

---

## Por que nada é aplicado automaticamente

`propor` não escreve arquivo, não altera configuração e não tem efeito colateral
nenhum. A proposta vira mudança quando uma pessoa altera `JEV_CRAP_LIMIAR` (ou o
que mais a proposta indicar). Quatro razões:

1. **O alvo se moveria sozinho.** Uma ferramenta que recalibra os próprios
   critérios acaba provando que está certa contra uma régua que ela mesma mudou.
   Nenhuma medição posterior seria evidência de nada.

2. **A amostra é enviesada por construção.** Só funções acima do limiar são
   julgadas e viram episódio. Subir o limiar automaticamente reduz o que a
   ferramenta passa a observar, o que reduz os sinais contrários, o que
   justificaria subir de novo. O laço se fecha sozinho e sempre na mesma
   direção.

3. **Os dois erros não custam o mesmo.** Falso positivo custa incômodo e é
   percebido na hora. Falso negativo custa defeito em produção e só aparece
   semanas depois. Automatizar o ajuste trataria os dois como simétricos, e eles
   não são.

4. **"Ignorou" não quer dizer "estava errado".** As causas mais comuns de uma
   sugestão ser ignorada — prazo, código de terceiro, decisão de não mexer —
   não dizem nada sobre a régua. Quem sabe distinguir isso é quem estava lá.

O que a ferramenta faz é o que uma ferramenta pode fazer com honestidade:
mostrar o número, mostrar a evidência e dizer o que ela autorizaria mudar.
Agente propõe; gente decide.

---

## Uma sessão típica

1. **Avaliar.** `avaliar_arquivos(["src"], "coverage.lcov")` com o limiar padrão
   30 aponta uma função com complexidade 13 e 50% de cobertura de linha: risco
   **34,13**, acima do limiar. O Jev responde que a complexidade é essencial e
   que os testes não verificam comportamento → veredito `revisar`, conselho
   "escrever teste, não refatorar: a complexidade vem do domínio".

2. **Registrar.** `registrar_episodio` com os campos copiados do relatório.
   Devolve o `id`.

3. **Decidir e anotar o desfecho.** Se o teste foi escrito:
   `registrar_episodio(id_episodio=..., aceita=true, acao="teste de borda e erro
   adicionado", risco_depois=...)`. Se o time decidiu não mexer agora:
   `aceita=false`, e só. Os dois são úteis; o segundo é o que a ferramenta não
   consegue descobrir sozinha.

4. **Anotar o que aparecer depois.** Um defeito naquele trecho, semanas à
   frente: `registrar_episodio(id_episodio=..., defeito=true)`.

5. **Consultar.** Passados 30 episódios, `consultar_aprendizado` mostra a taxa
   de aceitação, a cobertura de risco e — se o histórico sustentar — as
   propostas, cada uma com o número que a justifica. A decisão continua sendo
   de quem lê.
