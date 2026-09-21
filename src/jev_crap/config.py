"""O que muda de projeto para projeto, sem precisar editar código.

Um servidor MCP não tem linha de comando: ele é iniciado por um cliente que
passa, no máximo, variáveis de ambiente. Então toda escolha ajustável vira
variável de ambiente, e este módulo é o único lugar que as lê — o resto do
código recebe um :class:`Config` pronto e não sabe de onde os valores vieram.
É isso que permite ao teste montar uma configuração na mão, sem mexer no
ambiente do processo.

Duas decisões guiam o módulo:

1. **Variável mal escrita não derruba o servidor.** ``JEV_CRAP_LIMIAR=muito``
   não é motivo para o servidor não subir: o valor é descartado, o padrão entra
   no lugar e a explicação vai para :attr:`Config.avisos`, que aparece na
   resposta das tools. Um servidor que recusa iniciar por causa de uma variável
   torta deixa a pessoa sem ferramenta e sem mensagem, porque a saída de erro de
   um processo stdio some dentro do cliente MCP.

2. **A chave do Jev não passa por aqui.** Ela é lida por
   ``julgamento.obter_julgador`` direto de ``TYPESAFE_API_KEY``. Guardar segredo
   num objeto que é serializado em relatório e em log é como vazamento costuma
   começar.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jev_crap.aprendizado.episodio import Repositorio
from jev_crap.metrica.risco import Formula, formulas_disponiveis, obter_formula

__all__ = ["Config", "VARIAVEIS"]

VARIAVEL_FORMULA = "JEV_CRAP_FORMULA"
VARIAVEL_LIMIAR = "JEV_CRAP_LIMIAR"
VARIAVEL_RAIZ = "JEV_CRAP_RAIZ"
VARIAVEL_EXCLUIR = "JEV_CRAP_EXCLUIR"
VARIAVEL_MAX_JULGAMENTOS = "JEV_CRAP_MAX_JULGAMENTOS"
VARIAVEL_MAX_NO_RELATORIO = "JEV_CRAP_MAX_NO_RELATORIO"
VARIAVEL_MAX_LINHAS = "JEV_CRAP_MAX_LINHAS"
VARIAVEL_CONCORRENCIA = "JEV_CRAP_CONCORRENCIA"
VARIAVEL_BLOQUEIO = "JEV_CRAP_BLOQUEIO"
VARIAVEL_SUSPEITA = "JEV_CRAP_SUSPEITA"
VARIAVEL_LIMITE_TAMANHO = "JEV_CRAP_LIMITE_TAMANHO"
VARIAVEL_LIMITE_CCN = "JEV_CRAP_LIMITE_CCN"
VARIAVEL_NOTA_MINIMA = "JEV_CRAP_NOTA_MINIMA"
VARIAVEL_CUSTO = "JEV_CRAP_CUSTO_POR_JULGAMENTO"
VARIAVEL_MOEDA = "JEV_CRAP_MOEDA"
VARIAVEL_EPISODIOS = "JEV_CRAP_EPISODIOS"

FORMULA_PADRAO = "crap"

#: Teto de funções enviadas ao Jev numa avaliação. Existe porque o eixo
#: semântico é o único que custa dinheiro e tempo: medir mil funções é grátis,
#: julgar mil funções não. Vinte é o que costuma caber numa revisão humana de
#: uma sentada — acima disso a lista deixa de ser acionável.
MAX_JULGAMENTOS_PADRAO = 20

#: Teto de funções detalhadas na resposta. Relatório com milhares de itens
#: estoura a janela de contexto de quem o lê (um modelo) e esconde justamente
#: as poucas linhas que importam.
MAX_NO_RELATORIO_PADRAO = 50

#: Teto de linhas de código enviadas por função. Função gigante não fica mais
#: bem julgada por ir inteira: a documentação do modelo é explícita sobre
#: acurácia cair com estado grande, e o trecho que decide o julgamento está no
#: começo. O que passar disso é truncado **com aviso no estado**.
MAX_LINHAS_PADRAO = 400

#: Requisições simultâneas ao Jev. O teto real é o rate limit da conta; oito é
#: o que mantém a batelada rápida sem transformar cada rodada numa rajada que
#: provoca o próprio 429.
CONCORRENCIA_PADRAO = 8

#: Probabilidade a partir da qual um gate grave barra o código. Alto de
#: propósito: bloqueio automático só se sustenta enquanto for raro e certo.
BLOQUEIO_PADRAO = 0.80

#: Probabilidade a partir da qual um risco vira dúvida e vai para olho humano.
SUSPEITA_PADRAO = 0.50

#: Acima deste tamanho a função é reprovada por tamanho, e só. Tamanho é
#: contável, então é gate e não opinião.
LIMITE_TAMANHO_PADRAO = 2000

#: Acima desta complexidade, cobrir todos os caminhos deixa de ser viável — a
#: função entra na lista de revisão mesmo com nota boa.
LIMITE_CCN_PADRAO = 10

#: Abaixo desta nota a função vai para revisão mesmo sem dúvida nenhuma.
NOTA_MINIMA_PADRAO = 60.0

MOEDA_PADRAO = "BRL"

#: Divisor grosseiro de caracteres por token. Não é a tokenização real de
#: nenhum modelo; serve para dar ordem de grandeza a quem decide se roda a
#: avaliação num repositório inteiro ou só no que mudou.
CARACTERES_POR_TOKEN = 4

#: Todas as variáveis que a ferramenta lê. Inclui a que é lida noutro módulo
#: (``JEV_CRAP_MODELO``, em ``julgamento.jev``): uma lista que se anuncia como
#: "os ajustes" e omite um deles manda a documentação mentir por omissão.
#: ``TYPESAFE_API_KEY`` de propósito não entra — segredo não passa por aqui.
VARIAVEIS: tuple[str, ...] = (
    VARIAVEL_FORMULA,
    VARIAVEL_LIMIAR,
    VARIAVEL_RAIZ,
    VARIAVEL_EXCLUIR,
    VARIAVEL_MAX_JULGAMENTOS,
    VARIAVEL_MAX_NO_RELATORIO,
    VARIAVEL_MAX_LINHAS,
    VARIAVEL_CONCORRENCIA,
    VARIAVEL_BLOQUEIO,
    VARIAVEL_SUSPEITA,
    VARIAVEL_LIMITE_TAMANHO,
    VARIAVEL_LIMITE_CCN,
    VARIAVEL_NOTA_MINIMA,
    VARIAVEL_CUSTO,
    VARIAVEL_MOEDA,
    VARIAVEL_EPISODIOS,
    "JEV_CRAP_MODELO",
)


def _texto(ambiente: Mapping[str, str], variavel: str) -> str:
    """O valor da variável como texto limpo; ``""`` quando ela não serve.

    Uma variável ausente, vazia ou só de espaços são o mesmo caso para todo
    chamador daqui — "não foi configurada" — e colapsá-las num só valor evita
    que cada um deles reimplemente a distinção, sempre um pouco diferente.

    ``os.environ`` só guarda texto, mas ``Config.do_ambiente`` aceita qualquer
    ``Mapping``, e em teste é comum passar um dicionário com número dentro. Sem
    a conversão, o ``.strip()`` levantaria ``AttributeError`` com uma mensagem
    que não menciona nem a variável nem o valor.
    """
    bruto = ambiente.get(variavel)
    if bruto is None:
        return ""
    return str(bruto).strip()


def _numero(
    ambiente: Mapping[str, str],
    variavel: str,
    padrao: float | None,
    avisos: list[str],
    *,
    minimo: float | None = None,
    maximo: float | None = None,
) -> float | None:
    """Um número vindo do ambiente, ou o padrão com um aviso dizendo por quê.

    Nenhuma configuração malformada derruba a montagem: ela vira aviso e o
    padrão vale. A razão é que a lista de avisos sai no relatório, em
    ``regua.avisos_de_configuracao`` — quem vê um número estranho descobre ali
    que a variável dele foi ignorada, o que uma exceção na inicialização do
    servidor MCP (que ninguém vê, porque some no stderr do processo filho) não
    contaria.

    A vírgula é aceita como separador decimal porque `0,8` é o que sai de um
    teclado brasileiro, e recusá-lo produziria "não é um número" para algo que
    todo mundo lê como número.
    """
    bruto = _texto(ambiente, variavel)
    if not bruto:
        return padrao
    try:
        valor = float(bruto.replace(",", "."))
    except (TypeError, ValueError):
        avisos.append(f"{variavel}={bruto!r} não é um número; usando {padrao!r}")
        return padrao
    if not math.isfinite(valor):
        # `float("nan")` e `float("inf")` são conversões bem-sucedidas: sem esta
        # guarda, JEV_CRAP_LIMIAR=nan passaria, e toda comparação `risco >=
        # limiar` daria falso — a varredura sairia "0 acima do limiar" sem erro
        # nenhum, que é a forma mais cara de errar que esta ferramenta tem.
        avisos.append(f"{variavel}={bruto!r} não é um número finito; usando {padrao!r}")
        return padrao
    if minimo is not None and valor < minimo:
        avisos.append(f"{variavel}={bruto!r} é menor que {minimo}; usando {padrao!r}")
        return padrao
    if maximo is not None and valor > maximo:
        avisos.append(f"{variavel}={bruto!r} é maior que {maximo}; usando {padrao!r}")
        return padrao
    return valor


def _inteiro(
    ambiente: Mapping[str, str],
    variavel: str,
    padrao: int,
    avisos: list[str],
    *,
    minimo: int = 1,
) -> int:
    """Um contador vindo do ambiente, truncado para inteiro.

    Trunca em vez de arredondar porque todo uso daqui é de teto — quantas
    funções julgar, quantas linhas enviar, quantas mostrar. Arredondar `4.7`
    para 5 gastaria uma chamada a mais do que o configurado pediu, e teto que
    estoura não é teto.
    """
    valor = _numero(ambiente, variavel, float(padrao), avisos, minimo=float(minimo))
    if valor is None:
        return padrao
    return int(valor)


@dataclass(frozen=True)
class Config:
    """Tudo que o servidor precisa saber antes de olhar para o código analisado.

    Congelado porque uma única avaliação atravessa vários passos (medir, filtrar,
    julgar, decidir, compor relatório) e nenhum deles pode mudar a régua no meio
    do caminho — um relatório em que metade das funções foi comparada com um
    limiar e a outra metade com outro não descreve nada.
    """

    raiz: Path = field(default_factory=Path.cwd)
    """Raiz do projeto analisado. É contra ela que os caminhos do relatório de
    cobertura são normalizados e é dentro dela que o histórico de episódios
    mora — nunca o diretório do pacote instalado, que é comum a todos os
    projetos da máquina."""

    formula: str = FORMULA_PADRAO
    limiar: float | None = None
    """``None`` significa "use o limiar que a própria fórmula recomenda". Guardar
    ``None`` em vez de copiar o número mantém as duas coisas ligadas: trocar de
    fórmula troca o limiar junto, sem configuração órfã apontando para uma
    escala que não existe mais."""

    excluir: tuple[str, ...] = ()
    max_julgamentos: int = MAX_JULGAMENTOS_PADRAO
    max_no_relatorio: int = MAX_NO_RELATORIO_PADRAO
    max_linhas: int = MAX_LINHAS_PADRAO
    concorrencia: int = CONCORRENCIA_PADRAO
    bloqueio: float = BLOQUEIO_PADRAO
    suspeita: float = SUSPEITA_PADRAO
    limite_tamanho: int = LIMITE_TAMANHO_PADRAO
    limite_ccn: int = LIMITE_CCN_PADRAO
    nota_minima: float = NOTA_MINIMA_PADRAO

    custo_por_julgamento: float | None = None
    """Preço de uma chamada ao Jev, se a pessoa souber o dela. Não há tabela
    embutida: preço de API muda sem avisar, e um número desatualizado no código
    é pior do que nenhum, porque parece autoridade."""

    moeda: str = MOEDA_PADRAO
    caminho_episodios: Path | None = None
    avisos: tuple[str, ...] = ()
    """Variáveis de ambiente que foram ignoradas e por quê (ver o cabeçalho)."""

    @classmethod
    def do_ambiente(
        cls,
        ambiente: Mapping[str, str] | None = None,
        *,
        raiz: Path | str | None = None,
    ) -> Config:
        """Monta a configuração a partir das variáveis de ambiente.

        ``ambiente`` entra por parâmetro (e não é lido de ``os.environ`` lá
        dentro) para que o teste descreva um cenário sem mexer no processo —
        variável de ambiente é estado global, e teste que a altera passa a
        depender da ordem em que roda.

        **Isto não levanta.** Toda variável passa por um leitor que, diante de
        valor inválido, acrescenta um aviso e devolve o padrão: o resultado é
        sempre uma ``Config`` utilizável, no pior caso a padrão inteira com a
        lista de avisos explicando o que foi ignorado. A razão é onde esta
        função roda — na inicialização do servidor MCP, cujo stderr o cliente
        descarta: uma exceção aqui deixaria a pessoa sem ferramenta e sem
        mensagem, enquanto o aviso chega a ela dentro da resposta da tool, em
        ``regua.avisos_de_configuracao``.

        Nada é escrito, lido de disco ou enviado: a montagem só lê o mapa que
        recebeu. O que se perde num valor mal configurado é o ajuste daquele
        valor, e o aviso diz qual foi.
        """
        fonte: Mapping[str, str] = os.environ if ambiente is None else ambiente
        avisos: list[str] = []

        return cls(
            raiz=cls._raiz(fonte, raiz),
            formula=cls._formula(fonte, avisos),
            limiar=_numero(fonte, VARIAVEL_LIMIAR, None, avisos, minimo=0.0),
            excluir=cls._lista(fonte, VARIAVEL_EXCLUIR),
            max_julgamentos=_inteiro(
                fonte, VARIAVEL_MAX_JULGAMENTOS, MAX_JULGAMENTOS_PADRAO, avisos
            ),
            max_no_relatorio=_inteiro(
                fonte, VARIAVEL_MAX_NO_RELATORIO, MAX_NO_RELATORIO_PADRAO, avisos
            ),
            max_linhas=_inteiro(fonte, VARIAVEL_MAX_LINHAS, MAX_LINHAS_PADRAO, avisos, minimo=20),
            concorrencia=_inteiro(fonte, VARIAVEL_CONCORRENCIA, CONCORRENCIA_PADRAO, avisos),
            bloqueio=_fracao(fonte, VARIAVEL_BLOQUEIO, BLOQUEIO_PADRAO, avisos),
            suspeita=_fracao(fonte, VARIAVEL_SUSPEITA, SUSPEITA_PADRAO, avisos),
            limite_tamanho=_inteiro(
                fonte, VARIAVEL_LIMITE_TAMANHO, LIMITE_TAMANHO_PADRAO, avisos, minimo=10
            ),
            limite_ccn=_inteiro(fonte, VARIAVEL_LIMITE_CCN, LIMITE_CCN_PADRAO, avisos),
            nota_minima=_numero(
                fonte, VARIAVEL_NOTA_MINIMA, NOTA_MINIMA_PADRAO, avisos, minimo=0.0, maximo=100.0
            )
            or 0.0,
            custo_por_julgamento=_numero(fonte, VARIAVEL_CUSTO, None, avisos, minimo=0.0),
            moeda=_texto(fonte, VARIAVEL_MOEDA) or MOEDA_PADRAO,
            caminho_episodios=cls._episodios(fonte),
            avisos=tuple(avisos),
        )

    @staticmethod
    def _raiz(fonte: Mapping[str, str], raiz: Path | str | None) -> Path:
        """A raiz do projeto analisado: argumento, variável, ou o cwd.

        Nunca levanta, e a razão é que esta é a primeira decisão da montagem:
        uma falha aqui acontece antes de qualquer mensagem útil existir. Os
        dois modos de falha reais são ``expanduser`` sem ``HOME`` (contêiner com
        usuário sem entrada em ``/etc/passwd``) e ``cwd`` apagado embaixo do
        processo (CI que limpa o workspace entre passos); os dois caem em algo
        utilizável em vez de derrubar o servidor MCP na inicialização, onde o
        traceback some no stderr do processo filho.
        """
        if raiz is not None:
            return _expandir(str(raiz))
        declarada = _texto(fonte, VARIAVEL_RAIZ)
        if declarada:
            return _expandir(declarada)
        try:
            return Path.cwd()
        except OSError:
            return Path(".")

    @staticmethod
    def _episodios(fonte: Mapping[str, str]) -> Path | None:
        """Onde gravar o histórico, ou ``None`` para o lugar padrão do projeto.

        ``None`` e um caminho são respostas diferentes e ambas válidas: ``None``
        delega a escolha a ``aprendizado.caminho_padrao``, que a toma em função
        da raiz do projeto. Devolver um caminho inventado aqui tiraria dele essa
        decisão e espalharia a regra por dois lugares.
        """
        declarado = _texto(fonte, VARIAVEL_EPISODIOS)
        return _expandir(declarado) if declarado else None

    @staticmethod
    def _formula(fonte: Mapping[str, str], avisos: list[str]) -> str:
        nome = _texto(fonte, VARIAVEL_FORMULA)
        if not nome:
            return FORMULA_PADRAO
        if nome not in formulas_disponiveis():
            disponiveis = ", ".join(formulas_disponiveis())
            avisos.append(
                f"{VARIAVEL_FORMULA}={nome!r} não é uma fórmula registrada "
                f"(disponíveis: {disponiveis}); usando {FORMULA_PADRAO!r}"
            )
            return FORMULA_PADRAO
        return nome

    @staticmethod
    def _lista(fonte: Mapping[str, str], variavel: str) -> tuple[str, ...]:
        """Uma lista separada por vírgula, sem itens vazios.

        Itens vazios são descartados em vez de preservados porque a lista vira
        padrão de exclusão: um item `""` casaria de formas imprevisíveis no
        ``fnmatch`` e poderia podar mais do que se pediu. E eles aparecem
        sozinhos — `"a,,b"` e `"a,b,"` saem de shell que concatena variáveis, e
        de gente que deixa a vírgula final.
        """
        bruto = _texto(fonte, variavel)
        if not bruto:
            return ()
        return tuple(item.strip() for item in bruto.split(",") if item.strip())

    def obter_formula(self) -> Formula:
        """A fórmula de risco configurada, já instanciada.

        Cai na padrão se o nome guardado não resolver. Não deveria acontecer —
        ``_formula`` confere o nome contra o registro na montagem —, mas o
        registro é global e mutável: um plugin que remova uma fórmula depois de
        a configuração ter sido lida deixaria este nome órfão. Levantar aqui
        derrubaria uma avaliação por causa de um registro que mudou; cair na
        clássica devolve um número comparável e explicado.
        """
        try:
            return obter_formula(self.formula)
        except ValueError:
            return obter_formula(FORMULA_PADRAO)

    def limiar_efetivo(self, formula: Formula, pedido: float | None = None) -> float:
        """O limiar que vale nesta chamada.

        Precedência: o que a tool recebeu, depois o da configuração, depois o
        que a fórmula recomenda. O parâmetro da tool ganha porque limiar é
        decisão de quem está olhando o relatório agora — é ele que sabe se está
        varrendo um módulo crítico ou um script descartável.

        Valor não numérico ou não finito em qualquer dos três níveis cai para o
        próximo, em vez de levantar: um ``nan`` aqui faria toda comparação
        ``risco >= limiar`` dar falso e a varredura terminaria dizendo "nenhuma
        função acima do limiar" — resultado caro, plausível e errado.
        """
        escolhido = _finito(pedido)
        if escolhido is not None:
            return escolhido
        escolhido = _finito(self.limiar)
        if escolhido is not None:
            return escolhido
        return float(formula.limiar_padrao())

    def repositorio(self) -> Repositorio:
        """O histórico de episódios deste projeto.

        Construir não toca em disco — o diretório nasce na primeira gravação —,
        então chamar isto para só perguntar ao histórico não deixa rastro num
        projeto onde ninguém registrou nada.

        Um caminho que não **nomeia um arquivo** é tratado como ausente e
        delega ao padrão. ``Path("")``, ``Path(".")`` e ``Path("/")`` têm
        ``name`` vazio: os três apontam para um diretório, e gravar o histórico
        num diretório falha com ``IsADirectoryError`` na primeira tool que
        registrar algo — bem longe da variável de ambiente exportada vazia que
        causou tudo, que é exatamente o caso de "não configurei nada".
        """
        caminho = self.caminho_episodios
        if caminho is not None and not Path(caminho).name.strip():
            caminho = None
        return Repositorio(caminho, raiz_projeto=self.raiz)

    def exclusoes(self, extras: Sequence[str] = ()) -> tuple[str, ...]:
        """Exclusões da configuração somadas às que a chamada trouxe.

        As exclusões embutidas no analisador de complexidade não aparecem aqui:
        elas são sempre aplicadas por ele, e repeti-las daria a impressão de que
        removê-las desta lista as desliga.

        Três cuidados com o que sai daqui, porque esta tupla vira padrão de
        ``fnmatch`` aplicado a cada arquivo de cada varredura:

        - **item vazio é descartado.** Ele aparece sozinho, de shell que
          concatena variáveis, e casa de formas imprevisíveis;
        - **tudo vira texto.** Um ``Path`` chegando em ``extras`` faria o
          ``fnmatch`` levantar ``TypeError`` dentro do laço de arquivos, longe
          de quem o passou;
        - **repetido entra uma vez só,** na ordem da primeira aparição. Padrão
          repetido não muda o resultado e custa uma passada de ``fnmatch`` por
          arquivo — e a mesma exclusão vir da configuração e da chamada é o
          caso comum, não o raro.
        """
        vistos: dict[str, None] = {}
        for item in (*self.excluir, *extras):
            if item is None:
                continue
            padrao = str(item).strip()
            if padrao:
                vistos.setdefault(padrao, None)
        return tuple(vistos)

    def para_regua(self) -> dict[str, object]:
        """Versão serializável, para o relatório dizer sob qual régua ele foi feito.

        Dois cuidados, e os dois existem porque este dicionário atravessa uma
        fronteira JSON e acaba em log:

        - **nada aqui pode deixar de serializar.** A régua é o rodapé de toda
          resposta; um valor exótico num campo — um ``Path`` que alguém pôs em
          ``excluir``, um ``Decimal`` vindo de configuração — derrubaria a
          resposta inteira com erro de protocolo, trocando um relatório
          completo por nada. Cada valor passa por ``_json_seguro``;
        - **nenhum campo é lido do objeto por varredura.** A lista é escrita à
          mão, campo a campo, justamente para que um atributo novo não entre na
          resposta por acidente. Se um dia a configuração guardar algo sensível,
          ele não vaza por esquecimento — precisa ser adicionado aqui de
          propósito. É a mesma razão pela qual a chave do Jev nunca passa por
          este módulo.

        O que está em jogo se algo aqui sair errado é **a legenda, não o
        resultado**: nenhum veredito, nota ou prioridade é calculado a partir
        deste dicionário. Ele é montado depois de tudo decidido, só para o
        relatório poder dizer sob qual régua foi feito. Um campo torto deixa a
        legenda imprecisa; os números ao lado continuam os mesmos, e nada é
        escrito em disco nem enviado a lugar nenhum por causa dele.
        """
        return {
            "raiz": _json_seguro(self.raiz),
            "formula": self.formula,
            "limiar_configurado": self.limiar,
            "excluir": [_json_seguro(item) for item in self.excluir],
            "max_julgamentos": self.max_julgamentos,
            "max_no_relatorio": self.max_no_relatorio,
            "max_linhas": self.max_linhas,
            "bloqueio": self.bloqueio,
            "suspeita": self.suspeita,
            "limite_tamanho": self.limite_tamanho,
            "limite_ccn": self.limite_ccn,
            "nota_minima": self.nota_minima,
            "moeda": _json_seguro(self.moeda),
            "avisos_de_configuracao": [_json_seguro(aviso) for aviso in self.avisos],
        }


def _fracao(
    ambiente: Mapping[str, str], variavel: str, padrao: float, avisos: list[str]
) -> float:
    """Uma fração 0..1 do ambiente, com o padrão de volta quando não é uma.

    Existe separado de :func:`_numero` por causa do tipo de retorno: os limiares
    de probabilidade (bloqueio, suspeita) são sempre números, nunca ``None``.
    Quem os usa compara direto — ``if p >= config.bloqueio`` — e um ``None``
    escapando levantaria ``TypeError`` dentro do laço de funções, a uma camada
    de distância da variável mal configurada que o causou.
    """
    valor = _numero(ambiente, variavel, padrao, avisos, minimo=0.0, maximo=1.0)
    return padrao if valor is None else valor


def _json_seguro(valor: object) -> object:
    """O valor quando o JSON o aceita; o texto dele quando não.

    Converter em vez de recusar: a régua é contexto do relatório, e perder o
    relatório inteiro porque um campo dela veio com tipo inesperado seria
    trocar a resposta pelo rodapé.
    """
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    try:
        return str(valor)
    except Exception:  # noqa: BLE001 - o rodapé nunca pode derrubar a resposta
        return f"<{type(valor).__name__} não textualizável>"


def _expandir(bruto: str) -> Path:
    """``Path(bruto).expanduser()``, tolerando ambiente sem diretório pessoal.

    ``expanduser`` levanta ``RuntimeError`` quando o caminho começa com ``~`` e
    nem ``HOME`` nem o banco de usuários sabem quem é — contêiner com usuário
    sem entrada em ``/etc/passwd``, comum em CI. O ``~`` literal produz um
    diretório de nome esquisito, mas a ferramenta continua rodando.
    """
    caminho = Path(bruto)
    try:
        return caminho.expanduser()
    except RuntimeError:
        return caminho


def _finito(valor: object) -> float | None:
    """O valor como float quando é número finito de verdade; ``None`` se não é.

    ``bool`` é recusado apesar de ``isinstance(True, int)`` ser verdadeiro: um
    ``True`` virando limiar ``1.0`` julgaria o repositório inteiro sem que
    ninguém tivesse pedido isso.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    numero = float(valor)
    return numero if math.isfinite(numero) else None
