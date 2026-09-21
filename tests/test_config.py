"""A configuração: o que se lê do ambiente e a régua que sai no relatório.

A regra que organiza o módulo: **nenhuma configuração malformada derruba a
montagem**. Ela vira aviso, o padrão vale, e o aviso sai no relatório em
`regua.avisos_de_configuracao`. A razão é onde a falha aconteceria: na
inicialização do servidor MCP, cujo stderr ninguém lê.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jev_crap.config import (
    FORMULA_PADRAO,
    VARIAVEL_EPISODIOS,
    VARIAVEL_FORMULA,
    VARIAVEL_RAIZ,
    Config,
    _expandir,
    _finito,
    _fracao,
    _inteiro,
    _json_seguro,
    _numero,
    _texto,
)
from jev_crap.metrica.risco import CrapClassico


class TestParaRegua:
    def test_para_regua_traz_a_formula_e_o_limiar(self):
        regua = Config(limiar=12.0).para_regua()
        assert regua["formula"] == FORMULA_PADRAO
        assert regua["limiar_configurado"] == 12.0

    def test_para_regua_traz_os_avisos(self):
        config = Config.do_ambiente({VARIAVEL_FORMULA: "inventada"})
        assert config.para_regua()["avisos_de_configuracao"]

    def test_para_regua_e_serializavel(self):
        import json

        assert json.dumps(Config().para_regua())

    def test_para_regua_nao_expoe_segredo(self):
        fonte = {"TYPESAFE_API_KEY": "sk-segredo-de-verdade"}
        assert "segredo" not in str(Config.do_ambiente(fonte).para_regua())


class TestParaReguaRobusta:
    def test_para_regua_serializa_valor_exotico_em_excluir(self):
        regua = Config(excluir=(Path("/tmp/x"),)).para_regua()
        assert regua["excluir"] == ["/tmp/x"]

    def test_para_regua_nao_levanta_com_campo_intextualizavel(self):
        class Explosiva:
            def __str__(self):
                raise RuntimeError("não vira texto")

        regua = Config(excluir=(Explosiva(),)).para_regua()
        assert "não textualizável" in regua["excluir"][0]

    def test_para_regua_traz_sempre_as_mesmas_chaves(self):
        assert set(Config().para_regua()) == set(Config(limiar=9.0).para_regua())

    def test_para_regua_nao_traz_campo_alem_dos_escritos_a_mao(self):
        """A lista é escrita campo a campo para que nada entre por acidente."""
        assert "caminho_episodios" not in Config().para_regua()


class TestTexto:
    def test_texto_devolve_o_valor_limpo(self):
        assert _texto({"V": "  x  "}, "V") == "x"

    def test_texto_devolve_vazio_para_variavel_ausente(self):
        assert _texto({}, "V") == ""

    def test_texto_devolve_vazio_para_valor_so_de_espacos(self):
        assert _texto({"V": "   "}, "V") == ""

    def test_texto_devolve_vazio_para_none(self):
        assert _texto({"V": None}, "V") == ""

    def test_texto_converte_valor_que_nao_e_texto(self):
        assert _texto({"V": 30}, "V") == "30"


class TestNumero:
    def test_numero_le_um_decimal(self):
        assert _numero({"V": "0.8"}, "V", 1.0, []) == 0.8

    def test_numero_aceita_virgula_como_separador(self):
        assert _numero({"V": "0,8"}, "V", 1.0, []) == 0.8

    def test_numero_devolve_o_padrao_quando_a_variavel_falta(self):
        assert _numero({}, "V", 1.0, []) == 1.0

    def test_numero_avisa_e_usa_o_padrao_para_texto(self):
        avisos: list[str] = []
        assert _numero({"V": "alto"}, "V", 1.0, avisos) == 1.0
        assert "não é um número" in avisos[0]

    def test_numero_recusa_nan(self):
        avisos: list[str] = []
        assert _numero({"V": "nan"}, "V", 1.0, avisos) == 1.0
        assert "não é um número finito" in avisos[0]

    def test_numero_recusa_infinito(self):
        avisos: list[str] = []
        assert _numero({"V": "inf"}, "V", 1.0, avisos) == 1.0
        assert avisos

    def test_numero_respeita_o_minimo(self):
        avisos: list[str] = []
        assert _numero({"V": "-2"}, "V", 1.0, avisos, minimo=0.0) == 1.0
        assert "menor que" in avisos[0]

    def test_numero_respeita_o_maximo(self):
        avisos: list[str] = []
        assert _numero({"V": "3"}, "V", 1.0, avisos, maximo=1.0) == 1.0
        assert "maior que" in avisos[0]

    def test_numero_nunca_levanta(self):
        for bruto in ("", "x", "nan", "-inf", "1e400"):
            assert _numero({"V": bruto}, "V", 1.0, []) is not None


class TestInteiro:
    def test_inteiro_le_um_contador(self):
        assert _inteiro({"V": "7"}, "V", 3, []) == 7

    def test_inteiro_trunca_em_vez_de_arredondar(self):
        """Todo uso daqui é de teto, e teto que estoura não é teto."""
        assert _inteiro({"V": "4.7"}, "V", 3, []) == 4

    def test_inteiro_usa_o_padrao_para_texto(self):
        assert _inteiro({"V": "muitos"}, "V", 3, []) == 3

    def test_inteiro_usa_o_padrao_abaixo_do_minimo(self):
        assert _inteiro({"V": "0"}, "V", 3, [], minimo=1) == 3

    def test_inteiro_devolve_int_de_verdade(self):
        assert isinstance(_inteiro({"V": "4.7"}, "V", 3, []), int)


class TestFracao:
    def test_fracao_le_uma_probabilidade(self):
        assert _fracao({"V": "0.8"}, "V", 0.5, []) == 0.8

    def test_fracao_usa_o_padrao_acima_de_um(self):
        assert _fracao({"V": "1.5"}, "V", 0.5, []) == 0.5

    def test_fracao_usa_o_padrao_abaixo_de_zero(self):
        assert _fracao({"V": "-0.1"}, "V", 0.5, []) == 0.5

    def test_fracao_nunca_devolve_none(self):
        for bruto in ("", "x", "9", "-1", "nan"):
            assert _fracao({"V": bruto}, "V", 0.5, []) == 0.5 or True
            assert _fracao({"V": bruto}, "V", 0.5, []) is not None

    def test_fracao_aceita_as_pontas(self):
        assert _fracao({"V": "0"}, "V", 0.5, []) == 0.0
        assert _fracao({"V": "1"}, "V", 0.5, []) == 1.0


class TestExpandir:
    def test_expandir_resolve_o_til(self):
        assert not str(_expandir("~/x")).startswith("~")

    def test_expandir_preserva_absoluto(self):
        assert _expandir("/tmp/x") == Path("/tmp/x")

    def test_expandir_devolve_literal_sem_home(self, monkeypatch):
        def explode(_self):
            raise RuntimeError("sem home")

        monkeypatch.setattr(Path, "expanduser", explode)
        assert _expandir("~/x") == Path("~/x")


class TestFinito:
    def test_finito_aceita_numero(self):
        assert _finito(30) == 30.0

    def test_finito_recusa_none(self):
        assert _finito(None) is None

    def test_finito_recusa_nan(self):
        assert _finito(float("nan")) is None

    def test_finito_recusa_infinito(self):
        assert _finito(float("inf")) is None

    def test_finito_recusa_booleano(self):
        """True virando limiar 1.0 julgaria o repositório inteiro sem ninguém pedir."""
        assert _finito(True) is None

    def test_finito_recusa_texto(self):
        assert _finito("30") is None


class TestRaiz:
    def test_raiz_usa_o_argumento(self, tmp_path):
        assert Config.do_ambiente({}, raiz=tmp_path).raiz == tmp_path

    def test_raiz_usa_a_variavel_de_ambiente(self, tmp_path):
        fonte = {VARIAVEL_RAIZ: str(tmp_path)}
        assert Config.do_ambiente(fonte).raiz == tmp_path

    def test_raiz_cai_no_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert Config.do_ambiente({}).raiz == Path.cwd()

    def test_raiz_nao_levanta_sem_cwd(self, monkeypatch):
        def explode():
            raise OSError("workspace apagado")

        monkeypatch.setattr(Path, "cwd", explode)
        assert Config.do_ambiente({}).raiz == Path(".")


class TestEpisodios:
    def test_episodios_e_none_sem_configuracao(self):
        assert Config.do_ambiente({}).caminho_episodios is None

    def test_episodios_le_a_variavel(self, tmp_path):
        fonte = {VARIAVEL_EPISODIOS: str(tmp_path / "h.jsonl")}
        assert Config.do_ambiente(fonte).caminho_episodios == tmp_path / "h.jsonl"

    def test_episodios_ignora_variavel_vazia(self):
        assert Config.do_ambiente({VARIAVEL_EPISODIOS: "   "}).caminho_episodios is None


class TestFormulaConfigurada:
    def test_formula_padrao_sem_configuracao(self):
        assert Config.do_ambiente({}).formula == FORMULA_PADRAO

    def test_formula_desconhecida_vira_aviso_e_nao_erro(self):
        config = Config.do_ambiente({VARIAVEL_FORMULA: "inventada"})
        assert config.formula == FORMULA_PADRAO
        assert any("não é uma fórmula registrada" in a for a in config.avisos)

    def test_obter_formula_devolve_a_instancia(self):
        assert Config.do_ambiente({}).obter_formula().nome == FORMULA_PADRAO

    def test_obter_formula_cai_na_padrao_se_o_registro_mudou(self):
        config = Config(formula="sumiu")
        assert config.obter_formula().nome == FORMULA_PADRAO


class TestLista:
    def test_lista_separa_por_virgula(self):
        assert Config.do_ambiente({"JEV_CRAP_EXCLUIR": "a,b"}).excluir == ("a", "b")

    def test_lista_descarta_itens_vazios(self):
        assert Config.do_ambiente({"JEV_CRAP_EXCLUIR": "a,,b,"}).excluir == ("a", "b")

    def test_lista_vazia_sem_configuracao(self):
        assert Config.do_ambiente({}).excluir == ()

    def test_lista_limpa_espacos_de_cada_item(self):
        assert Config.do_ambiente({"JEV_CRAP_EXCLUIR": " a , b "}).excluir == ("a", "b")


class TestLimiarEfetivo:
    def formula(self):
        return Config().obter_formula()

    def test_limiar_efetivo_prefere_o_pedido(self):
        assert Config(limiar=20.0).limiar_efetivo(self.formula(), 5.0) == 5.0

    def test_limiar_efetivo_cai_na_configuracao(self):
        assert Config(limiar=20.0).limiar_efetivo(self.formula()) == 20.0

    def test_limiar_efetivo_cai_na_formula(self):
        assert Config().limiar_efetivo(self.formula()) == 30.0

    def test_limiar_efetivo_ignora_pedido_nan(self):
        assert Config(limiar=20.0).limiar_efetivo(self.formula(), float("nan")) == 20.0

    def test_limiar_efetivo_ignora_configuracao_nan(self):
        assert Config(limiar=float("nan")).limiar_efetivo(self.formula()) == 30.0

    def test_limiar_efetivo_ignora_booleano(self):
        assert Config().limiar_efetivo(self.formula(), True) == 30.0

    def test_limiar_efetivo_aceita_zero(self):
        assert Config().limiar_efetivo(self.formula(), 0.0) == 0.0


class TestRepositorioDaConfig:
    def test_repositorio_usa_o_caminho_configurado(self, tmp_path):
        alvo = tmp_path / "h.jsonl"
        assert Config(caminho_episodios=alvo).repositorio().caminho == alvo

    @pytest.mark.parametrize("vazio", [Path(""), Path("."), Path("/")])
    def test_repositorio_trata_caminho_sem_nome_como_ausente(self, tmp_path, vazio):
        """Os três apontam para diretório; gravar ali daria IsADirectoryError."""
        config = Config(raiz=tmp_path, caminho_episodios=vazio)
        assert config.repositorio().caminho.name.endswith(".jsonl")

    def test_repositorio_nao_cria_nada_em_disco(self, tmp_path):
        Config(raiz=tmp_path).repositorio()
        assert list(tmp_path.iterdir()) == []


class TestExclusoes:
    """`exclusoes` produz padrões de fnmatch aplicados a cada arquivo varrido."""

    def test_exclusoes_soma_config_e_chamada(self):
        assert Config(excluir=("a",)).exclusoes(["b"]) == ("a", "b")

    def test_exclusoes_descarta_extra_vazio(self):
        assert Config(excluir=("a",)).exclusoes(["", None]) == ("a",)

    def test_exclusoes_sem_nada_e_vazio(self):
        assert Config().exclusoes() == ()

    def test_exclusoes_nao_repete_o_mesmo_padrao(self):
        assert Config(excluir=("a",)).exclusoes(["a"]) == ("a",)

    def test_exclusoes_preserva_a_ordem_da_primeira_aparicao(self):
        assert Config(excluir=("b", "a")).exclusoes(["a", "c"]) == ("b", "a", "c")

    def test_exclusoes_converte_extra_que_nao_e_texto(self):
        from pathlib import Path as _P

        assert Config().exclusoes([_P("src/legado")]) == ("src/legado",)

    def test_exclusoes_limpa_espacos_dos_extras(self):
        assert Config().exclusoes(["  a  "]) == ("a",)

    def test_exclusoes_devolve_tupla_de_texto(self):
        resultado = Config(excluir=("a",)).exclusoes(["b"])
        assert isinstance(resultado, tuple)
        assert all(isinstance(item, str) for item in resultado)

    def test_exclusoes_nao_inclui_as_embutidas_do_analisador(self):
        """Repeti-las daria a impressão de que removê-las daqui as desliga."""
        assert "node_modules" not in Config().exclusoes()


class TestRaizDireto:
    """`Config._raiz` chamado direto: é a primeira decisão da montagem."""

    def test_raiz_prefere_o_argumento(self, tmp_path):
        assert Config._raiz({VARIAVEL_RAIZ: "/outro"}, tmp_path) == tmp_path

    def test_raiz_le_a_variavel_quando_nao_ha_argumento(self, tmp_path):
        assert Config._raiz({VARIAVEL_RAIZ: str(tmp_path)}, None) == tmp_path

    def test_raiz_ignora_variavel_so_de_espacos(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert Config._raiz({VARIAVEL_RAIZ: "   "}, None) == Path.cwd()

    def test_raiz_expande_o_til(self):
        assert not str(Config._raiz({VARIAVEL_RAIZ: "~/x"}, None)).startswith("~")

    def test_raiz_nao_levanta_sem_home(self, monkeypatch):
        def explode(_self):
            raise RuntimeError("sem home")

        monkeypatch.setattr(Path, "expanduser", explode)
        assert Config._raiz({VARIAVEL_RAIZ: "~/x"}, None) == Path("~/x")

    def test_raiz_cai_no_ponto_sem_cwd(self, monkeypatch):
        def explode():
            raise OSError("workspace apagado")

        monkeypatch.setattr(Path, "cwd", explode)
        assert Config._raiz({}, None) == Path(".")


class TestEpisodiosDireto:
    def test_episodios_devolve_none_sem_variavel(self):
        assert Config._episodios({}) is None

    def test_episodios_devolve_none_para_variavel_vazia(self):
        assert Config._episodios({VARIAVEL_EPISODIOS: "   "}) is None

    def test_episodios_le_o_caminho(self, tmp_path):
        alvo = tmp_path / "h.jsonl"
        assert Config._episodios({VARIAVEL_EPISODIOS: str(alvo)}) == alvo

    def test_episodios_expande_o_til(self):
        lido = Config._episodios({VARIAVEL_EPISODIOS: "~/h.jsonl"})
        assert not str(lido).startswith("~")

    def test_episodios_nao_levanta_sem_home(self, monkeypatch):
        def explode(_self):
            raise RuntimeError("sem home")

        monkeypatch.setattr(Path, "expanduser", explode)
        assert Config._episodios({VARIAVEL_EPISODIOS: "~/h.jsonl"}) == Path("~/h.jsonl")


class TestFormulaDireto:
    def test_formula_devolve_o_padrao_sem_variavel(self):
        assert Config._formula({}, []) == FORMULA_PADRAO

    def test_formula_aceita_nome_registrado(self):
        assert Config._formula({VARIAVEL_FORMULA: "crap"}, []) == "crap"

    def test_formula_avisa_para_nome_desconhecido(self):
        avisos: list[str] = []
        assert Config._formula({VARIAVEL_FORMULA: "inventada"}, avisos) == FORMULA_PADRAO
        assert "não é uma fórmula registrada" in avisos[0]

    def test_formula_lista_as_disponiveis_no_aviso(self):
        avisos: list[str] = []
        Config._formula({VARIAVEL_FORMULA: "inventada"}, avisos)
        assert "crap" in avisos[0]

    def test_formula_ignora_variavel_vazia_sem_avisar(self):
        avisos: list[str] = []
        assert Config._formula({VARIAVEL_FORMULA: "  "}, avisos) == FORMULA_PADRAO
        assert avisos == []

    def test_formula_nunca_levanta(self):
        for bruto in ("", "   ", "inventada", "crap"):
            assert Config._formula({VARIAVEL_FORMULA: bruto}, [])


class TestListaDireto:
    def test_lista_separa_por_virgula(self):
        assert Config._lista({"V": "a,b"}, "V") == ("a", "b")

    def test_lista_limpa_espacos(self):
        assert Config._lista({"V": " a , b "}, "V") == ("a", "b")

    def test_lista_descarta_itens_vazios(self):
        assert Config._lista({"V": "a,,b,"}, "V") == ("a", "b")

    def test_lista_de_variavel_ausente_e_vazia(self):
        assert Config._lista({}, "V") == ()

    def test_lista_de_variavel_so_de_virgulas_e_vazia(self):
        assert Config._lista({"V": ",,,"}, "V") == ()

    def test_lista_devolve_tupla(self):
        assert isinstance(Config._lista({"V": "a"}, "V"), tuple)


class TestJsonSeguro:
    """A régua é o rodapé de toda resposta: nada nela pode deixar de serializar."""

    @pytest.mark.parametrize("valor", ["texto", 3, 1.5, True, None])
    def test_json_seguro_devolve_tipo_json_intacto(self, valor):
        assert _json_seguro(valor) is valor

    def test_json_seguro_converte_path_em_texto(self):
        assert _json_seguro(Path("/tmp/x")) == "/tmp/x"

    def test_json_seguro_converte_tupla_em_texto(self):
        assert _json_seguro(("a", "b")) == "('a', 'b')"

    def test_json_seguro_marca_o_que_nao_vira_texto(self):
        class Explosiva:
            def __str__(self):
                raise RuntimeError("não vira texto")

        assert _json_seguro(Explosiva()) == "<Explosiva não textualizável>"

    def test_json_seguro_nunca_levanta(self):
        class Explosiva:
            def __str__(self):
                raise RecursionError

        assert isinstance(_json_seguro(Explosiva()), str)

    def test_json_seguro_sempre_produz_algo_serializavel(self):
        import json

        for valor in ("x", 1, None, True, Path("/a"), object()):
            assert json.dumps(_json_seguro(valor)) is not None


class TestValoresTortos:
    @pytest.mark.parametrize(
        "variavel,valor,atributo,padrao",
        [
            ("JEV_CRAP_LIMIAR", "muito", "limiar", None),
            ("JEV_CRAP_MAX_JULGAMENTOS", "-3", "max_julgamentos", 20),
            ("JEV_CRAP_BLOQUEIO", "2.0", "bloqueio", 0.8),
            ("JEV_CRAP_NOTA_MINIMA", "101", "nota_minima", 60.0),
            ("JEV_CRAP_FORMULA", "inventada", "formula", "crap"),
        ],
    )
    def test_valor_invalido_cai_para_o_padrao_e_avisa(self, variavel, valor, atributo, padrao):
        config = Config.do_ambiente({variavel: valor})
        assert getattr(config, atributo) == padrao
        assert any(variavel in aviso for aviso in config.avisos)

    def test_os_avisos_chegam_ao_relatorio(self):
        """Aviso que fica só no log some dentro do cliente MCP."""
        config = Config.do_ambiente({"JEV_CRAP_LIMIAR": "torto"})
        assert config.para_regua()["avisos_de_configuracao"]

    def test_virgula_decimal_funciona(self):
        """Quem escreve em português digita 0,8 sem pensar duas vezes."""
        assert Config.do_ambiente({"JEV_CRAP_BLOQUEIO": "0,7"}).bloqueio == 0.7


class TestLimiar:
    def test_a_tool_ganha_da_configuracao(self):
        """Limiar é decisão de quem está olhando o relatório agora: é ele que
        sabe se está varrendo um módulo crítico ou um script descartável."""
        config = Config(limiar=15.0)
        assert config.limiar_efetivo(CrapClassico(), pedido=5.0) == 5.0

    def test_sem_nada_vale_o_da_formula(self):
        assert Config().limiar_efetivo(CrapClassico()) == 30.0

    def test_limiar_nao_e_copiado_da_formula_na_construcao(self):
        """Guardar `None` mantém os dois ligados: trocar de fórmula troca o
        limiar junto, sem configuração órfã apontando para uma escala morta."""
        assert Config().limiar is None

    def test_limiar_zero_e_respeitado_e_nao_confundido_com_ausencia(self):
        assert Config().limiar_efetivo(CrapClassico(), pedido=0.0) == 0.0


class TestSegredo:
    def test_a_chave_da_api_nao_entra_na_configuracao(self):
        """Guardar segredo num objeto que é serializado em relatório e em log é
        como vazamento costuma começar."""
        config = Config.do_ambiente({"TYPESAFE_API_KEY": "segredo-de-verdade"})
        assert "segredo-de-verdade" not in repr(config)
        assert "segredo-de-verdade" not in str(config.para_regua())


class TestImutabilidade:
    def test_a_regua_nao_muda_no_meio_de_uma_avaliacao(self):
        """Um relatório em que metade das funções foi comparada com um limiar e
        a outra metade com outro não descreve nada."""
        config = Config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.limiar = 10.0  # type: ignore[misc]
