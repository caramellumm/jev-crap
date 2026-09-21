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
import os
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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
        """Devolve ``{"respostas": {...}, "modelo": str, "usage": {...}}`` ou ``{}``."""
        ...


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
    """
    linhas = codigo.splitlines()
    estado: dict[str, Any] = {"linguagem": linguagem}
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
    if cobertura_branch is not None:
        estado["cobertura_branch"] = round(cobertura_branch, 2)
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
        corpo = {
            "model": self._modelo,
            "state": dict(estado),
            "questions": rubrica.perguntas_para(estado),
        }
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
        cliente = self._cliente or httpx.Client(timeout=self._timeout)
        try:
            return self._pedir_com_espera(cliente, corpo)
        finally:
            if self._cliente is None:
                cliente.close()

    def _pedir_com_espera(self, cliente: httpx.Client, corpo: dict[str, Any]) -> dict[str, Any]:
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
        """Espera exponencial com jitter.

        O jitter existe porque as requisições concorrentes tomam 429 no mesmo
        instante: sem ele, voltam juntas e recriam o pico que causou o 429.
        """
        return ESPERA_BASE_SEGUNDOS * (2**tentativa) + self._sortear() * JITTER_MAXIMO_SEGUNDOS


class JulgadorFake:
    """Julgador de teste: devolve o que lhe entregaram e guarda o que recebeu.

    Existe para que o teste de quem consome o eixo semântico não dependa de rede
    nem de chave. Guardar ``chamadas`` permite verificar o estado enviado — é
    assim que a suíte confere que a complexidade ciclomática **não** foi
    mandada, que é o tipo de regressão que passaria despercebida no resultado.
    """

    ativo = True

    def __init__(
        self,
        respostas: Mapping[str, Resposta] | None = None,
        *,
        modelo: str = "jev-fake",
        usage: Mapping[str, int] | None = None,
        erro: bool = False,
    ) -> None:
        """Guarda as respostas que serão devolvidas, sem rede nenhuma.

        ``respostas`` é copiado e conferido: um teste que passe uma lista ou um
        gerador receberia um dicionário vazio em silêncio, e o teste passaria
        pelo motivo errado — julgando "sem resposta nenhuma" em vez do cenário
        que ele queria montar.
        """
        if respostas is not None and not isinstance(respostas, Mapping):
            raise TypeError(
                f"respostas precisa ser um mapa nome -> Resposta; "
                f"veio {type(respostas).__name__}"
            )
        self.respostas = dict(respostas or {})
        self.modelo = modelo
        self.usage = dict(usage or {"input_tokens": 0, "output_tokens": 0})
        self.erro = erro
        self.chamadas: list[dict[str, Any]] = []

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
        self.chamadas.append(dict(estado))
        if self.erro:
            return {}
        pedidas = rubrica.perguntas_para(estado)
        return {
            "respostas": {n: r for n, r in self.respostas.items() if n in pedidas},
            "modelo": self.modelo,
            "usage": dict(self.usage),
        }


class JulgadorDesligado:
    """Usado quando não há chave: devolve ``{}`` sem erro e sabe dizer por quê.

    Ausência de chave é configuração normal — a ferramenta é publicável e o eixo
    contável roda sozinho —, não defeito. Por isso não é exceção nem aviso a
    cada chamada; o relatório lê ``motivo`` e informa que o eixo está desligado.
    """

    ativo = False

    def __init__(self, motivo: str = f"{VARIAVEL_DA_CHAVE} não definida no ambiente") -> None:
        """Guarda por que o eixo está desligado.

        O motivo é obrigatório na prática, ainda que tenha padrão: ele é a
        única coisa que este julgador entrega, e o relatório o imprime como a
        explicação de por que não há nota. Vazio, a linha sairia como
        "eixo semântico desligado: " e quem lê ficaria sem saber se falta
        chave, se foi escolha, ou se algo quebrou.
        """
        if not isinstance(motivo, str) or not motivo.strip():
            raise ValueError(
                "JulgadorDesligado precisa de um motivo: é a única informação que ele "
                "entrega, e o relatório a imprime no lugar da nota"
            )
        self.motivo = motivo.strip()

    def julgar(self, estado: Mapping[str, Any], rubrica: Rubrica) -> dict[str, Any]:
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
    """
    fonte: Mapping[str, str] = os.environ if ambiente is None else ambiente
    chave = (fonte.get(VARIAVEL_DA_CHAVE) or "").strip()
    if not chave:
        return JulgadorDesligado()
    modelo = (fonte.get(VARIAVEL_DO_MODELO) or "").strip() or MODELO_PADRAO
    return JulgadorJev(chave, cliente=cliente, modelo=modelo)


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
    if not isinstance(bruta, Mapping):
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

    probabilidades = bruta.get("probabilities")
    return Resposta(
        tipo=esperado,
        bruto=valor,
        normalizado=rubrica.normalizar(nome, valor),
        confianca=confianca,
        probabilidades=dict(probabilidades) if isinstance(probabilidades, Mapping) else None,
    )


def _como_numero(bruto: Any) -> float | None:
    # bool é subclasse de int em Python; aceitar True como 1.0 esconderia erro.
    if isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
        return None
    return float(bruto)
