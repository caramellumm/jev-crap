# A fórmula de risco

O número de risco de uma função sai de uma fórmula que é **trocável de
propósito**. Este documento explica qual é a fórmula em vigor, o que há de
errado com ela (há bastante) e como uma fórmula melhor entra sem mexer em mais
nada.

Código correspondente: `src/jev_crap/metrica/risco.py`.

---

## A fórmula atual: CRAP clássico

```
CRAP = cc² × (1 − cobertura)³ + cc
```

onde `cc` é a complexidade ciclomática da função (número de caminhos
independentes de execução, mínimo 1) e `cobertura` é a fração de linhas
executáveis da função que os testes percorreram, de 0 a 1.

O limiar padrão é **30**: acima disso a função entra na lista de investigação.

A leitura da fórmula em uma frase: **a complexidade pesa sempre, e o que não
está coberto multiplica esse peso**. Com cobertura total, o termo do meio zera e
sobra `cc` — o risco de uma função testada é a própria complexidade dela. Sem
cobertura nenhuma, o termo vale `cc²` cheio, e o total é `cc² + cc`.

### Alguns valores, calculados com a fórmula

Complexidade 10, variando a cobertura:

| Cobertura | Risco |
|---|---|
| 0% | 110,00 |
| 25% | 52,19 |
| 50% | 22,50 |
| 75% | 11,56 |
| 90% | 10,10 |
| 100% | 10,00 |

O expoente 3 sobre o descoberto é o que produz essa queda íngreme: com 50% de
cobertura, o fator já caiu para 0,125 (um oitavo). Para uma função assim, cobrir
metade dos caminhos derruba o risco de 110 para 22,5, enquanto reduzir a
complexidade de 10 para 5 sem escrever teste nenhum o deixa em 30 — daí a
recomendação da ferramenta priorizar teste quando a cobertura está baixa. A
relação se inverte em código já coberto: aí o risco tende a `cc`, e só
simplificar o reduz.

Dois pontos de referência que caem exatamente no limiar 30:

- complexidade 5 com cobertura zero → `25 × 1 + 5` = **30,00**;
- complexidade 30 com cobertura total → `900 × 0 + 30` = **30,00**.

Isso já diz o que o limiar 30 significa na prática: ele trata como igualmente
dignas de atenção uma função pequena sem teste nenhum e uma função enorme
inteiramente testada.

---

## Por que ela é o padrão

Não por ser a melhor. Por três razões operacionais:

1. **É conferível na mão.** Qualquer pessoa reproduz o número com uma
   calculadora, e uma métrica que ninguém consegue reproduzir vira uma caixa
   que o time aprende a ignorar.
2. **É conhecida.** Quem já viu CRAP em outra ferramenta reconhece a escala e o
   limiar 30, e não precisa recalibrar a intuição.
3. **É um ponto de comparação estável** para a fórmula que vier depois: com o
   histórico de episódios (ver [`APRENDIZADO.md`](APRENDIZADO.md)) dá para
   medir se a nova ordena melhor os casos que realmente deram problema.

---

## Defeitos conhecidos

Estes quatro estão registrados na docstring de `CrapClassico` e são exatamente a
razão de a fórmula ser trocável. Quem for implementar a segunda precisa saber o
que está corrigindo.

### 1. Mistura unidades

A complexidade ciclomática conta **caminhos**. A cobertura de linha conta
**linhas**. Multiplicar uma pela outra trata grandezas diferentes como se
fossem comparáveis.

O caso patológico é comum: uma função com vários `if` sem `else`, cujos testes
passam sempre pelo ramo verdadeiro. Todas as linhas executáveis rodam, então a
cobertura de linha é 100% — e a fórmula declara risco mínimo:

- função com complexidade 7 e 100% de cobertura de linha → `49 × 0 + 7` =
  **7,00** (bem abaixo do limiar);
- a mesma função tem, digamos, 50% de cobertura de **branch**. Se a fórmula
  usasse branch, o número seria `49 × 0,125 + 7` = **13,13**, quase o dobro.

A cobertura de branch está na mesma unidade da complexidade (caminhos) e seria o
insumo coerente. A fórmula clássica não a usa porque foi definida antes de
branch coverage ser comum em relatório — e o campo `Insumos.cobertura_preferida`
já existe no código, escolhendo branch quando ela veio e linha quando não, à
espera de uma fórmula que o consuma.

