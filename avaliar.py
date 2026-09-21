"""Avalia código função a função, cruzando métrica contável com julgamento do Jev.

Cinco regras:
  1. O que dá para contar, lizard e o relatório de cobertura contam — não vira
     pergunta. Perguntar trocaria certeza por distribuição de probabilidade.
  2. O `state` só leva o que o modelo não vê no texto: cobertura e testes sim,
     ccn não. Mandar o ccn ancoraria o julgamento no número que você enviou.
  3. Risco vira gate e fica fora da nota — vulnerabilidade não se compensa com
     legibilidade boa. Tamanho também: função gigante é fato, não opinião.
     Mas só bloqueia o que é proposição verificável e de consequência alta:
     dimensão gradual ("dá para quebrar com entrada esquisita?") é quase sempre
     verdadeira em qualquer código real e barraria o repositório inteiro.
  4. Ausência de dado é SEM_DADOS, nunca 0.0. Zero diria "nada coberto" e
     puniria função sem desvio nenhum.
  5. A regra 4 vale também para o julgamento: se não há trecho de teste para
     mostrar, a pergunta sobre teste não é feita e o peso dela é redistribuído.
     Perguntar sem evidência devolveria "não há teste" para função testada
     indiretamente — e cobraria por isso um quarto da nota.

    python avaliar.py src/ --cobertura lcov.info --testes tests/
    python avaliar.py src/app.js --demo          # sem rede
    python avaliar.py src/ --so-risco            # só funções acima do CRAP

A chave sai de TYPESAFE_API_KEY: do ambiente, ou de um `.env` procurado a
partir do diretório atual. O ambiente vence o arquivo — ver `carregar_env`.

Sai com 0 aprovar, 1 revisar, 2 bloquear, 3 erro de uso ou configuração. O 3
existe separado de propósito: sem ele, falta de chave sairia como 1 e um CI
passaria meses achando que avalia.
"""

import argparse
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import lizard

URL = "https://api.typesafe.ai/v1/systemone"
SEM_DADOS = -1.0

LIMITE_CCN = 10       # acima disso, cobrir todos os caminhos deixa de ser viável
LIMIAR_CRAP = 30.0    # valor da ferramenta original: convenção, não medida
LIMITE_TAMANHO = 2000 # função maior que isso é reprovada por tamanho, só
MAX_ENVIO = 400       # linhas enviadas ao Jev; acima disso o trecho vai truncado
BLOQUEIO = 0.80       # Noul acima disso barra o código
SUSPEITA = 0.50       # entre os dois, manda para olho humano
CONF_MINIMA = 0.45
CONCORRENCIA = 8      # requisições simultâneas; o teto real é o rate limit

# Diretórios que nunca contêm código do projeto. Sem esta lista, um alvo `.`
# distraído varre o site-packages inteiro do virtualenv: medido neste
# repositório, 3156 arquivos no lugar de 14, cada um virando requisição paga.
IGNORAR = {".venv", "venv", "node_modules", "dist", "build", "target",
           "__pycache__", ".git", ".tox", ".mypy_cache", ".ruff_cache",
           ".pytest_cache", "vendor", "third_party"}

LINGUAGENS = {".py": "python", ".js": "javascript", ".ts": "typescript",
              ".java": "java", ".go": "go", ".rb": "ruby", ".rs": "rust"}

# Scores: dimensões graduais. `criteria` é a escala, do pior (0) ao melhor.
# Cada nível descreve situação concreta, nunca grau abstrato ("moderado"):
# é contra a descrição que o modelo compara.
QUALIDADE = {
    "complexidade_cognitiva": {
        "type": "score",
        "instructions": "Quanto esforço mental alguém gasta para seguir o fluxo"
                        " de `codigo`? Julgue aninhamento e quanto estado o"
                        " leitor precisa carregar, não o tamanho.",
        "criteria": [
            "Exige rastrear vários níveis de aninhamento e guardar estado mental",
            "Segue-se com atenção; um ou dois trechos exigem reler",
            "Fluxo linear; o leitor nunca precisa voltar",
        ],
    },
    "teste_verifica": {
        "type": "score",
        "instructions": "Os testes em `testes` verificam o comportamento de"
                        " `codigo`, com valor esperado explícito, ou apenas"
                        " executam a função sem afirmar nada sobre o resultado?",
        "criteria": [
            "Não há teste, ou o que existe chama a função sem verificar resultado",
            "Verifica o caminho principal; borda e erro ficam de fora",
            "Verifica resultado, borda e caminho de erro com valor esperado",
        ],
    },
    "manutenibilidade": {
        "type": "score",
        "instructions": "Quão seguro é mudar `codigo` sem quebrar outra coisa?"
                        " A cobertura em `cobertura_branch` faz parte do"
                        " julgamento: ramo sem teste é mudança sem rede.",
        "criteria": [
            "Mudar exige entender o corpo inteiro e nada avisa se quebrar",
            "Mudanças localizadas são possíveis com cuidado",
            "Cada parte muda de forma independente e com rede de teste",
        ],
    },
    "tratamento_de_erros": {
        "type": "score",
        "instructions": "`codigo` trata entrada inválida, dado ausente e falha"
                        " de forma explícita, ou assume o caminho feliz?",
        "criteria": [
            "Assume caminho feliz; entrada inesperada quebra ou passa calada",
            "Trata alguns casos e deixa outros descobertos",
            "Os casos de falha são explícitos e tratados",
        ],
    },
}

