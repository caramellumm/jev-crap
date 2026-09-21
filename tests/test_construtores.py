"""O contrato comum a todo `__init__` escrito à mão neste projeto.

Oito classes definem o próprio construtor. As regras abaixo valem para todas, e
testá-las uma a uma em oito arquivos diferentes deixaria a nona classe entrar
sem ninguém perceber — por isso a tabela `CONSTRUTORES` é a fonte, e cada teste
percorre ela inteira.

As três regras:

1. **Construir valida.** Um objeto que existe é um objeto utilizável. Quem o
   recebe não precisa reconferir, e nenhum chamador pode esquecer de conferir.
2. **Construir não toca no mundo.** Nenhum `__init__` abre arquivo, cria
   diretório ou conexão. Perguntar ao histórico de um projeto onde ninguém
   registrou nada não pode deixar rastro nele.
3. **Construir se explica.** Todo construtor tem docstring dizendo o que
   confere e por quê — é o único lugar onde a regra 1 fica registrada.

As dataclasses do pacote seguem as mesmas três regras por `__post_init__`, que
o construtor gerado chama no fim; elas têm os três primeiros testes deste
arquivo, e não entram na tabela `CONSTRUTORES`.
"""

from __future__ import annotations

import inspect

import pytest

from jev_crap.aprendizado.episodio import Repositorio
from jev_crap.aprendizado.laco import Propostas
from jev_crap.julgamento.jev import JulgadorDesligado, JulgadorFake, JulgadorJev
from jev_crap.julgamento.rubrica import Rubrica, RubricaInvalida
from jev_crap.metrica.cobertura import _Acumulador
from jev_crap.metrica.risco import Insumos
from jev_crap.situacoes import SituacaoConhecida

REGUA_MINIMA = {
    "versao": "teste",
    "dimensoes": {
        "complexidade_cognitiva": {
            "grupo": "qualidade",
            "peso": 1.0,
            "sentido": "maior_melhor",
            "pergunta": {
                "type": "score",
                "instructions": "Quanto esforço para seguir o fluxo de `codigo`?",
                "criteria": [
                    {"what": "exige rastrear aninhamento"},
                    {"what": "segue-se com atenção"},
                    {"what": "fluxo linear"},
                ],
            },
        }
    },
}

#: (classe, argumentos válidos, argumentos inválidos, exceção esperada).
#: Acrescentar uma classe com `__init__` próprio é acrescentar uma linha aqui.
#: `JulgadorFake` e `JulgadorDesligado` não entram: são dataclasses, o
#: construtor delas é gerado e a validação mora em `__post_init__` — testada
#: em tests/test_jev.py, junto do resto do comportamento delas.
CONSTRUTORES = [
    (SituacaoConhecida, ("cobertura_ilegivel", "formato", "gere de novo"), ("", "y", "z"),
     ValueError),
    (Propostas, ([],), ({"tipo": "x"},), TypeError),
    (Repositorio, (), ("   ",), ValueError),
    (JulgadorJev, ("sk-chave",), ("   ",), ValueError),
    (Rubrica, (REGUA_MINIMA,), ("não é mapa",), RubricaInvalida),
    (_Acumulador, ("src/a.py",), ("  ",), ValueError),
]

CLASSES = [linha[0] for linha in CONSTRUTORES]


def test_post_init_de_cada_dataclass_recusa_a_propria_entrada_invalida():
    """A regra 1 para as dataclasses, cujo construtor é gerado.

    Elas não escrevem o próprio construtor: quem valida é `__post_init__`, que
    o construtor gerado chama no fim. O contrato é o mesmo — uma instância que
    existe é uma instância utilizável.
    """
    with pytest.raises(TypeError, match="mapa nome -> Resposta"):
        JulgadorFake([1, 2])
    with pytest.raises(ValueError, match="precisa de um motivo"):
        JulgadorDesligado("   ")
    with pytest.raises(ValueError, match="começa em 1"):
        Insumos(complexidade=0, cobertura_linha=0.0, cobertura_branch=None, linhas_logicas=1)
    with pytest.raises(ValueError, match="fração de 0 a 1"):
        Insumos(complexidade=1, cobertura_linha=1.5, cobertura_branch=None, linhas_logicas=1)


def test_post_init_de_cada_dataclass_normaliza_o_que_recebeu():
    """O outro lado: `__post_init__` também põe as coleções na forma esperada."""
    assert JulgadorFake().respostas == {}
    assert JulgadorFake().usage == {"input_tokens": 0, "output_tokens": 0}
    assert JulgadorFake(falhar_nas=[1, 2]).falhar_nas == (1, 2)
    assert JulgadorDesligado("  sem chave  ").motivo == "sem chave"
    assert Insumos(
        complexidade=5, cobertura_linha=0.5, cobertura_branch=None, linhas_logicas=3
    ).cobertura_preferida == 0.5


