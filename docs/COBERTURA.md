# Linguagens e cobertura

O que a ferramenta consegue medir, e como produzir o relatório de cobertura que
ela lê em cada ecossistema.

---

## Linguagens suportadas

A complexidade vem do [lizard](https://github.com/terryyin/lizard), que traz 27
leitores de linguagem e aplica **o mesmo algoritmo** em todos — o que permite
somar maçãs com maçãs num repositório com backend numa linguagem e frontend em
outra:

C/C++, C#, Java, JavaScript, TypeScript, TSX/JSX, Vue, Python, Go, Rust, Kotlin,
Swift, Objective-C, Ruby, PHP, Perl, Lua, Scala, Erlang, Zig, Solidity,
GDScript, Fortran, R, PL/SQL, ST (Structured Text) e TTCN-3.

(C e C++ dividem o mesmo leitor, por isso a lista tem 27 itens.)

A escolha do lizard em vez do `radon` foi medida, não estética: o radon só lê
Python, e nos arquivos Python os dois devolvem o mesmo valor de complexidade.
Multilinguagem sai de graça trocando uma biblioteca pela outra.

Arquivo de extensão que o lizard não conhece é **pulado em silêncio** — um
repositório é cheio de `.md`, `.json` e `.svg`, e reclamar de cada um
transformaria o aviso útil em ruído.

---

## Dois formatos, não uma biblioteca

A ferramenta lê **LCOV** e **Cobertura XML**, não `coverage.py`. Amarrar a
leitura a uma biblioteca de Python prenderia tudo ao Python; esses dois formatos
são emitidos por praticamente todo ecossistema e, o que mais importa, os dois
carregam **cobertura de branch**.

Branch é o dado correto para cruzar com complexidade ciclomática porque os dois
contam a mesma coisa: caminhos. Uma função com `if` sem `else` chega a 100% de
cobertura de linha com metade dos caminhos nunca executada — e é justamente o
ramo não exercitado que carrega o risco. Quando o relatório não traz branch, a
ferramenta cai para cobertura de linha e **avisa no relatório** em vez de fingir
que o dado existe.

O formato é detectado pelo **conteúdo** do arquivo, não pela extensão:
relatórios chegam como `coverage.dat`, `lcov.txt` ou de um pipe de CI sem
extensão nenhuma.

Faixa de linhas sem nenhuma linha executável devolve `SEM_DADOS`, **nunca 0.0**.
Zero diria "nada coberto" e puniria uma função que simplesmente não tem desvio.

---

## Gerando o relatório

### Python

```bash
pytest --cov=src --cov-branch --cov-report=xml:coverage.xml
# ou
pytest --cov=src --cov-branch --cov-report=lcov:coverage.lcov
```

O `--cov-branch` é o que liga a cobertura de branch; sem ele o relatório sai só
com linhas.

### JavaScript e TypeScript

```bash
# Jest
npx jest --coverage --coverageReporters=lcov       # gera coverage/lcov.info

# Vitest
npx vitest run --coverage --coverage.reporter=lcov # gera coverage/lcov.info
```

Os provedores de cobertura de ambos (istanbul e v8) já contam branches, então o
`lcov.info` sai completo.

### Go

A cobertura nativa do Go não é LCOV, então passa por um conversor:

```bash
go install github.com/jandelgado/gcov2lcov@latest
go test -coverprofile=coverage.out ./...
gcov2lcov -infile=coverage.out -outfile=coverage.lcov
```

Vale saber: a cobertura do Go é por instrução, não por branch. O LCOV resultante
não traz registros de branch, e a avaliação cai para cobertura de linha — com o
aviso correspondente no relatório.

### Java

O JaCoCo é o padrão de fato, mas o XML dele tem esquema próprio (raiz
`<report>`), que **não** é Cobertura XML (raiz `<coverage>`). A conversão é um
passo a mais:

```bash
mvn test jacoco:report            # gera target/site/jacoco/jacoco.xml
python cover2cover.py target/site/jacoco/jacoco.xml src/main/java > coverage.xml
```

O `cover2cover.py` vem de <https://github.com/rix0rrr/cover2cover>. Em Gradle há
plugins equivalentes que produzem Cobertura XML direto da tarefa de relatório. O
JaCoCo mede branches, então o XML convertido chega com o dado completo.

---

## Sem relatório de cobertura

A ferramenta roda: o risco é calculado tratando a cobertura ausente como **o
pior caso**, e o relatório diz que o dado não existe. É útil para um primeiro
retrato de um repositório sem suíte, mas o cruzamento dos dois eixos só começa a
valer quando há cobertura de verdade para ler.