### 2. Os expoentes foram escolhidos por intuição

O 2 e o 3 não vêm de dado sobre densidade de defeito. Vêm da vontade de que a
curva subisse rápido quando falta cobertura. Não há derivação por trás deles nem
calibração contra bugs reais.

Trocá-los muda a ordem do relatório sem nenhum argumento que sustente uma
escolha sobre a outra. Para complexidade 10 com 50% de cobertura:

- com expoentes 2 e 3 (os atuais): **22,50**;
- com expoentes 1,5 e 2,5: **15,59**.

Duas funções cujos riscos ficam próximos podem trocar de posição na lista só por
causa dessa escolha arbitrária, e a lista ordenada é o produto principal da
ferramenta.

### 3. Não tem limite superior

O valor cresce com o quadrado da complexidade e não satura:

- complexidade 50 sem teste → **2.550**;
- complexidade 100 sem teste → **10.100**.

Como não existe teto, não existe escala. Os dois números dizem a mesma coisa
("está péssimo"), mas a razão de quase 4 vezes entre eles não corresponde a 4
vezes de nada observável. O efeito prático é na ordenação: as poucas funções
mais extremas dominam o topo da lista com uma margem que o dado não justifica, e
empurram para baixo casos que talvez merecessem atenção primeiro.

### 4. Ignora a consequência da falha

Um parser de arquivo de configuração usado no boot e um formatador de mensagem
de log, ambos com complexidade 8 e 50% de cobertura, recebem o mesmo número:
**16,00**.

O estrago que cada um causa ao falhar não entra na conta — e costuma ser o fator
que mais deveria pesar na decisão de onde gastar o próximo teste. É justamente o
que a dimensão `consequencia_de_falha` do Jev mede, e que esta fórmula, por
definição, não lê.

Esse buraco é remendado **fora** da fórmula: `consequencia_de_falha` é uma das
duas perguntas do grupo *contexto* da rubrica, e é ela que separa prioridade
alta de baixa entre funções de risco parecido. É decisão de ordenação, não
medição, e mora fora do módulo de risco de propósito, para que ninguém a
confunda com a fórmula.

---

## O desenho trocável

Três peças, e a separação entre elas é o que permite a troca.

### 1. `Insumos` — o que foi medido

Um `dataclass` congelado com tudo que se sabe sobre a função **antes** de virar
um número:

| Campo | O que é |
|---|---|
| `complexidade` | Caminhos independentes (mínimo 1) |
| `cobertura_linha` | Fração de 0 a 1 das linhas executadas, ou `SEM_DADOS` |
| `cobertura_branch` | Fração de 0 a 1 dos ramos executados, ou `None` |
| `linhas_logicas` | Tamanho da função em linhas de código |

É congelado porque o mesmo conjunto de insumos pode alimentar várias fórmulas na
mesma execução (para comparar uma com a outra), e nenhuma delas pode editar o
que as outras vão ler.

A validação é estrita: cobertura fora de 0..1 levanta erro em vez de ser cortada
em silêncio, porque cobertura acima de 1 quase sempre é bug de leitura do
relatório — e cortar escondendo o problema é perder justamente o defeito que
interessa descobrir.

**Só entra aqui o que foi contado.** Na geração anterior os insumos carregavam
também três notas do Jev (`teste_verifica`, `complexidade_essencial`,
`consequencia_de_falha`), disponíveis para uma fórmula futura ler. Elas saíram:
misturar medição e julgamento no mesmo objeto embaralha duas coisas com
propriedades opostas — uma é determinística e de graça, a outra é probabilística
e custa uma chamada de API. O julgamento agora entra pela rubrica
(ver [RUBRICA.md](RUBRICA.md)), depois do risco e em cima do recorte que ele
fez. Uma fórmula que queira ler nota do Jev precisa receber esse dado por outro
caminho — e a decisão de não oferecê-lo pronto é deliberada.

### 2. `Formula` — o contrato

Um `Protocol` com quatro obrigações:

```python
class Formula(Protocol):
    nome: str

    def calcular(self, i: Insumos) -> float: ...
    def interpretar(self, valor: float) -> str: ...
    def limiar_padrao(self) -> float: ...
```

