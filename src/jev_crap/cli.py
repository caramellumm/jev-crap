"""Linha de comando: a mesma avaliação, para quem não está falando por MCP.

Existe por três motivos que o servidor MCP não cobre:

1. **CI.** Um pipeline não fala MCP; ele roda um comando e lê o código de saída.
2. **Conferência.** Quando o relatório que chegou pelo MCP parece errado, rodar
   o mesmo caminho pela CLI separa "a ferramenta errou" de "o cliente MCP
   passou outra coisa".
3. **Ajuda.** Quem instala o pacote e digita o nome dele espera ajuda, não um
   processo parado esperando uma mensagem de protocolo que nunca chega — é por
   isso que o executável ``jev-crap`` aponta para cá e o servidor tem entrada
   própria, ``jev-crap-mcp``.

Os códigos de saída são o contrato com o CI: **0** aprovar, **1** revisar, **2**
bloquear, **3** erro de uso ou de configuração. O 3 existe separado de propósito:
sem ele, falta de chave sairia como 1 e um pipeline passaria meses achando que
avalia.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jev_crap import __version__, avaliacao, diagnostico
from jev_crap.ambiente import ARQUIVO_ENV, carregar_env
from jev_crap.config import Config
from jev_crap.julgamento.jev import VARIAVEL_DA_CHAVE, obter_julgador
from jev_crap.julgamento.rubrica import carregar_rubrica
from jev_crap.situacoes import SituacaoConhecida

__all__ = ["principal"]

SAIDA_POR_VEREDITO = {"aprovar": 0, "revisar": 1, "bloquear": 2, "sem_julgamento": 1}
ERRO_DE_USO = 3


def _limiar(bruto: str) -> float:
    """Converte e confere o ``--limiar``, para o argparse recusar no lugar certo.

    Um ``type=float`` cru aceita ``-5`` e ``nan``. O primeiro julgaria tudo (e
    quem digitou queria o contrário); o segundo faz toda comparação
    ``risco >= limiar`` dar falso, e a varredura sai "0 acima do limiar" —
    saída plausível, cara e errada, sem nenhuma mensagem de erro.

    Levantar ``ArgumentTypeError`` aqui faz o argparse imprimir o uso e sair 2,
    com o valor ofensor na mensagem. Conferir depois, dentro de ``principal``,
    daria a mesma recusa sem dizer qual argumento estava errado.
    """
    try:
        valor = float(bruto)
    except (TypeError, ValueError) as erro:
        raise argparse.ArgumentTypeError(
            f"--limiar precisa ser um número; veio {bruto!r}"
        ) from erro
    if valor != valor:
        raise argparse.ArgumentTypeError(
            "--limiar não pode ser nan: nenhuma função ficaria acima dele"
        )
    if valor < 0:
        raise argparse.ArgumentTypeError(f"--limiar não pode ser negativo; veio {valor}")
    return valor


def _argumentos(argv: list[str]) -> argparse.Namespace:
    """A linha de comando, já conferida.

    A validação do limiar mora no ``type=`` e não depois: o argparse é quem
    sabe imprimir o uso e apontar o argumento culpado, e reimplementar isso
    dentro de ``principal`` daria a mesma recusa com metade da informação.
    """
    p = argparse.ArgumentParser(
        prog="jev-crap",
        description=(
            "Avalia qualidade de código função a função, cruzando complexidade × cobertura "
            "com o julgamento do modelo Jev."
        ),
        epilog=(
            "Códigos de saída: 0 aprovar, 1 revisar, 2 bloquear, 3 erro de uso ou configuração.\n"
            "O servidor MCP é um comando separado: jev-crap-mcp"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("alvos", nargs="*", metavar="ALVO", help="arquivos ou pastas a avaliar")
    p.add_argument("--cobertura", metavar="REL", help="relatório LCOV (.info) ou Cobertura XML")
    p.add_argument("--testes", metavar="DIR", help="pasta onde estão os testes")
    p.add_argument("--limiar", type=_limiar, metavar="N",
                   help="risco a partir do qual a função é julgada (0 julga todas)")
    p.add_argument("--sem-julgamento", action="store_true", dest="sem_julgamento",
                   help="só o eixo contável: sem rede, sem custo")
    p.add_argument("--json", metavar="ARQ", help="grava o relatório completo neste arquivo")
    p.add_argument("--quieto", action="store_true", help="só o resultado final")
    p.add_argument("--version", action="version", version=f"jev-crap {__version__}")
    p.add_argument("--diagnostico", action="store_true",
                   help="imprime versão, ambiente e dependências, e sai")
    return p.parse_args(argv)


def _pct(valor: float | None) -> str:
    """Uma fração 0..1 como porcentagem, ou ``n/d`` quando não é uma.

    Três entradas viram ``n/d`` em vez de virarem número, e cada uma existe
    porque já apareceu na saída de alguém:

    - **``None``**, que é como o relatório representa "o arquivo não estava no
      relatório de cobertura";
    - **negativo**, que é a sentinela ``SEM_DADOS`` (-1) escapando de uma
      camada que devia tê-la traduzido. Sem esta guarda ela vira ``-100%``, que
      parece um número e faz quem lê procurar o defeito na medição;
    - **``nan``**, que sai de uma divisão 0/0 num arquivo sem linha executável.
      Formatado direto, imprime ``nan%``.

    Acima de 1 não é erro: cobertura de faixa pode passar de 100% quando o
    relatório conta linhas que o recorte da função não inclui, e esconder isso
    apagaria justamente o sinal de que os dois lados estão desalinhados.
    """
    if valor is None or not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return "n/d"
    if valor != valor or valor < 0:  # NaN nunca é igual a si mesmo
        return "n/d"
    return f"{valor:.0%}"


def _linhas_da_funcao(f: dict[str, Any]) -> tuple[Any, Any]:
    """O par (início, fim) do campo ``linhas``, tolerando o que não é par.

    O relatório chega como dicionário — do próprio módulo de avaliação ou de um
    JSON gravado numa versão anterior e relido. Desempacotar direto num par
    levanta ``ValueError`` para lista de tamanho diferente, e isso aconteceria
    no meio da impressão, com metade do relatório já na tela.
    """
    linhas = f.get("linhas")
    if isinstance(linhas, (list, tuple)) and len(linhas) == 2:
        return linhas[0], linhas[1]
    return "?", "?"


def _imprimir(f: dict[str, Any]) -> None:
    """Um bloco por função.

    Todo acesso é por ``get`` com padrão, de propósito. Este relatório é a
    única saída de uma execução que já custou tempo e requisição paga; uma
    chave faltando — porque o JSON veio de outra versão, ou porque uma função
    nova do avaliador ainda não preenche o campo — não pode derrubar a
    impressão e levar junto tudo que já foi apurado. Campo ausente aparece como
    ausente; o resto continua legível.
    """
    inicio, fim = _linhas_da_funcao(f)
    print(f"\n{f.get('arquivo', '?')}:{inicio}  {f.get('funcao', '?')}  "
          f"({f.get('tamanho', '?')} linhas)")
    print(
        f"  contável   ccn {f.get('complexidade', '?')}  risco {f.get('risco', '?')}  "
        f"linha {_pct(f.get('cobertura_linha'))}  branch {_pct(f.get('cobertura_branch'))}"
    )
    notas = f.get("notas") or {}
    if notas:
        julgado = "  ".join(f"{n.split('_')[0]} {_pct(v)}" for n, v in notas.items())
        nao_observadas = f.get("nao_observadas") or ()
        if nao_observadas:
            ausentes = ", ".join(f"{n.split('_')[0]} n/d" for n in nao_observadas)
            julgado += f"  [{ausentes}]"
        print(f"  julgado    {julgado}")
    # Quando não houve julgamento, o conselho é o mesmo texto para todas as
    # funções: repeti-lo por função transforma a explicação em ruído e esconde
    # as linhas que de fato variam.
    veredito = f.get("veredito") or "sem_julgamento"
    if veredito != "sem_julgamento":
        print(f"  → {f.get('conselho', '(sem conselho)')}")
    nota_bruta = f.get("nota")
    nota = (
        "sem nota"
        if nota_bruta is None
        else f"nota {nota_bruta}/100 ({f.get('faixa', '?')})"
    )
    print(f"  {nota} · {veredito.upper()} · prioridade {f.get('prioridade', '?')}")
    if f.get("graves"):
        print(f"  barrado por: {', '.join(f['graves'])}")
    if f.get("duvidas"):
        print(f"  revisar por: {', '.join(f['duvidas'])}")


def _sem_chave(env: Path | None) -> str:
    """A mensagem que separa os dois enganos que produzem a mesma tela em branco.

    "Não existe `.env` nenhum" e "existe um e ele não tem a chave" pedem ações
    opostas, e as duas aparecem como a mesma falta de saída. Dizer qual dos dois
    aconteceu é a única informação que a mensagem acrescenta.

    ``Path.cwd()`` pode levantar: o diretório de trabalho foi removido embaixo
    do processo — comum em CI que apaga o workspace entre passos. Aqui isso não
    pode virar exceção, porque esta função já está explicando outro erro e a
    segunda falha apagaria a primeira.
    """
    if env:
        onde = f"{env} não define a variável"
    else:
        onde = f"nenhum {ARQUIVO_ENV} encontrado a partir de {_diretorio_atual()}"
    return (
        f"defina {VARIAVEL_DA_CHAVE} no ambiente ou num {ARQUIVO_ENV} — {onde}.\n"
        "Ou rode com --sem-julgamento, que usa só o eixo contável."
    )


def _diretorio_atual() -> str:
    """O diretório de trabalho, ou um marcador quando ele não existe mais."""
    try:
        return str(Path.cwd())
    except OSError:
        return "(diretório de trabalho indisponível)"


def principal(argv: list[str] | None = None) -> int:
    args = _argumentos(sys.argv[1:] if argv is None else argv)

    if args.diagnostico:
        # Sem exigir ALVO: o diagnóstico serve justamente para quando a
        # ferramenta não está rodando, e pedir alvo para imprimi-lo faria a
        # única saída útil depender do que está quebrado.
        #
        # O `.env` é lido antes de perguntar pela chave, senão o diagnóstico
        # diria "chave_definida: nao" para quem tem um `.env` correto na raiz —
        # que é o diagnóstico errado e manda consertar o que já está certo.
        arquivo_env = carregar_env()
        linhas = dict(diagnostico())
        linhas["env_lido"] = str(arquivo_env) if arquivo_env else f"nenhum {ARQUIVO_ENV}"
        for campo, valor in linhas.items():
            print(f"{campo}: {valor}")
        return 0

    if not args.alvos:
        # `nargs="*"` existe só para liberar `--diagnostico` sem alvo; fora
        # desse caso, alvo continua obrigatório e a falta dele é erro de uso.
        print("informe ao menos um ALVO (arquivo ou pasta), ou use --diagnostico",
              file=sys.stderr)
        return ERRO_DE_USO

    env = carregar_env()
    config = Config.do_ambiente()
    julgador = obter_julgador()

    if not args.sem_julgamento and not julgador.ativo:
        # Falhar aqui, e não no meio da batelada: a verificação depois da
        # varredura inteira faria um erro de configuração aparecer com o
        # trabalho de medição já feito e jogado fora.
        print(_sem_chave(env), file=sys.stderr)
        return ERRO_DE_USO

    try:
        relatorio = avaliacao.avaliar(
            args.alvos,
            args.cobertura,
            config=config,
            julgador=julgador,
            rubrica=carregar_rubrica(),
            limiar=args.limiar,
            pasta_testes=args.testes,
            com_julgamento=not args.sem_julgamento,
        )
    except SituacaoConhecida as erro:
        print(erro.para_texto(), file=sys.stderr)
        return ERRO_DE_USO

    resumo = relatorio["resumo"]
    if not args.quieto:
        print(
            f"\n── {resumo['funcoes_medidas']} função(ões) medidas · "
            f"{resumo['acima_do_limiar']} acima do limiar {resumo['limiar']} · "
            f"{resumo['julgadas']} julgadas ──"
        )
        if not relatorio["eixo_semantico"]["ligado"]:
            print(f"   ({relatorio['eixo_semantico']['motivo']}; "
                  f"{relatorio['eixo_semantico']['consequencia']})")
        for funcao in relatorio["funcoes"]:
            if funcao["veredito"] != "sem_julgamento" or funcao["risco"] >= resumo["limiar"]:
                _imprimir(funcao)

        if relatorio["falhas"]:
            print(f"\n── {len(relatorio['falhas'])} função(ões) sem julgamento ──")
            for falha in relatorio["falhas"]:
                print(f"  {falha['funcao']}: {falha['erro']}")

        if relatorio["avisos"]:
            print("\n── avisos ──")
            for aviso in relatorio["avisos"]:
                print(f"  · {aviso}")

        tokens = resumo.get("tokens_usados")
        if tokens:
            print(
                f"\ncusto: {resumo['julgadas']} requisições · "
                f"{tokens['entrada']} tokens de entrada · {tokens['saida']} de saída"
            )

    if args.json:
        try:
            Path(args.json).write_text(
                json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError as erro:
            print(f"não consegui gravar {args.json}: {erro}", file=sys.stderr)
            return ERRO_DE_USO

    resultado = resumo["resultado"]
    print(f"\nresultado: {resultado.upper()}\n")
    return SAIDA_POR_VEREDITO.get(resultado, 1)


if __name__ == "__main__":  # pragma: no cover - conveniência de execução direta
    raise SystemExit(principal())
