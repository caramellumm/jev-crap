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
3. **Construir se explica.** Todo `__init__` tem docstring dizendo o que
   confere e por quê — é o único lugar onde a regra 1 fica registrada.
"""

from __future__ import annotations

import inspect

import pytest

from jev_crap.aprendizado.episodio import Repositorio
from jev_crap.aprendizado.laco import Propostas
from jev_crap.julgamento.jev import JulgadorDesligado, JulgadorFake, JulgadorJev
from jev_crap.julgamento.rubrica import Rubrica, RubricaInvalida
from jev_crap.metrica.cobertura import _Acumulador
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
CONSTRUTORES = [
    (SituacaoConhecida, ("cobertura_ilegivel", "formato", "gere de novo"), ("", "y", "z"),
     ValueError),
    (Propostas, ([],), ({"tipo": "x"},), TypeError),
    (Repositorio, (), ("   ",), ValueError),
    (JulgadorJev, ("sk-chave",), ("   ",), ValueError),
    (JulgadorFake, (), ([1, 2],), TypeError),
    (JulgadorDesligado, (), ("   ",), ValueError),
    (Rubrica, (REGUA_MINIMA,), ("não é mapa",), RubricaInvalida),
    (_Acumulador, ("src/a.py",), ("  ",), ValueError),
]

CLASSES = [linha[0] for linha in CONSTRUTORES]


def test_init_de_cada_classe_recusa_a_propria_entrada_invalida():
    """A regra 1, escrita por extenso: cada `__init__` recusa o que não serve.

    Por extenso e não em laço de propósito: quem lê precisa ver *o que* cada
    construtor recusa, e uma tabela percorrida esconde exatamente isso.
    """
    with pytest.raises(ValueError):
        SituacaoConhecida("", "explicação", "como resolver")
    with pytest.raises(TypeError):
        Propostas({"tipo": "não é lista"})
    with pytest.raises(ValueError):
        Repositorio("   ")
    with pytest.raises(ValueError):
        JulgadorJev("   ")
    with pytest.raises(TypeError):
        JulgadorFake([1, 2])
    with pytest.raises(ValueError):
        JulgadorDesligado("   ")
    with pytest.raises(RubricaInvalida):
        Rubrica("não é mapa")
    with pytest.raises(ValueError):
        _Acumulador("  ")


def test_init_de_cada_classe_aceita_a_propria_entrada_valida():
    """O outro lado da regra 1: com entrada boa, cada `__init__` constrói."""
    assert SituacaoConhecida("cobertura_ilegivel", "formato", "gere de novo").situacao
    assert Propostas([], motivo="histórico curto").motivo
    assert Repositorio().caminho
    assert JulgadorJev("sk-chave")._chave == "sk-chave"
    assert JulgadorFake().respostas == {}
    assert JulgadorDesligado().motivo
    assert Rubrica(REGUA_MINIMA).versao == "teste"
    assert _Acumulador("src/a.py").arquivo == "src/a.py"


def test_init_de_cada_classe_explica_o_que_confere():
    """A regra 3: o `__init__` é onde a validação fica documentada."""
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
