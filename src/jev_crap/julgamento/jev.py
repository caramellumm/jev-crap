"""Eixo semântico: a conversa com o Jev, e só ela.

A métrica conta caminhos e linhas. Ela não sabe se a complexidade vem do
domínio ou da escrita, nem se os testes verificam comportamento ou apenas
executam linhas, nem quanto custa a falha. Quem responde isso é o Jev, com
perguntas tipadas. Este módulo cuida do transporte e da tradução da resposta —
a régua está em :mod:`rubrica` e a decisão em :mod:`jev_crap.avaliacao`.

Quatro decisões guiam o módulo:

1. **Uma dimensão por pergunta, todas no mesmo pedido.** Elas rodam em paralelo
   do lado da API e não veem as respostas umas das outras — por isso nenhuma
   pergunta pede a decisão final: essa depende de todas e é montada em código.
   Misturar dimensões numa escala só ("qualidade geral") espalharia a
   probabilidade entre níveis que medem coisas diferentes, e a confiança cairia
   justamente onde ela mais importa.

2. **`noul` não tem confiança, e isso não é omissão.** A documentação da API é
   explícita: a distribuição de um Noul tem só dois desfechos, então o próprio
   número já a descreve por inteiro. Inventar ``confianca=1.0`` para uniformizar
   o tipo faria um 0.5 — que significa "o modelo não se decidiu" — se apresentar
   como certeza absoluta. Aqui o campo fica ``None``, e quem consome trata a
   ausência como ausência.

3. **Só vai no estado o que o modelo não vê no texto.** Cobertura e trechos de
   teste sim; complexidade ciclomática não. Mandar o ccn ancoraria o julgamento
   no número que nós mesmos enviamos, e a graça de ter dois eixos é que o
   segundo seja independente do primeiro. A documentação do modelo reforça pelo
   outro lado: acurácia cai à medida que o estado cresce com conteúdo que não
   decide nada.

4. **Falha degrada, não quebra.** Sem chave, com erro de rede, com resposta fora
   do contrato ou com a API fora do ar, o julgamento devolve ``{}`` e registra o
   motivo. O eixo contável continua valendo sozinho e o relatório diz que o
   semântico está desligado. Levantar exceção aqui transformaria uma
   indisponibilidade de terceiro em falha da ferramenta inteira.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx

from jev_crap.julgamento.rubrica import Rubrica

__all__ = [
    "JulgadorDesligado",
    "JulgadorFake",
    "JulgadorJev",
    "Julgador",
    "Resposta",
    "URL_API",
    "VARIAVEL_DA_CHAVE",
    "extrair_respostas",
    "montar_estado",
    "obter_julgador",
]

_log = logging.getLogger(__name__)

URL_API = "https://api.typesafe.ai/v1/systemone"
MODELO_PADRAO = "jev-latest"
VARIAVEL_DA_CHAVE = "TYPESAFE_API_KEY"
VARIAVEL_DO_MODELO = "JEV_CRAP_MODELO"
TIMEOUT_SEGUNDOS = 60.0
MAX_TENTATIVAS = 4

#: 429 (excesso de pedidos) e 529 (sobrecarga) são os dois que a API documenta
#: como transitórios. Os 5xx de proxy entram porque quem está no meio do
#: caminho também cai, e uma requisição perdida num gateway não é motivo para
#: descartar a função. O 529 é justamente o que aparece sob a concorrência que
#: esta ferramenta usa: a rajada que provoca a sobrecarga é a própria batelada.
STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504, 529})
ESPERA_BASE_SEGUNDOS = 1.0
JITTER_MAXIMO_SEGUNDOS = 0.5

#: Teto da espera entre tentativas. A base dobra a cada tentativa, então com
#: muitas tentativas configuradas a espera passaria de qualquer timeout de
#: cliente MCP — e a tool pareceria travada em vez de lenta.
ESPERA_MAXIMA_SEGUNDOS = 30.0


@dataclass(frozen=True)
class Resposta:
    """Uma pergunta respondida, com o tipo preservado.

    ``bruto`` é o número como a API o devolveu: para ``score``, a média dos
    níveis ponderada pelas probabilidades (0 a níveis−1); para ``noul``, a
    probabilidade de "sim" (0 a 1). ``normalizado`` é o mesmo valor trazido para
    0..1, que é a escala em que a nota e os limiares trabalham.

    Os dois são guardados porque respondem perguntas diferentes: ``normalizado``
    é o que o código compara, ``bruto`` é o que permite conferir o resultado
    contra a régua original quando alguém discorda do relatório.
    """

    tipo: str
    bruto: float
    normalizado: float
    confianca: float | None
    """``None`` em ``noul``, sempre — ver a decisão 2 no topo do módulo."""

    probabilidades: Mapping[str, float] | None = None
    """Distribuição por nível, quando a API a envia.

    Vale guardar porque o score sozinho é ambíguo: 1.0 tanto pode ser toda a
    probabilidade no nível 1 quanto metade no 0 e metade no 2, e as duas
    situações pedem reações diferentes.
    """

    @property
    def dispersa(self) -> bool:
        """Se o modelo não se decidiu o bastante para o número valer como veredito.

        Só faz sentido em ``score``: num ``noul``, dispersão já está dentro do
        próprio valor, e um 0.5 se denuncia sozinho.
        """
        return self.confianca is not None and self.confianca < CONFIANCA_MINIMA


#: Abaixo disto a nota de um score vira dúvida em vez de veredito. Não é
#: calibração: é o ponto em que a distribuição está espalhada o bastante para
#: que escrever "a complexidade é acidental" transfira ao leitor uma certeza
#: que o modelo não teve.
CONFIANCA_MINIMA = 0.45


@runtime_checkable
class Julgador(Protocol):
    """Contrato do eixo semântico.

    Implementações nunca levantam exceção: falha vira dicionário vazio. Assim o
    chamador não precisa de try/except para manter o eixo contável de pé.
    """

    ativo: bool

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
        """Devolve ``{"respostas": {...}, "modelo": str, "usage": {...}}`` ou ``{}``.

        Dicionário vazio é o contrato para *qualquer* falha — rede, chave,
        contrato de resposta, eixo desligado. É o que permite ao chamador
        cruzar os dois eixos sem ``try``/``except``: o eixo contável continua
        de pé e o relatório diz que o semântico não respondeu.

        O corpo levanta em vez de ser ``...`` porque ``...`` devolveria ``None``
        numa implementação incompleta, e ``None`` não é ``{}``: quem espera um
        mapa faria ``.get`` em ``None`` e estouraria com ``AttributeError``
        dentro do laço de funções, longe da classe que esqueceu o método.

        Este corpo não roda em produção. Ele só é alcançado por uma classe que
        herda o protocolo e não implementa o método — situação que estoura na
        primeira chamada, em desenvolvimento, antes de qualquer avaliação
        existir. As três implementações do pacote sobrescrevem o método, e a
        suíte confere isso. Nada é lido, escrito ou enviado aqui.
        """
        raise NotImplementedError(
            f"{type(self).__name__} não implementa julgar(estado, rubrica) -> dict; "
            "toda falha deve virar {} e nunca exceção"
        )


def montar_estado(
    codigo: str,
    linguagem: str,
    testes: list[str] | None = None,
    cobertura_branch: float | None = None,
    max_linhas: int = 400,
) -> dict[str, Any]:
    """Monta o ``state`` da requisição com campos nomeados.

    Campos separados (em vez de um texto só) deixam as perguntas apontarem para
    a parte certa — `codigo`, `testes`, `cobertura_branch` — sem o modelo ter
    que adivinhar onde uma coisa termina e a outra começa.

    Função maior que ``max_linhas`` vai truncada **com aviso dentro do próprio
    estado**. O aviso não é cortesia: sem ele o modelo julgaria casos-limite de
    um pedaço achando que viu a função inteira, e responderia com a confiança de
    quem viu tudo.

    Três conferências, e todas existem porque o que sai daqui vira o corpo de
    uma requisição paga:

    - **``codigo`` precisa ser texto.** Qualquer outra coisa estouraria no
      ``splitlines``, com uma mensagem que não diz qual função estava sendo
      montada;
    - **``max_linhas`` precisa ser ao menos 1.** Zero produz um ``codigo``
      vazio e um aviso dizendo que a função tem N linhas — o modelo julgaria
      texto nenhum e responderia mesmo assim;
    - **``cobertura_branch`` só entra se for número finito.** ``nan`` viraria
      ``"cobertura_branch": NaN`` no JSON, que não é JSON válido e faria a
      requisição ser recusada com HTTP 400 — um erro de protocolo no lugar de
      um julgamento.
    """
    if not isinstance(codigo, str):
        raise TypeError(f"`codigo` precisa ser texto; veio {type(codigo).__name__}")
    if max_linhas < 1:
        raise ValueError(f"max_linhas precisa ser ao menos 1; veio {max_linhas!r}")
    linhas = codigo.splitlines()
    estado: dict[str, Any] = {"linguagem": str(linguagem) if linguagem else "desconhecida"}
    if len(linhas) > max_linhas:
        estado["codigo"] = "\n".join(linhas[:max_linhas])
        estado["aviso"] = (
            f"a função tem {len(linhas)} linhas; `codigo` traz apenas as {max_linhas} primeiras"
        )
    else:
        estado["codigo"] = codigo
    if testes:
        estado["testes"] = list(testes)
    # Cobertura de branch entra porque o modelo não a vê no texto; a
    # complexidade ciclomática fica de fora de propósito (decisão 3 no topo).
    if isinstance(cobertura_branch, (int, float)) and not isinstance(cobertura_branch, bool):
        if math.isfinite(cobertura_branch):
            estado["cobertura_branch"] = round(float(cobertura_branch), 2)
    return estado


class JulgadorJev:
    """Implementação real: uma requisição HTTP com todas as perguntas juntas.

    O cliente HTTP, o relógio e o sorteio do jitter entram pelo construtor para
    que o teste use um transporte falso e não durma de verdade — nenhum teste
    unitário toca a API.
    """

    ativo = True

    def __init__(
        self,
        chave: str,
        *,
        cliente: httpx.Client | None = None,
        modelo: str = MODELO_PADRAO,
        max_tentativas: int = MAX_TENTATIVAS,
        timeout: float = TIMEOUT_SEGUNDOS,
        dormir: Any = time.sleep,
        sortear: Any = random.random,
    ) -> None:
        """Guarda a configuração do transporte, sem abrir conexão nenhuma.

        A chave é conferida aqui, e não na primeira chamada, porque é a
        diferença entre falhar na montagem e falhar depois de o recorte inteiro
        já ter sido feito: uma chave vazia produziria ``Bearer `` no cabeçalho,
        a API responderia 401 e o diagnóstico apareceria como "erro de rede"
        no meio de uma batelada paga.

        ``max_tentativas`` é elevado a 1 em vez de recusado: zero tentativa é
        um pedido incoerente (quem passa zero quer "não insista", e uma
        tentativa é o mínimo que faz sentido), e recusá-lo derrubaria a
        montagem por um valor que tem leitura óbvia.
        """
        if not isinstance(chave, str) or not chave.strip():
            raise ValueError(
                f"{VARIAVEL_DA_CHAVE} chegou vazia ao julgador; sem chave use "
                "JulgadorDesligado, que diz por que o eixo semântico não respondeu"
            )
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError(
                f"timeout precisa ser um número de segundos maior que zero; veio {timeout!r}"
            )
        self._chave = chave.strip()
        self._cliente = cliente
        self._modelo = modelo
        self._max_tentativas = max(1, max_tentativas)
        self._timeout = timeout
        self._dormir = dormir
        self._sortear = sortear

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
        """Uma requisição com todas as perguntas juntas; ``{}`` em qualquer falha.

        O ``except`` largo é o contrato do protocolo, não descuido: rede,
        chave, JSON malformado e contrato de resposta são todos "o eixo
        semântico não respondeu", e o chamador precisa continuar com o eixo
        contável de pé. O motivo real fica no log, com tipo e mensagem.

        A montagem do corpo também está protegida: ``rubrica.perguntas_para``
        lê a régua e pode levantar se ela estiver inconsistente, e uma falha
        ali antes do ``try`` derrubaria a avaliação inteira em vez de degradar.
        """
        try:
            corpo = {
                "model": self._modelo,
                "state": dict(estado),
                "questions": rubrica.perguntas_para(estado),
            }
        except Exception as erro:  # noqa: BLE001 - régua torta degrada o eixo, não quebra
            _log.warning("não consegui montar a pergunta: %s: %s", type(erro).__name__, erro)
            return {}
        try:
            dados = self._pedir(corpo)
        except Exception as erro:  # noqa: BLE001 - qualquer falha degrada o eixo, não quebra
            _log.warning("julgamento indisponível: %s: %s", type(erro).__name__, erro)
            return {}
        return {
            "respostas": extrair_respostas(dados, rubrica),
            "modelo": str(dados.get("model", self._modelo)),
            "usage": dict(dados.get("usage") or {}),
        }

    def _pedir(self, corpo: dict[str, Any]) -> dict[str, Any]:
        """Garante que um cliente criado aqui seja fechado aqui.

        O ``finally`` fecha **apenas** o cliente que esta chamada abriu. Fechar
        o que veio pelo construtor sabotaria quem o passou: nas bateladas, o
        mesmo cliente atende dezenas de funções em paralelo, e a primeira a
        terminar deixaria as outras sem conexão — falha que aparece como erro
        de rede intermitente e some quando se tenta reproduzir com uma função
        só.

        O que sobe daqui é sempre o erro de quem falhou primeiro — rede, HTTP
        ou JSON —, nunca um erro de encerramento. E o alcance é uma função: o
        chamador imediato converte qualquer exceção em ``{}``, o eixo contável
        segue de pé e o relatório diz que o semântico não respondeu por aquela
        função. Nada é escrito nem perdido.
        """
        proprio = self._cliente is None
        cliente = self._cliente or httpx.Client(timeout=self._timeout)
        try:
            return self._pedir_com_espera(cliente, corpo)
        finally:
            if proprio:
                try:
                    cliente.close()
                except Exception:  # noqa: BLE001 - fechar não pode apagar o erro original
                    # `close` de um cliente cujo transporte já morreu levanta, e
                    # isso aconteceria dentro do `finally` — substituindo a
                    # exceção real (rede, HTTP, JSON) por uma sobre encerramento
                    # de socket. O erro que interessa é o primeiro.
                    _log.debug("falha ao fechar o cliente HTTP criado para esta chamada")

    def _pedir_com_espera(self, cliente: httpx.Client, corpo: dict[str, Any]) -> dict[str, Any]:
        """Tenta até ``max_tentativas``, recuando só nos códigos transitórios.

        A distinção entre transitório e definitivo é o que torna o retry útil:
        repetir um 401 gasta quatro vezes o tempo para receber o mesmo 401, e
        repetir um 400 de pergunta malformada nunca vai dar certo. Só os
        códigos que a API documenta como temporários (mais os 5xx de quem está
        no meio do caminho) rendem nova tentativa; o resto sobe na hora, via
        ``raise_for_status``, e vira ``{}`` uma camada acima.

        A última tentativa não espera antes de falhar: dormir depois de decidir
        desistir só atrasa a resposta.

        O ``raise`` final é inalcançável enquanto ``max_tentativas >= 1`` — o
        construtor garante isso —, mas existe para que uma mudança no laço não
        produza um retorno ``None`` silencioso no lugar de um dicionário.
        """
        cabecalhos = {
            "Authorization": f"Bearer {self._chave}",
            "Content-Type": "application/json",
        }
        for tentativa in range(self._max_tentativas):
            resposta = cliente.post(URL_API, headers=cabecalhos, json=corpo)
            ultima = tentativa == self._max_tentativas - 1
            if resposta.status_code in STATUS_TRANSITORIOS and not ultima:
                espera = self._espera(tentativa)
                _log.info(
                    "Jev respondeu %s; nova tentativa em %.2fs (%d de %d)",
                    resposta.status_code,
                    espera,
                    tentativa + 1,
                    self._max_tentativas,
                )
                self._dormir(espera)
                continue
            resposta.raise_for_status()
            return resposta.json()
        raise RuntimeError("laço de tentativas terminou sem resposta nem erro")

    def _espera(self, tentativa: int) -> float:
        """Espera exponencial com jitter, em segundos.

        O jitter existe porque as requisições concorrentes tomam 429 no mesmo
        instante: sem ele, voltam juntas e recriam o pico que causou o 429.

        O resultado é limitado por ``ESPERA_MAXIMA_SEGUNDOS`` e nunca é
        negativo. O teto importa porque a base dobra a cada tentativa: com
        muitas tentativas configuradas, a espera cresceria além de qualquer
        timeout de cliente MCP e a tool pareceria travada. O piso importa
        porque ``sortear`` vem do construtor e um substituto de teste pode
        devolver número negativo — ``time.sleep`` de valor negativo levanta, e
        a falha apareceria como "erro de rede".
        """
        bruta = ESPERA_BASE_SEGUNDOS * (2**tentativa) + self._sortear() * JITTER_MAXIMO_SEGUNDOS
        if not math.isfinite(bruta):
            return ESPERA_MAXIMA_SEGUNDOS
        return min(max(bruta, 0.0), ESPERA_MAXIMA_SEGUNDOS)


@dataclass
class JulgadorFake:
    """Julgador de teste: devolve o que lhe entregaram e guarda o que recebeu.

    Existe para que o teste de quem consome o eixo semântico não dependa de rede
    nem de chave. Guardar ``chamadas`` permite verificar o estado enviado — é
    assim que a suíte confere que a complexidade ciclomática **não** foi
    mandada, que é o tipo de regressão que passaria despercebida no resultado.
    """

    ativo: ClassVar[bool] = True

    respostas: Mapping[str, Resposta] | None = None
    modelo: str = "jev-fake"
    usage: Mapping[str, int] | None = None
    erro: bool = False
    """Devolve ``{}``: o eixo respondeu que não sabe."""

    levanta: BaseException | None = None
    """Estoura em toda chamada: o transporte quebrou."""

    falhar_nas: Sequence[int] = ()
    """Estoura só nas chamadas de número indicado: falha parcial numa batelada."""

    chamadas: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normaliza as coleções e confere o formato de ``respostas``.

        Uma lista ou um gerador em ``respostas`` viraria um dicionário vazio em
        silêncio, e o teste passaria pelo motivo errado — julgando "sem
        resposta nenhuma" em vez do cenário que ele queria montar.

        Os três modos de falha são campos declarados, e não remendos aplicados
        ao método depois: um teste que troca ``julgar`` por uma lambda perde a
        contagem de ``chamadas`` e deixa de exercitar o caminho real — e é
        justamente a falha parcial que a batelada precisa sobreviver.
        """
        if self.respostas is not None and not isinstance(self.respostas, Mapping):
            raise TypeError(
                f"respostas precisa ser um mapa nome -> Resposta; "
                f"veio {type(self.respostas).__name__}"
            )
        self.respostas = dict(self.respostas or {})
        self.usage = dict(self.usage or {"input_tokens": 0, "output_tokens": 0})
        self.falhar_nas = tuple(self.falhar_nas)

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
        """Devolve o que foi configurado, registrando o estado recebido.

        O estado é registrado **antes** de qualquer falha: é o que permite a um
        teste de falha parcial conferir que as outras funções continuaram
        sendo julgadas, e conferir *o que* foi enviado mesmo na chamada que
        quebrou.

        Este é um dublê de teste, e levantar aqui é a função dele, não um
        defeito: a exceção só acontece quando quem construiu o objeto pediu por
        ela (``levanta`` ou ``falhar_nas``), para exercitar o caminho de falha
        de quem chama. Nenhum caminho de produção instancia esta classe — o
        servidor e a CLI passam por ``obter_julgador``, que escolhe entre o
        transporte real e o desligado. Nada é lido, escrito nem enviado.
        """
        self.chamadas.append(dict(estado))
        if len(self.chamadas) in self.falhar_nas:
            raise RuntimeError(f"falha combinada na chamada {len(self.chamadas)}")
        if self.levanta is not None:
            raise self.levanta
        if self.erro:
            return {}
        pedidas = rubrica.perguntas_para(estado)
        return {
            "respostas": {n: r for n, r in self.respostas.items() if n in pedidas},
            "modelo": self.modelo,
            "usage": dict(self.usage),
        }


