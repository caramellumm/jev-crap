"""A configuração: o que o ambiente pode mudar, e o que variável torta faz.

A regra que o módulo inteiro sustenta: **variável mal escrita não derruba o
servidor.** Um servidor que recusa iniciar por causa de uma variável torta deixa
a pessoa sem ferramenta e sem mensagem, porque a saída de erro de um processo
stdio some dentro do cliente MCP.
"""

from __future__ import annotations

import dataclasses

import pytest

from jev_crap.config import Config
from jev_crap.metrica.risco import CrapClassico


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


class TestExclusoes:
    def test_soma_as_da_configuracao_com_as_da_chamada(self):
        config = Config.do_ambiente({"JEV_CRAP_EXCLUIR": "legado, gerado"})
        assert config.exclusoes(("*test*",)) == ("legado", "gerado", "*test*")

    def test_lista_vazia_nao_vira_padrao_vazio(self):
        assert Config.do_ambiente({"JEV_CRAP_EXCLUIR": " , , "}).excluir == ()


class TestImutabilidade:
    def test_a_regua_nao_muda_no_meio_de_uma_avaliacao(self):
        """Um relatório em que metade das funções foi comparada com um limiar e
        a outra metade com outro não descreve nada."""
        config = Config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.limiar = 10.0  # type: ignore[misc]
