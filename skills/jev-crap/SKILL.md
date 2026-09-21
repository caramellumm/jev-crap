---
name: jev-crap
description: 'Avalia e melhora qualidade de código cruzando duas evidências que, sozinhas, mentem — a métrica contável (complexidade ciclomática × cobertura de testes, via lizard e relatórios LCOV ou Cobertura XML, o que vale para Python, JavaScript, TypeScript, Go, Java, C#, Rust e mais 20 linguagens) e o julgamento do modelo Jev sobre o que a métrica não enxerga: se a complexidade é essencial ao domínio ou acidental, se os testes verificam comportamento ou só executam linhas, quanto custa ler o código, se ele trata falha, qual o estrago se quebrar em produção, e se há execução dinâmica ou injeção. Use ao terminar de implementar uma funcionalidade, antes de abrir PR, ao revisar um diff ou código de outra pessoa, logo depois de escrever código novo que ainda não foi salvo, quando pedirem para "melhorar", "refatorar", "reduzir complexidade", "limpar", "deixar mais testável" ou "avaliar a qualidade" de um trecho, e sempre que perguntarem se um código está bom, onde ele é arriscado, o que testar primeiro ou por onde começar a mexer num arquivo legado. Vale mesmo quando ninguém citar métrica, CRAP ou cobertura — a pergunta "isso aqui tá bom?" já é o gatilho.'
---

# jev-crap — avaliar qualidade sem confiar em um eixo só

Esta skill ensina o **método**; o servidor MCP `jev-crap` dá as **ferramentas**.

| tool | o que faz | custa |
|---|---|---|
| `medir_risco` | só o eixo contável: complexidade × cobertura → número de risco | nada, sem rede |
| `avaliar_arquivos` | mede tudo, filtra pelo limiar, julga o recorte, cruza os eixos | 1 chamada por função julgada |
| `avaliar_trecho` | julga código que você tem em mãos e ainda não salvou | 1 chamada |
| `explicar_criterios` | a régua em vigor: perguntas, níveis, pesos, limiares | nada, sem rede |
| `registrar_episodio` | guarda o que a avaliação disse e o que foi feito depois | nada |
| `consultar_aprendizado` | o que o histórico mostra e o que ele autoriza mudar | nada |

## Por que dois eixos

O número (complexidade ciclomática cruzada com cobertura) é barato,
determinístico e conta caminhos e linhas. Ele não sabe se os caminhos vêm do
domínio ou da escrita, nem se os testes conferem resultado ou apenas executam a
linha. Duas funções em situações opostas — uma que precisa de teste, outra que
precisa ser reescrita — produzem exatamente o mesmo risco. Quem separa é o Jev,
e é por isso que ele é consultado; mas ele custa uma chamada por função, e por
isso só olha o que o número já apontou.

Nenhum dos dois manda sozinho. O número sem julgamento vira meta de planilha; o
julgamento sem número vira opinião sobre o arquivo que por acaso foi aberto.

## Como esta skill se comporta

Avaliação não é autorização para editar. Quem pediu "avalia isso" pediu
avaliação: entregue o diagnóstico com a evidência e a proposta da menor mudança,
e mexa no código quando o pedido for mudar o código. Trocar uma coisa pela outra
faz a pessoa revisar um diff que ela não pediu, no lugar de decidir o que quer.

