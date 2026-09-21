# Privacidade: o que sai da máquina

Esta página existe para você poder responder "o que essa ferramenta manda para
fora?" sem ler o código. Se a resposta precisa ser "nada", a última seção diz
como garantir isso.

---

## Sem chave, nada sai

**Sem `TYPESAFE_API_KEY`, ou com `--sem-julgamento` (CLI) / `com_julgamento=false`
(MCP), zero requisições de rede.** Complexidade, leitura de cobertura, cálculo de
risco e histórico de episódios são todos locais.

A tool `medir_risco` **nunca** chama a rede, em nenhuma configuração.

---

## Com chave, o que vai em cada requisição

O único destino é a API do Jev:
`POST https://api.typesafe.ai/v1/systemone`, sobre HTTPS, autenticada com
`Authorization: Bearer`. **Uma requisição por função julgada.**

O corpo leva um `state` com campos nomeados:

| campo | conteúdo | limite |
|---|---|---|
| `linguagem` | a linguagem detectada | — |
| `codigo` | as linhas da função, do início ao fim | 400 linhas (`JEV_CRAP_MAX_LINHAS`); o que passa é truncado **com aviso dentro do próprio estado** |
| `testes` | trechos de teste que citam a função pelo nome | no máximo 3 trechos, cada um do cabeçalho do teste até ~900 caracteres adiante |
| `cobertura_branch` | um número entre 0 e 1, arredondado | — |

Mais as perguntas da rubrica, que são texto fixo do próprio pacote
(`src/jev_crap/julgamento/perguntas.json`).

O truncamento vem com aviso porque, sem ele, o modelo julgaria casos-limite de
um pedaço achando que viu a função inteira — e responderia com a confiança de
quem viu tudo.

### O que não sai, em nenhuma hipótese

- o repositório inteiro, ou qualquer arquivo que não foi julgado;
- o relatório de cobertura (só o número de branch da função vai);
- **a complexidade ciclomática** — fica de fora de propósito: mandar o `ccn`
  ancoraria o julgamento no número que você mesmo enviou;
- variáveis de ambiente;
- o histórico de episódios;
- a chave, que só viaja como cabeçalho de autenticação.

### Quanto código sai, na prática

Dois limites mantêm o volume pequeno por construção: só funções **acima do
limiar** são julgadas, e no máximo **20 por avaliação**
(`JEV_CRAP_MAX_JULGAMENTOS`). Num repositório de mil funções, o que sai é o
texto de até vinte delas.

---

## O que fica no disco

O histórico de episódios é gravado em
`<raiz do projeto>/.jev-crap/episodios.jsonl` e **nunca é enviado a lugar
nenhum** — ele existe para calibrar o limiar daquele repositório. Mude o destino
com `JEV_CRAP_EPISODIOS`.

O log do servidor vai para **stderr** (stdout é o canal do protocolo MCP) e
registra tipos de erro e status HTTP, não o código analisado.

---

## Se parte do código não pode sair

Três saídas, da mais simples para a mais completa:

1. use `medir_risco`, que nunca chama a rede;
2. passe `com_julgamento=false` (MCP) ou `--sem-julgamento` (CLI);
3. exclua os caminhos sensíveis com `JEV_CRAP_EXCLUIR`.

Nos três casos o eixo contável continua funcionando inteiro, e o veredito sai
`sem_julgamento` — que **não é aprovação**.