# Contexto: não entra na nota nem barra nada — decide o que fazer e com que
# pressa. Consequência de falha é exatamente o que a fórmula CRAP ignora: um
# formatador de log e um parser de boot com o mesmo CRAP não merecem a mesma
# pressa. E complexidade essencial separa "escreva teste" de "refatore antes".
CONTEXTO = {
    "consequencia_de_falha": {
        "type": "score",
        "instructions": "Se `codigo` falhar em produção, qual o estrago?"
                        " Julgue pelo que ele faz, não por quão provável é.",
        "criteria": [
            "Corrompe dado, expõe informação ou derruba o sistema",
            "Degrada uma funcionalidade; há contorno",
            "Efeito cosmético ou isolado",
        ],
    },
    "complexidade_essencial": {
        "type": "noul",
        "instructions": "Os caminhos de `codigo` vêm das regras do domínio, de"
                        " modo que simplificar apagaria casos reais — em vez de"
                        " virem do jeito como foi escrito?",
    },
}

# Gates: proposições independentes, que podem valer ao mesmo tempo. Um Score
# único de "segurança" obrigaria o modelo a colapsar assuntos distintos numa
# posição só, espalhando a probabilidade justo onde a confiança mais importa.
#
# GRAVE barra o código. É restrito a proposições que ou valem ou não valem, e
# cuja consequência não se discute. Medido neste repositório: as duas juntas
# marcaram 0 de 145 funções — o modelo não inventa vulnerabilidade onde não há,
# e é isso que torna o bloqueio automático confiável.
RISCO_GRAVE = {
    "exec_dinamica": "`codigo` executa ou interpreta dinamicamente conteúdo"
                     " vindo de fora — eval, new Function, desserialização não"
                     " confiável, montagem de comando de shell?",
    "injecao": "`codigo` monta consulta, comando, caminho ou URL concatenando"
               " valor da entrada, sem parametrizar nem escapar?",
}

# ATENÇÃO manda para olho humano, nunca barra. São perguntas graduais disfarçadas
# de proposição: "existe entrada plausível que quebraria isto?" é verdade para
# quase toda função escrita em linguagem dinâmica. Medido aqui: 125 de 145
# funções acima de 0.50 e 32 acima de 0.80 — entre elas uma função de 5 linhas,
# ccn 1, com 100% de cobertura. Como gate de bloqueio isso não separa nada; como
# lista de revisão, é informação legítima.
RISCO_ATENCAO = {
    "entrada_nao_validada": "Os valores que chegam de fora em `codigo` são"
                            " usados sem checar tipo, formato ou presença?",
    "retorno_inconsistente": "O valor devolvido por `codigo` muda de tipo ou"
                             " forma conforme o caminho, de um jeito que quem"
                             " chama não consegue prever?",
    "caso_limite_nao_tratado": "Existe entrada plausível — vazia, nula, grande"
                               " demais — que faria `codigo` quebrar ou dar"
                               " resultado errado em silêncio?",
}

RISCO = {**RISCO_GRAVE, **RISCO_ATENCAO}

# Quanto cada dimensão compensável pesa. Só QUALIDADE entra: mexer num peso é
# decisão de produto e não deveria exigir rodar inferência de novo.
PESOS = {"complexidade_cognitiva": 0.30, "teste_verifica": 0.25,
         "manutenibilidade": 0.25, "tratamento_de_erros": 0.20}

assert set(PESOS) == set(QUALIDADE), "PESOS e QUALIDADE divergem"
assert round(sum(PESOS.values()), 6) == 1.0, "PESOS não somam 1.0"

PERGUNTAS = {**QUALIDADE, **CONTEXTO,
             **{n: {"type": "noul", "instructions": i} for n, i in RISCO.items()}}