@dataclass
class JulgadorDesligado:
    """Usado quando não há chave: devolve ``{}`` sem erro e sabe dizer por quê.

    Ausência de chave é configuração normal — a ferramenta é publicável e o eixo
    contável roda sozinho —, não defeito. Por isso não é exceção nem aviso a
    cada chamada; o relatório lê ``motivo`` e informa que o eixo está desligado.
    """

    ativo: ClassVar[bool] = False

    motivo: str = f"{VARIAVEL_DA_CHAVE} não definida no ambiente"

    def __post_init__(self) -> None:
        """Confere o motivo, que é a única coisa que este julgador entrega.

        O relatório o imprime no lugar da nota. Vazio, a linha sairia como
        "eixo semântico desligado: " e quem lê ficaria sem saber se falta
        chave, se foi escolha, ou se algo quebrou — três situações com ações
        diferentes, apresentadas como a mesma.
        """
        if not isinstance(self.motivo, str) or not self.motivo.strip():
            raise ValueError(
                "JulgadorDesligado precisa de um motivo: é a única informação que ele "
                "entrega, e o relatório a imprime no lugar da nota"
            )
        self.motivo = self.motivo.strip()

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
        """Sempre ``{}``, com o motivo no log — mas conferindo o que recebeu.

        Não levanta e não avisa a cada chamada: ausência de chave é
        configuração normal, e um aviso por função transformaria o uso sem
        custo — que é um modo de operação legítimo — num relatório cheio de
        ruído. Quem precisa da explicação a lê em ``motivo``, uma vez, no
        cabeçalho do relatório.

        **Por que conferir argumentos que não vão ser usados.** Este é o
        julgador que roda em ``--sem-julgamento`` e em todo CI sem chave, ou
        seja, o caminho mais exercitado do projeto. Aceitar qualquer coisa em
        silêncio faria dele um buraco: um chamador que montasse o estado errado
        passaria por aqui sem sinal nenhum e só quebraria no dia em que alguém
        configurasse a chave — longe da mudança que causou o problema, e com o
        eixo real levando a culpa. O aviso vai para o log, não para a resposta,
        porque o contrato do protocolo é devolver ``{}`` e nunca levantar.
        """
        if not isinstance(estado, Mapping) or "codigo" not in estado:
            _log.warning(
                "estado sem `codigo` chegou ao eixo desligado; com chave configurada "
                "esta chamada falharia (recebi %s)",
                type(estado).__name__,
            )
        elif not hasattr(rubrica, "perguntas_para"):
            _log.warning(
                "régua sem `perguntas_para` chegou ao eixo desligado; com chave "
                "configurada esta chamada falharia (recebi %s)",
                type(rubrica).__name__,
            )
        _log.debug("eixo semântico desligado: %s", self.motivo)
        return {}