Pelo mesmo motivo, nada aqui é afirmado sem o número que sustenta. Uma nota com
confiança baixa vira "vale um olhar humano", não vira veredito (ver
[Confiança baixa](#confiança-baixa-vale-um-olhar-humano)).

---

## 0. Quando não rodar

Rodar por reflexo custa chamada de API, custa tempo de quem lê e — o pior —
polui o histórico de episódios, que é o que faz o limiar deste projeto sair de
evidência em vez de convenção. Não rode quando:

- **o código não mudou desde a última avaliação.** O relatório seria idêntico.
  Se alguém quer revisitar a decisão anterior, releia o relatório anterior.
- **a mudança foi só de forma**: formatação, renomeação, import reorganizado,
  comentário. Complexidade ciclomática não muda com espaço em branco e cobertura
  não muda de dono; a rodada confirmaria que nada mudou pagando por isso.
- **não existe relatório de cobertura e não dá para gerar agora.** Sem ele todas
  as funções entram sem dados, o risco é calculado como se nada estivesse
  coberto e tudo sobe por igual — um relatório em que tudo é vermelho não ordena
  nada. Diga isso e ofereça rodar depois da suíte, em vez de avaliar sobre um
  número que você sabe estar errado.
- **o contexto é insuficiente**: um trecho colado sem os testes, sem o módulo em
  volta e sem saber o que a função significa no sistema. As perguntas do Jev são
  sobre domínio e consequência; sem contexto a resposta é chute com aparência de
  nota. Peça o arquivo, ou avalie o que dá e declare o que ficou de fora.
- **a pergunta era outra**: achar um bug, entender o que o código faz, resolver
  lentidão. Qualidade não responde nenhuma das três.

Dizer "não vale avaliar agora, e este é o motivo" é resposta completa. Procurar
problema para justificar a rodada é como a ferramenta perde credibilidade.

## 1. Medir o risco

Meça **tudo** primeiro. Medir é grátis e determinístico, então não há motivo
para escolher o que medir — e escolher a dedo já seria o viés que a métrica
existe para evitar.

- `avaliar_arquivos(caminhos, cobertura, testes)` quando o objetivo é decidir o
  que fazer: ele já mede, filtra, julga o recorte e cruza os eixos.
- `medir_risco(caminhos, cobertura)` quando você quer só o número: varredura de
  repositório grande, verificação em CI, comparação antes/depois de uma mudança,
  ou quando não há chave do Jev.
- `avaliar_trecho(codigo)` quando o código ainda não está no disco — o caso
  típico de quem acabou de escrever uma função e quer saber se ela presta antes
  de salvar.

O parâmetro `cobertura` é o caminho de um relatório **LCOV (`.info`)** ou
**Cobertura XML** que a suíte gerou:

```bash
# Python
pytest --cov=src --cov-branch --cov-report=xml      # gera coverage.xml
# JavaScript / TypeScript
jest --coverage --coverageReporters=lcov            # gera coverage/lcov.info
# Go
go test -coverprofile=c.out ./... && gcov2lcov -infile=c.out -outfile=lcov.info
```

Peça **cobertura de branch** sempre que o gerador souber produzi-la
(`--cov-branch` e equivalentes). Cobertura de linha declara coberto um `if` sem
`else` cujo ramo nunca rodou — e é justamente o ramo não exercitado que carrega
o risco. Quando o relatório não traz branch, a ferramenta avisa; repasse esse
aviso, porque ele muda o quanto o número merece confiança.

Passe `testes` com a pasta dos testes sempre que ela existir. É de lá que saem
os trechos que o Jev lê. Sem ela, a pergunta sobre teste **não é feita** e o peso
dela é redistribuído — o que é diferente de "os testes são ruins".

Ajustes que valem por projeto vivem em variáveis de ambiente do servidor
(`JEV_CRAP_LIMIAR`, `JEV_CRAP_FORMULA`, `JEV_CRAP_MAX_JULGAMENTOS`,
`JEV_CRAP_BLOQUEIO`, `JEV_CRAP_SUSPEITA`, `JEV_CRAP_RAIZ`, `JEV_CRAP_EXCLUIR`).
`explicar_criterios` mostra os valores em vigor. Mudá-los é decisão de quem
mantém o projeto, não ajuste de conveniência no meio de uma avaliação.

## 2. Filtrar pelo limiar

O limiar é **corte de atenção, não nota de aprovação**. Acima dele a função
merece ser olhada; abaixo dele ela não foi aprovada, apenas não foi investigada.

O padrão da fórmula clássica é 30 — valor herdado da ferramenta original, não
medida de nada. É exatamente por isso que existe o passo 10: com histórico
suficiente, o limiar deste projeto passa a sair da evidência dele.

Três leituras do resultado que costumam ser puladas:

- **nada passou do limiar** → a resposta é "nada a fazer agora", e ela está
  completa. Não desça o limiar até aparecer algo.
- **quase tudo passou** → desconfie do relatório de cobertura antes de concluir
  que o projeto inteiro é ruim. Olhe `avisos`: quando *nenhum* arquivo casa com
  o relatório, quase sempre é diferença de raiz entre o caminho do CI e o
  caminho local, não ausência de teste.
- **o filtro cega um caso real.** Complexidade baixa com código ilegível não
  passa do limiar e não é julgada. Quando essa é justamente a suspeita, passe
  `limiar=0` — é mais caro e está documentado como tal.

## 3. Julgar só o que passou

`avaliar_arquivos` já faz o recorte sozinho: julga apenas as funções acima do
limiar, no máximo `JEV_CRAP_MAX_JULGAMENTOS` delas, sempre as de maior risco.
Quando o teto corta alguém, o relatório diz quantas ficaram de fora — repasse
esse número, porque uma lista silenciosamente truncada vira decisão tomada sem
saber.

Use `avaliar_trecho(codigo, arquivo, funcao, testes)` quando já tiver o texto em
mãos: código recém-escrito, revisão de diff, trecho colado na conversa, ou
quando o número e a sua impressão ao ler o código discordam — essa discordância
costuma ser informação, não erro. O `arquivo` serve para dar a **extensão
certa** (é ela que escolhe a linguagem); o arquivo não é lido do disco.

Inverter a ordem (julgar tudo e depois medir) daria o mesmo relatório por um
preço proporcional ao tamanho do repositório. A filtragem no meio é o que torna
a ferramenta usável num projeto de verdade.

## 4. Ler o que voltou

**O atalho:** `veredito` responde "está bom?" — `aprovar` (nada a fazer),
`revisar` (o motivo está em `duvidas`), `bloquear` (o motivo está em `graves`),
`sem_julgamento` (não foi avaliado; **não é aprovação**). Se não for `aprovar`,
`conselho` já traz a ação. Para reportar, use a `faixa`, nunca o decimal da nota.

O resto desta seção é o porquê de cada um desses campos, para quando alguém
discordar do veredito.

O retorno separa quatro coisas que **não** se misturam. Confundi-las é o erro
mais comum ao ler este relatório.

### A nota (0 a 100) — ordena, não mede

Média ponderada de quatro dimensões **compensáveis**: legibilidade boa compensa
tratamento de erro mediano.

| dimensão | peso | nota alta quer dizer |
|---|---|---|
| `complexidade_cognitiva` | 0.30 | o fluxo se segue sem guardar estado na cabeça |
| `teste_verifica` | 0.25 | os testes conferem resultado, borda e erro |
| `manutenibilidade` | 0.25 | dá para mudar uma parte sem quebrar outra, com rede |
| `tratamento_de_erros` | 0.20 | entrada inválida e falha têm caminho próprio |

**Leia a `faixa`, não o decimal.** A calibração numérica de um score é fraca por
construção do modelo: 72.4 e 75.1 são o mesmo `aceitável`, e tratar a diferença
entre eles como informação é ler precisão que não existe. As faixas são
`sólido` (≥80), `aceitável` (≥60), `frágil` (≥40) e `ruim`.

Quando uma dimensão não foi perguntada, ela aparece em `nao_observadas` e o peso
dela é **redistribuído** entre as outras — a nota não cai por isso. Nota sobre
três dimensões é honesta; nota sobre quatro com uma inventada, não.

### Os gates graves — barram, e ficam fora da nota

`exec_dinamica` e `injecao`. Risco não se compensa com legibilidade boa: uma
função com injeção de SQL e nota 95 continua sendo uma função com injeção de
SQL. São proposições que ou valem ou não valem, e acima de 0.80 o veredito é
`bloquear`, independentemente da nota.

Eles são confiáveis justamente porque são raros. Medido neste projeto: 0 de 8
funções reais dispararam (probabilidades entre 0.02 e 0.08), contra 0.98 e 0.99
num trecho com `os.system` concatenado e `eval`. Separação dessa ordem é o que
torna o bloqueio automático usável.

Tamanho também é gate, e contável: acima de 2000 linhas a nota vai a zero
independentemente do que o modelo tenha achado do trecho que viu.

### As dúvidas — mandam para olho humano, nunca barram

`entrada_nao_validada`, `retorno_inconsistente` e `caso_limite_nao_tratado` são
perguntas graduais disfarçadas de proposição. "Existe entrada plausível que
quebraria isto?" é verdade para quase toda função escrita em linguagem dinâmica
— medido na geração anterior: 125 de 145 funções acima de 0.50. Como gate de
bloqueio isso não separaria nada; como lista de revisão, é informação legítima.

Entram em `duvidas` também: confiança baixa, complexidade acima do viável, teste
não localizado, julgamento feito sobre trecho truncado, e **gate que não foi
respondido** — que nunca conta como "não".

### O conselho — a decisão que a métrica não consegue tomar

Sai do cruzamento de `complexidade_cognitiva` com `complexidade_essencial` (a
complexidade vem do domínio?) e `teste_verifica`:

| | teste verifica | teste não verifica |
|---|---|---|
| **complexidade essencial** | `nada obrigatório` | **`escrever teste, não refatorar`** |
| **forma acidental** | `simplificar a forma` | `refatorar e só depois testar` |

**O quadrante do teste é o que esta skill existe para não errar.** Quando a
complexidade é essencial, cada ramo é um caso que o domínio impõe: simplificar
significa apagar casos reais. Você transformaria código certo em código errado —
e o número melhoraria, porque a métrica não sabe a diferença. O que falta ali é
verificação: cubra as bordas e os caminhos de erro com valor esperado explícito.

`refatorar e só depois testar` tem ordem por um motivo: escrever teste antes
congelaria o desenho acidental que se quer trocar, e aí a refatoração passa a
quebrar testes que estavam certos sobre um código que estava errado.

Quando `teste_verifica` não foi perguntada, quem responde é o fato contável — a
cobertura de linha. É a regra "o que dá para contar, conta-se" aplicada onde ela
sempre deveria ter valido.

`sem_julgamento` aparece quando o eixo semântico está desligado (sem
`TYPESAFE_API_KEY`), quando o Jev não respondeu, ou quando o filtro ou o teto
cortaram a função. **Não invente conselho para preencher**: diga que sem o eixo
semântico "falta teste" e "precisa refatorar" produzem o mesmo número, e trate
como "vale um olhar humano".

## 5. Priorizar

O relatório já traz `prioridade` por função, combinando o risco com
`consequencia_de_falha` — que é exatamente o que a fórmula clássica ignora. Use
isso, e some a leitura humana:

1. **consequência de falha alta** vem primeiro, mesmo com risco menor. Um parser
   de configuração usado no boot e um formatador de mensagem de log com o mesmo
   risco não merecem a mesma pressa.
2. **risco muito acima do limiar** (duas vezes ou mais) vem depois.
3. o resto entra na fila.

`consequencia_de_falha` é a única dimensão em que **nota alta é má notícia**: ela
mede o que está em jogo, não a qualidade. Código impecável pode ter consequência
alta e código descuidado pode ter consequência baixa.

Escolha **uma a três funções por rodada** e diga por que essas. Uma lista de
vinte itens não é um plano: ninguém a executa, e o que sobra é a sensação de que
o projeto é impossível. Uma função tratada e revalidada ensina mais — a você e
ao histórico — do que vinte apontadas.

## 6. Hipótese e a menor mudança justificada

Antes de propor qualquer mexida, escreva a hipótese em uma frase: **o que se
acredita que está errado, e o que a mudança deve alterar no número e no
julgamento**. Sem hipótese declarada, a mudança vira tentativa e o resultado não
ensina nada: se o número cair, ninguém sabe por quê.

Uma hipótese por vez, a menor mudança que a testa. Mexer em cinco coisas e ver o
risco cair não diz qual delas funcionou — e o episódio registrado depois passa a
ensinar a coisa errada ao histórico.

A proposta vai com a evidência colada nela: "teste_verifica 22% e o teste atual
só checa que o retorno não é nulo; a proposta é fixar o valor esperado para o
caso de entrada vazia e para o caminho de erro". Recomendação sem número é
indistinguível de palpite, e quem lê precisa poder discordar do número.

Três atalhos que baixam o número sem baixar o risco, e que o Jev existe para
pegar:

- escrever teste que executa e não afirma nada (sobe cobertura, `teste_verifica`
  continua no chão);
- apagar o ramo de erro que ninguém testava (some o caminho, some o cuidado);
- fatiar a função em duas só para dividir a complexidade, sem separar
  responsabilidade nenhuma.

Se a mudança que você está propondo é uma dessas, a hipótese está errada.

## 7. Revalidar e comparar

Mudança sem revalidação é crença. Rode a suíte, gere a cobertura de novo e chame
`medir_risco` com **os mesmos caminhos, o mesmo limiar e a mesma fórmula** —
riscos de fórmulas diferentes não são comparáveis, são escalas diferentes.

Compare função por função (a chave `arquivo:linha` identifica cada uma) e mostre
antes → depois. Três desfechos e o que cada um significa:

- **número caiu e o julgamento melhorou** → a hipótese se sustentou.
- **número caiu e o julgamento não mudou** (`teste_verifica` ainda no chão, por
  exemplo) → você mexeu na métrica, não no risco. Volte à hipótese; foi um dos
  atalhos do passo 6.
- **número não caiu e o julgamento melhorou** → pode estar certo, e vale dizer
  isso explicitamente: código essencial bem testado continua com risco alto,
  porque a complexidade que sobrou é a do domínio.

## 8. Parar

Pare quando qualquer uma destas for verdade, e diga qual:

- o conselho virou `nada obrigatório`;
- a ação sugerida foi feita e o julgamento confirma que ela pegou;
- o custo da próxima mudança passou do estrago que a falha causaria
  (`consequencia_de_falha` baixa é razão legítima para parar cedo);
- a complexidade é essencial e já está coberta — o risco que resta é o do
  domínio, e ele não sai com refatoração.

**As notas são evidência, não meta.** No instante em que o número vira alvo, ele
deixa de medir: o caminho mais barato para baixar risco é sempre o pior deles.
Nenhum projeto tem risco zero em tudo, e perseguir isso gasta no lugar errado o
pouco de atenção que existe para qualidade.

Declarar a parada — "parei aqui porque a complexidade é essencial e a cobertura
de branch subiu de 40% para 92%" — é o que permite à próxima pessoa discordar de
você com informação. Parada silenciosa vira dívida invisível.

## 9. Registrar o episódio

Chame `registrar_episodio` logo depois de decidir, copiando os campos do
relatório: `arquivo`, `funcao`, `risco`, `nota`, `conselho`, `veredito`,
`complexidade`, `cobertura_linha`, `cobertura_branch`, `limiar`, `formula` e o
bloco `notas` como ele veio.

**Registre principalmente quando a sugestão foi ignorada** (`aceita=false`). É o
sinal de falso positivo mais barato que existe: ninguém precisa escrever
relatório nenhum, basta anotar que não seguiu o conselho. Sem esses registros o
histórico guarda só as vezes em que a ferramenta pareceu certa — e um histórico
assim confirma qualquer régua, inclusive uma errada.

Semanas depois, quando aparecer defeito naquele trecho, volte com
`registrar_episodio(id_episodio=..., defeito=true)`. Defeito abaixo do limiar é
a única evidência que revela **falso negativo**, e é o que autoriza baixar a
régua. Se a função foi mudada, registre também `risco_depois`.

Não infle o histórico: um episódio por função por decisão. Reavaliar a mesma
função sem mudança não é episódio novo — proporções calculadas sobre repetição
enviesam a régua para o lado de quem rodou mais vezes.

## 10. Aprender

`consultar_aprendizado` lê o histórico e diz o que ele mostra. Abaixo de **30
episódios** a resposta é `ainda_sem_base`: as métricas vêm mesmo assim, para
acompanhar, mas não concluem nada. Com 5 avaliações, "60% foram ignoradas"
significa "3 de 5", e 3 de 5 é ruído.

O que ler:

- `taxa_aceitacao` — o quanto a ferramenta está incomodando à toa.
- `cobertura_de_risco` — entre os trechos que deram defeito depois, quantos a
  ferramenta já apontava. É o número mais honesto do conjunto, porque só pode
  ser calculado com informação que chegou depois e fora do controle de quem
  escreveu a régua.
- `variacao_dimensoes` — uma dimensão que responde quase sempre o mesmo não
  separa código bom de ruim; só acrescenta custo e uma coluna no relatório.

As propostas vêm com a evidência numérica junto: `baixar_limiar` (basta um
defeito que escapou), `subir_limiar` (faixa estreita acima do limiar sendo
majoritariamente ignorada, sem defeito nenhum nela) e `remover_dimensao`.

Nada é aplicado sozinho, e isso é de propósito: uma ferramenta que recalibra os
próprios critérios acaba provando que está certa contra um alvo que ela mesma
moveu. Leve a proposta e a evidência para uma pessoa decidir. Quando houver
proposta pendente, mencione no relatório sob qual limiar você avaliou.

---

## Formato do relatório

Reporte **por função**. Nunca publique um número agregado do projeto — nem média
de risco, nem nota de 0 a 10, nem "saúde do repositório". Três razões:

1. ninguém conserta uma média; conserta-se uma função;
2. a média esconde exatamente o que importa — cinco funções péssimas entre
   quinhentas boas desaparecem no arredondamento;
3. número agregado vira meta de planilha, e meta de planilha se persegue pelo
   caminho mais barato, que é sempre o pior.

Para cada função apontada:

```
### src/pagamento/conciliacao.py:88 — conciliar_lote
risco 85.7 (crap, limiar 30) · complexidade 12 · cobertura linha 20% / branch 15%
nota 47/100 (frágil) · REVISAR · prioridade alta
conselho: escrever teste, não refatorar — a complexidade vem do domínio
Jev — cognitiva 63% (conf. 0.66) · teste 11% (conf. 0.74)
      manutenibilidade 50% (conf. 0.71) · erros 65% (conf. 0.58)
      consequência de falha 95% · complexidade essencial 0.88
dúvidas: caso_limite_nao_tratado 0.72
Leitura: os 12 caminhos são casos reais de conciliação (formatos de arquivo do
banco); simplificar apagaria casos. O que falta é verificação — os testes
executam o lote feliz e não afirmam nada sobre divergência.
Proposta: um teste por caso de divergência (valor, data, duplicidade), com o
valor esperado explícito. Hipótese: teste_verifica sobe e o risco cai para ~35
sem tocar na estrutura.
```

Feche com o **escopo da rodada**, que é contexto e não nota: funções medidas,
quantas acima do limiar, quantas julgadas, se o eixo semântico estava ligado,
qual fórmula e limiar valeram, o custo em tokens, e os avisos que mudam a
leitura (cobertura sem branch, arquivos não casados, teto de julgamentos
atingido, funções abaixo do limiar não julgadas).

Quando não há nada acima do limiar, o relatório é uma linha: o escopo e "nada
acima do limiar nesta rodada".

## Confiança baixa: "vale um olhar humano"

As dimensões de tipo `score` vêm com `confianca` (0..1), que mede **o quanto o
modelo se decidiu**, não se ele acertou. Distribuição espalhada é informação real
sobre um caso ambíguo, e apagá-la na hora de escrever transfere para quem lê uma
certeza que ninguém tem.

Quando a confiança de uma dimensão é baixa (abaixo de 0.45 ela já entra em
`duvidas` sozinha), escreva a dúvida em vez do veredito:

- assim não: "o código é difícil de manter; refatore."
- assim sim: "vale um olhar humano: `manutenibilidade` 24% sugere acoplamento,
  mas o modelo não se decidiu (confiança 0.30). Se os ramos forem formatos reais
  do banco, o caso é falta de teste, não refatoração."

As perguntas de tipo `noul` (`injecao`, `caso_limite_nao_tratado` e as outras)
**não têm confiança, e isso não é omissão**: a distribuição delas tem só dois
desfechos, então o próprio número já a descreve por inteiro. Um `noul` em 0.5
significa "o modelo não se decidiu", não "intensidade média" — leia assim.

A mesma postura vale quando a dimensão não foi respondida, quando o eixo
semântico está desligado e quando o conselho saiu `sem_julgamento`. O custo dos
dois erros não é simétrico: mandar escrever um teste a mais custa uma tarde;
mandar simplificar complexidade essencial custa casos do domínio apagados por
alguém que confiou no relatório.

## Sem `TYPESAFE_API_KEY`

O eixo contável funciona sozinho: `medir_risco` continua valendo por inteiro,
`avaliar_arquivos` roda com `eixo_semantico.ligado = false` e todas as funções
saem como `sem_julgamento`. Só `avaliar_trecho` fica indisponível, e ela diz
isso com o nome `eixo_semantico_desligado` e aponta a alternativa.

Diga isso no relatório, em vez de deixar o leitor supor que o silêncio é
aprovação — sem o Jev, "falta teste" e "precisa refatorar" produzem o mesmo
número, e a escolha entre os dois volta a ser de quem lê o código.