# Resposta de exemplo para ver o fluxo sem rede. Não é inventada: é uma resposta
# real do jev-1.13.0 para a função `agregar` deste repositório, capturada e
# congelada. Vale a pena ser real porque a escala do Score é a armadilha fácil
# deste código — `score` volta de 0 a (níveis-1), não de 0 a 1, e um exemplo
# escrito na escala errada faria o modo --demo mentir com cara de resultado.
DEMO = {"model": "jev-1.13.0 (demo)",
        "usage": {"input_tokens": 2263, "output_tokens": 215},
        "answers": {
            "complexidade_cognitiva": {"score": 1.07, "confidence": 0.64},
            "teste_verifica": {"score": 0.22, "confidence": 0.67},
            "manutenibilidade": {"score": 1.01, "confidence": 0.44},
            "tratamento_de_erros": {"score": 1.43, "confidence": 0.33},
            "consequencia_de_falha": {"score": 0.88, "confidence": 0.59},
            "complexidade_essencial": {"noul": 0.63},
            "exec_dinamica": {"noul": 0.02}, "injecao": {"noul": 0.02},
            "entrada_nao_validada": {"noul": 0.37},
            "retorno_inconsistente": {"noul": 0.21},
            "caso_limite_nao_tratado": {"noul": 0.69},
        }}


# ------------------------------------------------------------------ cobertura

def ler_cobertura(caminho):
    """LCOV ou Cobertura XML: linhas executáveis e branches, por arquivo e por
    linha. Guardar branch por linha (e não só o total do arquivo) é o que
    permite recortar cobertura por função — o total não diz *onde* os desvios
    estão."""
    texto = Path(caminho).read_text(encoding="utf-8")
    return _lcov(texto) if texto.lstrip()[:1] != "<" else _cobertura_xml(texto)


def _lcov(texto):
    arquivos, atual = {}, None
    for linha in texto.splitlines():
        if linha.startswith("SF:"):
            atual = arquivos.setdefault(linha[3:].strip(), {"linhas": {}, "branches": {}})
        elif atual is None:
            continue
        elif linha.startswith("DA:"):
            n, hits = linha[3:].split(",")[:2]
            atual["linhas"][int(n)] = int(hits) > 0
        elif linha.startswith("BRDA:"):
            n, _, _, hits = linha[5:].split(",")[:4]
            c, t = atual["branches"].get(int(n), (0, 0))
            atual["branches"][int(n)] = (c + (hits not in ("-", "0")), t + 1)
    return arquivos


def _cobertura_xml(texto):
    arquivos = {}
    for classe in ET.fromstring(texto).iter("class"):
        alvo = arquivos.setdefault(classe.get("filename", ""),
                                   {"linhas": {}, "branches": {}})
        for ln in classe.iter("line"):
            n, hits = int(ln.get("number", 0)), int(ln.get("hits", 0))
            alvo["linhas"][n] = hits > 0
            # "50% (1/2)" é como o Cobertura grava condition-coverage.
            cond = ln.get("condition-coverage", "")
            if "(" in cond:
                c, t = cond.split("(")[1].rstrip(")").split("/")
                alvo["branches"][n] = (int(c), int(t))
    return arquivos


def _componentes(caminho):
    return tuple(p for p in Path(str(caminho).replace("\\", "/")).parts if p not in ("/", ""))


def _sufixo_comum(a, b):
    """Quantos componentes finais de caminho os dois têm em comum.

    Comparar componentes inteiros, e não texto, é o que impede `cobertura.py`
    de casar com `xcobertura.py`: por texto puro um é sufixo do outro.
    """
    pa, pb = _componentes(a), _componentes(b)
    n = 0
    while n < min(len(pa), len(pb)) and pa[-1 - n] == pb[-1 - n]:
        n += 1
    return n


def cobertura_do_arquivo(cobertura, alvo):
    """Entrada do relatório que corresponde a `alvo`, ou None.

    O relatório grava o caminho do CI ("/build/src/a.js"); casar por sufixo
    evita que o arquivo apareça como "sem cobertura" por diferença de prefixo.
    Entre vários candidatos vence o de maior sufixo comum — pegar o primeiro
    que serve atribuiria a cobertura do arquivo errado quando dois módulos têm
    o mesmo nome de base em pastas diferentes.
    """
    melhor, melhor_n = None, 0
    for k, v in cobertura.items():
        n = _sufixo_comum(k, alvo)
        if n > melhor_n:
            melhor, melhor_n = v, n
    return melhor


