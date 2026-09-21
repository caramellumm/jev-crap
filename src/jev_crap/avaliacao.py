"""Onde os dois eixos se cruzam e viram decisão.

O eixo contável (complexidade × cobertura) é barato, determinístico e conta
caminhos e linhas. O eixo semântico (o Jev) responde o que o número não sabe.
Este módulo mede o primeiro, pede o segundo só sobre o que vale a pena, e
combina os dois em algo acionável. As regras abaixo são o projeto inteiro:

1. **O que dá para contar, conta-se — não vira pergunta.** Complexidade,
   cobertura, tamanho. Perguntar trocaria certeza por distribuição de
   probabilidade.

2. **O estado só leva o que o modelo não vê no texto.** Cobertura e trechos de
   teste sim, complexidade ciclomática não. Mandar o ccn ancoraria o julgamento
   no número que nós mesmos enviamos.

3. **Risco vira gate e fica fora da nota.** Vulnerabilidade não se compensa com
   legibilidade boa. Tamanho também: função gigante é fato, não opinião. Mas só
   barra o que é proposição verificável e de consequência alta — dimensão
   gradual ("dá para quebrar com entrada esquisita?") é quase sempre verdadeira
   em qualquer código real e barraria o repositório inteiro.

4. **Ausência de dado é ausência, nunca zero.** Zero diria "nada coberto" e
   puniria função sem desvio nenhum.

5. **A regra 4 vale também para o julgamento.** Se não há trecho de teste para
   mostrar, a pergunta sobre teste não é feita e o peso dela é redistribuído.
   Perguntar sem evidência devolveria "não há teste" para função testada
   indiretamente — e cobraria por isso um quarto da nota.

E uma regra sobre como o resultado se apresenta: **a nota ordena, não mede.** A
documentação do modelo é explícita quanto à calibração numérica fraca de um
score ("use score outputs only to check threshold passage, not for precise
magnitude reconstruction"). Por isso a nota sai acompanhada de uma faixa
nomeada: 72.4 e 75.1 são o mesmo "aceitável", e tratar a diferença entre eles
como informação é ler precisão que o modelo não tem.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_crap.config import CARACTERES_POR_TOKEN, Config
from jev_crap.evidencia import PADROES_DE_TESTE, testes_de
from jev_crap.julgamento.jev import Julgador, Resposta, montar_estado
from jev_crap.julgamento.rubrica import Rubrica, carregar_rubrica
from jev_crap.metrica import cobertura as mod_cobertura
from jev_crap.metrica import complexidade as mod_complexidade
from jev_crap.metrica.cobertura import SEM_DADOS, CoberturaArquivo
from jev_crap.metrica.risco import Formula, Insumos
from jev_crap.situacoes import SituacaoConhecida

__all__ = [
    "FAIXAS",
    "FuncaoAvaliada",
    "FuncaoMedida",
    "Medicao",
    "avaliar",
    "decidir",
    "faixa_da_nota",
    "julgar_trecho",
    "medir",
    "relatorio_contavel",
]

_log = logging.getLogger(__name__)

VEREDITOS = ("aprovar", "revisar", "bloquear", "sem_julgamento")

#: Gravidade crescente. `sem_julgamento` fica entre aprovar e revisar porque
#: não é aprovação — é ausência de informação —, mas também não é uma dúvida
#: levantada por evidência.
ORDEM_DOS_VEREDITOS = ("aprovar", "sem_julgamento", "revisar", "bloquear")

#: O que a nota sustenta, em palavras. Ver a nota sobre calibração no topo do
#: módulo: a faixa é a unidade real da resposta; o decimal é ruído com aparência
#: de precisão.
FAIXAS: tuple[tuple[float, str], ...] = (
    (80.0, "sólido"),
    (60.0, "aceitável"),
    (40.0, "frágil"),
    (0.0, "ruim"),
)


def faixa_da_nota(nota: float | None) -> str:
    """A faixa em que a nota cai, ou ``"sem nota"`` quando não houve julgamento."""
    if nota is None:
        return "sem nota"
    for piso, nome in FAIXAS:
        if nota >= piso:
            return nome
    return "ruim"


# --------------------------------------------------------------------------- #
# Estruturas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FuncaoMedida:
    """O que se sabe de uma função sem gastar um token sequer."""

    arquivo: str
    nome: str
    linha_inicio: int
    linha_fim: int
    complexidade: int
    linhas_logicas: int
    linguagem: str
    cobertura_linha: float
    cobertura_branch: float
    risco: float
    codigo: str = ""
    testes: tuple[str, ...] = ()

    @property
    def chave(self) -> str:
        """Identificador estável: ``arquivo:linha_inicio``.

        Nome não serve de chave — há homônimos no mesmo arquivo e, em
        JavaScript, um monte de `(anonymous)`.
        """
        return f"{self.arquivo}:{self.linha_inicio}"

    @property
    def tamanho(self) -> int:
        """Linhas físicas que a função ocupa, pontas incluídas."""
        return self.linha_fim - self.linha_inicio + 1

    def para_medicao(self) -> dict[str, Any]:
        return {
            "chave": self.chave,
            "arquivo": self.arquivo,
            "funcao": self.nome,
            "linhas": [self.linha_inicio, self.linha_fim],
            "tamanho": self.tamanho,
            "linguagem": self.linguagem,
            "complexidade": self.complexidade,
            "linhas_logicas": self.linhas_logicas,
            "cobertura_linha": _ou_nulo(self.cobertura_linha),
            "cobertura_branch": _ou_nulo(self.cobertura_branch),
            "risco": self.risco,
            "trechos_de_teste": len(self.testes),
        }


@dataclass(frozen=True)
class FuncaoAvaliada:
    """A função medida, julgada e decidida."""

    medida: FuncaoMedida
    respostas: Mapping[str, Resposta]
    notas: Mapping[str, float]
    """Dimensões de qualidade observadas, já normalizadas em 0..1."""

    nao_observadas: tuple[str, ...]
    nota: float | None
    graves: tuple[str, ...]
    duvidas: tuple[str, ...]
    conselho: str
    prioridade: str
    veredito: str
    modelo: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)

    @property
    def faixa(self) -> str:
        return faixa_da_nota(self.nota)

    def para_avaliacao(self, *, com_respostas: bool = True) -> dict[str, Any]:
        corpo: dict[str, Any] = {
            **self.medida.para_medicao(),
            "nota": self.nota,
            "faixa": self.faixa,
            "notas": {n: round(v, 3) for n, v in self.notas.items()},
            "nao_observadas": list(self.nao_observadas),
            "graves": list(self.graves),
            "duvidas": list(self.duvidas),
            "conselho": self.conselho,
            "prioridade": self.prioridade,
            "veredito": self.veredito,
        }
        if com_respostas and self.respostas:
            corpo["respostas"] = {
                nome: {
                    "tipo": r.tipo,
                    "bruto": round(r.bruto, 4),
                    "normalizado": round(r.normalizado, 4),
                    "confianca": None if r.confianca is None else round(r.confianca, 4),
                }
                for nome, r in self.respostas.items()
            }
        if self.modelo:
            corpo["modelo"] = self.modelo
        return corpo


@dataclass(frozen=True)
class Medicao:
    """O resultado do eixo contável sobre um conjunto de caminhos."""

    funcoes: tuple[FuncaoMedida, ...]
    limiar: float
    formula: Formula
    avisos: tuple[str, ...] = ()

    @property
    def acima_do_limiar(self) -> tuple[FuncaoMedida, ...]:
        return tuple(f for f in self.funcoes if f.risco >= self.limiar)


def _ou_nulo(valor: float) -> float | None:
    """Traduz o sentinela de ausência para ``null`` no JSON.

    Do outro lado há um modelo lendo o relatório. ``-1.0`` numa coluna de
    porcentagem seria lido como um número, e um número negativo de cobertura é
    exatamente o tipo de coisa que vira conclusão errada com cara de dado.
    """
    return None if valor == SEM_DADOS else round(valor, 4)


# --------------------------------------------------------------------------- #
# Cruzamento com o relatório de cobertura
# --------------------------------------------------------------------------- #


def _componentes(caminho: str) -> tuple[str, ...]:
    return tuple(p for p in Path(str(caminho).replace("\\", "/")).parts if p not in ("/", ""))


def _sufixo_comum(a: str, b: str) -> int:
    """Quantos componentes finais de caminho os dois têm em comum.

    Comparar componentes inteiros, e não texto, é o que impede `cobertura.py` de
    casar com `xcobertura.py`: por texto puro um é sufixo do outro.
    """
    pa, pb = _componentes(a), _componentes(b)
    n = 0
    while n < min(len(pa), len(pb)) and pa[-1 - n] == pb[-1 - n]:
        n += 1
    return n


def _casar_arquivo(
    arquivo: str, cobertura: Mapping[str, CoberturaArquivo]
) -> tuple[CoberturaArquivo | None, bool]:
    """A entrada do relatório que corresponde a ``arquivo``, e se houve empate.

    O relatório grava o caminho do CI (``/build/src/a.js``); casar por sufixo
    evita que o arquivo apareça como "sem cobertura" por diferença de prefixo.
    Entre vários candidatos vence o de maior sufixo comum — pegar o primeiro que
    serve atribuiria a cobertura do arquivo errado quando dois módulos têm o
    mesmo nome de base em pastas diferentes.

    O empate é devolvido em vez de resolvido: quando dois candidatos casam
    igualmente bem, qualquer escolha é chute, e um chute silencioso vira
    cobertura atribuída ao arquivo errado. O chamador transforma isso em aviso.
    """
    melhor: CoberturaArquivo | None = None
    melhor_n = 0
    empate = False
    for candidato in cobertura.values():
        n = _sufixo_comum(candidato.arquivo, arquivo)
        if n > melhor_n:
            melhor, melhor_n, empate = candidato, n, False
        elif n == melhor_n and n > 0 and candidato is not melhor:
            empate = True
    return melhor, empate


# --------------------------------------------------------------------------- #
# Medir
# --------------------------------------------------------------------------- #


def ler_cobertura(caminho: str, raiz: Path) -> dict[str, CoberturaArquivo]:
    """Lê o relatório de cobertura, traduzindo as falhas previsíveis."""
    try:
        return mod_cobertura.ler(caminho, raiz=str(raiz))
    except FileNotFoundError as erro:
        raise SituacaoConhecida(
            "cobertura_inexistente",
            f"não há relatório de cobertura em {caminho!r}",
            "Rode a suíte gerando cobertura e aponte o arquivo: "
            "`pytest --cov=src --cov-branch --cov-report=xml` (Python) ou "
            "`jest --coverage --coverageReporters=lcov` (JS/TS). Sem relatório, use "
            "`com_julgamento=false` sabendo que toda função entra como 0% e tudo vira risco alto.",
            caminho=caminho,
        ) from erro
    except mod_cobertura.FormatoDeCoberturaDesconhecido as erro:
        raise SituacaoConhecida(
            "cobertura_ilegivel",
            str(erro),
            "O arquivo precisa ser LCOV (começa com `TN:` ou `SF:`) ou Cobertura XML "
            "(raiz `<coverage>`). Relatórios em HTML ou JSON do coverage.py não servem — "
            "gere de novo com `--cov-report=xml`.",
            caminho=caminho,
        ) from erro
    except OSError as erro:
        raise SituacaoConhecida(
            "cobertura_ilegivel",
            f"não consegui ler {caminho!r}: {erro}",
            "Confira permissão e se o caminho aponta para um arquivo, não para um diretório.",
            caminho=caminho,
        ) from erro


def medir(
    caminhos: Sequence[str],
    cobertura: str | None = None,
    *,
    config: Config,
    limiar: float | None = None,
    pasta_testes: str | None = None,
    com_codigo: bool = False,
) -> Medicao:
    """Mede o eixo contável de todas as funções encontradas em ``caminhos``.

    Arquivos de teste são podados na descida: avaliar o próprio teste polui o
    relatório e gasta token sem responder nada.

    ``com_codigo`` controla se o texto de cada função e os trechos de teste são
    carregados. Sai desligado porque a medição é usada também em varredura
    ampla, onde carregar o corpo de milhares de funções custa memória para nada;
    a avaliação com julgamento liga a chave só para o recorte que vai ao modelo.
    """
    if not caminhos:
        raise SituacaoConhecida(
            "sem_caminhos",
            "nenhum caminho foi informado",
            'Passe ao menos um arquivo ou diretório, por exemplo ["src"].',
        )

    formula = config.obter_formula()
    limiar_efetivo = config.limiar_efetivo(formula, limiar)
    avisos: list[str] = list(config.avisos)

    relatorio: dict[str, CoberturaArquivo] = {}
    if cobertura:
        relatorio = ler_cobertura(cobertura, config.raiz)
        if not relatorio:
            avisos.append(
                f"{cobertura} foi lido mas não descreve nenhum arquivo; "
                "toda função entra sem dados de cobertura"
            )
    else:
        avisos.append(
            "nenhum relatório de cobertura informado: o risco é calculado como se nada "
            "estivesse coberto, o que sobe o número de todas as funções por igual"
        )

    try:
        brutas = mod_complexidade.analisar(
            list(caminhos), excluir=config.exclusoes(PADROES_DE_TESTE)
        )
    except FileNotFoundError as erro:
        raise SituacaoConhecida(
            "caminho_inexistente",
            str(erro),
            "Confira o caminho. Ele é resolvido a partir do diretório em que o servidor "
            f"está rodando ({Path.cwd()}); use caminho absoluto se houver dúvida.",
        ) from erro

    arquivos_vistos: set[str] = set()
    nao_casados: set[str] = set()
    ambiguos: set[str] = set()
    sem_branch = 0
    insumos_invalidos: list[str] = []

    medidas: list[FuncaoMedida] = []
    textos: dict[str, list[str]] = {}
    for bruta in brutas:
        arquivos_vistos.add(bruta.arquivo)
        cob, empate = _casar_arquivo(bruta.arquivo, relatorio) if relatorio else (None, False)
        if relatorio and cob is None:
            nao_casados.add(bruta.arquivo)
        if empate:
            ambiguos.add(bruta.arquivo)

        if cob is None:
            cobertura_linha = cobertura_branch = float(SEM_DADOS)
        else:
            cobertura_linha, cobertura_branch = mod_cobertura.cobertura_de_faixa(
                cob, bruta.linha_inicio, bruta.linha_fim
            )
            if not cob.tem_dados_de_branch:
                sem_branch += 1

        # Uma função com insumo inválido não pode derrubar a varredura inteira:
        # cobertura acima de 1 num único arquivo (LCOV somando execuções em vez
        # de linhas distintas, por exemplo) abortaria a medição de todo o
        # repositório. A função vira "sem dados de cobertura" e a medição segue;
        # o aviso registra qual foi, para que o defeito do relatório apareça.
        try:
            insumos = Insumos(
                complexidade=max(1, bruta.complexidade),
                cobertura_linha=cobertura_linha,
                cobertura_branch=None if cobertura_branch == SEM_DADOS else cobertura_branch,
                linhas_logicas=max(0, bruta.linhas_logicas),
            )
        except ValueError as erro:
            insumos_invalidos.append(f"{bruta.arquivo}:{bruta.linha_inicio} ({erro})")
            cobertura_linha = cobertura_branch = float(SEM_DADOS)
            insumos = Insumos(
                complexidade=max(1, bruta.complexidade),
                cobertura_linha=cobertura_linha,
                cobertura_branch=None,
                linhas_logicas=max(0, bruta.linhas_logicas),
            )

        codigo = ""
        testes: tuple[str, ...] = ()
        if com_codigo:
            codigo = _trecho_do_arquivo(bruta.arquivo, bruta.linha_inicio, bruta.linha_fim, textos)
            testes = tuple(testes_de(bruta.nome, pasta_testes))

        medidas.append(
            FuncaoMedida(
                arquivo=bruta.arquivo,
                nome=bruta.nome,
                linha_inicio=bruta.linha_inicio,
                linha_fim=bruta.linha_fim,
                complexidade=insumos.complexidade,
                linhas_logicas=insumos.linhas_logicas,
                linguagem=bruta.linguagem,
                cobertura_linha=cobertura_linha,
                cobertura_branch=cobertura_branch,
                risco=formula.calcular(insumos),
                codigo=codigo,
                testes=testes,
            )
        )

    avisos.extend(
        _avisos_de_cruzamento(relatorio, arquivos_vistos, nao_casados, ambiguos, sem_branch)
    )
    if insumos_invalidos:
        amostra = ", ".join(insumos_invalidos[:3])
        avisos.append(
            f"{len(insumos_invalidos)} função(ões) tiveram insumo de cobertura inválido e "
            f"entraram como não medidas: {amostra}. Isso costuma ser defeito do relatório "
            "(cobertura acima de 100% sai de somar execuções em vez de linhas distintas)"
        )
    if not medidas:
        avisos.append(
            "nenhuma função foi encontrada — confira se o caminho tem código em linguagem "
            "que o lizard lê e se ele não está inteiro dentro de uma exclusão"
        )

    medidas.sort(key=lambda f: (-f.risco, f.arquivo, f.linha_inicio))
    return Medicao(
        funcoes=tuple(medidas),
        limiar=limiar_efetivo,
        formula=formula,
        avisos=tuple(avisos),
    )


def _trecho_do_arquivo(
    arquivo: str, inicio: int, fim: int, cache: dict[str, list[str]]
) -> str:
    """O texto de uma função. O arquivo é lido uma vez por varredura, não por função."""
    if arquivo not in cache:
        try:
            cache[arquivo] = Path(arquivo).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            cache[arquivo] = []
    return "\n".join(cache[arquivo][inicio - 1 : fim])


def _avisos_de_cruzamento(
    relatorio: Mapping[str, CoberturaArquivo],
    arquivos: set[str],
    nao_casados: set[str],
    ambiguos: set[str],
    sem_branch: int,
) -> list[str]:
    """Avisos que mudam a leitura do relatório — e que por isso não podem ficar calados.

    O caso mais perigoso é o primeiro: quando *nenhum* arquivo casa, o relatório
    sai inteiro em vermelho e parece dizer que o projeto é péssimo, quando na
    verdade diz que o caminho do relatório não bate com o caminho analisado.
    """
    avisos: list[str] = []
    if not relatorio:
        return avisos

    if arquivos and nao_casados >= arquivos:
        avisos.append(
            "nenhum arquivo analisado casou com o relatório de cobertura — quase sempre é "
            "diferença de raiz entre o caminho do CI e o caminho local, não ausência de teste. "
            "Confira o prefixo dos caminhos dentro do relatório antes de ler o risco."
        )
    elif nao_casados:
        exemplos = ", ".join(sorted(nao_casados)[:3])
        avisos.append(
            f"{len(nao_casados)} arquivo(s) não foram encontrados no relatório de cobertura "
            f"e entraram sem dados (ex.: {exemplos})"
        )
    if ambiguos:
        exemplos = ", ".join(sorted(ambiguos)[:3])
        avisos.append(
            f"{len(ambiguos)} arquivo(s) casaram com mais de uma entrada do relatório com a "
            f"mesma profundidade (ex.: {exemplos}); a cobertura deles pode ser de outro módulo "
            "de mesmo nome"
        )
    if sem_branch:
        avisos.append(
            f"{sem_branch} função(ões) vieram de arquivos sem cobertura de branch no relatório. "
            "Cobertura de linha declara coberto um `if` sem `else` cujo ramo nunca rodou — "
            "gere com `--cov-branch` (ou equivalente) para o número valer mais."
        )
    return avisos


def relatorio_contavel(medicao: Medicao, config: Config) -> dict[str, Any]:
    """O eixo contável em formato de resposta, sem nenhuma chamada de rede."""
    acima = medicao.acima_do_limiar
    mostradas = acima[: config.max_no_relatorio] or medicao.funcoes[: config.max_no_relatorio]
    return {
        "resumo": {
            "funcoes_medidas": len(medicao.funcoes),
            "acima_do_limiar": len(acima),
            "limiar": medicao.limiar,
            "formula": medicao.formula.nome,
            "detalhadas_no_relatorio": len(mostradas),
            "omitidas_do_relatorio": max(0, len(acima) - len(mostradas)),
        },
        "funcoes": [
            {**f.para_medicao(), "interpretacao": medicao.formula.interpretar(f.risco)}
            for f in mostradas
        ],
        "avisos": list(medicao.avisos),
        "o_que_isto_nao_responde": (
            "Se a complexidade é essencial ao domínio ou acidental, e se os testes verificam "
            "comportamento ou só executam linhas. Essas duas decidem entre escrever teste e "
            "refatorar — só avaliar_arquivos as responde."
        ),
        "regua": config.para_regua(),
    }


# --------------------------------------------------------------------------- #
# Decidir
# --------------------------------------------------------------------------- #


def _nota_ponderada(
    respostas: Mapping[str, Resposta], rubrica: Rubrica
) -> tuple[dict[str, float], tuple[str, ...], float | None]:
    """A nota, as dimensões que a formaram e as que não foram observadas.

    Acesso via ``pesos_observados`` de propósito: o peso de uma dimensão que não
    veio é redistribuído entre as que vieram, em vez de entrar como zero. Nota
    calculada sobre três dimensões é honesta; nota calculada sobre quatro com
    uma delas inventada, não.
    """
    pesos = rubrica.pesos_observados(respostas)
    notas = {nome: respostas[nome].normalizado for nome in pesos}
    nao_observadas = tuple(nome for nome in rubrica.pesos if nome not in respostas)
    if not pesos:
        return notas, nao_observadas, None
    return notas, nao_observadas, round(100 * sum(pesos[n] * notas[n] for n in pesos), 1)


def _gates(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    rubrica: Rubrica,
    config: Config,
) -> tuple[list[str], list[str]]:
    """Os bloqueios e as dúvidas. Nenhum dos dois entra na nota.

    Um gate que não foi respondido vira dúvida e **nunca** passa em silêncio:
    "a pergunta não foi feita" e "a resposta foi não" são coisas diferentes, e
    tratá-las igual é exatamente como um gate de segurança deixa de valer.
    """
    graves: list[str] = []
    duvidas: list[str] = []
    julgou = bool(respostas)

    for nome in rubrica.risco_grave:
        resposta = respostas.get(nome)
        if resposta is None:
            if julgou:
                duvidas.append(f"{nome}: gate não respondido, não vale como 'não'")
            continue
        if resposta.normalizado >= config.bloqueio:
            graves.append(f"{nome} {resposta.normalizado:.2f}")

    barrados = {g.split()[0] for g in graves}
    for nome, resposta in respostas.items():
        if nome not in rubrica.dimensoes_de_risco or nome in barrados:
            continue
        if resposta.normalizado >= config.suspeita:
            duvidas.append(f"{nome} {resposta.normalizado:.2f}")

    # Confiança baixa não invalida a nota; transforma o veredito em dúvida. A
    # distribuição espalhada é informação real sobre um caso ambíguo, e apagá-la
    # transferiria a quem lê uma certeza que ninguém teve.
    duvidas.extend(
        f"{nome} disperso (confiança {resposta.confianca:.2f})"
        for nome, resposta in respostas.items()
        if nome in rubrica.pesos and resposta.dispersa
    )

    if medida.complexidade > config.limite_ccn:
        duvidas.append(
            f"complexidade {medida.complexidade}: acima de {config.limite_ccn}, "
            "cobrir todos os caminhos deixa de ser viável"
        )
    if medida.tamanho > config.max_linhas and julgou:
        # A nota vale sobre um pedaço. Isso não barra — barrar aqui puniria
        # tamanho duas vezes —, mas precisa aparecer, senão a nota se apresenta
        # como se o modelo tivesse visto a função toda.
        duvidas.append(
            f"julgado sobre as {config.max_linhas} primeiras de {medida.tamanho} linhas"
        )
    return graves, duvidas


def _conselho(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    notas: Mapping[str, float],
) -> str:
    """O que fazer com esta função, em uma frase.

    Aqui mora a decisão que a métrica sozinha não consegue tomar: o número não
    distingue complexidade que veio do domínio de complexidade que veio da
    escrita, e a ação muda por completo. Testar antes de refatorar congela
    justamente o desenho que se quer trocar; simplificar complexidade essencial
    apaga casos reais do domínio — e o número melhora, porque a métrica não sabe
    a diferença.
    """
    if not respostas:
        return (
            "sem julgamento: com só o eixo contável, 'falta teste' e 'precisa refatorar' "
            "produzem o mesmo número, e a escolha entre os dois volta a ser de quem lê o código"
        )

    cognitiva = notas.get("complexidade_cognitiva")
    essencial_resposta = respostas.get("complexidade_essencial")
    if cognitiva is None or essencial_resposta is None:
        return (
            "conselho indisponível: sem complexidade_cognitiva e complexidade_essencial não dá "
            "para separar forma acidental de complexidade que o domínio impõe"
        )

    essencial = essencial_resposta.normalizado >= 0.5
    forma_fraca = cognitiva < 0.5 and not essencial

    if "teste_verifica" in notas:
        teste_fraco = notas["teste_verifica"] < 0.5
    else:
        # Sem julgamento sobre teste, quem responde é o fato contável — que é a
        # regra 1 do módulo aplicada onde ela sempre deveria ter valido.
        cobertura = medida.cobertura_linha
        teste_fraco = cobertura != SEM_DADOS and cobertura < 0.5

    if forma_fraca and teste_fraco:
        return "refatorar e só depois testar: testar agora congela o desenho que se quer trocar"
    if forma_fraca:
        return "simplificar a forma: os caminhos vieram da escrita, não do domínio"
    if teste_fraco:
        return "escrever teste, não refatorar: a complexidade vem do domínio"
    return "nada obrigatório"


def _prioridade(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    limiar: float,
    graves: Sequence[str],
    duvidas: Sequence[str],
) -> str:
    """Com que pressa. O risco diz o tamanho do problema; a consequência, se vale a pressa.

    Consequência de falha é exatamente o que a fórmula CRAP ignora: um
    formatador de log e um parser de boot com o mesmo risco não merecem a mesma
    fila. Ela entra **normalizada** e na direção declarada pela régua
    (``maior_mais_em_jogo``): comparar o score cru com 0.5 mediria metade de uma
    escala que vai até 2, e a condição quase nunca dispararia.
    """
    consequencia = respostas.get("consequencia_de_falha")
    muito_em_jogo = consequencia is not None and consequencia.normalizado >= 0.5
    if graves or (medida.risco >= limiar and muito_em_jogo):
        return "alta"
    if medida.risco >= limiar or duvidas:
        return "media"
    return "baixa"


def decidir(
    medida: FuncaoMedida,
    respostas: Mapping[str, Resposta],
    *,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
    modelo: str = "",
    usage: Mapping[str, int] | None = None,
) -> FuncaoAvaliada:
    """Cruza os dois eixos e produz o veredito de uma função."""
    notas, nao_observadas, nota = _nota_ponderada(respostas, rubrica)
    graves, duvidas = _gates(medida, respostas, rubrica, config)

    if nao_observadas and respostas:
        duvidas.append(
            "teste não localizado: "
            + ", ".join(nao_observadas)
            + " não foi perguntado porque não havia trecho de teste para mostrar"
        )

    # Tamanho é contável, então é gate e não opinião: acima do limite a nota vai
    # a zero independentemente do que o modelo tenha achado do trecho que viu.
    if medida.tamanho > config.limite_tamanho:
        nota = 0.0
        graves.insert(0, f"{medida.tamanho} linhas (limite {config.limite_tamanho})")

    if graves:
        veredito = "bloquear"
    elif not respostas:
        veredito = "sem_julgamento"
    elif duvidas or (nota is not None and nota < config.nota_minima):
        veredito = "revisar"
    else:
        veredito = "aprovar"

    return FuncaoAvaliada(
        medida=medida,
        respostas=dict(respostas),
        notas=notas,
        nao_observadas=nao_observadas,
        nota=nota,
        graves=tuple(graves),
        duvidas=tuple(duvidas),
        conselho=_conselho(medida, respostas, notas),
        prioridade=_prioridade(medida, respostas, limiar, graves, duvidas),
        veredito=veredito,
        modelo=modelo,
        usage=dict(usage or {}),
    )


# --------------------------------------------------------------------------- #
# Orquestrar
# --------------------------------------------------------------------------- #


def _julgar_uma(
    medida: FuncaoMedida,
    *,
    julgador: Julgador,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
) -> FuncaoAvaliada:
    estado = montar_estado(
        codigo=medida.codigo,
        linguagem=medida.linguagem,
        testes=list(medida.testes),
        cobertura_branch=(
            None if medida.cobertura_branch == SEM_DADOS else medida.cobertura_branch
        ),
        max_linhas=config.max_linhas,
    )
    resultado = julgador.julgar(estado, rubrica)
    return decidir(
        medida,
        resultado.get("respostas", {}),
        rubrica=rubrica,
        config=config,
        limiar=limiar,
        modelo=str(resultado.get("modelo", "")),
        usage=resultado.get("usage", {}),
    )


def _julgar_lote(
    medidas: Sequence[FuncaoMedida],
    *,
    julgador: Julgador,
    rubrica: Rubrica,
    config: Config,
    limiar: float,
) -> tuple[list[FuncaoAvaliada], list[dict[str, str]]]:
    """Julga em paralelo, e uma falha não derruba a batelada.

    ``as_completed`` no lugar de ``map``: com ``map``, uma exceção descartava
    tudo que já tinha sido pago. A função que falhou vira linha de relatório; as
    outras continuam valendo.
    """
    if not medidas:
        return [], []

    avaliadas: list[FuncaoAvaliada] = []
    falhas: list[dict[str, str]] = []
    trabalhadores = max(1, min(config.concorrencia, len(medidas)))
    with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
        tarefas = {
            pool.submit(
                _julgar_uma,
                medida,
                julgador=julgador,
                rubrica=rubrica,
                config=config,
                limiar=limiar,
            ): medida
            for medida in medidas
        }
        for tarefa in as_completed(tarefas):
            medida = tarefas[tarefa]
            try:
                avaliadas.append(tarefa.result())
            except Exception as erro:  # noqa: BLE001 - uma função não derruba o resto
                _log.exception("falha ao julgar %s", medida.chave)
                falhas.append(
                    {
                        "funcao": f"{medida.chave} ({medida.nome})",
                        "erro": f"{type(erro).__name__}: {erro}",
                    }
                )
    return avaliadas, falhas


def _estimar_custo(medidas: Sequence[FuncaoMedida], config: Config) -> dict[str, Any]:
    """Ordem de grandeza do que a rodada custa, antes de ela acontecer.

    Deliberadamente grosseiro e dito como tal: não há tabela de preço embutida,
    porque preço de API muda sem avisar e um número desatualizado no código é
    pior do que nenhum — parece autoridade.
    """
    caracteres = sum(len(m.codigo) + sum(len(t) for t in m.testes) for m in medidas)
    estimativa: dict[str, Any] = {
        "chamadas": len(medidas),
        "tokens_de_entrada_estimados": caracteres // CARACTERES_POR_TOKEN,
        "base_da_estimativa": (
            f"{CARACTERES_POR_TOKEN} caracteres por token; é ordem de grandeza, "
            "não a tokenização real do modelo"
        ),
    }
    if config.custo_por_julgamento is not None:
        estimativa["custo"] = round(len(medidas) * config.custo_por_julgamento, 4)
        estimativa["moeda"] = config.moeda
    return estimativa


def _estado_do_eixo(julgador: Julgador, com_julgamento: bool, julgadas: int) -> dict[str, Any]:
    if not com_julgamento:
        return {
            "ligado": False,
            "motivo": "com_julgamento=false nesta chamada",
            "consequencia": "todas as funções saem como 'sem_julgamento'",
        }
    if not getattr(julgador, "ativo", False):
        return {
            "ligado": False,
            "motivo": getattr(julgador, "motivo", "eixo semântico indisponível"),
            "consequencia": (
                "o eixo contável vale sozinho; 'falta teste' e 'precisa refatorar' produzem "
                "o mesmo número e não são distinguíveis"
            ),
        }
    return {"ligado": True, "funcoes_julgadas": julgadas}


def avaliar(
    caminhos: Sequence[str],
    cobertura: str | None = None,
    *,
    config: Config,
    julgador: Julgador,
    rubrica: Rubrica | None = None,
    limiar: float | None = None,
    pasta_testes: str | None = None,
    com_julgamento: bool = True,
) -> dict[str, Any]:
    """Mede tudo, julga o recorte que vale a pena, cruza os eixos e decide.

    O recorte é o que torna a ferramenta usável num projeto de verdade: medir
    mil funções é grátis, julgar mil funções não. Inverter a ordem (julgar tudo
    e depois medir) daria o mesmo relatório por um preço proporcional ao tamanho
    do repositório.

    O preço desse recorte está dito no relatório e não escondido: filtrar por
    risco cega o sistema justamente no caso mais interessante — complexidade
    baixa com código ilegível, que o número não pega. Quem quiser julgar tudo
    passa ``limiar=0``.
    """
    regua = rubrica if rubrica is not None else carregar_rubrica()
    ligado = com_julgamento and getattr(julgador, "ativo", False)

    medicao = medir(
        caminhos,
        cobertura,
        config=config,
        limiar=limiar,
        pasta_testes=pasta_testes,
        com_codigo=ligado,
    )

    acima = list(medicao.acima_do_limiar)
    a_julgar = acima[: config.max_julgamentos] if ligado else []
    cortadas = len(acima) - len(a_julgar) if ligado else 0

    avaliadas, falhas = _julgar_lote(
        a_julgar, julgador=julgador, rubrica=regua, config=config, limiar=medicao.limiar
    )

    julgadas = {f.medida.chave for f in avaliadas}
    for medida in medicao.funcoes:
        if medida.chave not in julgadas:
            avaliadas.append(
                decidir(
                    medida, {}, rubrica=regua, config=config, limiar=medicao.limiar
                )
            )
    avaliadas.sort(key=lambda f: (-f.medida.risco, f.medida.arquivo, f.medida.linha_inicio))

    avisos = list(medicao.avisos)
    if cortadas:
        avisos.append(
            f"{cortadas} função(ões) acima do limiar não foram julgadas porque o teto de "
            f"{config.max_julgamentos} julgamentos por rodada foi atingido; as de maior risco "
            "tiveram prioridade. Suba JEV_CRAP_MAX_JULGAMENTOS ou rode sobre menos caminhos."
        )
    abaixo = len(medicao.funcoes) - len(acima)
    if ligado and abaixo:
        avisos.append(
            f"{abaixo} função(ões) abaixo do limiar não foram julgadas. O filtro economiza "
            "chamada mas cega o caso mais interessante — complexidade baixa com código "
            "ilegível, que o número não pega. Passe limiar=0 para julgar tudo."
        )
    if falhas:
        avisos.append(
            f"{len(falhas)} função(ões) não obtiveram julgamento por falha na chamada; "
            "elas aparecem como 'sem_julgamento' e não como aprovadas"
        )

    mostradas = avaliadas[: config.max_no_relatorio]
    return {
        "resumo": _resumo(medicao, avaliadas, mostradas, a_julgar),
        "eixo_semantico": _estado_do_eixo(julgador, com_julgamento, len(a_julgar)),
        "custo": _estimar_custo(a_julgar, config),
        "funcoes": [f.para_avaliacao() for f in mostradas],
        "falhas": falhas,
        "avisos": avisos,
        "regua": config.para_regua(),
        "como_ler": COMO_LER,
    }


def _resumo(
    medicao: Medicao,
    avaliadas: Sequence[FuncaoAvaliada],
    mostradas: Sequence[FuncaoAvaliada],
    a_julgar: Sequence[FuncaoMedida],
) -> dict[str, Any]:
    contagem: dict[str, int] = {v: 0 for v in VEREDITOS}
    for f in avaliadas:
        contagem[f.veredito] = contagem.get(f.veredito, 0) + 1
    entrada = sum(f.usage.get("input_tokens", 0) for f in avaliadas)
    saida = sum(f.usage.get("output_tokens", 0) for f in avaliadas)
    return {
        "funcoes_medidas": len(medicao.funcoes),
        "acima_do_limiar": len(medicao.acima_do_limiar),
        "julgadas": len(a_julgar),
        "limiar": medicao.limiar,
        "formula": medicao.formula.nome,
        "por_veredito": contagem,
        "resultado": _pior_veredito(avaliadas),
        "detalhadas_no_relatorio": len(mostradas),
        "omitidas_do_relatorio": max(0, len(avaliadas) - len(mostradas)),
        "tokens_usados": {"entrada": entrada, "saida": saida} if entrada or saida else None,
    }


def _pior_veredito(avaliadas: Iterable[FuncaoAvaliada]) -> str:
    """O veredito da rodada é o pior de suas funções.

    ``sem_julgamento`` conta como pior que ``aprovar``: o que não foi julgado
    não foi aprovado, e quem lê o resultado agregado (um CI, por exemplo)
    precisa que o silêncio não se apresente como sinal verde.
    """
    pior = "aprovar"
    for f in avaliadas:
        if ORDEM_DOS_VEREDITOS.index(f.veredito) > ORDEM_DOS_VEREDITOS.index(pior):
            pior = f.veredito
    return pior


COMO_LER: dict[str, str] = {
    "risco": (
        "Eixo contável: complexidade ciclomática cruzada com cobertura. Ordena o que olhar "
        "primeiro. Não sabe se a complexidade é do domínio nem se os testes verificam algo."
    ),
    "nota": (
        "Eixo semântico: média ponderada das dimensões de qualidade, 0 a 100. A nota ORDENA, "
        "não mede — a calibração numérica de um score é fraca, então leia a 'faixa' e trate "
        "diferenças de poucos pontos como empate."
    ),
    "graves": (
        "Gates que barram. Ficam fora da nota porque risco não se compensa com legibilidade "
        "boa: uma função com injeção e nota 95 continua sendo uma função com injeção."
    ),
    "duvidas": (
        "Vão para olho humano, nunca barram. Incluem riscos graduais, confiança baixa, "
        "complexidade acima do viável e o que não foi possível perguntar."
    ),
    "conselho": (
        "A decisão que a métrica não consegue tomar: refatorar apaga casos reais quando a "
        "complexidade é essencial; testar antes de refatorar congela o desenho a trocar."
    ),
    "confianca": (
        "Mede o quanto o modelo se decidiu, não se acertou. Abaixo de ~0.45 a nota vira "
        "'vale um olhar humano' em vez de veredito. Respostas do tipo noul não têm confiança: "
        "a própria probabilidade já descreve a distribuição inteira."
    ),
}


def julgar_trecho(
    codigo: str,
    *,
    config: Config,
    julgador: Julgador,
    rubrica: Rubrica | None = None,
    arquivo: str = "trecho.py",
    funcao: str = "",
    testes: Sequence[str] = (),
    cobertura_branch: float | None = None,
    limiar: float | None = None,
) -> dict[str, Any]:
    """Avalia um trecho que já está em mãos, sem tocar no disco.

    É o caminho para o código que um agente acabou de escrever e ainda não
    salvou, para um pedaço de diff, e para quando o número e a impressão de quem
    leu o código discordam — essa discordância costuma ser informação, não erro.

    A complexidade é medida do próprio texto, então o eixo contável continua
    valendo: ``arquivo`` existe para dar a extensão certa ao analisador (é ela
    que escolhe a linguagem) e para identificar o trecho no relatório; ele não é
    lido do disco.

    A cobertura entra só se quem chamou souber dizer: não há de onde inferi-la
    para um texto solto, e inventar 0% transformaria todo trecho colado em risco
    alto por construção.
    """
    if not codigo.strip():
        raise SituacaoConhecida(
            "trecho_vazio",
            "não veio código para julgar",
            "Passe o texto da função em `codigo`. O caminho do arquivo não serve: "
            "esta tool julga o texto que recebe, não lê disco — para isso use avaliar_arquivos.",
        )

    regua = rubrica if rubrica is not None else carregar_rubrica()
    formula = config.obter_formula()
    limiar_efetivo = config.limiar_efetivo(formula, limiar)

    medidas = mod_complexidade.medir_fonte(arquivo, codigo)
    escolhida = _escolher_funcao(medidas, funcao)

    linhas = codigo.splitlines()
    if escolhida is None:
        # Nenhuma função reconhecida: pode ser um trecho solto, um arquivo de
        # script ou uma linguagem que o lizard não lê. O julgamento semântico
        # continua valendo sobre o texto; o que não existe é complexidade por
        # função, e dizer complexidade 1 aqui seria inventar um fato.
        avisos = [
            f"nenhuma função foi reconhecida em {arquivo!r}: o eixo contável vale pouco "
            "(complexidade tratada como 1). Confira se a extensão do nome em `arquivo` "
            "corresponde à linguagem do trecho."
        ]
        complexidade, nloc, inicio, fim = 1, len(linhas), 1, max(1, len(linhas))
        linguagem = Path(arquivo).suffix.lstrip(".") or "desconhecida"
        nome = funcao or "(trecho)"
    else:
        avisos = []
        complexidade = max(1, escolhida.complexidade)
        nloc = max(0, escolhida.linhas_logicas)
        inicio, fim = escolhida.linha_inicio, escolhida.linha_fim
        linguagem = escolhida.linguagem
        nome = escolhida.nome
        if len(medidas) > 1 and not funcao:
            avisos.append(
                f"o trecho tem {len(medidas)} funções; foi julgada a de maior complexidade "
                f"({nome}). Informe `funcao` para escolher outra."
            )

    insumos = Insumos(
        complexidade=complexidade,
        cobertura_linha=float(SEM_DADOS) if cobertura_branch is None else cobertura_branch,
        cobertura_branch=cobertura_branch,
        linhas_logicas=nloc,
    )
    medida = FuncaoMedida(
        arquivo=arquivo,
        nome=nome,
        linha_inicio=inicio,
        linha_fim=fim,
        complexidade=complexidade,
        linhas_logicas=nloc,
        linguagem=linguagem,
        cobertura_linha=insumos.cobertura_linha,
        cobertura_branch=float(SEM_DADOS) if cobertura_branch is None else cobertura_branch,
        risco=formula.calcular(insumos),
        codigo=codigo,
        testes=tuple(t for t in testes if t and t.strip()),
    )

    if not getattr(julgador, "ativo", False):
        raise SituacaoConhecida(
            "eixo_semantico_desligado",
            getattr(julgador, "motivo", "o julgamento do Jev não está disponível"),
            "Defina TYPESAFE_API_KEY no ambiente do servidor MCP. Sem ela, esta tool não tem "
            "o que fazer — use medir_risco, que funciona sem chave e sem rede.",
        )

    avaliada = _julgar_uma(
        medida, julgador=julgador, rubrica=regua, config=config, limiar=limiar_efetivo
    )
    if not avaliada.respostas:
        raise SituacaoConhecida(
            "julgamento_indisponivel",
            "a chamada ao Jev não devolveu nenhuma resposta utilizável",
            "Costuma ser rede, chave inválida ou a API fora do ar. O log do servidor MCP tem o "
            "motivo. O eixo contável continua disponível em medir_risco.",
        )

    if not medida.testes:
        avisos.append(
            "nenhum trecho de teste foi enviado: a pergunta sobre teste não foi feita e o peso "
            "dela foi redistribuído. Isso não é 'os testes são ruins' — é 'não havia teste para "
            "olhar'. Mande os testes em `testes` se existirem."
        )
    if cobertura_branch is None:
        avisos.append(
            "sem cobertura informada: o risco foi calculado como se nada estivesse coberto. "
            "Para um trecho solto isso é o padrão seguro, mas o número não é comparável com o "
            "de avaliar_arquivos, que lê cobertura de verdade."
        )

    return {
        "funcao": avaliada.para_avaliacao(),
        "custo": _estimar_custo([medida], config),
        "avisos": avisos,
        "regua": config.para_regua(),
        "como_ler": COMO_LER,
    }


def _escolher_funcao(medidas: Sequence[Any], pedida: str) -> Any | None:
    """A função a julgar dentro do trecho: a pedida pelo nome, ou a mais complexa.

    A mais complexa e não a primeira: quem cola um trecho com uma função
    auxiliar de duas linhas no topo quer a resposta sobre a outra, e julgar a
    primeira devolveria uma nota excelente sobre o pedaço que ninguém perguntou.
    """
    if not medidas:
        return None
    if pedida:
        exatas = [m for m in medidas if m.nome == pedida]
        if exatas:
            return exatas[0]
        parciais = [m for m in medidas if pedida in m.nome]
        if parciais:
            return parciais[0]
    return max(medidas, key=lambda m: (m.complexidade, m.linhas_logicas))
