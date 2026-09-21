"""Servidor MCP do jev-crap: seis tools, nomes e campos em português.

Quem lê a descrição de uma tool é um modelo decidindo o próximo passo, não uma
pessoa procurando referência. Por isso cada docstring aqui diz três coisas na
ordem — **o que a tool faz**, **quando usá-la** e **o que ela devolve** — e não
descreve implementação, que o leitor não pode usar para decidir nada.

Quatro decisões de desenho do servidor, todas testáveis:

1. **Falha prevista vira ``ToolError`` com nome e com "como resolver".** O
   cliente MCP a recebe como erro de verdade, porque foi isso que aconteceu:
   devolver um dicionário de sucesso contendo uma falha faria o agente tratar o
   fracasso como resultado. Já *estado normal que muda a leitura* — não há
   cobertura de branch, o eixo semântico está desligado — não é erro: vira campo
   no relatório, porque a resposta continua válida e apenas vale menos.

2. **Nenhuma exceção crua atravessa a fronteira.** Uma exceção que sobe vira
   erro de protocolo sem texto útil, e quem está do outro lado de um transporte
   stdio fica com uma tool que "não funciona" e nenhuma pista. O traceback vai
   para o log em stderr; a mensagem que volta diz o que fazer.

3. **A configuração e o julgador entram por parâmetro** em
   :func:`criar_servidor`, em vez de serem lidos do ambiente lá dentro. É o que
   permite à suíte descrever o cenário inteiro — julgador falso, diretório
   temporário, régua própria — sem tocar em ``os.environ`` nem na rede.

4. **As tools são síncronas e não usam ``Context``.** O FastMCP já roda função
   síncrona numa thread (``run_in_thread=True``), então nada bloqueia o laço de
   eventos e a indireção some. ``Context`` serviria para mandar log ao cliente,
   mas a capability de logging do MCP foi descontinuada no protocolo de
   2026-07-28 (SEP-2577) — e, mesmo que não fosse, aviso que chega como log o
   agente não lê: os avisos viajam dentro do relatório, em ``avisos``, onde são
   dado e não ruído.

5. **As anotações de cada tool dizem o que ela faz ao mundo.** ``readOnlyHint``
   separa as cinco que só leem da única que grava; ``openWorldHint`` separa as
   que chamam uma API paga das que rodam offline. Um cliente MCP usa isso para
   decidir se pede confirmação — e uma tool de leitura marcada como escrita
   ensina o usuário a confirmar no automático, que é como a confirmação deixa
   de proteger.

Agnóstico a agente por construção: não há nada aqui sobre um cliente
específico. O transporte é stdio, as tools são MCP puro, e a chave da API vem do
ambiente do processo — que é o que todo cliente MCP sabe passar.
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Mapping
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from jev_crap import avaliacao
from jev_crap.ambiente import carregar_env
from jev_crap.aprendizado.episodio import Episodio, Repositorio
from jev_crap.aprendizado.laco import MINIMO_EPISODIOS, agregar, propor
from jev_crap.config import Config
from jev_crap.julgamento.jev import CONFIANCA_MINIMA, Julgador, obter_julgador
from jev_crap.julgamento.rubrica import Rubrica, carregar_rubrica
from jev_crap.situacoes import SituacaoConhecida

__all__ = ["criar_servidor", "main"]

_log = logging.getLogger(__name__)

#: O que aparece no lugar de uma contagem que não pôde ser feita. Negativo de
#: propósito: zero seria lido como "histórico vazio", e é justamente o oposto
#: de "não consegui contar".
SEM_CONTAGEM = -1

#: Saída do processo quando o servidor não sobe por configuração. Combina com
#: o 3 da CLI, que também é "erro de uso ou de configuração".
ERRO_DE_CONFIGURACAO = 3

VERSAO = "0.2.0"

INSTRUCOES = """
jev-crap avalia qualidade de código função a função, cruzando dois eixos que,
sozinhos, mentem.

O eixo contável (complexidade ciclomática × cobertura de testes) é barato e
determinístico, mas não sabe se a complexidade vem do domínio ou da escrita, nem
se os testes verificam comportamento ou apenas executam linhas.

O eixo semântico (o modelo Jev, da TypeSafe) responde exatamente isso, e custa
uma chamada de API por função — por isso ele só é consultado sobre as funções
que o eixo contável já apontou.

Por onde começar:
- avaliar_arquivos — o caminho normal. Mede, filtra, julga o recorte e decide.
- avaliar_trecho — para código que você acabou de escrever e ainda não salvou.
- medir_risco — só o número, sem rede e sem custo. Serve em CI e em repositório
  grande, e funciona sem chave de API.
- explicar_criterios — a régua em vigor, antes de discordar de uma nota.
- registrar_episodio / consultar_aprendizado — o que foi decidido e o que o
  histórico mostra depois.