def cobertura_de_faixa(cob, inicio, fim):
    """(cobertura_linha, cobertura_branch) dentro da faixa de uma função.

    Faixa sem linha executável devolve SEM_DADOS, não 0.0: função só de
    docstring não é função sem teste.
    """
    if cob is None:
        return SEM_DADOS, SEM_DADOS
    linhas = [ok for n, ok in cob["linhas"].items() if inicio <= n <= fim]
    br = [(c, t) for n, (c, t) in cob["branches"].items() if inicio <= n <= fim]
    totais = sum(t for _, t in br)
    return (sum(linhas) / len(linhas) if linhas else SEM_DADOS,
            sum(c for c, _ in br) / totais if totais else SEM_DADOS)


def crap(ccn, cobertura_linha):
    """cc² × (1 − cobertura)³ + cc, a fórmula clássica.

    Sem cobertura conhecida assume o pior: um número otimista aqui esconderia
    exatamente o que se quer achar. Os expoentes 2 e 3 são intuição do autor
    original, não calibração contra defeito real — trate como ordenação, não
    como medida.
    """
    cob = 0.0 if cobertura_linha == SEM_DADOS else cobertura_linha
    return round(ccn ** 2 * (1 - cob) ** 3 + ccn, 1)


# --------------------------------------------------------------------- medir

MARCAS_DE_TESTE = ("test", "spec", "fixture", "conftest", "__mocks__")


def parece_teste(caminho, raiz=None):
    """Arquivo de teste, por nome ou por pasta.

    Duas sutilezas que custaram falso negativo e falso positivo:

    - a comparação é por *substring* em cada componente, porque a convenção
      dominante é `tests/` no plural e um teste de igualdade exata contra
      "test" não pega nenhuma pasta real;
    - só os componentes abaixo de `raiz` são olhados. Sem isso, um projeto que
      mora em `~/dev/jev-crap-test/` teria todo o código classificado como
      teste, e a varredura devolveria zero função sem explicar por quê.
    """
    p = Path(caminho)
    if raiz is not None:
        try:
            p = p.relative_to(raiz)
        except ValueError:
            pass
    return any(m in parte.lower() for parte in p.parts for m in MARCAS_DE_TESTE)


def alvos(caminhos):
    """Arquivos de código a avaliar. Teste não entra: avaliar o próprio teste
    polui o resultado e gasta token sem responder nada. Diretório de IGNORAR
    não é percorrido — a poda é na descida, não na filtragem, para não pagar a
    travessia de um site-packages inteiro antes de descartá-lo."""
    for bruto in caminhos:
        p = Path(bruto)
        if p.is_file():
            if p.suffix in LINGUAGENS and not parece_teste(p, p.parent):
                yield p
            continue
        for pasta, subpastas, arquivos in os.walk(p):
            subpastas[:] = sorted(d for d in subpastas if d not in IGNORAR)
            for nome in sorted(arquivos):
                arq = Path(pasta) / nome
                if arq.suffix in LINGUAGENS and not parece_teste(arq, p):
                    yield arq


# Uma menção sozinha não é um teste: o que serve ao julgamento é o corpo da
# função de teste em volta dela. 200 caracteres para trás alcançam o `def`; 900
# para frente costumam cobrir as asserções.
JANELA_ANTES, JANELA_DEPOIS, MAX_TRECHOS = 200, 900, 3
INICIO_DE_TESTE = re.compile(
    r"^[ \t]*(?:async\s+)?(?:def|function|it|test|describe)\b.*$", re.M)


def _trecho_ao_redor(texto, pos):
    """Recorte que começa no cabeçalho do teste que contém `pos`, ou None.

    Voltar até o `def` importa mais do que parece: sem isso o trecho começa no
    meio de uma asserção e o modelo não vê o que estava sendo montado.

    Não achar cabeçalho nenhum é a resposta para o caso chato: a menção está
    fora de qualquer função de teste, quase sempre dentro de um
    `from x import (\\n  nome,\\n)` — que passa pelo filtro de linha porque a
    linha em si é só o nome. Menção fora de teste não é teste.
    """
    cabecalhos = [m.start() for m in INICIO_DE_TESTE.finditer(texto, 0, pos)]
    if not cabecalhos or pos - cabecalhos[-1] > 2000:
        return None
    return texto[cabecalhos[-1]:pos + JANELA_DEPOIS]


