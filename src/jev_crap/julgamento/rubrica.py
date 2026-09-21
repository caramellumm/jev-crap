"""A régua: quais perguntas são feitas, o que cada nível significa, quanto pesa.

A rubrica mora em ``perguntas.json`` e não no código por um motivo prático: a
redação dos níveis é o que faz o modelo distinguir um nível do outro, então
mexer nela é ajuste de conteúdo — revisado como texto, sem tocar em lógica de
transporte ou de decisão. Este módulo é quem lê esse arquivo, confere que ele
faz sentido e o entrega em pedaços que o resto do programa consegue usar.

Quatro grupos, e a diferença entre eles é a única coisa que realmente importa
entender aqui:

``qualidade``
    Dimensões **compensáveis**: legibilidade boa compensa tratamento de erro
    mediano, e a média ponderada delas é a nota. Só este grupo tem peso.

``contexto``
    Não entra na nota e não barra nada. Decide *o que fazer* e *com que pressa*
    — consequência de falha é exatamente o que a fórmula CRAP ignora, e
    complexidade essencial é o que separa "escreva teste" de "refatore antes".

``risco_grave``
    **Gates.** Ficam fora da nota porque risco não se compensa com legibilidade:
    uma função com injeção de SQL e nota 95 continua sendo uma função com
    injeção de SQL. São restritos a proposições que ou valem ou não valem e
    cuja consequência não se discute.

``risco_atencao``
    Mandam para olho humano, nunca barram. São perguntas graduais disfarçadas
    de proposição: "existe entrada plausível que quebraria isto?" é verdade
    para quase toda função escrita em linguagem dinâmica. Medido na geração
    anterior deste projeto: 125 de 145 funções acima de 0.50 e 32 acima de
    0.80 — entre elas uma função de 5 linhas, complexidade 1, com 100% de
    cobertura. Como gate de bloqueio isso não separa nada; como lista de
    revisão, é informação legítima.

A validação é feita na carga, e é deliberadamente barulhenta: um peso somando
0.99 ou um grupo escrito errado precisa quebrar aqui, no início do processo,
não virar uma nota silenciosamente errada num relatório que alguém vai ler
como se fosse medida.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ARQUIVO_PADRAO",
    "Dimensao",
    "GRUPOS",
    "Rubrica",
    "RubricaInvalida",
    "carregar_rubrica",
]

_log = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path(__file__).with_name("perguntas.json")

GRUPO_QUALIDADE = "qualidade"
GRUPO_CONTEXTO = "contexto"
GRUPO_RISCO_GRAVE = "risco_grave"
GRUPO_RISCO_ATENCAO = "risco_atencao"

GRUPOS: tuple[str, ...] = (
    GRUPO_QUALIDADE,
    GRUPO_CONTEXTO,
    GRUPO_RISCO_GRAVE,
    GRUPO_RISCO_ATENCAO,
)

TIPOS = ("score", "noul")

#: Direções possíveis de uma escala. Declarar o sentido como dado, em vez de
#: deixá-lo implícito na ordem dos níveis, existe por causa de um erro
#: específico e caro: `consequencia_de_falha` é a única dimensão em que nota
#: alta é má notícia, e inverter essa leitura despriorizaria justamente o
#: código perigoso — silenciosamente, porque o número continuaria plausível.
SENTIDOS = (
    "maior_melhor",
    "maior_pior",
    "maior_mais_em_jogo",
    "maior_mais_essencial",
)


class RubricaInvalida(ValueError):
    """O arquivo de perguntas não descreve uma régua utilizável."""


@dataclass(frozen=True)
class Dimensao:
    """Uma pergunta da régua, com o que o código precisa saber sobre ela."""

    nome: str
    grupo: str
    tipo: str
    sentido: str
    peso: float
    """Participação na nota. Zero fora do grupo ``qualidade``."""

    exige: str | None
    """Campo do estado que precisa estar preenchido para a pergunta ser feita.

    Só ``teste_verifica`` usa: sem trecho de teste para olhar, a resposta seria
    "não há teste" para toda função privada exercitada indiretamente — medido
    na geração anterior: 87 de 145 funções chegavam sem trecho, e 78 delas
    tinham 80% ou mais de cobertura de linha. O programa já sabia que estavam
    testadas; perguntar assim mesmo trocava um fato por um palpite mal
    informado, e cobrava um quarto da nota por ele.
    """

    pergunta: Mapping[str, Any]
    """O objeto enviado à API, exatamente como ela o espera."""

    @property
    def niveis(self) -> int:
        """Quantidade de níveis de um ``score``; 0 para ``noul``.

        Devolve 0 — e não levanta — quando ``criteria`` está ausente ou com o
        tipo errado, porque quem chama já trata o 0: ``_ler_dimensoes`` recusa a
        régua com menos de dois níveis, e ``normalizar`` recusa dividir por
        zero. Levantar aqui trocaria essas duas mensagens específicas, que
        dizem qual dimensão está torta, por um erro de atributo cru.

        ``noul`` devolve 0 de propósito: ele não tem níveis, já chega em 0..1,
        e devolver 1 ou 2 faria ``normalizar`` dividir uma probabilidade por um
        número — o resultado continuaria entre 0 e 1 e ninguém perceberia.
        """
        if self.tipo != "score":
            return 0
        criterios = self.pergunta.get("criteria")
        return len(criterios) if isinstance(criterios, list) else 0

    def descrever_niveis(self) -> list[str]:
        """Os níveis em texto, do pior (0) ao melhor, para o relatório explicar a régua."""
        criterios = self.pergunta.get("criteria")
        if self.tipo == "noul":
            if isinstance(criterios, Mapping):
                return [f"não: {criterios.get('false', '')}", f"sim: {criterios.get('true', '')}"]
            return []
        if not isinstance(criterios, list):
            return []
        return [c["what"] if isinstance(c, Mapping) else str(c) for c in criterios]


class Rubrica:
    """A régua inteira, já conferida, com as consultas que o resto do código faz."""

    def __init__(self, bruto: Mapping[str, Any]) -> None:
        """Lê a régua e a confere antes de existir como objeto.

        Uma ``Rubrica`` que existe é uma régua válida: os pesos já fecham, toda
        dimensão tem grupo conhecido e texto legível. Conferir aqui, e não em
        quem pergunta, é o que permite ao resto do código tratá-la como dado e
        não como possibilidade — e é o que faz um erro de redação no JSON
        aparecer na carga da régua, com o nome da dimensão, em vez de virar uma
        pergunta malformada que a API recusa com HTTP 400.

        Recusar aqui é erro de **configuração**, não falha em produção: a régua
        é lida e conferida na montagem, antes de qualquer arquivo ser medido e
        antes de qualquer requisição paga. As duas entradas traduzem isso em
        erro de uso — saída 3 na CLI, situação conhecida no MCP. Nada é escrito
        em disco, nada é enviado, nenhum relatório anterior muda. O que se
        perde é a execução que ainda não começou.
        """
        if not isinstance(bruto, Mapping):
            raise RubricaInvalida(
                f"a régua precisa ser um objeto JSON; veio {type(bruto).__name__}"
            )
        self.versao: str = str(bruto.get("versao", "desconhecida"))
        self.dimensoes: dict[str, Dimensao] = _ler_dimensoes(bruto)
        _conferir_pesos(self.dimensoes)

    # -- consultas por grupo -------------------------------------------------

    def do_grupo(self, grupo: str) -> dict[str, Dimensao]:
        """As dimensões de um grupo, recusando nome de grupo que não existe.

        Sem a conferência, um grupo escrito errado devolve ``{}`` e o efeito é
        invisível: as perguntas daquele grupo simplesmente não seriam feitas, a
        nota sairia calculada sobre menos dimensões e o relatório não teria
        como dizer que algo faltou. É o tipo de erro que se paga em requisição
        e em confiança no número, sem nunca aparecer como erro.

        Recusar aqui é erro de **configuração**, não falha em produção: a régua
        é lida e conferida na montagem, antes de qualquer arquivo ser medido e
        antes de qualquer requisição paga. As duas entradas traduzem isso em
        erro de uso — saída 3 na CLI, situação conhecida no MCP. Nada é escrito
        em disco, nada é enviado, nenhum relatório anterior muda. O que se
        perde é a execução que ainda não começou.
        """
        if grupo not in GRUPOS:
            raise RubricaInvalida(
                f"grupo {grupo!r} desconhecido; a régua tem {', '.join(GRUPOS)}"
            )
        return {n: d for n, d in self.dimensoes.items() if d.grupo == grupo}

    @property
    def qualidade(self) -> dict[str, Dimensao]:
        """Dimensões compensáveis, as únicas que formam a nota.

        Conferido a cada consulta, e não só na carga: a régua pode ser
        substituída por arquivo do usuário (ver ``carregar_rubrica``), e um
        grupo de qualidade vazio produziria nota calculada sobre nada — um
        número entre 0 e 100 que não é média de coisa nenhuma, e que ninguém
        consegue distinguir de uma nota legítima olhando o relatório.

        Recusar aqui é erro de **configuração**, não falha em produção: a régua
        é lida e conferida na montagem, antes de qualquer arquivo ser medido e
        antes de qualquer requisição paga. As duas entradas traduzem isso em
        erro de uso — saída 3 na CLI, situação conhecida no MCP. Nada é escrito
        em disco, nada é enviado, nenhum relatório anterior muda. O que se
        perde é a execução que ainda não começou.
        """
        dimensoes = self.do_grupo(GRUPO_QUALIDADE)
        if not dimensoes:
            raise RubricaInvalida(
                f"nenhuma dimensão no grupo '{GRUPO_QUALIDADE}': não haveria nota"
            )
        return dimensoes

    @property
    def contexto(self) -> dict[str, Dimensao]:
        """Dimensões que priorizam sem entrar na nota.

        Pode estar vazio, como ``risco_atencao``: sem contexto a ferramenta
        ainda mede, julga e decide — só deixa de saber se uma falha corrompe
        dado ou desalinha um log, e todas as funções acabam na mesma pressa.
        Isso degrada a priorização, não falsifica o resultado, e por isso é
        registrado em log em vez de levantar.
        """
        dimensoes = self.do_grupo(GRUPO_CONTEXTO)
        if not dimensoes:
            _log.info(
                "régua sem dimensões em '%s': a prioridade sairá só do risco contável",
                GRUPO_CONTEXTO,
            )
        return dimensoes

    @property
    def risco_grave(self) -> dict[str, Dimensao]:
        """Proposições verificáveis que barram o código.

        O grupo precisa existir, e a recusa é na direção segura: em vez de
        avaliar com uma régua incompleta, a ferramenta não avalia. Uma execução
        que não começa, com mensagem dizendo o que editar no JSON, é o desfecho
        mais barato possível — nenhum dado muda, nenhum relatório anterior é
        alterado, nada é exposto e nenhuma requisição é paga.

        O contrário é que seria caro: seguir com o grupo vazio significaria
        rodar sem gate nenhum, e o relatório não teria como avisar disso.
        """
        dimensoes = self.do_grupo(GRUPO_RISCO_GRAVE)
        if not dimensoes:
            raise RubricaInvalida(
                f"a régua precisa de ao menos uma dimensão no grupo '{GRUPO_RISCO_GRAVE}'; "
                "acrescente-a ao JSON da régua e rode de novo"
            )
        return dimensoes
        """Gates: acima do limiar de bloqueio, barram o código."""
        return self.do_grupo(GRUPO_RISCO_GRAVE)

    @property
    def risco_atencao(self) -> dict[str, Dimensao]:
        """Mandam para revisão humana; nunca barram.

        Este grupo **pode** estar vazio, e a diferença em relação a
        ``risco_grave`` é deliberada: sem gate de atenção a ferramenta perde
        linhas de revisão, o que degrada o relatório; sem gate grave ela perde
        a capacidade de barrar, o que o falsifica. Por isso um levanta e o
        outro só registra no log.
        """
        dimensoes = self.do_grupo(GRUPO_RISCO_ATENCAO)
        if not dimensoes:
            _log.info(
                "régua sem dimensões em '%s': nenhuma função receberá linha de revisão "
                "por risco, só por nota e por gate grave",
                GRUPO_RISCO_ATENCAO,
            )
        return dimensoes

    @property
    def dimensoes_de_risco(self) -> dict[str, Dimensao]:
        """Os dois grupos de risco juntos, conferindo que não se sobrepõem.

        O nome diz "dimensões" e não "risco" de propósito: o que sai daqui é a
        régua daquelas perguntas, não um número de risco. Este projeto usa
        ``risco`` para o valor contável em toda parte, e os dois sob o mesmo
        nome tornam impossível procurar por um sem achar o outro.

        Um nome nos dois grupos seria ambíguo de um jeito perigoso: a mesma
        dimensão barraria e aconselharia, e qual das duas coisas acontece
        dependeria da ordem do ``**`` aqui — uma decisão de bloqueio decidida
        por ordem de dicionário. Impossível pela estrutura da régua (cada
        dimensão declara um grupo só), mas esta propriedade é o único lugar
        onde os dois se encontram, então é aqui que a garantia é barata.

        Recusar aqui é erro de **configuração**, não falha em produção: a régua
        é lida e conferida na montagem, antes de qualquer arquivo ser medido e
        antes de qualquer requisição paga. As duas entradas traduzem isso em
        erro de uso — saída 3 na CLI, situação conhecida no MCP. Nada é escrito
        em disco, nada é enviado, nenhum relatório anterior muda. O que se
        perde é a execução que ainda não começou.
        """
        graves = self.risco_grave
        atencao = self.risco_atencao
        repetidos = sorted(set(graves) & set(atencao))
        if repetidos:
            raise RubricaInvalida(
                f"{repetidos} aparece nos dois grupos de risco; barrar e aconselhar "
                "não podem depender da ordem em que os grupos são unidos"
            )
        return {**graves, **atencao}

    @property
    def pesos(self) -> dict[str, float]:
        """Peso de cada dimensão de qualidade, conferido contra 1.0.

        A soma é reconferida aqui, e não só na carga, porque este dicionário é
        o divisor da nota: pesos somando 0.9 produzem nota sistematicamente
        alta, e 1.1, sistematicamente baixa. Nos dois casos o número continua
        entre 0 e 100 e parece uma nota — o erro não se denuncia sozinho, e é
        por isso que ele é conferido toda vez em vez de uma vez só.

        Recusar aqui é erro de **configuração**, não falha em produção: a régua
        é lida e conferida na montagem, antes de qualquer arquivo ser medido e
        antes de qualquer requisição paga. As duas entradas traduzem isso em
        erro de uso — saída 3 na CLI, situação conhecida no MCP. Nada é escrito
        em disco, nada é enviado, nenhum relatório anterior muda. O que se
        perde é a execução que ainda não começou.
        """
        pesos = {n: d.peso for n, d in self.qualidade.items()}
        total = round(sum(pesos.values()), 6)
        if total != 1.0:
            raise RubricaInvalida(
                f"os pesos das dimensões de qualidade somam {total}, não 1.0 — "
                "uma nota calculada sobre pesos que não fecham não é média de nada"
            )
        return pesos

    # -- uso ------------------------------------------------------------------

    def perguntas_para(self, estado: Mapping[str, Any]) -> dict[str, Any]:
        """As perguntas que *este* estado tem como responder.

        Uma dimensão com ``exige`` só entra quando o campo correspondente do
        estado tem conteúdo. O peso dela não vira zero: ele é redistribuído
        entre as dimensões que foram de fato observadas (ver
        :meth:`pesos_observados`). Nota calculada sobre três dimensões é
        honesta; nota calculada sobre quatro com uma delas inventada, não.
        """
        return {
            nome: dict(d.pergunta)
            for nome, d in self.dimensoes.items()
            if d.exige is None or estado.get(d.exige)
        }

    def pesos_observados(self, respondidas: Mapping[str, Any]) -> dict[str, float]:
        """Pesos renormalizados para somar 1.0 sobre o que realmente foi respondido."""
        presentes = {n: p for n, p in self.pesos.items() if n in respondidas}
        total = sum(presentes.values())
        if not total:
            return {}
        return {n: p / total for n, p in presentes.items()}

    def normalizar(self, nome: str, bruto: float) -> float:
        """Traz a resposta para 0..1, seja ela ``score`` ou ``noul``.

        A API devolve o ``score`` como média dos níveis ponderada pelas
        probabilidades, então uma escala de três níveis vai de 0 a 2 — dividir
        por 1 aqui daria nota acima de 100 e ninguém perceberia, porque o número
        continuaria parecendo plausível. ``noul`` já chega em 0..1 e passa
        inteiro.

        O ``RubricaInvalida`` daqui só acontece com régua de usuário mal
        escrita: a que vem no pacote é conferida na carga, e ``_ler_dimensoes``
        já recusa score com menos de dois níveis. Quando acontece, acontece na
        conversão da primeira resposta — o relatório ainda não existe, nada foi
        escrito, e as duas entradas traduzem o erro em erro de uso.
        """
        dimensao = self.dimensoes[nome]
        if dimensao.tipo == "noul":
            return bruto
        divisor = dimensao.niveis - 1
        if divisor <= 0:
            raise RubricaInvalida(f"{nome}: um score precisa de ao menos dois níveis")
        return bruto / divisor

    def para_rubrica(self) -> dict[str, Any]:
        """A régua em formato serializável, para a ferramenta poder explicar a si mesma.

        Este dicionário sai na resposta de ``explicar_criterios`` e atravessa
        uma fronteira JSON, então nenhum campo pode deixar de serializar: a
        régua vem de arquivo do usuário, e um valor exótico dentro de
        ``instructions`` derrubaria a resposta inteira com erro de protocolo —
        trocando a explicação dos critérios por nada. ``_texto_de`` converte o
        que não for texto e nunca levanta.

        O peso aparece como ``None`` fora do grupo de qualidade, e não como
        ``0``: zero leria como "entra na nota com peso nenhum", quando o fato é
        que a dimensão não entra na nota. São coisas diferentes e a segunda é a
        verdadeira.

        Falhar aqui não corromperia dado nem perderia trabalho: é uma leitura
        da régua já validada, sem escrita e sem rede.
        """
        return {
            "versao": _texto_de(self.versao),
            "dimensoes": {
                nome: {
                    "grupo": d.grupo,
                    "tipo": d.tipo,
                    "sentido": d.sentido,
                    "peso": d.peso if d.grupo == GRUPO_QUALIDADE else None,
                    "pergunta": _texto_de(d.pergunta.get("instructions", "")),
                    "niveis": d.descrever_niveis(),
                    "condicionada_a": d.exige,
                }
                for nome, d in self.dimensoes.items()
            },
        }


def _texto_de(valor: Any) -> str:
    """O valor como texto, sem nunca levantar.

    A régua pode vir de arquivo do usuário, então qualquer campo pode conter
    qualquer coisa que o JSON aceite — e ``explicar_criterios`` precisa
    responder mesmo assim. Um ``__str__`` que estoura vira marcador em vez de
    derrubar a resposta.
    """
    if isinstance(valor, str):
        return valor
    try:
        return str(valor)
    except Exception:  # noqa: BLE001 - explicar a régua nunca pode falhar por um campo
        return f"<{type(valor).__name__} não textualizável>"


def _ler_dimensoes(bruto: Mapping[str, Any]) -> dict[str, Dimensao]:
    """Converte o JSON da régua em dimensões, recusando tudo que não fecha.

    A validação é deliberadamente barulhenta e acontece **na carga**: um grupo
    escrito errado ou uma instrução vazia precisa quebrar no início do
    processo, não virar uma pergunta malformada que a API recusa com HTTP 400 —
    ou, pior, uma pergunta aceita e respondida sobre outra coisa.

    Cada recusa nomeia a dimensão culpada. É a diferença entre "a régua está
    inválida" e "complexidade_cognitiva: um score precisa de ao menos dois
    níveis", e só a segunda diz o que editar.

    Falhar aqui é erro de configuração: acontece antes de qualquer medição e
    de qualquer requisição paga, nada é escrito em disco, e as duas entradas
    traduzem o erro em erro de uso (saída 3 na CLI, situação conhecida no MCP).
    """
    cruas = bruto.get("dimensoes")
    if not isinstance(cruas, Mapping) or not cruas:
        raise RubricaInvalida("a régua precisa de um objeto 'dimensoes' não vazio")

    dimensoes: dict[str, Dimensao] = {}
    for nome, corpo in cruas.items():
        if not isinstance(corpo, Mapping):
            raise RubricaInvalida(f"{nome}: a dimensão precisa ser um objeto")
        grupo = corpo.get("grupo")
        if grupo not in GRUPOS:
            raise RubricaInvalida(
                f"{nome}: grupo {grupo!r} desconhecido; use um de {', '.join(GRUPOS)}"
            )
        sentido = corpo.get("sentido")
        if sentido not in SENTIDOS:
            raise RubricaInvalida(
                f"{nome}: sentido {sentido!r} desconhecido; use um de {', '.join(SENTIDOS)}"
            )
        pergunta = corpo.get("pergunta")
        if not isinstance(pergunta, Mapping):
            raise RubricaInvalida(f"{nome}: falta o objeto 'pergunta'")
        tipo = pergunta.get("type")
        if tipo not in TIPOS:
            raise RubricaInvalida(f"{nome}: tipo {tipo!r} desconhecido; use 'score' ou 'noul'")
        if not str(pergunta.get("instructions", "")).strip():
            raise RubricaInvalida(f"{nome}: 'instructions' não pode ser vazio")

        peso = float(corpo.get("peso", 0.0))
        if grupo == GRUPO_QUALIDADE and peso <= 0:
            raise RubricaInvalida(f"{nome}: dimensão de qualidade precisa de peso positivo")
        if grupo != GRUPO_QUALIDADE and peso:
            raise RubricaInvalida(
                f"{nome}: só dimensão do grupo '{GRUPO_QUALIDADE}' tem peso — "
                "peso fora dele daria a impressão de que a dimensão entra na nota"
            )

        dimensao = Dimensao(
            nome=str(nome),
            grupo=str(grupo),
            tipo=str(tipo),
            sentido=str(sentido),
            peso=peso,
            exige=(str(corpo["exige"]) if corpo.get("exige") else None),
            pergunta=dict(pergunta),
        )
        if dimensao.tipo == "score" and dimensao.niveis < 2:
            raise RubricaInvalida(f"{nome}: um score precisa de ao menos dois níveis")
        dimensoes[dimensao.nome] = dimensao
    return dimensoes


def _conferir_pesos(dimensoes: Mapping[str, Dimensao]) -> None:
    """Recusa, na carga, a régua cujos pesos de qualidade não somam 1.0.

    A mesma conferência existe em :attr:`Rubrica.pesos`, e a repetição é de
    propósito: aqui ela falha **na carga**, com a régua inteira em mãos e
    antes de qualquer requisição paga; lá ela protege o divisor da nota contra
    uma régua construída por outro caminho. A primeira dá a boa mensagem, a
    segunda dá a garantia.
    """
    pesos = [d.peso for d in dimensoes.values() if d.grupo == GRUPO_QUALIDADE]
    if not pesos:
        raise RubricaInvalida(f"nenhuma dimensão no grupo '{GRUPO_QUALIDADE}': não haveria nota")
    total = round(sum(pesos), 6)
    if total != 1.0:
        raise RubricaInvalida(
            f"os pesos das dimensões de qualidade somam {total}, não 1.0 — "
            "uma nota calculada sobre pesos que não fecham não é média de nada"
        )


def carregar_rubrica(caminho: Path | str | None = None) -> Rubrica:
    """Lê e valida a régua. Sem argumento, a que vem com o pacote.

    As duas falhas de leitura viram ``RubricaInvalida`` com o caminho na
    mensagem, em vez de subirem cruas: ``FileNotFoundError`` não diz onde foi
    procurado quando o caminho veio de configuração, e o ``JSONDecodeError``
    do Python fala de linha e coluna de um arquivo que ele não nomeia. Quem lê
    isso costuma ser um agente, e "não encontrada em /caminho/x.json" é
    acionável do jeito que um traceback não é.

    Falhar aqui é erro de configuração e acontece na montagem: nenhum arquivo
    foi medido, nenhuma requisição foi paga, nada foi escrito. As duas entradas
    traduzem em erro de uso — saída 3 na CLI, situação conhecida no MCP.
    """
    destino = Path(caminho) if caminho is not None else ARQUIVO_PADRAO
    try:
        bruto = json.loads(destino.read_text(encoding="utf-8"))
    except FileNotFoundError as erro:
        raise RubricaInvalida(f"régua não encontrada em {destino}") from erro
    except json.JSONDecodeError as erro:
        raise RubricaInvalida(f"{destino} não é JSON válido: {erro}") from erro
    return Rubrica(bruto)