Duas coisas que este servidor não faz, de propósito: não edita código (avaliação
não é autorização para mexer) e não publica número agregado do projeto — ninguém
conserta uma média, conserta-se uma função.
""".strip()

SO_LEITURA_LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
"""Lê disco, não chama ninguém, não grava nada."""

SO_LEITURA_COM_API = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
"""Não muda nada no projeto, mas conversa com uma API externa e paga."""

GRAVA_HISTORICO = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
"""Acrescenta ao histórico local. Não destrói nada (o arquivo é append-only),
mas duas chamadas iguais produzem dois episódios — daí ``idempotentHint=False``."""


def _erro(erro: Exception) -> ToolError:
    """Traduz qualquer exceção em ``ToolError`` legível.

    O caso genérico existe porque um defeito nosso não pode chegar ao outro lado
    como ruído: ele chega dizendo que é um defeito nosso, e o traceback fica no
    log do servidor.
    """
    if isinstance(erro, SituacaoConhecida):
        return ToolError(erro.para_texto())
    _log.exception("falha inesperada numa tool do jev-crap")
    return ToolError(
        f"falha_inesperada: {type(erro).__name__}: {erro}\n"
        "Como resolver: isto é um defeito do jev-crap, não da sua configuração. "
        "O log do servidor MCP (stderr) tem o traceback completo."
    )


def _normalizar_notas(notas: dict[str, Any] | None) -> dict[str, dict]:
    """Aceita as notas como o relatório as devolve ou como número solto.

    Quem registra o episódio costuma copiar o bloco ``notas`` do relatório, mas
    também é comum digitar ``{"teste_verifica": 0.4}``. Os dois viram o mesmo
    formato guardado, porque o módulo de aprendizado lê a série histórica
    inteira de uma vez e não pode encontrar dois formatos.

    Nada aqui levanta. Tudo que vem do cliente MCP pode ter qualquer forma, e
    o que não dá para interpretar é descartado silenciosamente: o episódio
    ainda carrega risco, limiar, veredito e desfecho — os campos que sustentam
    a calibração — e recusar o registro inteiro por causa de uma nota torta
    perderia a série histórica para proteger um campo acessório.
    """
    if not isinstance(notas, Mapping):
        # `None` é o caso normal (episódio sem julgamento); qualquer outra coisa
        # é o cliente mandando lista ou texto no lugar do bloco `notas`. Os dois
        # viram histórico sem notas, que é verdade — e melhor que recusar o
        # registro inteiro, porque risco, limiar e desfecho continuam valendo.
        return {}
    normalizadas: dict[str, dict] = {}
    for dimensao, bruto in notas.items():
        if isinstance(bruto, Mapping):
            normalizadas[str(dimensao)] = dict(bruto)
            continue
        if isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
            continue
        valor = float(bruto)
        if not math.isfinite(valor):
            # nan contamina qualquer média da série histórica depois, e o
            # agregador não teria como apontar de qual episódio ele veio.
            continue
        normalizadas[str(dimensao)] = {"normalizado": valor, "confianca": None}
    return normalizadas


def criar_servidor(
    config: Config | None = None,
    julgador: Julgador | None = None,
    rubrica: Rubrica | None = None,
) -> FastMCP:
    """Monta o servidor com as tools já registradas.

    Os três parâmetros entram por injeção para que o teste monte o cenário
    inteiro sem ambiente global nem rede. Em produção quem os monta é
    :func:`main`.
    """
    ajuste = config if config is not None else Config.do_ambiente()
    jev = julgador if julgador is not None else obter_julgador()
    regua = rubrica if rubrica is not None else carregar_rubrica()

    mcp: FastMCP = FastMCP(name="jev-crap", instructions=INSTRUCOES, version=VERSAO)

    # ---------------------------------------------------------------- avaliar

    @mcp.tool(annotations=SO_LEITURA_COM_API, tags={"qualidade", "avaliacao"})
    def avaliar_arquivos(
        caminhos: Annotated[
            list[str],
            Field(
                description=(
                    "Arquivos ou pastas a avaliar, relativos ao diretório onde o servidor "
                    'roda. Exemplo: ["src"]. Arquivos de teste são ignorados automaticamente.'
                ),
                min_length=1,
            ),
        ],
        cobertura: Annotated[
            str,
            Field(
                description=(
                    "Caminho do relatório LCOV (.info) ou Cobertura XML gerado pela suíte. "
                    "Sem ele, toda função entra como 0% coberta e o risco de todas sobe por "
                    "igual — o relatório sai inteiro vermelho e não ordena nada."
                )
            ),
        ] = "",
        testes: Annotated[
            str,
            Field(
                description=(
                    "Pasta onde estão os testes, por exemplo 'tests'. É de onde saem os "
                    "trechos que o Jev lê para julgar se os testes verificam comportamento. "
                    "Sem ela, essa pergunta não é feita e o peso dela é redistribuído."
                )
            ),
        ] = "",
        limiar: Annotated[
            float | None,
            Field(
                description=(
                    "Risco a partir do qual a função é julgada. Omita para usar o da fórmula "
                    "(a clássica do CRAP usa 30). Passe 0 para julgar todas — mais caro, mas "
                    "é o único jeito de pegar código ilegível com complexidade baixa."
                ),
                ge=0,
            ),
        ] = None,
        com_julgamento: Annotated[
            bool,
            Field(
                description=(
                    "false roda só o eixo contável, sem chamar o Jev e sem custo. Toda função "
                    "sai como 'sem_julgamento'."
                )
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Avalia a qualidade do código cruzando a métrica contável com o julgamento do Jev.

        Use ao terminar de implementar algo, antes de abrir PR, ao revisar
        código de outra pessoa, quando pedirem para "melhorar", "refatorar",
        "reduzir complexidade" ou "avaliar a qualidade", e quando perguntarem se
        um código está bom, onde ele é arriscado ou o que testar primeiro.

        O processo: mede complexidade × cobertura de todas as funções (barato e
        determinístico), separa as que passaram do limiar e manda só essas para
        o Jev — é esse recorte que mantém o custo baixo —, cruza os dois eixos e
        devolve a lista ordenada por risco, do pior para o melhor.

        Devolve, por função: os fatos contáveis (complexidade, coberturas,
        risco), a `nota` de qualidade 0..100 com a `faixa` em que ela cai, as
        `notas` por dimensão, os `graves` que barram, as `duvidas` que mandam
        para olho humano, o `conselho` (refatorar, testar, ou os dois nesta
        ordem), a `prioridade` e o `veredito`. E, no topo: `resumo` com a
        contagem por veredito e o `resultado` da rodada, `eixo_semantico`
        dizendo se o julgamento estava ligado, `custo`, `avisos` que mudam a
        leitura e `como_ler`.

        Duas leituras que costumam ser puladas: **nada acima do limiar** é
        resposta completa ("nada a fazer agora"), não convite a baixar o limiar;
        e **quase tudo acima** costuma ser relatório de cobertura que não casou
        com os caminhos — confira `avisos` antes de concluir que o projeto é
        ruim.

        Nada é gravado. Para registrar o que foi decidido, use
        registrar_episodio.
        """
        try:
            return avaliacao.avaliar(
                caminhos,
                cobertura or None,
                config=ajuste,
                julgador=jev,
                rubrica=regua,
                limiar=limiar,
                pasta_testes=testes or None,
                com_julgamento=com_julgamento,
            )
        except Exception as erro:
            raise _erro(erro) from erro

    @mcp.tool(annotations=SO_LEITURA_COM_API, tags={"qualidade", "avaliacao"})
    def avaliar_trecho(
        codigo: Annotated[
            str,
            Field(
                description=(
                    "O texto da função. É ISTO que será julgado — não um caminho de arquivo. "
                    "Mande a função inteira, não um pedaço: casos-limite julgados sobre meia "
                    "função saem errados com cara de certo."
                ),
                min_length=1,
            ),
        ],
        arquivo: Annotated[
            str,
            Field(
                description=(
                    "Nome com a extensão certa, por exemplo 'pagamento.py' ou 'app.ts'. A "
                    "extensão é o que escolhe o analisador de complexidade e a linguagem; o "
                    "arquivo NÃO é lido do disco e não precisa existir."
                )
            ),
        ] = "trecho.py",
        funcao: Annotated[
            str,
            Field(
                description=(
                    "Qual função julgar, quando o trecho tem mais de uma. Omitido, é julgada "
                    "a de maior complexidade."
                )
            ),
        ] = "",
        testes: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Trechos dos testes que exercitam essa função, se existirem. Sem eles a "
                    "pergunta sobre teste não é feita — o que é diferente de 'os testes são "
                    "ruins' e leva a uma ação diferente."
                )
            ),
        ] = None,
        cobertura_branch: Annotated[
            float | None,
            Field(
                description=(
                    "Fração de 0 a 1 dos ramos cobertos, se você souber. Omita quando não "
                    "souber: o risco é então calculado como se nada estivesse coberto."
                ),
                ge=0,
                le=1,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Avalia uma função que você já tem em mãos, sem ler o disco.

        Use para código que acabou de ser escrito e ainda não foi salvo, para um
        pedaço de diff em revisão, e quando o número de risco e a sua impressão
        ao ler o código discordam — essa discordância costuma ser informação,
        não erro.

        A complexidade é medida do próprio texto, então o eixo contável continua
        valendo; o que falta é a cobertura, que não há de onde inferir para um
        texto solto.

        Devolve `funcao` com os mesmos campos de avaliar_arquivos (risco, nota,
        faixa, notas por dimensão, graves, duvidas, conselho, prioridade,
        veredito), mais `custo`, `avisos` e `como_ler`.

        Precisa de TYPESAFE_API_KEY no ambiente do servidor. Sem ela, devolve
        erro nomeado 'eixo_semantico_desligado' — nesse caso use medir_risco,
        que funciona sem chave.
        """
        try:
            return avaliacao.julgar_trecho(
                codigo,
                config=ajuste,
                julgador=jev,
                rubrica=regua,
                arquivo=arquivo or "trecho.py",
                funcao=funcao,
                testes=list(testes or []),
                cobertura_branch=cobertura_branch,
            )
        except Exception as erro:
            raise _erro(erro) from erro

    @mcp.tool(annotations=SO_LEITURA_LOCAL, tags={"qualidade", "metrica"})
    def medir_risco(
        caminhos: Annotated[
            list[str],
            Field(description='Arquivos ou pastas a medir. Exemplo: ["src"].', min_length=1),
        ],
        cobertura: Annotated[
            str,
            Field(
                description=(
                    "Caminho do relatório LCOV (.info) ou Cobertura XML. Sem ele o risco é "
                    "calculado como se nada estivesse coberto."
                )
            ),
        ] = "",
        limiar: Annotated[
            float | None,
            Field(description="Risco a partir do qual a função é apontada.", ge=0),
        ] = None,
    ) -> dict[str, Any]:
        """Mede só o eixo contável: complexidade ciclomática cruzada com cobertura.

        Use quando quiser apenas o número — varredura ampla de um repositório
        grande, verificação em CI, comparação antes/depois de uma refatoração —
        ou quando não houver chave do Jev. Não faz chamada de rede, não custa
        nada e devolve sempre o mesmo resultado para a mesma entrada.

        Devolve `resumo` (funções medidas, quantas acima do limiar, limiar e
        fórmula em vigor), `funcoes` com complexidade, coberturas, risco e a
        interpretação do número em uma frase, `avisos` e `regua`.

        O que ela NÃO responde está dito no próprio retorno, em
        `o_que_isto_nao_responde`: se a complexidade é essencial ou acidental, e
        se os testes verificam comportamento ou só executam linhas. Essas duas
        decidem entre escrever teste e refatorar, e só avaliar_arquivos as
        responde.
        """
        try:
            medicao = avaliacao.medir(caminhos, cobertura or None, config=ajuste, limiar=limiar)
            return avaliacao.relatorio_contavel(medicao, ajuste)
        except Exception as erro:
            raise _erro(erro) from erro

    @mcp.tool(annotations=SO_LEITURA_LOCAL, tags={"qualidade", "referencia"})
    def explicar_criterios() -> dict[str, Any]:
        """Mostra a régua em vigor: quais perguntas são feitas, o que cada nível significa.

        Use antes de discordar de uma nota, ao explicar um relatório para outra
        pessoa, e quando quiser saber por que uma dimensão pesa mais que outra.
        É a tool que torna o resultado discutível em vez de oracular.

        Devolve `rubrica` (cada dimensão com grupo, tipo, sentido da escala,
        peso, a pergunta feita e a descrição de cada nível), `grupos`
        explicando o que cada um faz com o resultado, `limiares` em vigor,
        `como_ler` e a `configuracao` atual do servidor.

        Não faz chamada de rede e não custa nada.
        """
        try:
            return {
                "rubrica": regua.para_rubrica(),
                "grupos": {
                    "qualidade": (
                        "Dimensões compensáveis: a média ponderada delas é a nota. "
                        "Legibilidade boa compensa tratamento de erro mediano."
                    ),
                    "contexto": (
                        "Não entra na nota e não barra nada. Decide o que fazer e com que "
                        "pressa — consequência de falha é o que a fórmula CRAP ignora."
                    ),
                    "risco_grave": (
                        "Gates que barram. Fora da nota porque risco não se compensa com "
                        "legibilidade boa. Restritos a proposições que ou valem ou não valem."
                    ),
                    "risco_atencao": (
                        "Mandam para olho humano, nunca barram. São perguntas graduais "
                        "disfarçadas de proposição, verdadeiras em quase todo código real — "
                        "como gate não separariam nada, como lista de revisão são legítimas."
                    ),
                },
                "limiares": {
                    "bloqueio": ajuste.bloqueio,
                    "suspeita": ajuste.suspeita,
                    "nota_minima": ajuste.nota_minima,
                    "limite_ccn": ajuste.limite_ccn,
                    "limite_tamanho": ajuste.limite_tamanho,
                    "limiar_de_risco": ajuste.limiar_efetivo(ajuste.obter_formula()),
                    "confianca_minima": CONFIANCA_MINIMA,
                },
                "faixas_da_nota": [
                    {"a_partir_de": piso, "faixa": nome} for piso, nome in avaliacao.FAIXAS
                ],
                "como_ler": avaliacao.COMO_LER,
                "configuracao": ajuste.para_regua(),
            }
        except Exception as erro:
            raise _erro(erro) from erro

    # ------------------------------------------------------------ aprendizado

    @mcp.tool(annotations=GRAVA_HISTORICO, tags={"aprendizado"})
    def registrar_episodio(
        arquivo: Annotated[str, Field(description="Arquivo da função, copiado do relatório.")] = "",
        funcao: Annotated[str, Field(description="Nome da função.")] = "",
        risco: Annotated[
            float | None, Field(description="O risco medido na avaliação.", ge=0)
        ] = None,
        conselho: Annotated[
            str, Field(description="O que a ferramenta aconselhou, copiado do relatório.")
        ] = "",
        veredito: Annotated[
            str, Field(description="aprovar, revisar, bloquear ou sem_julgamento.")
        ] = "",
        nota: Annotated[
            float | None, Field(description="A nota de qualidade 0..100, se houve.", ge=0, le=100)
        ] = None,
        acao: Annotated[
            str | None, Field(description="O que foi de fato feito com a função.")
        ] = None,
        aceita: Annotated[
            bool | None,
            Field(
                description=(
                    "true se a sugestão foi seguida, false se foi ignorada. Registrar o false "
                    "é o sinal de falso positivo mais barato que existe."
                )
            ),
        ] = None,
        complexidade: Annotated[int, Field(description="Complexidade medida.", ge=1)] = 1,
        cobertura_linha: Annotated[float, Field(description="0 a 1.", ge=0, le=1)] = 0.0,
        cobertura_branch: Annotated[
            float | None, Field(description="0 a 1, se o relatório trouxe.", ge=0, le=1)
        ] = None,
        limiar: Annotated[
            float | None, Field(description="Limiar vigente na avaliação.", ge=0)
        ] = None,
        formula: Annotated[str, Field(description="Fórmula vigente, por exemplo 'crap'.")] = "",
        notas: Annotated[
            dict[str, Any] | None,
            Field(description="O bloco `notas` do relatório, copiado como veio."),
        ] = None,
        risco_depois: Annotated[
            float | None, Field(description="Risco medido após a mudança.", ge=0)
        ] = None,
        defeito: Annotated[
            bool | None,
            Field(
                description=(
                    "true quando aquele trecho apresentou defeito depois. É a única evidência "
                    "que revela falso negativo."
                )
            ),
        ] = None,
        id_episodio: Annotated[
            str,
            Field(
                description=(
                    "Para anotar o desfecho de um episódio já registrado, em vez de criar um "
                    "novo. Use o id devolvido no registro original."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        """Guarda o que a avaliação disse e o que foi feito depois.

        Use logo depois de decidir o que fazer com uma função que
        avaliar_arquivos apontou, e de novo quando aparecer defeito naquele
        trecho semanas depois. É o que permite à ferramenta descobrir mais tarde
        que errou: sem episódio, o limiar continua sendo convenção herdada em
        vez de sair da evidência deste projeto.

        Registre os dois desfechos, e principalmente o segundo:

        - a sugestão foi seguida: aceita=true, com a `acao` tomada;
        - a sugestão foi ignorada: aceita=false. Este é o sinal de falso
          positivo mais barato que existe — ninguém precisa escrever relatório,
          basta registrar que não seguiu o conselho.

        Dois modos. **Episódio novo**: informe arquivo, funcao e risco (o resto
        vem do relatório). Devolve o id gerado. **Desfecho**: informe
        id_episodio e só os campos que mudaram. Os números medidos na época são
        preservados — medição não se corrige com informação que ainda não
        existia.

        Não infle o histórico: um episódio por função por decisão. Reavaliar a
        mesma função sem mudança não é episódio novo, e proporções calculadas
        sobre repetição enviesam a régua.

        Devolve confirmação com o id, o arquivo onde o histórico foi gravado e
        quantos episódios já existem.
        """
        try:
            return _registrar(
                ajuste,
                arquivo=arquivo,
                funcao=funcao,
                risco=risco,
                conselho=conselho,
                veredito=veredito,
                nota=nota,
                acao=acao,
                aceita=aceita,
                complexidade=complexidade,
                cobertura_linha=cobertura_linha,
                cobertura_branch=cobertura_branch,
                limiar=limiar,
                formula=formula,
                notas=notas,
                risco_depois=risco_depois,
                defeito=defeito,
                id_episodio=id_episodio,
            )
        except Exception as erro:
            raise _erro(erro) from erro

    @mcp.tool(annotations=SO_LEITURA_LOCAL, tags={"aprendizado"})
    def consultar_aprendizado() -> dict[str, Any]:
        """Diz o que o histórico de episódios mostra, e o que ele autoriza mudar.

        Use antes de confiar no limiar em vigor, ao revisar se a ferramenta vem
        ajudando ou incomodando, e quando quiser saber se alguma dimensão do
        julgamento deixou de valer o que custa.

        Devolve as métricas agregadas — taxa de aceitação, cobertura de risco
        (entre os trechos que deram defeito depois, quantos a ferramenta já
        apontava), distribuição por conselho e por veredito, variação de cada
        dimensão — e propostas de ajuste, cada uma com a evidência numérica que
        a sustenta.

        Nada é aplicado: as propostas vão para uma pessoa decidir. Uma
        ferramenta que recalibra sozinha os próprios critérios acaba provando
        que está certa contra um alvo que ela mesma moveu.

        Abaixo de 30 episódios a resposta é 'ainda_sem_base': com poucas
        avaliações, "60% das sugestões foram ignoradas" quer dizer "3 de 5", e 3
        de 5 é ruído. As métricas vêm mesmo assim, para acompanhamento.
        """
        try:
            return _consultar(ajuste)
        except Exception as erro:
            raise _erro(erro) from erro

    return mcp


# --------------------------------------------------------------------------- #
# Implementação das tools de aprendizado
# --------------------------------------------------------------------------- #


def _registrar(
    config: Config,
    *,
    arquivo: str,
    funcao: str,
    risco: float | None,
    conselho: str,
    veredito: str,
    nota: float | None,
    acao: str | None,
    aceita: bool | None,
    complexidade: int,
    cobertura_linha: float,
    cobertura_branch: float | None,
    limiar: float | None,
    formula: str,
    notas: dict[str, Any] | None,
    risco_depois: float | None,
    defeito: bool | None,
    id_episodio: str,
) -> dict[str, Any]:
    """Grava um episódio novo, ou anexa o desfecho de um que já existe.

    ``id_episodio`` é o que separa os dois verbos, e a separação evita o erro
    mais caro deste módulo: um desfecho registrado como episódio novo cria uma
    segunda linha com os mesmos números e o histórico passa a contar duas vezes
    a mesma avaliação, inflando toda proporção que o laço de aprendizado
    calcular depois.

    Esta função é só o despacho: cada verbo tem a própria validação e a
    própria mensagem de recusa, em :func:`_anexar_desfecho` e
    :func:`_gravar_episodio_novo`. Juntá-los num corpo só fazia as duas
    recusas compartilharem um caminho e obrigava quem lê a descobrir qual
    metade da assinatura vale em cada caso.

    **O que está em jogo se algo aqui falhar.** Nada do que já existe. O
    histórico é append-only: toda gravação é uma linha nova no fim do arquivo,
    nenhuma linha antiga é lida para ser reescrita, e uma falha no meio deixa o
    arquivo exatamente como estava. Não há atualização em lugar, não há índice
    para corromper, não há transação para ficar pela metade.

    E o alcance é uma tool, não a ferramenta. ``registrar_episodio`` é um verbo
    separado de ``avaliar_arquivos``: quando ele falha, o relatório já foi
    entregue. O que se perde é uma linha de série histórica — a ferramenta
    continua medindo, julgando e decidindo exatamente igual, só deixa de
    acumular evidência para calibrar o limiar mais tarde. Nenhum veredito muda,
    nenhum dado é exposto, nada é enviado para fora da máquina.
    """
    repositorio = config.repositorio()

    if id_episodio:
        return _anexar_desfecho(
            repositorio,
            id_episodio,
            acao=acao,
            aceita=aceita,
            risco_depois=risco_depois,
            defeito=defeito,
        )
    return _gravar_episodio_novo(
        config,
        repositorio,
        arquivo=arquivo,
        funcao=funcao,
        risco=risco,
        conselho=conselho,
        veredito=veredito,
        nota=nota,
        acao=acao,
        aceita=aceita,
        complexidade=complexidade,
        cobertura_linha=cobertura_linha,
        cobertura_branch=cobertura_branch,
        limiar=limiar,
        formula=formula,
        notas=notas,
        risco_depois=risco_depois,
        defeito=defeito,
    )


def _anexar_desfecho(
    repositorio: Repositorio,
    id_episodio: str,
    *,
    acao: str | None,
    aceita: bool | None,
    risco_depois: float | None,
    defeito: bool | None,
) -> dict[str, Any]:
    """Anexa o que se soube depois a um episódio já registrado.

    Só os quatro campos de desfecho mudam: risco, limiar, fórmula e notas são
    copiados do registro original de propósito, porque descrevem o que foi
    medido naquele dia — e medição não se corrige retroativamente com
    informação que ainda não existia.

    O ``KeyError`` do repositório vira situação nomeada porque o caso comum
    tem uma causa específica: o histórico é **por repositório**, e um id de
    outro projeto não existe aqui. A mensagem traz o arquivo onde foi
    procurado, que é o que faz a pessoa perceber isso.

    Nada é reescrito: o desfecho entra como linha nova com o mesmo id.
    Falhar aqui deixa o histórico exatamente como estava.
    """
    try:
        gravado = repositorio.registrar_desfecho(
            id_episodio,
            acao=acao,
            aceita=aceita,
            risco_depois=risco_depois,
            defeito=defeito,
        )
    except KeyError as erro:
        raise SituacaoConhecida(
            "episodio_desconhecido",
            f"não há episódio com id {id_episodio!r} no histórico deste projeto",
            "Confira o id devolvido no registro original, ou registre um episódio novo "
            "omitindo id_episodio.",
            arquivo_do_historico=str(repositorio.caminho),
        ) from erro
    return _confirmacao(repositorio, gravado, "desfecho_registrado")


def _gravar_episodio_novo(
    config: Config,
    repositorio: Repositorio,
    *,
    arquivo: str,
    funcao: str,
    risco: float | None,
    conselho: str,
    veredito: str,
    nota: float | None,
    acao: str | None,
    aceita: bool | None,
    complexidade: int,
    cobertura_linha: float,
    cobertura_branch: float | None,
    limiar: float | None,
    formula: str,
    notas: dict[str, Any] | None,
    risco_depois: float | None,
    defeito: bool | None,
) -> dict[str, Any]:
    """Grava a fotografia de uma avaliação, completando o que faltou.

    Os três campos exigidos — arquivo, função e risco — são o mínimo que torna
    o episódio comparável depois. Sem eles a linha entraria no histórico sem
    poder ser cruzada com nada, e inflaria as contagens que o laço de
    aprendizado usa para propor limiar.

    O limiar vigente é preenchido da configuração quando não vem, e não fica
    vazio: um episódio sem limiar não diz se a função estava acima ou abaixo da
    régua da época, que é exatamente a pergunta que a calibração faz depois.

    ``complexidade`` é elevada a 1 porque uma função sem desvio já tem um
    caminho, e zero indicaria função não medida — que o histórico registraria
    como a mais segura de todas.
    """
    if not arquivo or not funcao or risco is None:
        raise SituacaoConhecida(
            "episodio_incompleto",
            "um episódio novo precisa de arquivo, funcao e risco",
            "Copie esses três campos do relatório de avaliar_arquivos. Para anotar o desfecho "
            "de um episódio que já existe, informe id_episodio em vez destes campos.",
        )

    limiar_vigente = limiar
    if limiar_vigente is None:
        limiar_vigente = config.limiar_efetivo(config.obter_formula())

    gravado = repositorio.registrar(
        Episodio(
            id="",
            em="",
            arquivo=arquivo,
            funcao=funcao,
            risco=float(risco),
            formula=formula or config.formula,
            limiar_vigente=float(limiar_vigente),
            complexidade=max(1, int(complexidade)),
            cobertura_linha=float(cobertura_linha),
            cobertura_branch=None if cobertura_branch is None else float(cobertura_branch),
            notas=_normalizar_notas(notas),
            nota=None if nota is None else float(nota),
            conselho=conselho,
            veredito=veredito,
            acao=acao,
            aceita=aceita,
            risco_depois=risco_depois,
            defeito=defeito,
        )
    )
    return _confirmacao(repositorio, gravado, "episodio_registrado")


def _confirmacao(repositorio: Repositorio, gravado: Episodio, situacao: str) -> dict[str, Any]:
    """A resposta de um registro bem-sucedido, com a posição no histórico.

    O ``id_episodio`` é o que importa: é com ele que o desfecho será anexado
    semanas depois, e quem não o guardar terá de procurá-lo no JSONL à mão.

    A releitura do histórico é protegida porque **a gravação já aconteceu**.
    Um erro de disco aqui — arquivo que ficou ilegível, permissão trocada entre
    a escrita e a leitura — faria a tool responder erro sobre um episódio que
    está gravado, e quem chamou registraria de novo achando que falhou. A
    contagem some da resposta; o registro, não.
    """
    try:
        historico = repositorio.carregar()
        episodios = len(historico)
        linhas_invalidas = repositorio.linhas_invalidas
    except OSError as erro:
        _log.warning("episódio gravado, mas o histórico não pôde ser relido: %s", erro)
        episodios = linhas_invalidas = SEM_CONTAGEM
    return {
        "situacao": situacao,
        "id_episodio": gravado.id,
        "em": gravado.em,
        "arquivo": gravado.arquivo,
        "funcao": gravado.funcao,
        "aceita": gravado.aceita,
        "defeito": gravado.defeito,
        "historico": {
            "arquivo": str(repositorio.caminho),
            "episodios": episodios,
            "faltam_para_propor": (
                SEM_CONTAGEM
                if episodios == SEM_CONTAGEM
                else max(0, MINIMO_EPISODIOS - episodios)
            ),
            "linhas_invalidas": linhas_invalidas,
        },
    }


def _consultar(config: Config) -> dict[str, Any]:
    """O que o histórico deste projeto mostra, e o que ele ainda não autoriza.

    A distinção entre ``ok`` e ``ainda_sem_base`` é o ponto: abaixo de
    :data:`MINIMO_EPISODIOS` as métricas continuam sendo devolvidas, mas com a
    explicação de que servem para acompanhar e não para concluir. Esconder os
    números convidaria a registrar episódios só para destravar a resposta;
    devolvê-los sem a ressalva convidaria a concluir de três casos.

    Histórico ilegível não é erro: quem pergunta ao aprendizado de um projeto
    onde ninguém registrou nada — ou onde o arquivo ficou sem permissão de
    leitura — recebe "ainda sem base" e o motivo, que é a resposta verdadeira.
    Levantar aqui faria uma tool de consulta falhar por causa de um arquivo
    opcional.
    """
    repositorio = config.repositorio()
    try:
        episodios = repositorio.carregar()
        linhas_invalidas = repositorio.linhas_invalidas
    except OSError as erro:
        _log.warning("histórico ilegível em %s: %s", repositorio.caminho, erro)
        episodios, linhas_invalidas = [], 0
    metricas = agregar(episodios)
    propostas = propor(episodios)

    avisos = list(metricas["avisos"])
    if linhas_invalidas:
        avisos.append(
            f"{linhas_invalidas} linha(s) do histórico estavam ilegíveis e foram "
            "puladas; o resto do arquivo continua valendo"
        )

    tem_base = len(episodios) >= MINIMO_EPISODIOS
    return {
        "situacao": "ok" if tem_base else "ainda_sem_base",
        "explicacao": (
            ""
            if tem_base
            else (
                f"{len(episodios)} episódio(s) registrados; {MINIMO_EPISODIOS} é o mínimo para "
                "que qualquer proporção signifique algo. As métricas abaixo valem para "
                "acompanhar, não para concluir."
            )
        ),
        "episodios": len(episodios),
        "minimo_para_propor": MINIMO_EPISODIOS,
        "faltam": max(0, MINIMO_EPISODIOS - len(episodios)),
        "arquivo_do_historico": str(repositorio.caminho),
        "metricas": metricas,
        "propostas": list(propostas),
        "motivo_das_propostas": propostas.motivo,
        "avisos": avisos,
    }


def main() -> None:
    """Sobe o servidor por stdio — o ponto de entrada do executável ``jev-crap-mcp``.

    Stdio é o transporte certo aqui porque a ferramenta lê o código do disco da
    própria máquina: um servidor em rede precisaria receber o repositório junto,
    o que muda o problema inteiro.

    O log vai para stderr de propósito: stdout é o canal do protocolo MCP, e
    qualquer coisa impressa lá corrompe a conversa com o cliente.

    Falha na montagem vira mensagem em stderr e saída
    :data:`ERRO_DE_CONFIGURACAO`, nunca traceback: o cliente MCP mata o processo
    filho e descarta o que ele imprimiu de forma não estruturada, então um
    servidor que morre calado aparece para a pessoa como "não conecta", sem
    pista nenhuma de que o problema é a régua ou a configuração.
    """
    logging.basicConfig(level=logging.WARNING)
    # Um `.env` na raiz do projeto é o jeito mais comum de a chave existir na
    # máquina de quem desenvolve. O ambiente continua vencendo (`setdefault`),
    # então o que o cliente MCP passou na configuração não é sobrescrito — e é
    # por isso que carregar aqui é seguro, e não um atalho.
    carregar_env()
    try:
        servidor = criar_servidor()
    except SituacaoConhecida as erro:
        # A montagem falhou por configuração — régua ilegível, quase sempre. O
        # cliente MCP descarta o traceback de um processo filho que morre, então
        # sem esta tradução a pessoa vê "o servidor não conecta" e nada mais.
        print(erro.para_texto(), file=sys.stderr, flush=True)
        raise SystemExit(ERRO_DE_CONFIGURACAO) from erro
    except Exception as erro:  # noqa: BLE001 - morrer calado é o pior desfecho aqui
        print(
            f"jev-crap-mcp não subiu: {type(erro).__name__}: {erro}\n"
            "Rode `jev-crap --diagnostico` para ver versão, dependências e onde a chave "
            "foi procurada.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(ERRO_DE_CONFIGURACAO) from erro
    servidor.run(show_banner=False)


if __name__ == "__main__":  # pragma: no cover - conveniência de execução direta
    main()