def testes_de(nome, pasta):
    """Trechos de teste que citam a função pelo nome.

    É heurística: teste que exercita sem citar escapa. Serve para o Jev julgar
    o que existe — quem prova cobertura é o relatório, não isto. E quando ela
    não acha nada, o chamador não pergunta: ver a regra 5 no topo do arquivo.

    A versão anterior mandava `texto[:1500]`, que é o começo do *arquivo* e não
    o trecho relevante. Medido: das 13 menções a `agregar` em
    test_aprendizado.py, esse corte alcançava exatamente uma — a linha de
    import. O Jev respondia "não há teste" corretamente, sobre o material
    errado: 14% contra 92% na mesma função depois do conserto.
    """
    if not pasta:
        return []
    raiz = Path(pasta)
    achados = []
    padrao = re.compile(rf"\b{re.escape(nome)}\b")
    for arq in sorted(raiz.rglob("*")):
        if set(arq.parts) & IGNORAR or not arq.is_file():
            continue
        if arq.suffix not in LINGUAGENS or not parece_teste(arq, raiz.parent):
            continue
        texto = arq.read_text(encoding="utf-8", errors="ignore")
        for m in padrao.finditer(texto):
            linha = texto[texto.rfind("\n", 0, m.start()) + 1:
                          texto.find("\n", m.start())]
            # Import não é exercício da função; conta como menção e não é uma.
            if re.match(r"\s*(from|import|#|//|\*)", linha):
                continue
            trecho = _trecho_ao_redor(texto, m.start())
            if trecho is None:
                continue
            achados.append(f"# {arq}\n{trecho}")
            if len(achados) >= MAX_TRECHOS:
                return achados
    return achados


def medir(caminho, cobertura, pasta_testes):
    """Fato contável por função, offline, antes de gastar token."""
    codigo = caminho.read_text(encoding="utf-8", errors="ignore")
    linhas = codigo.splitlines()
    alvo = str(caminho)
    cob = cobertura_do_arquivo(cobertura, alvo)

    funcoes = []
    for f in lizard.analyze_file.analyze_source_code(alvo, codigo).function_list:
        cob_linha, cob_branch = cobertura_de_faixa(cob, f.start_line, f.end_line)
        funcoes.append({
            "arquivo": alvo, "nome": f.name, "ccn": f.cyclomatic_complexity,
            "linhas": (f.start_line, f.end_line),
            "tamanho": f.end_line - f.start_line + 1,
            "cobertura_linha": cob_linha, "cobertura_branch": cob_branch,
            "crap": crap(f.cyclomatic_complexity, cob_linha),
            "codigo": "\n".join(linhas[f.start_line - 1:f.end_line]),
            "testes": testes_de(f.name, pasta_testes),
        })
    return funcoes


# -------------------------------------------------------------------- julgar

# 429 e 529 são os dois códigos que a API documenta como transitórios; os 5xx
# de proxy entram porque quem está no meio do caminho também cai. O 529 estava
# faltando, e ele é justamente o que aparece sob a concorrência que este script
# usa: a rajada que provoca a sobrecarga é a própria batelada.
STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504, 529})
ESPERA_BASE, JITTER = 1.0, 0.5
MAX_TENTATIVAS = 4


def _post(corpo, chave, dormir=time.sleep, sortear=random.random):
    """Retry exponencial com jitter nos códigos transitórios.

    O jitter existe porque as oito requisições concorrentes tomam 429 no mesmo
    instante: sem ele, voltam juntas e recriam o pico que causou o 429.
    """
    for tentativa in range(MAX_TENTATIVAS):
        r = httpx.post(URL, json=corpo, timeout=60,
                       headers={"Authorization": f"Bearer {chave}"})
        if r.status_code in STATUS_TRANSITORIOS and tentativa < MAX_TENTATIVAS - 1:
            dormir(ESPERA_BASE * (2 ** tentativa) + sortear() * JITTER)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("laço de tentativas terminou sem resposta nem erro")


def perguntas_para(funcao):
    """As perguntas que esta função tem como responder.

    `teste_verifica` só vai quando há trecho de teste para olhar. Sem isso a
    resposta seria "não há teste" para toda função privada exercitada
    indiretamente — medido aqui: 87 das 145 funções chegam sem trecho, e 78
    delas têm 80% ou mais de cobertura de linha. O script já sabia que estavam
    testadas; perguntar assim mesmo trocava um fato por um palpite mal
    informado, e cobrava 25% da nota por ele.
    """
    perguntas = dict(PERGUNTAS)
    if not funcao["testes"]:
        del perguntas["teste_verifica"]
    return perguntas


def julgar(funcao, linguagem, chave):
    """Uma requisição por função, com todas as perguntas. Elas correm em
    paralelo e não veem as respostas umas das outras — por isso nenhuma pede a
    decisão final: essa depende de todas e é montada em código, logo abaixo."""
    codigo = funcao["codigo"]
    estado = {"codigo": codigo, "linguagem": linguagem, "testes": funcao["testes"]}
    if funcao["tamanho"] > MAX_ENVIO:
        # Avisar que o trecho é parcial evita que o modelo julgue casos-limite
        # de um pedaço achando que viu a função inteira.
        estado["codigo"] = "\n".join(codigo.splitlines()[:MAX_ENVIO])
        estado["aviso"] = (f"a função tem {funcao['tamanho']} linhas; `codigo`"
                           f" traz apenas as {MAX_ENVIO} primeiras")
    if funcao["cobertura_branch"] != SEM_DADOS:
        estado["cobertura_branch"] = round(funcao["cobertura_branch"], 2)
    return _post({"model": "jev-latest", "state": estado,
                  "questions": perguntas_para(funcao)}, chave)