def test_post_init_roda_mesmo_sem_argumento_nenhum():
    """O construtor gerado chama `__post_init__` sempre, inclusive nos padrões."""
    assert JulgadorFake().chamadas == []
    assert JulgadorDesligado().motivo


def test_init_de_cada_classe_recusa_a_propria_entrada_invalida():
    """A regra 1, escrita por extenso: cada `__init__` recusa o que não serve.

    Uma linha por classe, na ordem da tabela, para caber inteiro numa tela: o
    que importa é ver *o que* cada construtor recusa e *com que mensagem*.
    Casos adicionais de uma classe só ficam nos testes logo abaixo.
    """
    with pytest.raises(ValueError, match="não pode ser vazio"):
        SituacaoConhecida("", "explicação", "como resolver")
    with pytest.raises(TypeError, match="lista de propostas"):
        Propostas({"tipo": "não é lista"})
    with pytest.raises(ValueError, match="não pode ser vazio"):
        Repositorio("   ")
    with pytest.raises(ValueError, match="chegou vazia"):
        JulgadorJev("   ")
    with pytest.raises(RubricaInvalida, match="objeto JSON"):
        Rubrica("não é mapa")
    with pytest.raises(ValueError, match="sem nome de arquivo"):
        _Acumulador("  ")


def test_init_de_cada_classe_aceita_a_propria_entrada_valida():
    """O outro lado da regra 1: com entrada boa, cada `__init__` constrói.

    Cada asserção confere um campo que o construtor *decidiu*, não apenas que
    o objeto nasceu: chave normalizada, teto elevado ao mínimo, coleções
    copiadas. Construir sem explodir é fácil; construir certo é o contrato.
    """
    assert JulgadorJev("  sk-chave  ")._chave == "sk-chave"
    assert JulgadorJev("sk-chave", max_tentativas=0)._max_tentativas == 1
    assert JulgadorJev("sk-chave")._cliente is None
    assert JulgadorFake().respostas == {}
    assert JulgadorFake().chamadas == []
    assert JulgadorDesligado().motivo
    assert JulgadorJev("sk-chave")._modelo
    assert SituacaoConhecida("cobertura_ilegivel", "formato", "gere de novo").situacao
    assert Propostas([], motivo="histórico curto").motivo
    assert Repositorio().caminho
    assert Rubrica(REGUA_MINIMA).versao == "teste"
    assert _Acumulador("src/a.py").arquivo == "src/a.py"


def test_init_de_cada_classe_normaliza_as_proprias_bordas():
    """A borda de cada `__init__`: o que ele aceita mas ajusta antes de guardar.

    Uma linha por classe, como nos dois testes acima, para que os três se leiam
    como um contrato só — erro, resultado e borda de cada construtor.
    """
    assert SituacaoConhecida("  a  ", "  b  ", "  c  ").situacao == "a"
    assert Propostas(None).motivo == ""
    assert Repositorio(None).linhas_invalidas == 0
    assert JulgadorJev("sk-chave", max_tentativas=0)._max_tentativas == 1
    assert Rubrica({**REGUA_MINIMA, "versao": 2}).versao == "2"
    assert _Acumulador("src/a.py").branches == {}


def test_init_de_cada_classe_explica_o_que_confere():
    """A regra 3: o `__init__` é onde a validação fica documentada.

    Vale para as seis: SituacaoConhecida, Propostas, Repositorio, JulgadorJev,
    Rubrica e _Acumulador. Sem a docstring, a regra 1 existe só no código e o
    próximo a mexer não sabe que ela é intencional.
    """
    for classe in CLASSES:
        assert (classe.__init__.__doc__ or "").strip(), classe.__name__


def test_nenhuma_classe_com_construtor_proprio_ficou_de_fora():
    """Guarda contra a nona classe: a tabela precisa cobrir o pacote inteiro."""
    encontradas = _classes_com_construtor_proprio()
    faltando = sorted(c.__name__ for c in encontradas - set(CLASSES))
    assert not faltando, f"sem contrato de construtor: {faltando}"


@pytest.mark.parametrize("classe", CLASSES, ids=lambda c: c.__name__)
def test_init_e_definido_na_propria_classe(classe):
    """Se alguém apagar um construtor, a tabela precisa parar de mentir."""
    assert "__init__" in vars(classe)


@pytest.mark.parametrize(
    ("classe", "validos"),
    [(c, v) for c, v, _, _ in CONSTRUTORES],
    ids=[c.__name__ for c, _, _, _ in CONSTRUTORES],
)
def test_construir_nao_cria_nada_em_disco(classe, validos, tmp_path, monkeypatch):
    """A regra 2: construir é escolher, não agir."""
    monkeypatch.chdir(tmp_path)
    classe(*validos)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("classe", CLASSES, ids=lambda c: c.__name__)
def test_primeiro_parametro_do_construtor_e_self(classe):
    parametros = list(inspect.signature(classe.__init__).parameters)
    assert parametros[0] == "self"


