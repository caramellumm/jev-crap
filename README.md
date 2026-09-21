# jev-crap

Avalia qualidade de código **função a função**. O número aponta onde olhar; o
modelo **Jev**, da TypeSafe, diz o que fazer.

A métrica contável (complexidade × cobertura) é barata e cega: ela não sabe se a
complexidade vem do domínio ou da escrita, nem se os testes conferem resultado
ou só executam linhas. O Jev responde isso — e só nas funções que o número já
apontou, que é o que mantém o custo baixo.

Entrega três coisas: uma **CLI**, um **servidor MCP** e uma **skill**.

---

## Começando

```bash
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
echo 'TYPESAFE_API_KEY=sua-chave' > .env        # chave em console.typesafe.ai
```

Gere a cobertura e rode:

```bash
pytest --cov=src --cov-branch --cov-report=xml        # → coverage.xml
.venv/bin/jev-crap src --cobertura coverage.xml --testes tests
```

Sem chave? Funciona também — `--sem-julgamento` roda só o eixo contável, sem
rede e sem custo.

---

## Lendo o resultado

Um bloco por função. **A penúltima linha responde "está bom?":**

```
src/pagamento/conciliacao.py:88  conciliar_lote  (42 linhas)
  contável   ccn 12  risco 85.7  linha 20%  branch 15%
  julgado    complexidade 63%  teste 11%  manutenibilidade 50%  tratamento 65%
  → escrever teste, não refatorar: a complexidade vem do domínio
  nota 47.2/100 (frágil) · REVISAR · prioridade alta
  revisar por: caso_limite_nao_tratado 0.72, complexidade 12: acima de 10
```

Olhe o **veredito** e pare aí:

| valor | significa |
|---|---|
| `aprovar` | nada a fazer |
| `revisar` | olho humano — o motivo está escrito na última linha |
| `bloquear` | não vai assim — há risco grave (injeção, execução dinâmica) |
| `sem_julgamento` | não foi avaliado. **Não é aprovação** |

Se não for `aprovar`, a linha do `→` já traz a ação: refatorar, escrever teste,
ou os dois nessa ordem.

Precisando de um segundo dado, use a **faixa** — `sólido` (≥80) · `aceitável`
(≥60) · `frágil` (≥40) · `ruim` — e não o decimal: 72.4 e 75.1 são a mesma coisa.

---

## No seu agente

Um `.mcp.json` na raiz do projeto liga o servidor MCP:

```json
{
  "mcpServers": {
    "jev-crap": {
      "command": "uv",
      "args": ["run", "--directory", ".", "jev-crap-mcp"],
      "env": {}
    }
  }
}
```

São seis tools — `avaliar_arquivos`, `avaliar_trecho`, `medir_risco`,
`explicar_criterios`, `registrar_episodio` e `consultar_aprendizado`. Só as duas
primeiras usam rede. Em qualquer outro cliente MCP o comando é o mesmo:
`jev-crap-mcp`, transporte stdio.

Copie também `skills/jev-crap/` para `.claude/skills/` (ou onde o seu cliente
procura skills): o servidor dá as ferramentas, a skill ensina o **método** — e
impede os dois erros mais caros, que são tratar avaliação como autorização para
editar e publicar média de projeto em vez de apontar funções.

---

## No CI

Os códigos de saída são o contrato:

| código | significado |
|---|---|
| 0 | aprovar |
| 1 | revisar (ou `sem_julgamento` — o que não foi julgado não foi aprovado) |
| 2 | bloquear |
| 3 | erro de uso ou de configuração |

O **3 é separado de propósito**: sem ele, falta de chave sairia como 1 e um
pipeline passaria meses achando que avalia.

```bash
export TYPESAFE_API_KEY=${{ secrets.TYPESAFE_API_KEY }}
jev-crap src --cobertura coverage.xml --testes tests --quieto
```

---

## Documentação

| documento | quando ler |
|---|---|
| [CONFIGURACAO.md](docs/CONFIGURACAO.md) | ligar em Claude Code, Codex, Cursor ou OpenCode; onde a chave mora; todos os ajustes |
| [RUBRICA.md](docs/RUBRICA.md) | as 11 perguntas, como viram nota e veredito, as seis tools |
| [COBERTURA.md](docs/COBERTURA.md) | as 27 linguagens; gerar cobertura em Python, JS/TS, Go e Java |
| [PRIVACIDADE.md](docs/PRIVACIDADE.md) | exatamente o que sai da máquina, e como fazer nada sair |
| [FORMULA.md](docs/FORMULA.md) | a fórmula de risco, seus defeitos conhecidos e como trocá-la |
| [APRENDIZADO.md](docs/APRENDIZADO.md) | registrar decisões e descobrir que a régua errou |

---

## Desenvolvimento

```bash
.venv/bin/python -m pytest -q                              # 342 testes, sem rede
.venv/bin/ruff check .
.venv/bin/jev-crap src --cobertura coverage.xml --testes tests   # sobre si mesmo
```

Nenhum teste toca a API, grava fora de um diretório temporário ou depende da
ordem em que roda. As decisões de desenho e seus porquês estão nas docstrings de
módulo — valem a leitura antes de mexer em qualquer coisa.

## Licença

MIT — ver [LICENSE](LICENSE).