A terceira — `interpretar` — é a que costuma faltar em ferramenta de métrica, e
existe por um motivo concreto: número sem interpretação vira meta de planilha, e
meta de planilha vira teste escrito para subir cobertura em vez de verificar
comportamento. Toda fórmula é obrigada a dizer, em uma frase, o que o número
significa para quem vai decidir o que fazer.

Quatro propriedades que a suíte de testes cobra de **todas** as fórmulas
registradas, de uma vez só:

- mais cobertura nunca aumenta o risco;
- mais complexidade nunca diminui o risco;
- com cobertura total, o valor não depende de qual era a cobertura antes;
- mesma entrada, mesma saída — sem estado guardado entre chamadas.

Uma fórmula nova passa a ser testada por essas propriedades no instante em que é
registrada, sem ninguém escrever teste novo para elas.

### 3. O registro — como a troca acontece

```python
from jev_crap.metrica.risco import obter_formula, registrar_formula, formulas_disponiveis

obter_formula()  # a clássica do CRAP
obter_formula("crap")  # idem, pelo nome
formulas_disponiveis()  # ("crap",)
registrar_formula("nome-novo", MinhaFormula)
```

`registrar_formula` recusa sobrescrever um nome já registrado: duas fórmulas
diferentes respondendo pelo mesmo nome fariam relatórios antigos mudarem de
significado sem aviso.

Quem escolhe a fórmula em uso é a variável `JEV_CRAP_FORMULA`. Nome não
registrado não derruba o servidor: o padrão entra no lugar e o motivo aparece
nos avisos do relatório.

O limiar acompanha a fórmula. A configuração guarda `None` quando ninguém
escolheu um limiar explícito, em vez de copiar o 30 — assim trocar de fórmula
troca o limiar junto, sem sobrar configuração órfã apontando para uma escala que
não existe mais. A precedência é: o limiar passado na chamada da tool, depois o
de `JEV_CRAP_LIMIAR`, depois o que a fórmula recomenda.

### Por que o episódio guarda o nome da fórmula

Cada episódio do histórico registra `formula` junto com `risco` e
`limiar_vigente`. Risco 12 pela fórmula clássica e risco 12 por outra não são a
mesma coisa: são escalas diferentes com o mesmo nome.

Por isso o módulo de aprendizado separa as séries por fórmula antes de comparar
e só trabalha sobre a fórmula dominante do histórico. Um limiar calibrado sobre
duas escalas somadas não serve a nenhuma das duas.

---

## Escrevendo a segunda fórmula

O roteiro completo são cinco passos, e nenhum deles toca em módulo que não seja
o de risco:

1. Escrever uma classe com `nome`, `calcular`, `interpretar` e `limiar_padrao`.
   Não precisa herdar de nada — o `Protocol` é estrutural.
2. Acrescentar a entrada no registro (ou chamar `registrar_formula`, se ela vive
   fora do pacote).
3. Rodar a suíte: as propriedades comuns passam a valer para ela
   automaticamente. Testes específicos da fórmula nova entram junto.
4. Usar `JEV_CRAP_FORMULA` para experimentá-la em paralelo, num projeto que já
   tenha histórico de episódios.
5. Comparar as duas pela **cobertura de risco** (quantos defeitos posteriores
   cada uma já apontava acima do limiar) e pela taxa de sugestões ignoradas.
   Ver [`APRENDIZADO.md`](APRENDIZADO.md).

O que a fórmula nova provavelmente precisa corrigir, na ordem em que os defeitos
custam caro:

- **usar `cobertura_preferida`** (branch quando existe, linha como último
  recurso) para parar de misturar unidades;
- **saturar**, para que a escala tenha teto e a ordenação pare de ser dominada
  pelos poucos extremos;
- **incorporar a consequência da falha**, trazendo para dentro da medição o
  fator que hoje só aparece na ordenação por prioridade — o que exige antes
  decidir por qual caminho um dado probabilístico entra numa fórmula que hoje é
  determinística e de graça;
- **justificar os expoentes** — ou eliminá-los, se o formato escolhido não
  precisar deles.

A fórmula clássica continua registrada como `crap` depois disso. Ela não é
removida: relatórios e episódios antigos foram produzidos por ela, e apagá-la
tornaria esse histórico ilegível.
