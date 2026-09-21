"""Os ajudantes do laço de aprendizado.

A regra do módulo é que nada se aplica sozinho: `agregar` descreve e `propor`
sugere. Os testes daqui cobrem o que sustenta essa regra — série sem dado não
vira zero, configuração torta não derruba a consulta, e proposta sem evidência
não existe.
"""

from __future__ import annotations

import pytest

from jev_crap.aprendizado.episodio import Episodio
from jev_crap.aprendizado.laco import (
    CONFIG_PADRAO,
    Propostas,
    _baixar_limiar,
    _estatisticas,
    _fracao_de_config,
    _inteiro_de_config,
    _limiar_vigente,
    _numero,
    _proposta,
    _remover_dimensoes,
    _subir_limiar,
    _valor_da_nota,
)


def ep(**mudancas) -> Episodio:
    base = dict(
        id="e", em="2026-01-01T00:00:00Z", arquivo="src/a.py", funcao="f",
        risco=40.0, formula="crap", limiar_vigente=30.0, complexidade=8,
        cobertura_linha=0.5, cobertura_branch=0.4,
    )
    return Episodio(**{**base, **mudancas})


class TestPropostas:
    def test_propostas_sem_itens_e_uma_lista_vazia(self):
        assert list(Propostas()) == []

    def test_propostas_guarda_o_motivo_de_estar_vazia(self):
        assert Propostas(motivo="histórico curto").motivo == "histórico curto"

    def test_propostas_continua_sendo_lista(self):
        assert len(Propostas([{"tipo": "x"}])) == 1

    def test_propostas_recusa_o_que_nao_e_lista(self):
        with pytest.raises(TypeError, match="lista de propostas"):
            Propostas({"tipo": "x"})

    def test_propostas_recusa_gerador_que_se_esvaziaria(self):
        with pytest.raises(TypeError, match="veio generator"):
            Propostas(x for x in [1])

    def test_propostas_recusa_motivo_que_nao_e_texto(self):
        with pytest.raises(TypeError, match="motivo precisa ser texto"):
            Propostas([], motivo=7)


class TestNumero:
    def test_numero_converte_inteiro(self):
        assert _numero(3) == 3.0

    def test_numero_preserva_float(self):
        assert _numero(0.25) == 0.25

    def test_numero_recusa_booleano(self):
        assert _numero(True) is None

    def test_numero_recusa_texto(self):
        assert _numero("0.5") is None

    def test_numero_recusa_none(self):
        assert _numero(None) is None

    def test_numero_recusa_nan(self):
        assert _numero(float("nan")) is None

    def test_numero_recusa_infinito(self):
        assert _numero(float("inf")) is None


class TestValorDaNota:
    def test_valor_da_nota_aceita_numero_solto(self):
        assert _valor_da_nota(0.7) == 0.7

    def test_valor_da_nota_le_normalizado_de_dicionario(self):
        assert _valor_da_nota({"normalizado": 0.4, "confianca": 0.9}) == 0.4

    def test_valor_da_nota_le_atributo_de_objeto(self):
        class Resposta:
            normalizado = 0.8

        assert _valor_da_nota(Resposta()) == 0.8

    def test_valor_da_nota_cai_em_qualquer_numero_do_dicionario(self):
        assert _valor_da_nota({"coisa": 0.3}) == 0.3

    def test_valor_da_nota_devolve_none_sem_numero_nenhum(self):
        assert _valor_da_nota({"texto": "alto"}) is None

    def test_valor_da_nota_ignora_nan_dentro_do_dicionario(self):
        assert _valor_da_nota({"normalizado": float("nan")}) is None


class TestEstatisticas:
    def test_estatisticas_de_uma_serie(self):
        resumo = _estatisticas([0.2, 0.4, 0.6])
        assert resumo["n"] == 3
        assert resumo["media"] == pytest.approx(0.4)
        assert resumo["amplitude"] == pytest.approx(0.4)

    def test_estatisticas_de_um_valor_tem_desvio_zero(self):
        assert _estatisticas([0.5])["desvio"] == 0.0

    def test_estatisticas_de_lista_vazia_nao_levanta(self):
        assert _estatisticas([])["n"] == 0

    def test_estatisticas_de_lista_vazia_usa_none_e_nao_zero(self):
        vazio = _estatisticas([])
        assert vazio["media"] is None
        assert vazio["amplitude"] is None

    def test_estatisticas_conta_valores_distintos(self):
        assert _estatisticas([0.5, 0.5, 0.5])["distintos"] == 1