# ------------------------------------------------------------------- avaliar

def _normalizar(resposta, definicao):
    """Score na escala 0..(níveis-1) vira 0..1.

    A API devolve a média dos níveis ponderada pelas probabilidades, então uma
    escala de três níveis vai até 2 — dividir por 1 aqui daria nota acima de
    100 e ninguém perceberia, porque o número continua parecendo plausível.
    """
    return resposta["score"] / (len(definicao["criteria"]) - 1)


def avaliar(funcao, linguagem, chave=None, demo=False):
    resposta = DEMO if demo else julgar(funcao, linguagem, chave)
    r = resposta["answers"]

    # Acesso direto de propósito: se a API não responder uma pergunta que foi
    # feita, é melhor quebrar aqui do que publicar uma nota parcial sem avisar.
    # A exceção é `teste_verifica`, que pode não ter sido feita — e aí o peso
    # dela é redistribuído entre o que foi de fato observado, em vez de entrar
    # como zero. Nota calculada sobre três dimensões é honesta; nota calculada
    # sobre quatro com uma delas inventada, não.
    pedidas = [n for n in PESOS if n in r]
    notas = {n: _normalizar(r[n], QUALIDADE[n]) for n in pedidas}
    nao_observadas = [n for n in PESOS if n not in r]
    peso_total = sum(PESOS[n] for n in pedidas)
    nota = round(100 * sum(PESOS[n] * v for n, v in notas.items()) / peso_total, 1)

    # Gates: fora da nota, porque risco não se compensa com qualidade boa. Só
    # RISCO_GRAVE barra; o resto vira dúvida (ver o comentário em RISCO_ATENCAO).
    graves = [n for n in RISCO_GRAVE if r[n]["noul"] >= BLOQUEIO]
    duvidas = [n for n in RISCO if r[n]["noul"] >= SUSPEITA and n not in graves]
    duvidas += [f"{n} disperso" for n in pedidas if r[n].get("confidence", 1) < CONF_MINIMA]
    if funcao["ccn"] > LIMITE_CCN:
        duvidas.append(f"ccn {funcao['ccn']}")
    if nao_observadas:
        duvidas.append("teste não localizado")
    # O trecho enviado foi parcial, então a nota vale sobre um pedaço. Isso não
    # barra — barrar aqui puniria tamanho duas vezes —, mas precisa aparecer,
    # senão a nota se apresenta como se o modelo tivesse visto a função toda.
    if funcao["tamanho"] > MAX_ENVIO:
        duvidas.append(f"julgado sobre as {MAX_ENVIO} primeiras de"
                       f" {funcao['tamanho']} linhas")

    # Tamanho é contável, então é gate e não opinião: acima do limite a nota vai
    # a zero independentemente do que o modelo tenha achado do trecho que viu.
    if funcao["tamanho"] > LIMITE_TAMANHO:
        nota = 0.0
        graves.insert(0, f"{funcao['tamanho']} linhas (limite {LIMITE_TAMANHO})")

    # Quadrante: o número não distingue complexidade que veio do domínio de
    # complexidade que veio da escrita, e a ação muda por completo. Testar antes
    # de refatorar congela justamente o desenho que se quer trocar.
    essencial = r["complexidade_essencial"]["noul"] >= 0.5
    forma_fraca = notas["complexidade_cognitiva"] < 0.5 and not essencial
    if "teste_verifica" in notas:
        teste_fraco = notas["teste_verifica"] < 0.5
    else:
        # Sem julgamento sobre teste, quem responde é o fato contável — que é a
        # regra 1 do arquivo aplicada onde ela sempre deveria ter valido.
        cob = funcao["cobertura_linha"]
        teste_fraco = cob != SEM_DADOS and cob < 0.5
    if forma_fraca and teste_fraco:
        conselho = "refatorar e só depois testar: testar agora congela o desenho a trocar"
    elif forma_fraca:
        conselho = "simplificar a forma: os caminhos vieram da escrita, não do domínio"
    elif teste_fraco:
        conselho = "escrever teste, não refatorar: a complexidade vem do domínio"
    else:
        conselho = "nada obrigatório"

    # Prioridade: o CRAP diz o tamanho do problema, a consequência de falha diz
    # se vale a pressa — e é justamente o que a fórmula clássica ignora. A
    # consequência entra normalizada; comparar o score cru com 0.5 media metade
    # de uma escala que vai até 2, e a condição quase nunca disparava.
    consequencia = _normalizar(r["consequencia_de_falha"],
                               CONTEXTO["consequencia_de_falha"])
    if graves or (funcao["crap"] >= LIMIAR_CRAP and consequencia <= 0.5):
        prioridade = "alta"
    elif funcao["crap"] >= LIMIAR_CRAP or duvidas:
        prioridade = "media"
    else:
        prioridade = "baixa"

    acao = "bloquear" if graves else "revisar" if duvidas or nota < 60 else "aprovar"
    return {**funcao, "notas": notas, "nao_observadas": nao_observadas,
            "nota": nota, "graves": graves, "duvidas": duvidas,
            "conselho": conselho, "prioridade": prioridade, "acao": acao,
            "respostas": r, "modelo": resposta["model"],
            "usage": resposta.get("usage", {})}


