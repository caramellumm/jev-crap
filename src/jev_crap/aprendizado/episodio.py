"""Registro do que aconteceu em cada avaliação.

Um episódio é a fotografia de uma avaliação: o número que saiu, a fórmula que
produziu esse número, o limiar que valia naquele instante, as notas do Jev, o
que foi sugerido e o que a pessoa fez depois. Ele existe por um motivo só —
permitir que a ferramenta descubra, mais tarde, que errou.

Três decisões de desenho sustentam o módulo inteiro:

1. **O episódio carrega o limiar vigente e o nome da fórmula da época.**
   Histórico que muda de valor quando o limiar é recalibrado não é histórico,
   é uma projeção do presente sobre o passado. E risco 12 calculado pela
   fórmula clássica do CRAP não é comparável com risco 12 de outra fórmula:
   são escalas diferentes com o mesmo nome. Guardar `formula` permite separar
   as séries antes de comparar; sem isso, qualquer média mistura unidades.

2. **Sugestão ignorada também vira episódio (`aceita=False`).** É o sinal mais
   barato de falso positivo que existe: ninguém precisa escrever um relatório,
   basta não seguir o conselho. Uma ferramenta que só registra o que deu certo
   nunca descobre que incomoda.

3. **O arquivo mora no projeto analisado, não no pacote instalado.** Os
   episódios calibram um limiar, e limiar é propriedade do código daquele
   repositório: complexidade tolerável em um parser de protocolo não é a mesma
   de um CRUD. Misturar episódios de projetos diferentes num arquivo global
   produziria um limiar médio que não serve a nenhum deles.

O formato é JSONL (uma linha por episódio) porque a escrita é sempre um
append: nenhuma gravação nova reescreve linha antiga, uma linha corrompida
custa um episódio e não o histórico inteiro, e o arquivo continua legível com
`tail`/`grep` — importante para quem quiser auditar o que a ferramenta aprendeu
sem abrir um cliente de banco.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jev_crap.situacoes import SituacaoConhecida

NOME_DIRETORIO: str = ".jev-crap"
NOME_ARQUIVO: str = "episodios.jsonl"
VARIAVEL_CAMINHO: str = "JEV_CRAP_EPISODIOS"

#: Largura de ``2026-09-21T15:04:05Z``. A conferência por tamanho pega o caso
#: que a conferência por sufixo deixa passar: um carimbo com microssegundos
#: ordena diferente de um sem, e os dois terminam em ``Z``.
TAMANHO_DO_CARIMBO = len("0000-00-00T00:00:00Z")


def agora_iso() -> str:
    """Instante atual em ISO 8601 UTC com sufixo ``Z``.

    O histórico é ordenado por essa string; usar sempre UTC evita que um
    episódio gravado em fuso diferente (ou durante a virada do horário de
    verão) apareça fora de ordem.

    A ordenação é **lexicográfica**, e é isso que torna o formato parte do
    contrato e não detalhe de apresentação: largura fixa e sufixo constante são
    o que fazem comparar texto dar o mesmo resultado que comparar instante.
    Por isso o resultado é conferido antes de sair — se um dia
    ``isoformat`` deixar de emitir ``+00:00`` (mudança de versão, ou um
    ``datetime.now`` trocado num teste), o ``replace`` não faria nada e a
    string sairia com ``+00:00`` no fim. Ela continuaria parecendo uma data
    válida e ordenaria *antes* de todas as outras, jogando o episódio novo para
    o começo do histórico sem nenhum erro.

    O alcance de uma falha aqui é uma tool, não uma avaliação. ``registrar_episodio``
    é um verbo separado de ``avaliar_arquivos``: quando ele falha, o relatório já foi
    entregue e o que se perde é uma linha de série histórica — a ferramenta continua
    medindo e julgando exatamente como antes, só deixa de acumular evidência para
    calibrar o limiar depois.
    """
    momento = datetime.now(timezone.utc).isoformat(timespec="seconds")
    carimbo = momento.replace("+00:00", "Z")
    if not carimbo.endswith("Z") or len(carimbo) != TAMANHO_DO_CARIMBO:
        raise ValueError(
            f"carimbo de tempo fora do formato esperado ({carimbo!r}); o histórico é "
            "ordenado por texto e depende de largura fixa terminada em Z"
        )
    return carimbo


@dataclass
class Episodio:
    """Uma avaliação registrada, com o contexto que a torna comparável depois.

    Os campos de desfecho (`acao`, `aceita`, `risco_depois`, `defeito`) nascem
    vazios de propósito: no instante da avaliação ainda não existe decisão
    humana nem defeito observado. Eles são preenchidos depois, por
    :meth:`Repositorio.registrar_desfecho`, que só toca neles — os números
    medidos permanecem como foram medidos.
    """

    id: str
    em: str
    """ISO 8601 UTC."""
    arquivo: str
    funcao: str
    risco: float
    formula: str
    """Nome da fórmula que produziu `risco` — o `nome` de `metrica.risco.Formula`.

    Guardar o nome, e não só o número, é o que permite comparar séries: risco
    12 pela fórmula clássica e risco 12 por outra não são a mesma coisa.
    """
    limiar_vigente: float
    complexidade: int
    cobertura_linha: float
    cobertura_branch: float | None
    notas: dict[str, dict] = field(default_factory=dict)
    """Julgamento do Jev por dimensão, como veio — sem reinterpretação aqui."""
    nota: float | None = None
    """Nota de qualidade 0..100 no instante da avaliação, ou ``None`` quando o
    eixo semântico estava desligado. Guardada separada de `risco` porque as duas
    são escalas diferentes: risco ordena o que olhar primeiro, nota diz o quão
    bom é o que se olhou. Somá-las produziria um número que não responde
    nenhuma das duas perguntas."""

    conselho: str = ""
    """O que a ferramenta recomendou fazer: refatorar, escrever teste, os dois
    nesta ordem, ou nada obrigatório. Substitui o `quadrante` da geração
    anterior — mesmo papel, vocabulário que diz a ação em vez da casinha."""

    veredito: str = ""
    """O que a ferramenta decidiu: aprovar, revisar ou bloquear."""

    acao: str | None = None
    aceita: bool | None = None
    """``None`` = ninguém decidiu ainda; ``False`` = a sugestão foi ignorada."""
    risco_depois: float | None = None
    defeito: bool | None = None
    """Marcação posterior: esse trecho apresentou defeito depois da avaliação.

    Não está na fotografia original — é o que se descobre semanas depois, e é
    a única evidência que permite medir falso negativo (código que passou pelo
    limiar e quebrou mesmo assim).
    """

    def para_linha_de_historico(self) -> dict[str, Any]:
        """O episódio como dicionário, pronto para virar uma linha de JSONL.

        ``asdict`` faz cópia profunda, e ``notas`` é o único campo que chega de
        fora inteiro — vem do cliente MCP, que pode mandar qualquer coisa que o
        JSON aceite ou não. Um valor não copiável (ou uma estrutura cíclica,
        que o próprio ``asdict`` percorre) levantaria aqui, no meio de um
        ``registrar`` que já decidiu gravar.

        O campo problemático é substituído por um marcador em vez de a gravação
        inteira ser perdida: o histórico existe para comparar risco, limiar e
        desfecho ao longo do tempo, e todos esses campos são escalares que
        sobrevivem. Perder o episódio inteiro para preservar ``notas`` seria
        trocar o essencial pelo acessório.
        """
        try:
            return asdict(self)
        except (TypeError, ValueError, RecursionError):
            corpo = {campo.name: getattr(self, campo.name) for campo in fields(self)}
            corpo["notas"] = {"_ilegivel": {"motivo": "notas não puderam ser serializadas"}}
            return corpo

    @classmethod
    def de_dict(cls, bruto: dict[str, Any]) -> Episodio:
        """Reconstrói um episódio ignorando chaves desconhecidas.

        Um arquivo escrito por uma versão mais nova (com campos a mais) precisa
        continuar legível por uma versão antiga: perder um campo é aceitável,
        perder o histórico inteiro por um `TypeError` não é.

        O que **não** é tolerado é a linha não ser um objeto: ``json.loads`` de
        uma linha contendo só ``3`` ou ``[1,2]`` devolve algo sem ``.items()``,
        e o ``AttributeError`` resultante não diz que o problema é a forma da
        linha. Quem lê o histórico trata este ``TypeError`` como linha inválida
        e segue para a próxima.
        """
        if not isinstance(bruto, dict):
            raise TypeError(
                f"uma linha de histórico precisa ser objeto JSON; veio {type(bruto).__name__}"
            )
        aceitos = {f.name for f in fields(cls)}
        return cls(**{str(chave): valor for chave, valor in bruto.items() if chave in aceitos})


def caminho_padrao(raiz_projeto: Path | str | None = None) -> Path:
    """Onde o arquivo de episódios fica quando ninguém escolhe um lugar.

    Nada aqui levanta: este caminho é decidido na montagem do servidor MCP, e
    uma falha ao *escolher* onde guardar histórico opcional não pode impedir
    uma avaliação de acontecer.

    Ordem de precedência: variável de ambiente ``JEV_CRAP_EPISODIOS`` (para CI,
    sandbox ou quem quer o arquivo fora do repositório), depois
    ``<raiz do projeto>/.jev-crap/episodios.jsonl``. A raiz padrão é o
    diretório de trabalho porque é ele que o servidor MCP recebe como projeto
    analisado — nunca o diretório do pacote instalado, que é compartilhado
    entre todos os projetos da máquina.
    """
    bruto = os.environ.get(VARIAVEL_CAMINHO)
    if bruto and bruto.strip():
        return _expandir(bruto.strip())
    raiz = Path(raiz_projeto) if raiz_projeto is not None else _cwd_ou_ponto()
    return raiz / NOME_DIRETORIO / NOME_ARQUIVO


def _expandir(bruto: str) -> Path:
    """``Path(bruto).expanduser()``, tolerando o ambiente sem diretório pessoal.

    ``expanduser`` levanta ``RuntimeError`` quando o caminho começa com ``~`` e
    nem ``HOME`` nem o banco de usuários sabem quem é — o que acontece em
    contêiner com usuário sem entrada em ``/etc/passwd``, que é comum em CI.
    Deixar o ``~`` literal produz um diretório com esse nome no lugar errado,
    mas mantém a ferramenta funcionando; levantar aqui mataria a avaliação por
    causa do caminho de um arquivo opcional.
    """
    caminho = Path(bruto)
    try:
        return caminho.expanduser()
    except RuntimeError:
        return caminho


def _cwd_ou_ponto() -> Path:
    """O diretório de trabalho, ou ``.`` quando ele não existe mais.

    O processo pode estar rodando num diretório que foi apagado — CI que limpa
    o workspace entre passos faz isso. ``Path(".")`` continua resolvendo
    relativo ao processo e é melhor que derrubar a gravação do histórico.
    """
    try:
        return Path.cwd()
    except OSError:
        return Path(".")


class Repositorio:
    """Histórico de episódios persistido em JSONL, append-only."""

    def __init__(
        self,
        caminho: Path | str | None = None,
        *,
        raiz_projeto: Path | str | None = None,
    ) -> None:
        """Escolhe onde o histórico mora, sem tocar em disco ainda.

        Construir um ``Repositorio`` nunca cria arquivo nem diretório: quem
        pergunta ao histórico (``consultar_aprendizado``) não deve deixar
        rastro num projeto onde ninguém registrou nada. O diretório nasce no
        primeiro ``_anexar``.
        """
        if caminho is not None and not str(caminho).strip():
            raise ValueError(
                "caminho do histórico não pode ser vazio; omita o argumento para usar o padrão"
            )
        self.caminho: Path = (
            _expandir(str(caminho)) if caminho is not None else caminho_padrao(raiz_projeto)
        )
        self.linhas_invalidas: int = 0
        """Linhas que a última leitura não conseguiu interpretar (ver `carregar`)."""

    def registrar(self, ep: Episodio) -> Episodio:
        """Acrescenta um episódio ao histórico e devolve o que foi gravado.

        `id` e `em` vazios são preenchidos aqui: quem chama está descrevendo
        uma avaliação, não administrando identificadores. O retorno é o
        episódio já completo para que o chamador saiba o `id` que precisará
        usar quando o desfecho aparecer.

        O arquivo é append-only e a gravação é de uma linha só: nada do que
        já está escrito pode ser corrompido por esta chamada, nem por uma que
        aconteça ao mesmo tempo em outro processo. Se a gravação falhar, o
        histórico fica exatamente como estava.

    O alcance de uma falha aqui é uma tool, não uma avaliação. ``registrar_episodio``
    é um verbo separado de ``avaliar_arquivos``: quando ele falha, o relatório já foi
    entregue e o que se perde é uma linha de série histórica — a ferramenta continua
    medindo e julgando exatamente como antes, só deixa de acumular evidência para
    calibrar o limiar depois.
        """
        completo = ep
        if not ep.id or not ep.em:
            completo = Episodio.de_dict(
                {
                    **ep.para_linha_de_historico(),
                    "id": ep.id or uuid.uuid4().hex,
                    "em": ep.em or agora_iso(),
                }
            )
        self._anexar(completo)
        return completo

    def carregar(self) -> list[Episodio]:
        """Lê o histórico inteiro, com o desfecho mais recente de cada episódio.

        Linhas ilegíveis são puladas e contadas em `linhas_invalidas`: um
        episódio corrompido não pode impedir a leitura dos outros — o arquivo
        é escrito por processos que podem ser interrompidos no meio.

        Quando o mesmo `id` aparece mais de uma vez (registro original seguido
        de desfecho), vence a última ocorrência, mas a posição na lista é a da
        primeira: a ordem continua sendo a cronologia das avaliações, não a das
        correções.
        """
        self.linhas_invalidas = 0
        if not self.caminho.exists():
            return []

        por_id: dict[str, Episodio] = {}
        soltos: list[Episodio] = []
        with self.caminho.open("r", encoding="utf-8") as arquivo:
            for linha in arquivo:
                if not linha.strip():
                    continue
                try:
                    ep = Episodio.de_dict(json.loads(linha))
                except (json.JSONDecodeError, TypeError, ValueError):
                    self.linhas_invalidas += 1
                    continue
                if ep.id:
                    por_id[ep.id] = ep
                else:
                    soltos.append(ep)
        return [*por_id.values(), *soltos]

    def registrar_desfecho(
        self,
        id_episodio: str,
        *,
        acao: str | None = None,
        aceita: bool | None = None,
        risco_depois: float | None = None,
        defeito: bool | None = None,
    ) -> Episodio:
        """Anexa o que se soube depois sobre um episódio já registrado.

        Só os campos de desfecho mudam. Risco, limiar vigente, fórmula e notas
        são copiados do registro original de propósito: eles descrevem o que
        foi medido naquele dia, e medição não se corrige retroativamente com
        informação que ainda não existia.

        Argumentos deixados em ``None`` preservam o valor atual — é assim que
        marcar um defeito meses depois não apaga a decisão registrada na época.

        Nada é reescrito: o desfecho entra como uma linha nova com o mesmo
        ``id``, e quem lê fica com a última ocorrência. O registro original
        permanece no arquivo, legível com ``grep`` — é o que permite auditar
        depois o que a ferramenta sabia em cada momento.

    O alcance de uma falha aqui é uma tool, não uma avaliação. ``registrar_episodio``
    é um verbo separado de ``avaliar_arquivos``: quando ele falha, o relatório já foi
    entregue e o que se perde é uma linha de série histórica — a ferramenta continua
    medindo e julgando exatamente como antes, só deixa de acumular evidência para
    calibrar o limiar depois.
        """
        historico = self.carregar()
        original = next((ep for ep in historico if ep.id == id_episodio), None)
        if original is None:
            raise KeyError(f"episódio não encontrado no histórico: {id_episodio}")

        atualizado = Episodio.de_dict(
            {
                **original.para_linha_de_historico(),
                "acao": original.acao if acao is None else acao,
                "aceita": original.aceita if aceita is None else aceita,
                "risco_depois": original.risco_depois if risco_depois is None else risco_depois,
                "defeito": original.defeito if defeito is None else defeito,
            }
        )
        self._anexar(atualizado)
        return atualizado

    def _anexar(self, ep: Episodio) -> None:
        """Escreve uma linha no fim do arquivo, criando o diretório se preciso.

        As duas falhas possíveis viram :class:`SituacaoConhecida` porque quem
        lê isto do outro lado é um agente decidindo o próximo passo, e
        ``PermissionError: [Errno 13]`` não diz o que fazer:

        - **serialização**: ``notas`` chegou com algo que o JSON não aceita.
          ``para_linha_de_historico`` já trata o caso de cópia, mas um objeto
          copiável e não serializável passa por lá e falha aqui;
        - **disco**: diretório sem permissão, sistema de arquivos cheio ou
          somente-leitura. Comum em contêiner com volume montado read-only.

        A escrita é de uma linha só, em modo append: é o que torna o arquivo
        seguro entre processos concorrentes sem trava — o sistema operacional
        garante a atomicidade de um ``write`` pequeno em ``O_APPEND``. Falhar
        aqui não corrompe nem perde nada do que já estava gravado.

    O alcance de uma falha aqui é uma tool, não uma avaliação. ``registrar_episodio``
    é um verbo separado de ``avaliar_arquivos``: quando ele falha, o relatório já foi
    entregue e o que se perde é uma linha de série histórica — a ferramenta continua
    medindo e julgando exatamente como antes, só deixa de acumular evidência para
    calibrar o limiar depois.
        """
        try:
            linha = json.dumps(ep.para_linha_de_historico(), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as erro:
            raise SituacaoConhecida(
                "episodio_nao_serializavel",
                f"o episódio {ep.id or '(sem id)'} tem campo que não vira JSON: {erro}",
                "Envie `notas` como objeto de números e texto — é o formato que o relatório "
                "de avaliar_arquivos devolve. Objetos de outros tipos não atravessam o MCP.",
            ) from erro
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            with self.caminho.open("a", encoding="utf-8") as arquivo:
                arquivo.write(linha + "\n")
        except OSError as erro:
            raise SituacaoConhecida(
                "historico_nao_gravavel",
                f"não consegui escrever em {self.caminho}: {erro}",
                f"Confira permissão de escrita no diretório, ou aponte outro lugar em "
                f"{VARIAVEL_CAMINHO}. O histórico é opcional: sem ele a avaliação continua "
                "funcionando, só não acumula série para comparar depois.",
                caminho=str(self.caminho),
            ) from erro