class TestLimiarVigente:
    def test_limiar_vigente_prefere_o_informado(self):
        assert _limiar_vigente([ep()], {"limiar": 12.0}) == 12.0

    def test_limiar_vigente_cai_no_episodio_mais_recente(self):
        antigo = ep(id="a", em="2026-01-01T00:00:00Z", limiar_vigente=20.0)
        novo = ep(id="b", em="2026-06-01T00:00:00Z", limiar_vigente=25.0)
        assert _limiar_vigente([antigo, novo], {}) == 25.0

    def test_limiar_vigente_sem_historico_e_sem_config_e_none(self):
        assert _limiar_vigente([], {}) is None

    def test_limiar_vigente_ignora_limiar_que_nao_e_numero(self):
        assert _limiar_vigente([ep(limiar_vigente=30.0)], {"limiar": "trinta"}) == 30.0

    def test_limiar_vigente_ignora_limiar_nan(self):
        assert _limiar_vigente([ep(limiar_vigente=30.0)], {"limiar": float("nan")}) == 30.0


class TestFracaoDeConfig:
    def test_fracao_de_config_usa_o_valor_informado(self):
        assert _fracao_de_config({"faixa_acima": 0.5}, "faixa_acima") == 0.5

    def test_fracao_de_config_cai_no_padrao_quando_falta(self):
        assert _fracao_de_config({}, "faixa_acima") == CONFIG_PADRAO["faixa_acima"]

    def test_fracao_de_config_cai_no_padrao_para_texto(self):
        assert _fracao_de_config({"faixa_acima": "meio"}, "faixa_acima") == (
            CONFIG_PADRAO["faixa_acima"]
        )

    def test_fracao_de_config_cai_no_padrao_para_negativo(self):
        assert _fracao_de_config({"faixa_acima": -1}, "faixa_acima") == (
            CONFIG_PADRAO["faixa_acima"]
        )

    def test_fracao_de_config_aceita_zero(self):
        assert _fracao_de_config({"faixa_acima": 0}, "faixa_acima") == 0.0


class TestInteiroDeConfig:
    def test_inteiro_de_config_usa_o_valor_informado(self):
        assert _inteiro_de_config({"minimo_na_faixa": 3}, "minimo_na_faixa") == 3

    def test_inteiro_de_config_arredonda_para_cima(self):
        assert _inteiro_de_config({"minimo_na_faixa": 4.2}, "minimo_na_faixa") == 5

    def test_inteiro_de_config_cai_no_padrao_quando_falta(self):
        assert _inteiro_de_config({}, "minimo_na_faixa") == CONFIG_PADRAO["minimo_na_faixa"]

    def test_inteiro_de_config_cai_no_padrao_para_texto(self):
        assert _inteiro_de_config({"minimo_na_faixa": "cinco"}, "minimo_na_faixa") == (
            CONFIG_PADRAO["minimo_na_faixa"]
        )

    def test_inteiro_de_config_devolve_int_de_verdade(self):
        assert isinstance(_inteiro_de_config({"minimo_na_faixa": 4.2}, "minimo_na_faixa"), int)


class TestProposta:
    def test_proposta_monta_o_formato_unico(self):
        corpo = _proposta("subir_limiar", "limiar", 30, 37, "porque sim", {"n": 5})
        assert set(corpo) == {"tipo", "alvo", "de", "para", "motivo", "evidencia"}

    def test_proposta_recusa_motivo_vazio(self):
        with pytest.raises(ValueError, match="sem motivo legível"):
            _proposta("subir_limiar", "limiar", 30, 37, "   ", {"n": 5})

    def test_proposta_recusa_evidencia_vazia(self):
        with pytest.raises(ValueError, match="sem evidência"):
            _proposta("subir_limiar", "limiar", 30, 37, "porque sim", {})

    def test_proposta_nomeia_o_tipo_no_erro(self):
        with pytest.raises(ValueError, match="baixar_limiar"):
            _proposta("baixar_limiar", "limiar", 30, 20, "", {"n": 1})


