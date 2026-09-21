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

NOME_DIRETORIO: str = ".jev-crap"
NOME_ARQUIVO: str = "episodios.jsonl"
VARIAVEL_CAMINHO: str = "JEV_CRAP_EPISODIOS"


def agora_iso() -> str:
    """Instante atual em ISO 8601 UTC com sufixo ``Z``.

    O histórico é ordenado por essa string; usar sempre UTC evita que um
    episódio gravado em fuso diferente (ou durante a virada do horário de
    verão) apareça fora de ordem.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, bruto: dict[str, Any]) -> Episodio:
        """Reconstrói um episódio ignorando chaves desconhecidas.

        Um arquivo escrito por uma versão mais nova (com campos a mais) precisa
        continuar legível por uma versão antiga: perder um campo é aceitável,
        perder o histórico inteiro por um `TypeError` não é.
        """
        aceitos = {f.name for f in fields(cls)}
        return cls(**{chave: valor for chave, valor in bruto.items() if chave in aceitos})


def caminho_padrao(raiz_projeto: Path | str | None = None) -> Path:
    """Onde o arquivo de episódios fica quando ninguém escolhe um lugar.

    Ordem de precedência: variável de ambiente ``JEV_CRAP_EPISODIOS`` (para CI,
    sandbox ou quem quer o arquivo fora do repositório), depois
    ``<raiz do projeto>/.jev-crap/episodios.jsonl``. A raiz padrão é o
    diretório de trabalho porque é ele que o servidor MCP recebe como projeto
    analisado — nunca o diretório do pacote instalado, que é compartilhado
    entre todos os projetos da máquina.
    """
    bruto = os.environ.get(VARIAVEL_CAMINHO)
    if bruto:
        return Path(bruto).expanduser()
    raiz = Path(raiz_projeto) if raiz_projeto is not None else Path.cwd()
    return raiz / NOME_DIRETORIO / NOME_ARQUIVO


class Repositorio:
    """Histórico de episódios persistido em JSONL, append-only."""

    def __init__(
        self,
        caminho: Path | str | None = None,
        *,
        raiz_projeto: Path | str | None = None,
    ) -> None:
        self.caminho: Path = (
            Path(caminho).expanduser() if caminho is not None else caminho_padrao(raiz_projeto)
        )
        self.linhas_invalidas: int = 0
        """Linhas que a última leitura não conseguiu interpretar (ver `carregar`)."""

    def registrar(self, ep: Episodio) -> Episodio:
        """Acrescenta um episódio ao histórico e devolve o que foi gravado.

        `id` e `em` vazios são preenchidos aqui: quem chama está descrevendo
        uma avaliação, não administrando identificadores. O retorno é o
        episódio já completo para que o chamador saiba o `id` que precisará
        usar quando o desfecho aparecer.
        """
        completo = ep
        if not ep.id or not ep.em:
            completo = Episodio.de_dict(
                {**ep.para_dict(), "id": ep.id or uuid.uuid4().hex, "em": ep.em or agora_iso()}
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
        """
        historico = self.carregar()
        original = next((ep for ep in historico if ep.id == id_episodio), None)
        if original is None:
            raise KeyError(f"episódio não encontrado no histórico: {id_episodio}")

        atualizado = Episodio.de_dict(
            {
                **original.para_dict(),
                "acao": original.acao if acao is None else acao,
                "aceita": original.aceita if aceita is None else aceita,
                "risco_depois": original.risco_depois if risco_depois is None else risco_depois,
                "defeito": original.defeito if defeito is None else defeito,
            }
        )
        self._anexar(atualizado)
        return atualizado

    def _anexar(self, ep: Episodio) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        linha = json.dumps(ep.para_dict(), ensure_ascii=False, sort_keys=True)
        with self.caminho.open("a", encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")