# ------------------------------------------------------------------ ambiente

ARQUIVO_ENV = ".env"


def carregar_env(inicio=None):
    """Exporta para o ambiente o que estiver num `.env`. Devolve o arquivo lido.

    Feito à mão, sem python-dotenv: a única coisa que este script precisa do
    arquivo é uma linha `NOME=valor`, e uma dependência a mais é uma a mais
    para instalar no CI antes que a primeira avaliação rode.

    Três decisões que mudam o resultado:

    - o ambiente vence o arquivo (`setdefault`). No CI a chave chega por
      secret; se o arquivo sobrescrevesse, um `.env` esquecido no disco trocaria
      silenciosamente a chave da organização pela de alguém;
    - a procura sobe os diretórios pais, porque `python avaliar.py .` rodado de
      dentro de `src/` é comum e o `.env` mora na raiz — olhar só o diretório
      atual falharia exatamente aí, e falharia dizendo "defina a variável", que
      é o diagnóstico errado;
    - aspas em volta do valor são do formato do arquivo, não do segredo. Sem
      tirá-las, a chave vai para o header com as aspas juntas e a API responde
      401 sem dizer por quê. `#` no fim da linha *não* vira comentário: é
      caractere válido dentro de uma chave, e cortar ali corromperia o valor.
    """
    partida = Path(inicio or Path.cwd()).resolve()
    for pasta in (partida, *partida.parents):
        arquivo = pasta / ARQUIVO_ENV
        if arquivo.is_file():
            break
    else:
        return None

    for linha in arquivo.read_text(encoding="utf-8", errors="ignore").splitlines():
        linha = linha.strip().removeprefix("export ").lstrip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        nome, valor = nome.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if nome:
            os.environ.setdefault(nome, valor)
    return arquivo


# ---------------------------------------------------------------------- main

def _pct(v):
    return "n/d" if v == SEM_DADOS else f"{v:.0%}"


def _argumentos(argv):
    p = argparse.ArgumentParser(
        prog="avaliar.py",
        description="Avalia código função a função cruzando métrica com julgamento.")
    p.add_argument("alvos", nargs="+", metavar="ALVO")
    p.add_argument("--cobertura", metavar="REL", help="LCOV ou Cobertura XML")
    p.add_argument("--testes", metavar="DIR", help="pasta com os testes")
    p.add_argument("--demo", action="store_true", help="resposta fixa, sem rede")
    p.add_argument("--so-risco", action="store_true", dest="so_risco",
                   help=f"só funções com CRAP >= {LIMIAR_CRAP}")
    p.add_argument("--json", metavar="ARQ", help="grava o resultado bruto")
    return p.parse_args(argv)


def _imprimir(v):
    ini, fim = v["linhas"]
    print(f"\n{v['arquivo']}:{ini}  {v['nome']}  ({v['tamanho']} linhas)")
    print(f"  contável   ccn {v['ccn']}  crap {v['crap']}  "
          f"linha {_pct(v['cobertura_linha'])}  branch {_pct(v['cobertura_branch'])}")
    julgado = "  ".join(f"{n.split('_')[0]} {x:.0%}" for n, x in v["notas"].items())
    if v["nao_observadas"]:
        julgado += "  [" + ", ".join(f"{n.split('_')[0]} n/d" for n in v["nao_observadas"]) + "]"
    print("  julgado    " + julgado)
    for n in RISCO:
        p = v["respostas"][n]["noul"]
        if p >= SUSPEITA:
            grave = n in RISCO_GRAVE and p >= BLOQUEIO
            print(f"  {'✗' if grave else '!'} {n} {p:.2f}")
    print(f"  → {v['conselho']}")
    print(f"  nota {v['nota']}/100 · {v['acao'].upper()} · prioridade {v['prioridade']}")
    if v["graves"]:
        print(f"  barrado por: {', '.join(v['graves'])}")
    if v["duvidas"]:
        print(f"  revisar por: {', '.join(v['duvidas'])}")