def obter_julgador(
    *,
    ambiente: Mapping[str, str] | None = None,
    cliente: httpx.Client | None = None,
) -> Julgador:
    """Escolhe o julgador pela presença de ``TYPESAFE_API_KEY``.

    A chave vem só do ambiente. Procurar um arquivo no diretório do autor
    funcionaria na máquina dele e falharia em toda instalação publicada — e o
    cliente MCP já tem um lugar próprio para passar variáveis de ambiente ao
    servidor, que é onde a chave deve estar.

    Nunca levanta: sem chave, ou com chave que não serve, devolve o julgador
    desligado carregando o motivo. Falta de chave é configuração normal — a
    ferramenta é publicável e o eixo contável roda sozinho —, e transformá-la
    em exceção impediria justamente o uso sem custo.
    """
    fonte: Mapping[str, str] = os.environ if ambiente is None else ambiente
    chave = str(fonte.get(VARIAVEL_DA_CHAVE) or "").strip()
    if not chave:
        return JulgadorDesligado()
    modelo = str(fonte.get(VARIAVEL_DO_MODELO) or "").strip() or MODELO_PADRAO
    try:
        return JulgadorJev(chave, cliente=cliente, modelo=modelo)
    except ValueError as erro:
        # A chave existe mas não serve. Desligar o eixo com o motivo é melhor
        # que levantar: a ferramenta continua medindo, e o relatório diz por
        # que não há nota — que é a informação que faz alguém consertar a
        # variável. Uma exceção aqui morreria no stderr do processo MCP.
        return JulgadorDesligado(f"{VARIAVEL_DA_CHAVE} presente mas inválida: {erro}")


