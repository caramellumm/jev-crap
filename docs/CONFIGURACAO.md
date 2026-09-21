# Configuração

Como ligar o `jev-crap` no seu agente, onde a chave mora e o que dá para
ajustar. Para começar em dois minutos, o [README](../README.md) basta — este
documento é para quando você precisa de um cliente específico ou quer mudar a
régua.

---

## A chave da TypeSafe

O eixo semântico usa o modelo Jev. Crie a conta e gere a chave em
<https://console.typesafe.ai/>.

A ferramenta lê `TYPESAFE_API_KEY` de três lugares, **nesta ordem de
precedência**:

**1. Variável de ambiente** — ganha de tudo. É como o CI deve passar a chave.

```bash
export TYPESAFE_API_KEY="sua-chave"
```

**2. Arquivo `.env`** — o jeito normal em máquina de desenvolvimento. A busca
**sobe os diretórios pais** a partir de onde o processo começou, porque rodar de
dentro de `src/` é comum e o `.env` mora na raiz.

```bash
echo 'TYPESAFE_API_KEY=sua-chave' > .env
```

**3. Bloco `env` do `.mcp.json`** — funciona, mas **não faça**: esse arquivo vai
para o repositório e a chave vai junto.

O ambiente **vence** o arquivo (`os.environ.setdefault`). Um `.env` esquecido no
disco nunca sobrescreve a chave que o CI passou — se fosse o contrário, um
arquivo local trocaria silenciosamente a chave da organização pela de alguém.
Confira que `.env` está no seu `.gitignore`.

### Sem a chave

O servidor sobe igual e o **eixo contável funciona inteiro**. Ausência de chave
é configuração normal, não defeito:

- `medir_risco` funciona integralmente;
- `avaliar_arquivos` mede, calcula risco e ordena, mas o veredito sai
  `sem_julgamento` — que **não é aprovação**, e o relatório diz isso em vez de
  deixar o silêncio passar por aprovado;
- `registrar_episodio` e `consultar_aprendizado` seguem funcionando: o
  aprendizado calibra o limiar do eixo contável, que existe com ou sem Jev.

O mesmo vale quando a chave existe e a chamada falha (rede, cota, resposta fora
do contrato). Falha de terceiro degrada a avaliação, não derruba a ferramenta.

---

## Instalação

O pacote ainda não está no PyPI, então a instalação é a partir do clone:

```bash
git clone <url-do-repositorio> jev-crap
cd jev-crap
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
```

Isso cria dois comandos dentro de `.venv/bin/`:

| comando | o que é |
|---|---|
| `jev-crap` | a CLI, para uso humano e para CI |
| `jev-crap-mcp` | o servidor MCP, transporte stdio, para o agente |

São separados de propósito: quem roda `jev-crap --help` precisa receber ajuda, e
não um processo stdio parado esperando uma mensagem MCP que nunca chega.

---

## Registrando no cliente MCP

Troque `/caminho/para/jev-crap` pelo caminho do seu clone, e `sua_chave` pela
chave de `console.typesafe.ai`.

Onde a chave acaba depende da forma que você escolher, e a diferença importa:
passá-la na linha de comando a grava no arquivo de configuração pessoal do
cliente (fora do repositório, mas em texto claro e no histórico do shell),
enquanto a expansão do próprio cliente (`${VAR}`, `${env:VAR}`, `{env:VAR}`)
deixa o valor só no ambiente. As duas são legítimas; o que não pode é a chave
entrar num arquivo versionado.

### Claude Code

Pela linha de comando, com a chave:

```bash
claude mcp add --scope user --env TYPESAFE_API_KEY="sua_chave" jev-crap -- uv run --directory /caminho/para/jev-crap jev-crap-mcp
```

Ou sem ela, deixando o servidor procurar a chave onde ela já estiver:

```bash
claude mcp add --scope user jev-crap -- uv run --directory /caminho/para/jev-crap jev-crap-mcp
```

Os escopos são `local` (só este projeto, privado), `project` (vai para o
`.mcp.json` do repositório, compartilhado com o time) e `user` (todos os seus
projetos). O `--` separa as opções do Claude Code do comando do servidor.

**Qual das duas usar.** O `--env` grava o valor da chave em `~/.claude.json`,
que é pessoal e fica fora do repositório — é o caminho direto e o que funciona
quando o Claude Code sobe o servidor sem herdar o seu shell. Sem o `--env`, o
servidor procura `TYPESAFE_API_KEY` no ambiente que recebeu e, se não achar, num
`.env` a partir do diretório do projeto; a chave então não fica gravada em
arquivo de configuração nenhum. Sem chave em lugar algum, o eixo semântico
apenas desliga: `medir_risco` continua funcionando, e `avaliar_arquivos` diz
por que não há nota.

Uma terceira forma, quando você quer a chave fora de arquivo **e** fora da linha
de comando: o `.mcp.json` abaixo, onde o Claude Code expande `${VAR}` e
`${VAR:-padrao}` em `command`, `args` e `env`:

```json
{
  "mcpServers": {
    "jev-crap": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/jev-crap", "jev-crap-mcp"],
      "env": {
        "TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"
      }
    }
  }
}
```

Rodando de dentro do próprio repositório, `--directory .` resolve o caminho.

