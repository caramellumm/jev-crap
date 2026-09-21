"""Testes de `jev_crap.situacoes`.

O que se verifica aqui é o contrato que o servidor MCP depende: toda situação
carrega as três partes, e nenhum detalhe extra consegue apagá-las nem quebrar a
serialização da resposta.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from jev_crap.situacoes import (
    PARTES_OBRIGATORIAS,
    PROFUNDIDADE_MAXIMA,
    SituacaoConhecida,
    _detalhe_serializavel,
    _parte_valida,
    _texto_seguro,
)


class Explosiva:
    """Objeto cujo `__str__` levanta — o caso que `_texto_seguro` existe para cobrir."""

    def __str__(self) -> str:
        raise RuntimeError("não dá para virar texto")


class TestParteValida:
    def test_parte_valida_devolve_o_texto_sem_espacos_nas_pontas(self):
        assert _parte_valida("situacao", "  cobertura_inexistente  ") == "cobertura_inexistente"

    def test_parte_valida_preserva_texto_ja_limpo(self):
        assert _parte_valida("explicacao", "não há relatório") == "não há relatório"

    def test_parte_valida_recusa_texto_vazio(self):
        with pytest.raises(ValueError, match="como_resolver.*não pode ser vazio"):
            _parte_valida("como_resolver", "")

    def test_parte_valida_recusa_texto_só_de_espacos(self):
        with pytest.raises(ValueError, match="situacao.*não pode ser vazio"):
            _parte_valida("situacao", "   \n\t ")

    def test_parte_valida_recusa_valor_que_nao_e_texto(self):
        with pytest.raises(ValueError, match="precisa ser texto, veio int"):
            _parte_valida("explicacao", 42)

    def test_parte_valida_recusa_none(self):
        with pytest.raises(ValueError, match="precisa ser texto, veio NoneType"):
            _parte_valida("situacao", None)


class TestDetalheSerializavel:
    @pytest.mark.parametrize("valor", ["texto", 3, 1.5, True, None])
    def test_detalhe_serializavel_devolve_tipos_json_intactos(self, valor):
        assert _detalhe_serializavel(valor) is valor

    def test_detalhe_serializavel_converte_path_em_texto(self):
        assert _detalhe_serializavel(Path("/tmp/coverage.xml")) == "/tmp/coverage.xml"

    def test_detalhe_serializavel_converte_lista_item_a_item(self):
        assert _detalhe_serializavel([Path("/a"), 2]) == ["/a", 2]

    def test_detalhe_serializavel_converte_tupla_em_lista(self):
        assert _detalhe_serializavel((Path("/a"), "b")) == ["/a", "b"]

    def test_detalhe_serializavel_converte_chave_e_valor_de_dicionario(self):
        assert _detalhe_serializavel({1: Path("/a")}) == {"1": "/a"}

    def test_detalhe_serializavel_para_de_descer_na_profundidade_maxima(self):
        ciclica: list = []
        ciclica.append(ciclica)
        resultado = _detalhe_serializavel(ciclica)
        for _ in range(PROFUNDIDADE_MAXIMA):
            assert isinstance(resultado, list)
            resultado = resultado[0]
        assert isinstance(resultado, str)

    def test_detalhe_serializavel_nao_levanta_com_str_que_explode(self):
        assert _detalhe_serializavel(Explosiva()) == "<Explosiva não textualizável>"


class TestTextoSeguro:
    def test_texto_seguro_usa_str_quando_funciona(self):
        assert _texto_seguro(Path("/tmp/a")) == "/tmp/a"

    def test_texto_seguro_devolve_marcador_quando_str_levanta(self):
        assert _texto_seguro(Explosiva()) == "<Explosiva não textualizável>"

    def test_texto_seguro_nunca_propaga_excecao(self):
        assert isinstance(_texto_seguro(Explosiva()), str)


class TestSituacaoConhecida:
    def test_situacao_conhecida_guarda_as_tres_partes(self):
        erro = SituacaoConhecida("cobertura_inexistente", "não há relatório", "rode pytest --cov")
        assert erro.situacao == "cobertura_inexistente"
        assert erro.explicacao == "não há relatório"
        assert erro.como_resolver == "rode pytest --cov"

    def test_situacao_conhecida_usa_situacao_e_explicacao_como_mensagem(self):
        erro = SituacaoConhecida("trecho_vazio", "não veio código", "passe o texto")
        assert str(erro) == "trecho_vazio: não veio código"

    def test_situacao_conhecida_exige_como_resolver(self):
        with pytest.raises(ValueError, match="como_resolver.*não pode ser vazio"):
            SituacaoConhecida("x", "y", "")

    def test_situacao_conhecida_exige_explicacao(self):
        with pytest.raises(ValueError, match="explicacao.*não pode ser vazio"):
            SituacaoConhecida("x", "  ", "z")

    def test_situacao_conhecida_continua_sendo_exception(self):
        with pytest.raises(SituacaoConhecida):
            raise SituacaoConhecida("x", "y", "z")

    def test_situacao_conhecida_serializa_detalhe_nao_json(self):
        erro = SituacaoConhecida("x", "y", "z", caminho=Path("/tmp/lcov.info"))
        assert erro.detalhes == {"caminho": "/tmp/lcov.info"}

    def test_situacao_conhecida_sem_detalhes_guarda_dicionario_vazio(self):
        assert SituacaoConhecida("x", "y", "z").detalhes == {}

    def test_partes_obrigatorias_sao_as_tres_chaves_fixas(self):
        assert PARTES_OBRIGATORIAS == ("situacao", "explicacao", "como_resolver")


class TestContratoDoConstrutor:
    """O contrato de `SituacaoConhecida.__init__`, conferido diretamente.

    Vale um teste próprio porque o construtor é o único lugar que garante as
    três partes: se a assinatura mudar e `como_resolver` virar opcional, todo
    o resto continua passando e a garantia some sem aviso.
    """

    def test_init_recebe_as_tres_partes_como_posicionais_obrigatorias(self):
        assinatura = inspect.signature(SituacaoConhecida.__init__)
        parametros = list(assinatura.parameters)
        assert parametros == ["self", "situacao", "explicacao", "como_resolver", "detalhes"]

    def test_init_nao_da_valor_padrao_a_nenhuma_das_tres_partes(self):
        assinatura = inspect.signature(SituacaoConhecida.__init__)
        for nome in PARTES_OBRIGATORIAS:
            assert assinatura.parameters[nome].default is inspect.Parameter.empty

    def test_init_recolhe_o_resto_em_detalhes_como_kwargs(self):
        assinatura = inspect.signature(SituacaoConhecida.__init__)
        detalhes = assinatura.parameters["detalhes"]
        assert detalhes.kind is inspect.Parameter.VAR_KEYWORD

    def test_init_chamado_com_menos_de_tres_partes_levanta_type_error(self):
        with pytest.raises(TypeError):
            SituacaoConhecida("so_a_situacao")  # type: ignore[call-arg]

    def test_init_normaliza_as_tres_partes_tirando_espacos(self):
        erro = SituacaoConhecida("  a  ", "  b  ", "  c  ")
        assert (erro.situacao, erro.explicacao, erro.como_resolver) == ("a", "b", "c")

    def test_init_monta_a_mensagem_da_exception_base(self):
        erro = SituacaoConhecida("a", "b", "c")
        assert erro.args == ("a: b",)


class TestSubclasseSemSuperInit:
    """Formatar o erro não pode falhar quando o construtor não rodou.

    É o modo de falha mais cruel da classe: a exceção que deveria explicar um
    problema vira um `AttributeError` sem explicação nenhuma, três camadas
    acima de onde nasceu.
    """

    class Incompleta(SituacaoConhecida):
        def __init__(self) -> None:  # noqa: D107 - de propósito não chama super()
            pass

    def test_para_corpo_de_erro_nao_levanta_sem_super_init(self):
        corpo = self.Incompleta().para_corpo_de_erro()
        assert set(corpo) == {"situacao", "explicacao", "como_resolver"}

    def test_para_corpo_de_erro_marca_a_parte_ausente(self):
        assert "ausente" in self.Incompleta().para_corpo_de_erro()["como_resolver"]

    def test_para_texto_nao_levanta_sem_super_init(self):
        assert "Como resolver:" in self.Incompleta().para_texto()


class TestParte:
    def test_parte_devolve_o_valor_quando_ele_existe(self):
        erro = SituacaoConhecida("cobertura_ilegivel", "y", "z")
        assert erro._parte("situacao") == "cobertura_ilegivel"

    def test_parte_devolve_marcador_quando_o_atributo_nao_existe(self):
        erro = SituacaoConhecida("x", "y", "z")
        del erro.explicacao
        assert erro._parte("explicacao").startswith("<explicacao ausente")

    def test_parte_devolve_marcador_quando_o_valor_nao_e_texto(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.situacao = 7  # type: ignore[assignment]
        assert erro._parte("situacao").startswith("<situacao ausente")

    def test_parte_devolve_marcador_quando_o_valor_e_so_espaco(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.como_resolver = "   "
        assert erro._parte("como_resolver").startswith("<como_resolver ausente")


class TestDetalhesSeguros:
    def test_detalhes_seguros_devolve_os_detalhes_da_construcao(self):
        erro = SituacaoConhecida("x", "y", "z", caminho="cov.xml")
        assert erro._detalhes_seguros() == {"caminho": "cov.xml"}

    def test_detalhes_seguros_devolve_vazio_quando_detalhes_nao_e_dicionario(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = ["não", "é", "dicionário"]  # type: ignore[assignment]
        assert erro._detalhes_seguros() == {}

    def test_detalhes_seguros_descarta_chave_que_apagaria_parte_obrigatoria(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = {"como_resolver": "sequestrado", "caminho": "cov.xml"}
        assert erro._detalhes_seguros() == {"caminho": "cov.xml"}


class TestParaCorpoDeErro:
    def test_para_corpo_de_erro_traz_as_tres_partes(self):
        erro = SituacaoConhecida("cobertura_ilegivel", "formato desconhecido", "gere com --cov")
        assert erro.para_corpo_de_erro() == {
            "situacao": "cobertura_ilegivel",
            "explicacao": "formato desconhecido",
            "como_resolver": "gere com --cov",
        }

    def test_para_corpo_de_erro_acrescenta_os_detalhes(self):
        erro = SituacaoConhecida("x", "y", "z", caminho="cov.xml", linhas=3)
        assert erro.para_corpo_de_erro() == {
            "situacao": "x",
            "explicacao": "y",
            "como_resolver": "z",
            "caminho": "cov.xml",
            "linhas": 3,
        }

    def test_para_corpo_de_erro_e_serializavel_em_json(self):
        import json

        erro = SituacaoConhecida("x", "y", "z", caminho=Path("/tmp/a"))
        assert json.loads(json.dumps(erro.para_corpo_de_erro()))["caminho"] == "/tmp/a"

    def test_para_corpo_de_erro_mantem_as_partes_mesmo_com_detalhes_hostis(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = {"como_resolver": "sequestrado"}
        assert erro.para_corpo_de_erro()["como_resolver"] == "z"

    def test_para_corpo_de_erro_marca_detalhes_ilegiveis_em_vez_de_levantar(self):
        class DicionarioHostil(dict):
            def items(self):
                raise RuntimeError("iteração quebrada")

        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = DicionarioHostil()
        corpo = erro.para_corpo_de_erro()
        assert corpo["detalhes_ilegiveis"] is True
        assert corpo["como_resolver"] == "z"

    def test_para_corpo_de_erro_sempre_traz_as_tres_chaves(self):
        erro = SituacaoConhecida("x", "y", "z", situacao_parecida="nao_substitui")
        assert PARTES_OBRIGATORIAS[0] in erro.para_corpo_de_erro()
        assert set(PARTES_OBRIGATORIAS) <= set(erro.para_corpo_de_erro())


class TestParaTexto:
    def test_para_texto_sem_detalhes_tem_duas_linhas(self):
        erro = SituacaoConhecida("trecho_vazio", "não veio código", "passe o texto")
        assert erro.para_texto() == "trecho_vazio: não veio código\nComo resolver: passe o texto"

    def test_para_texto_acrescenta_uma_linha_por_detalhe(self):
        erro = SituacaoConhecida("x", "y", "z", caminho="cov.xml")
        assert erro.para_texto().splitlines() == [
            "x: y",
            "Como resolver: z",
            "caminho: cov.xml",
        ]

    def test_para_texto_nunca_termina_em_linha_vazia(self):
        erro = SituacaoConhecida("x", "y", "z")
        assert not erro.para_texto().endswith("\n")

    def test_para_texto_nao_levanta_com_detalhe_que_explode_ao_virar_texto(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = {"objeto": Explosiva()}
        assert "não textualizável" in erro.para_texto()

    def test_para_texto_ignora_chave_que_apagaria_parte_obrigatoria(self):
        erro = SituacaoConhecida("x", "y", "z")
        erro.detalhes = {"situacao": "sequestrado"}
        assert erro.para_texto() == "x: y\nComo resolver: z"