def extrair_respostas(dados: Any, rubrica: Rubrica) -> dict[str, Resposta]:
    """Converte a resposta da API em :class:`Resposta`, descartando o que vier torto.

    Uma resposta malformada em uma pergunta não descarta as outras: o relatório
    prefere dez dimensões a nenhuma. Pergunta desconhecida também é ignorada —
    régua e resposta podem divergir quando o arquivo de perguntas muda entre
    uma versão e outra, e travar nisso derrubaria a avaliação inteira por um
    campo a mais.
    """
    if not isinstance(dados, Mapping):
        _log.warning("resposta do Jev não é um objeto JSON: %s", type(dados).__name__)
        return {}
    respostas = dados.get("answers")
    if not isinstance(respostas, Mapping):
        _log.warning("resposta do Jev sem o campo 'answers'")
        return {}

    convertidas: dict[str, Resposta] = {}
    for identificador, bruta in respostas.items():
        nome = str(identificador)
        if nome not in rubrica.dimensoes:
            _log.debug("pergunta %r não está na régua em uso; ignorada", nome)
            continue
        convertida = _para_resposta(bruta, rubrica, nome)
        if convertida is None:
            _log.warning("resposta inesperada para a pergunta %r; ignorada", nome)
            continue
        convertidas[nome] = convertida
    return convertidas