### Codex

```bash
codex mcp add jev-crap -- uv run --directory /caminho/para/jev-crap jev-crap-mcp
```

Ou em `~/.codex/config.toml`. O campo `env_vars` declara quais variáveis do seu
ambiente são repassadas ao servidor, o que evita escrever a chave no arquivo:

```toml
[mcp_servers.jev-crap]
command = "uv"
args = ["run", "--directory", "/caminho/para/jev-crap", "jev-crap-mcp"]
env_vars = ["TYPESAFE_API_KEY"]
```

Para fixar ajustes no próprio arquivo, use a subtabela `env`:

```toml
[mcp_servers.jev-crap.env]
JEV_CRAP_LIMIAR = "25"
```

### Cursor

Em `.cursor/mcp.json` (por projeto) ou `~/.cursor/mcp.json` (global). O Cursor
lê variáveis com a sintaxe `${env:VAR}`:

```json
{
  "mcpServers": {
    "jev-crap": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/jev-crap", "jev-crap-mcp"],
      "env": {
        "TYPESAFE_API_KEY": "${env:TYPESAFE_API_KEY}"
      }
    }
  }
}
```

### OpenCode

Em `opencode.json` na raiz do projeto ou `~/.config/opencode/opencode.json`. A
substituição de variáveis é `{env:VAR}`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "jev-crap": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/caminho/para/jev-crap", "jev-crap-mcp"],
      "enabled": true,
      "environment": {
        "TYPESAFE_API_KEY": "{env:TYPESAFE_API_KEY}"
      }
    }
  }
}
```

### Qualquer outro cliente

O comando é sempre o mesmo — `jev-crap-mcp`, transporte stdio. Não há nada
específico de agente nenhum na ferramenta.

---

## A skill

O servidor dá as ferramentas; a [skill](../skills/jev-crap/SKILL.md) ensina o
**método**: quando não rodar, como filtrar, como ler cada parte do relatório,
como formular a hipótese antes de mexer, como revalidar e quando parar.

Copie `skills/jev-crap/` para onde o seu cliente procura skills: `.claude/skills/`
(projeto) ou `~/.claude/skills/` (global) no Claude Code; `.agents/skills/` ou
`~/.agents/skills/` no Codex. Em clientes sem suporte a skills, o `SKILL.md`
continua sendo um bom roteiro de leitura.

As tools funcionam sem ela; a skill muda a iniciativa e o rigor do agente.

---

## Ajustes

Tudo por variável de ambiente, porque um servidor MCP não tem linha de comando.
Variável mal escrita **não derruba o servidor**: o padrão entra no lugar e a
explicação aparece em `avisos_de_configuracao` na resposta das tools.

### As que você provavelmente vai mexer

| variável | padrão | o que muda |
|---|---|---|
| `JEV_CRAP_LIMIAR` | 30 | risco a partir do qual a função é julgada |
| `JEV_CRAP_MAX_JULGAMENTOS` | 20 | teto de funções enviadas ao Jev por rodada |
| `JEV_CRAP_NOTA_MINIMA` | 60 | abaixo disso o veredito é `revisar` |
| `JEV_CRAP_EXCLUIR` | — | padrões a podar, separados por vírgula |
| `JEV_CRAP_RAIZ` | cwd | raiz do projeto analisado |

### As que mudam a régua

| variável | padrão | o que muda |
|---|---|---|
| `JEV_CRAP_BLOQUEIO` | 0.80 | probabilidade a partir da qual um gate grave barra |
| `JEV_CRAP_SUSPEITA` | 0.50 | probabilidade a partir da qual um risco vira dúvida |
| `JEV_CRAP_LIMITE_CCN` | 10 | complexidade acima da qual entra em `duvidas` |
| `JEV_CRAP_LIMITE_TAMANHO` | 2000 | linhas acima das quais a função é reprovada por tamanho |
| `JEV_CRAP_FORMULA` | `crap` | fórmula de risco em vigor (ver [FORMULA.md](FORMULA.md)) |

O limiar 30 é convenção herdada da ferramenta original, **não medida de nada** —
ver [APRENDIZADO.md](APRENDIZADO.md) para o ciclo que existe justamente para
descobrir que ele está errado.

### As operacionais

| variável | padrão | o que muda |
|---|---|---|
| `JEV_CRAP_MAX_NO_RELATORIO` | 50 | teto de funções detalhadas na resposta |
| `JEV_CRAP_MAX_LINHAS` | 400 | linhas de código enviadas por função (trunca com aviso) |
| `JEV_CRAP_CONCORRENCIA` | 8 | requisições simultâneas ao Jev |
| `JEV_CRAP_EPISODIOS` | `.jev-crap/episodios.jsonl` | onde o histórico é gravado |
| `JEV_CRAP_MODELO` | `jev-latest` | versão do modelo |
| `JEV_CRAP_CUSTO_POR_JULGAMENTO` | — | preço por chamada, para ver dinheiro no relatório |
| `JEV_CRAP_MOEDA` | `BRL` | moeda do campo acima |

Não há tabela de preço embutida: preço de API muda sem avisar, e um número
desatualizado no código parece autoridade. O preço corrente é publicado em
<https://typesafe.ai>.