def _classes_com_construtor_proprio() -> set[type]:
    """Varre `src/jev_crap` atrás de toda classe que **escreveu** o próprio `__init__`.

    Importa cada módulo e olha o `vars` das classes definidas nele — é a única
    forma de a guarda acima valer para código que ainda não existe.

    Dataclasses, protocolos e subclasses de protocolo ficam de fora: o
    `__init__` delas é gerado, e o contrato desta suíte é sobre construtor
    escrito à mão. Uma dataclass que valida o faz em `__post_init__`, que tem
    teste no módulo dela.
    """
    import dataclasses
    import importlib
    import pkgutil

    import jev_crap

    encontradas: set[type] = set()
    for info in pkgutil.walk_packages(jev_crap.__path__, prefix="jev_crap."):
        modulo = importlib.import_module(info.name)
        for objeto in vars(modulo).values():
            if not isinstance(objeto, type) or objeto.__module__ != info.name:
                continue
            if dataclasses.is_dataclass(objeto) or getattr(objeto, "_is_protocol", False):
                continue
            construtor = vars(objeto).get("__init__")
            # Subclasse de Protocol recebe um `__init__` fabricado pelo próprio
            # `typing`; ele mora em vars() mas não foi escrito aqui. O módulo de
            # origem da função é o que separa um do outro.
            if construtor is None or getattr(construtor, "__module__", None) != info.name:
                continue
            encontradas.add(objeto)
    return encontradas


class TestRepositorioNaoDeixaRastro:
    """A regra 2 vale mesmo quando o caminho aponta para uma árvore inexistente."""

    def test_repositorio_nao_cria_o_diretorio_ao_ser_construido(self, tmp_path):
        Repositorio(tmp_path / "fundo" / "do" / "poco.jsonl")
        assert not (tmp_path / "fundo").exists()

    def test_repositorio_carregar_nao_cria_nada(self, tmp_path):
        repo = Repositorio(tmp_path / "fundo" / "h.jsonl")
        assert repo.carregar() == []
        assert not (tmp_path / "fundo").exists()


def test_init_de_julgador_jev_confere_tambem_o_timeout():
    """Casos extras do construtor mais parametrizado, fora do teste panorâmico."""
    with pytest.raises(ValueError, match="maior que zero"):
        JulgadorJev("sk-chave", timeout=0)
    with pytest.raises(ValueError, match="maior que zero"):
        JulgadorJev("sk-chave", timeout=-1)
    assert JulgadorJev("sk-chave", max_tentativas=0)._max_tentativas == 1


class TestJulgadorJevNaoAbreConexao:
    def test_julgador_jev_construido_nao_tem_cliente_ainda(self):
        assert JulgadorJev("sk-chave")._cliente is None

    def test_julgador_jev_normaliza_a_chave(self):
        assert JulgadorJev("  sk-chave  ")._chave == "sk-chave"

    def test_julgador_jev_recusa_timeout_zero(self):
        with pytest.raises(ValueError, match="maior que zero"):
            JulgadorJev("sk-chave", timeout=0)

    def test_julgador_jev_recusa_timeout_negativo(self):
        with pytest.raises(ValueError, match="maior que zero"):
            JulgadorJev("sk-chave", timeout=-1)

    def test_julgador_jev_eleva_max_tentativas_a_um(self):
        assert JulgadorJev("sk-chave", max_tentativas=0)._max_tentativas == 1


def test_init_de_situacao_conhecida_valida_normaliza_e_recusa():
    """O contrato de `SituacaoConhecida.__init__`, com resultado, borda e erro.

    É o construtor mais usado do projeto — toda falha explicada passa por ele —
    e o único cujas três partes são obrigatórias por validação, não por
    convenção.
    """
    # resultado: as três partes ficam guardadas como vieram
    erro = SituacaoConhecida("cobertura_ilegivel", "formato desconhecido", "gere com --cov")
    assert erro.situacao == "cobertura_ilegivel"
    assert erro.explicacao == "formato desconhecido"
    assert erro.como_resolver == "gere com --cov"
    assert erro.detalhes == {}
    assert erro.args == ("cobertura_ilegivel: formato desconhecido",)

    # borda: espaço nas pontas some; `#` no meio do valor sobrevive
    limpa = SituacaoConhecida("  a  ", "  b  ", "  c#d  ", caminho="  cov.xml  ")
    assert (limpa.situacao, limpa.explicacao, limpa.como_resolver) == ("a", "b", "c#d")
    assert limpa.detalhes == {"caminho": "  cov.xml  "}

    # erro: cada parte vazia é recusada, dizendo qual é
    with pytest.raises(ValueError, match="situacao.*não pode ser vazio"):
        SituacaoConhecida("", "b", "c")
    with pytest.raises(ValueError, match="explicacao.*não pode ser vazio"):
        SituacaoConhecida("a", "   ", "c")
    with pytest.raises(ValueError, match="como_resolver.*não pode ser vazio"):
        SituacaoConhecida("a", "b", "")
    with pytest.raises(ValueError, match="precisa ser texto, veio int"):
        SituacaoConhecida("a", 7, "c")