def main(argv=None):
    args = _argumentos(sys.argv[1:] if argv is None else argv)

    env = carregar_env()
    chave = os.environ.get("TYPESAFE_API_KEY")
    if not args.demo and not chave:
        # Falhar aqui, e não dentro do pool: a verificação ficava depois da
        # varredura inteira, então um erro de configuração só aparecia com o
        # trabalho de medição já feito e jogado fora.
        #
        # Dizer qual arquivo foi lido separa os dois enganos que produzem a
        # mesma tela em branco: não existe `.env` nenhum, ou existe um e ele
        # não tem a chave — quase sempre um `.env` de outro projeto, achado
        # ao subir os diretórios.
        onde = (f"{env} não define a variável" if env
                else f"nenhum {ARQUIVO_ENV} encontrado a partir de {Path.cwd()}")
        print(f"defina TYPESAFE_API_KEY no ambiente ou num {ARQUIVO_ENV}"
              f" — {onde} (ou use --demo)", file=sys.stderr)
        return 3
    try:
        cobertura = ler_cobertura(args.cobertura) if args.cobertura else {}
    except (OSError, ET.ParseError) as erro:
        print(f"não consegui ler {args.cobertura}: {erro}", file=sys.stderr)
        return 3

    pendentes = []
    for arq in alvos(args.alvos):
        lang = LINGUAGENS[arq.suffix]
        for f in medir(arq, cobertura, args.testes):
            # Filtrar por CRAP economiza token, mas cega o sistema justamente no
            # caso mais interessante: ccn baixa com código ilegível. Opt-in.
            if not args.so_risco or f["crap"] >= LIMIAR_CRAP:
                pendentes.append((f, lang))

    print(f"\n── {len(pendentes)} função(ões) · {CONCORRENCIA} em paralelo ──")
    if args.demo:
        print("   (--demo: resposta fixa, toda função recebe o mesmo julgamento)")
    if not pendentes:
        print("\nnada a avaliar\n")
        return 0

    # as_completed no lugar de map: uma falha derrubava a batelada inteira e
    # descartava tudo que já tinha sido pago. A função que falhou vira linha de
    # relatório; as outras continuam valendo.
    avaliadas, falhas = [], []
    with ThreadPoolExecutor(max_workers=CONCORRENCIA) as pool:
        tarefas = {pool.submit(avaliar, f, lang, chave, args.demo): f
                   for f, lang in pendentes}
        for tarefa in as_completed(tarefas):
            try:
                avaliadas.append(tarefa.result())
            except Exception as erro:  # noqa: BLE001 - uma função não derruba o resto
                f = tarefas[tarefa]
                falhas.append((f"{f['arquivo']}:{f['nome']}", f"{type(erro).__name__}: {erro}"))
    avaliadas.sort(key=lambda v: -v["crap"])

    for v in avaliadas:
        _imprimir(v)

    if falhas:
        print(f"\n── {len(falhas)} função(ões) sem julgamento ──")
        for nome, erro in falhas:
            print(f"  {nome}: {erro}")

    entrada = sum(v["usage"].get("input_tokens", 0) for v in avaliadas)
    saida = sum(v["usage"].get("output_tokens", 0) for v in avaliadas)
    if entrada or saida:
        # O `usage` vinha na resposta e era descartado. Sem ele não há como
        # responder "quanto custou esta varredura" senão por estimativa.
        print(f"\ncusto: {len(avaliadas)} requisições · {entrada} tokens de entrada"
              f" · {saida} de saída")

    if args.json:
        Path(args.json).write_text(json.dumps(
            [{k: v for k, v in a.items() if k != "codigo"} for a in avaliadas],
            ensure_ascii=False, indent=1), encoding="utf-8")

    ordem = ["bloquear", "revisar", "aprovar"]
    pior = min((v["acao"] for v in avaliadas), key=ordem.index, default="aprovar")
    # Falha parcial não pode sair como "aprovar": o que não foi julgado não foi
    # aprovado, e o exit code é o que o CI lê.
    if falhas and pior == "aprovar":
        pior = "revisar"
    print(f"\nresultado: {pior.upper()}\n")
    return {"aprovar": 0, "revisar": 1, "bloquear": 2}[pior]


if __name__ == "__main__":
    raise SystemExit(main())