def _para_resposta(bruta: Any, rubrica: Rubrica, nome: str) -> Resposta | None:
    """Uma resposta da API convertida, ou ``None`` quando não dá para confiar nela.

    ``None`` e não exceção: quem chama descarta esta dimensão e fica com as
    outras. O relatório prefere dez dimensões a nenhuma, e uma resposta torta
    numa pergunta não diz nada sobre as demais.

    A normalização é protegida porque ``rubrica.normalizar`` faz aritmética com
    o que a API mandou: um valor fora da escala declarada produziria número
    estranho, e uma régua inconsistente pode levantar. Nos dois casos a
    dimensão é descartada, e não o julgamento inteiro.
    """
    if not isinstance(bruta, Mapping):
        return None
    if nome not in rubrica.dimensoes:
        return None
    esperado = rubrica.dimensoes[nome].tipo
    valor = _como_numero(bruta.get("noul" if esperado == "noul" else "score"))
    if valor is None:
        return None

    if esperado == "noul":
        # Ver a decisão 2 no topo: ausência de confiança é a resposta correta.
        confianca = None
    else:
        confianca = _como_numero(bruta.get("confidence"))
        if confianca is None:
            _log.warning("score de %r veio sem 'confidence'; tratado como não declarada", nome)

    try:
        normalizado = rubrica.normalizar(nome, valor)
    except Exception:  # noqa: BLE001 - dimensão torta é descartada, não o julgamento
        _log.warning("não consegui normalizar %r com valor %r; dimensão ignorada", nome, valor)
        return None

    probabilidades = bruta.get("probabilities")
    return Resposta(
        tipo=esperado,
        bruto=valor,
        normalizado=normalizado,
        confianca=confianca,
        probabilidades=dict(probabilidades) if isinstance(probabilidades, Mapping) else None,
    )


def _como_numero(bruto: Any) -> float | None:
    """O valor como float quando é número de verdade; ``None`` quando não é.

    ``bool`` é recusado apesar de ``isinstance(True, int)`` ser verdadeiro em
    Python: um ``true`` no JSON da API viraria nota 1.0 — a melhor possível —
    e esconderia um contrato de resposta quebrado atrás de um resultado ótimo.

    ``nan`` e infinito também viram ``None``. Eles atravessariam toda a
    aritmética da nota sem erro: ``nan`` contamina qualquer média em que entre,
    e a nota final sairia ``nan`` sem apontar a dimensão de origem.
    """
    if isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
        return None
    numero = float(bruto)
    return numero if math.isfinite(numero) else None