class TestSubirLimiar:
    def ignorados_na_faixa(self, quantos: int) -> list[Episodio]:
        return [
            ep(id=str(i), risco=32.0, aceita=False, acao="nada")
            for i in range(quantos)
        ]

    def test_subir_limiar_propoe_quando_a_faixa_e_ignorada(self):
        proposta = _subir_limiar(self.ignorados_na_faixa(6), 30.0, CONFIG_PADRAO)
        assert proposta["tipo"] == "subir_limiar"

    def test_subir_limiar_cala_com_poucos_episodios(self):
        assert _subir_limiar(self.ignorados_na_faixa(2), 30.0, CONFIG_PADRAO) is None

    def test_subir_limiar_cala_quando_houve_defeito_na_faixa(self):
        eps = self.ignorados_na_faixa(6)
        eps[0].defeito = True
        assert _subir_limiar(eps, 30.0, CONFIG_PADRAO) is None

    def test_subir_limiar_nao_levanta_com_config_incompleta(self):
        assert _subir_limiar(self.ignorados_na_faixa(6), 30.0, {}) is not None

    def test_subir_limiar_nao_levanta_com_config_de_texto(self):
        cfg = {"faixa_acima": "muito", "minimo_na_faixa": "cinco"}
        assert _subir_limiar(self.ignorados_na_faixa(6), 30.0, cfg) is not None


class TestBaixarLimiar:
    def test_baixar_limiar_propoe_com_um_defeito_abaixo(self):
        eps = [ep(risco=12.0, defeito=True)]
        assert _baixar_limiar(eps, 30.0, CONFIG_PADRAO)["tipo"] == "baixar_limiar"

    def test_baixar_limiar_cala_sem_defeito(self):
        assert _baixar_limiar([ep(risco=12.0)], 30.0, CONFIG_PADRAO) is None

    def test_baixar_limiar_deixa_folga_abaixo_do_menor(self):
        proposta = _baixar_limiar([ep(risco=12.0, defeito=True)], 30.0, CONFIG_PADRAO)
        assert proposta["para"] < 12.0

    def test_baixar_limiar_nao_levanta_com_config_incompleta(self):
        assert _baixar_limiar([ep(risco=12.0, defeito=True)], 30.0, {}) is not None

    def test_baixar_limiar_registra_a_folga_aplicada(self):
        proposta = _baixar_limiar([ep(risco=12.0, defeito=True)], 30.0, {})
        assert proposta["evidencia"]["folga_aplicada"] == CONFIG_PADRAO["folga_ao_baixar"]


class TestRemoverDimensoes:
    def resumo(self, **estatisticas):
        return {"variacao_dimensoes": estatisticas}

    def test_remover_dimensoes_propoe_para_dimensao_parada(self):
        resumo = self.resumo(
            manutenibilidade={"n": 20, "amplitude": 0.01, "desvio": 0.0,
                              "media": 0.5, "distintos": 2}
        )
        assert _remover_dimensoes(resumo, CONFIG_PADRAO)[0]["alvo"] == "manutenibilidade"

    def test_remover_dimensoes_cala_com_poucas_notas(self):
        resumo = self.resumo(
            x={"n": 2, "amplitude": 0.0, "desvio": 0.0, "media": 0.5, "distintos": 1}
        )
        assert _remover_dimensoes(resumo, CONFIG_PADRAO) == []

    def test_remover_dimensoes_cala_para_dimensao_que_varia(self):
        resumo = self.resumo(
            x={"n": 20, "amplitude": 0.6, "desvio": 0.2, "media": 0.5, "distintos": 9}
        )
        assert _remover_dimensoes(resumo, CONFIG_PADRAO) == []

    def test_remover_dimensoes_nao_confunde_sem_dado_com_sem_variacao(self):
        """Amplitude None é 'não houve nota', não 'a nota não variou'."""
        resumo = self.resumo(
            x={"n": 20, "amplitude": None, "desvio": None, "media": None, "distintos": 0}
        )
        assert _remover_dimensoes(resumo, CONFIG_PADRAO) == []

    def test_remover_dimensoes_aceita_resumo_sem_a_chave(self):
        assert _remover_dimensoes({}, CONFIG_PADRAO) == []

    def test_remover_dimensoes_nao_levanta_com_config_incompleta(self):
        resumo = self.resumo(
            x={"n": 20, "amplitude": 0.01, "desvio": 0.0, "media": 0.5, "distintos": 2}
        )
        assert _remover_dimensoes(resumo, {}) != []
