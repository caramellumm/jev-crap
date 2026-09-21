"""Testes da fórmula de risco e do registro dela.

O foco aqui é o que acontece quando a entrada não é o que a fórmula espera:
`Insumos` é o ponto onde a medição vira número, e um valor torto que passe
daqui vira relatório plausível e errado.
"""

from __future__ import annotations

import pytest

from jev_crap.metrica.risco import (
    COMPLEXIDADE_DE_REFERENCIA,
    SEM_DADOS,
    CrapClassico,
    Formula,
    Insumos,
    _validar_fracao,
    formulas_disponiveis,
    obter_formula,
    registrar_formula,
)


def insumos(**ajustes) -> Insumos:
    campos = dict(
        complexidade=5, cobertura_linha=0.0, cobertura_branch=None, linhas_logicas=10
    )
    return Insumos(**{**campos, **ajustes})


class Metade(Formula):
    """Fórmula mínima: implementa só `calcular`, que é o único obrigatório."""

    nome = "metade"

    def calcular(self, i: Insumos) -> float:
        return float(i.complexidade) * 2.0


class SemCalcular(Formula):
    """Fórmula que esqueceu o método obrigatório."""

    nome = "sem_calcular"


class TestCoberturaPreferida:
    """Branch quando existe, linha quando não: as duas contam coisas diferentes."""

    def test_cobertura_preferida_usa_branch_quando_existe(self):
        assert insumos(cobertura_linha=0.9, cobertura_branch=0.3).cobertura_preferida == 0.3

    def test_cobertura_preferida_cai_na_linha_sem_branch(self):
        assert insumos(cobertura_linha=0.9, cobertura_branch=None).cobertura_preferida == 0.9

    def test_cobertura_preferida_trata_a_sentinela_como_ausencia(self):
        preferida = insumos(cobertura_linha=0.9, cobertura_branch=SEM_DADOS).cobertura_preferida
        assert preferida == 0.9

    def test_cobertura_preferida_aceita_branch_zero(self):
        """Zero é dado, não ausência: metade dos caminhos nunca exercitada."""
        assert insumos(cobertura_linha=1.0, cobertura_branch=0.0).cobertura_preferida == 0.0

    def test_cobertura_preferida_devolve_a_sentinela_quando_nao_ha_nada(self):
        sem_nada = insumos(cobertura_linha=SEM_DADOS, cobertura_branch=None)
        assert sem_nada.cobertura_preferida == SEM_DADOS

    def test_cobertura_preferida_nao_levanta_em_nenhuma_combinacao(self):
        for linha in (0.0, 0.5, 1.0, SEM_DADOS):
            for branch in (0.0, 0.5, 1.0, None, SEM_DADOS):
                assert insumos(
                    cobertura_linha=linha, cobertura_branch=branch
                ).cobertura_preferida is not None


class TestContratoDeCalcular:
    """`calcular` visto dos dois lados: quem implementa e quem esquece.

    Cada teste toca a implementação real e o corpo do protocolo no mesmo
    lugar — é assim que o contrato fica legível: o que `calcular` devolve
    quando existe, e o que ele faz quando não existe.
    """

    def test_calcular_devolve_numero_na_formula_implementada(self):
        assert isinstance(CrapClassico().calcular(insumos()), float)
        assert CrapClassico().calcular(insumos(complexidade=5, cobertura_linha=0.0)) == 30.0
        assert CrapClassico().calcular(insumos(complexidade=5, cobertura_linha=1.0)) == 5.0

    def test_calcular_levanta_quando_a_formula_nao_implementa(self):
        assert isinstance(CrapClassico().calcular(insumos()), float)
        with pytest.raises(NotImplementedError, match="calcular"):
            SemCalcular().calcular(insumos())

    def test_calcular_nomeia_a_classe_que_esqueceu(self):
        with pytest.raises(NotImplementedError, match="SemCalcular"):
            SemCalcular().calcular(insumos())
        assert CrapClassico().calcular(insumos()) > 0

    def test_calcular_diz_que_os_outros_dois_metodos_tem_padrao(self):
        with pytest.raises(NotImplementedError, match="implementação padrão"):
            SemCalcular().calcular(insumos())

    def test_calcular_nunca_devolve_none_em_silencio(self):
        for formula in (CrapClassico(), Metade()):
            assert formula.calcular(insumos()) is not None

    def test_calcular_trata_cobertura_ausente_como_o_pior_caso(self):
        """Um número otimista esconderia exatamente o que se quer achar."""
        sem_dado = CrapClassico().calcular(insumos(cobertura_linha=SEM_DADOS))
        descoberta = CrapClassico().calcular(insumos(cobertura_linha=0.0))
        assert sem_dado == descoberta


@pytest.fixture(params=formulas_disponiveis())
def formula(request):
    return obter_formula(request.param)


class TestPropriedadesDeQualquerFormula:
    def test_mais_cobertura_nunca_aumenta_o_risco(self, formula):
        anterior = None
        for cobertura in (0.0, 0.25, 0.5, 0.75, 1.0):
            valor = formula.calcular(Insumos(10, cobertura, None, 30))
            if anterior is not None:
                assert valor <= anterior
            anterior = valor

    def test_mais_complexidade_nunca_diminui_o_risco(self, formula):
        anterior = None
        for complexidade in (1, 2, 5, 10, 25):
            valor = formula.calcular(Insumos(complexidade, 0.5, None, 30))
            if anterior is not None:
                assert valor >= anterior
            anterior = valor

    def test_mesma_entrada_mesma_saida(self, formula):
        insumos = Insumos(7, 0.3, 0.2, 20)
        assert formula.calcular(insumos) == formula.calcular(insumos)

    def test_interpreta_o_numero_em_vez_de_so_devolve_lo(self, formula):
        """Número sem interpretação vira meta de planilha."""
        texto = formula.interpretar(formula.limiar_padrao() + 1)
        assert len(texto) > 40
        assert str(int(formula.limiar_padrao())) in texto

    def test_cobertura_ausente_e_tratada_como_o_pior_caso(self, formula):
        """Um número otimista esconderia exatamente o que se quer achar."""
        assert formula.calcular(Insumos(5, SEM_DADOS, None, 10)) == formula.calcular(
            Insumos(5, 0.0, None, 10)
        )


class TestCrapClassico:
    @pytest.mark.parametrize(
        "complexidade,cobertura,esperado",
        [(5, 0.0, 30.0), (5, 1.0, 5.0), (30, 1.0, 30.0), (1, 0.0, 2.0), (10, 0.5, 22.5)],
    )
    def test_os_numeros_da_formula_publicada(self, complexidade, cobertura, esperado):
        """Os dois 30 não são coincidência: é onde a convenção do limiar mora."""
        assert obter_formula("crap").calcular(
            Insumos(complexidade, cobertura, None, 10)
        ) == pytest.approx(esperado)


class TestValidarFracao:
    def test_validar_fracao_aceita_as_pontas(self):
        assert _validar_fracao(0.0, "cobertura_linha") is None
        assert _validar_fracao(1.0, "cobertura_linha") is None

    def test_validar_fracao_aceita_valor_no_meio(self):
        assert _validar_fracao(0.42, "cobertura_branch") is None

    def test_validar_fracao_recusa_acima_de_um(self):
        with pytest.raises(ValueError, match="fração de 0 a 1"):
            _validar_fracao(1.5, "cobertura_linha")

    def test_validar_fracao_recusa_negativo(self):
        with pytest.raises(ValueError, match="fração de 0 a 1"):
            _validar_fracao(-0.2, "cobertura_linha")

    def test_validar_fracao_nomeia_nan_como_caso_proprio(self):
        with pytest.raises(ValueError, match="divisão 0/0"):
            _validar_fracao(float("nan"), "cobertura_linha")

    def test_validar_fracao_recusa_texto_dizendo_qual_campo(self):
        with pytest.raises(ValueError, match="cobertura_branch precisa ser número"):
            _validar_fracao("0.5", "cobertura_branch")

    def test_validar_fracao_recusa_booleano(self):
        with pytest.raises(ValueError, match="precisa ser número"):
            _validar_fracao(True, "cobertura_linha")


class TestPostInit:
    """`Insumos.__post_init__` é a fronteira entre medir e pontuar."""

    def test_post_init_aceita_insumos_validos(self):
        assert insumos().complexidade == 5

    def test_post_init_recusa_complexidade_zero(self):
        with pytest.raises(ValueError, match="começa em 1"):
            insumos(complexidade=0)

    def test_post_init_recusa_complexidade_negativa(self):
        with pytest.raises(ValueError, match="começa em 1"):
            insumos(complexidade=-3)

    def test_post_init_recusa_complexidade_fracionaria(self):
        with pytest.raises(ValueError, match="precisa ser inteiro"):
            insumos(complexidade=2.5)

    def test_post_init_recusa_linhas_logicas_negativas(self):
        with pytest.raises(ValueError, match="linhas_logicas"):
            insumos(linhas_logicas=-1)

    def test_post_init_deixa_passar_a_sentinela_sem_dados(self):
        assert insumos(cobertura_linha=SEM_DADOS).cobertura_linha == SEM_DADOS

    def test_post_init_deixa_passar_branch_none(self):
        assert insumos(cobertura_branch=None).cobertura_branch is None

    def test_post_init_recusa_cobertura_de_linha_fora_da_faixa(self):
        with pytest.raises(ValueError, match="cobertura_linha"):
            insumos(cobertura_linha=1.2)

    def test_post_init_recusa_cobertura_de_branch_fora_da_faixa(self):
        with pytest.raises(ValueError, match="cobertura_branch"):
            insumos(cobertura_branch=-0.5)


class TestLimiarPadrao:
    """O limiar sai da fórmula; escrever 30.0 o desligaria da curva."""

    def test_limiar_padrao_da_trinta_na_formula_classica(self):
        assert CrapClassico().limiar_padrao() == 30.0

    def test_limiar_padrao_e_o_risco_da_complexidade_de_referencia_sem_teste(self):
        formula = CrapClassico()
        referencia = insumos(complexidade=COMPLEXIDADE_DE_REFERENCIA, cobertura_linha=0.0)
        assert formula.limiar_padrao() == formula.calcular(referencia)

    def test_limiar_padrao_e_estavel_entre_chamadas(self):
        formula = CrapClassico()
        assert formula.limiar_padrao() == formula.limiar_padrao()

    def test_limiar_padrao_e_positivo(self):
        assert CrapClassico().limiar_padrao() > 0

    def test_crap_nao_redefine_limiar_padrao(self):
        assert "limiar_padrao" not in vars(CrapClassico)


class TestInterpretar:
    def test_interpretar_chama_risco_baixo_abaixo_de_dez(self):
        assert "risco baixo" in CrapClassico().interpretar(4.0)

    def test_interpretar_chama_moderado_entre_dez_e_o_limiar(self):
        assert "moderado" in CrapClassico().interpretar(20.0)

    def test_interpretar_aponta_acima_do_limiar(self):
        assert "acima do limiar" in CrapClassico().interpretar(90.0)

    def test_interpretar_nao_inventa_recomendacao_para_nan(self):
        frase = CrapClassico().interpretar(float("nan"))
        assert "não pode ser lida" in frase
        assert "risco baixo" not in frase

    def test_interpretar_nao_inventa_recomendacao_para_texto(self):
        assert "não numérico" in CrapClassico().interpretar("alto")

    def test_interpretar_recusa_booleano_como_numero(self):
        assert "não numérico" in CrapClassico().interpretar(True)

    def test_interpretar_sempre_devolve_texto(self):
        for valor in (0.0, 9.9, 10.0, 29.9, 30.0, 1e6, float("nan"), None, "x"):
            assert isinstance(CrapClassico().interpretar(valor), str)


class TestRegistrarFormula:
    def test_registrar_formula_recusa_nome_repetido(self):
        with pytest.raises(ValueError, match="já existe fórmula"):
            registrar_formula("crap", CrapClassico)

    def test_registrar_formula_recusa_nome_vazio(self):
        with pytest.raises(ValueError, match="texto não vazio"):
            registrar_formula("", CrapClassico)

    def test_registrar_formula_recusa_nome_so_de_espacos(self):
        with pytest.raises(ValueError, match="texto não vazio"):
            registrar_formula("   ", CrapClassico)

    def test_registrar_formula_recusa_nome_que_nao_e_texto(self):
        with pytest.raises(ValueError, match="texto não vazio"):
            registrar_formula(7, CrapClassico)

    def test_registrar_formula_recusa_fabrica_nao_chamavel(self):
        with pytest.raises(ValueError, match="precisa ser chamável"):
            registrar_formula("nova", "não é fábrica")

    def test_registrar_formula_aceita_uma_formula_nova(self, formula_temporaria):
        assert "temporaria" in formulas_disponiveis()


class TestFormulasDisponiveis:
    def test_formulas_disponiveis_traz_a_classica(self):
        assert "crap" in formulas_disponiveis()

    def test_formulas_disponiveis_vem_ordenada(self):
        nomes = formulas_disponiveis()
        assert list(nomes) == sorted(nomes)

    def test_formulas_disponiveis_devolve_tupla(self):
        assert isinstance(formulas_disponiveis(), tuple)

    def test_formulas_disponiveis_ignora_chave_que_nao_e_texto(self, monkeypatch):
        from jev_crap.metrica import risco

        monkeypatch.setitem(risco._REGISTRO, 7, CrapClassico)
        assert formulas_disponiveis() == ("crap",)


class TestObterFormula:
    def test_obter_formula_sem_argumento_da_a_classica(self):
        assert obter_formula().nome == "crap"

    def test_obter_formula_lista_as_disponiveis_no_erro(self):
        with pytest.raises(ValueError, match="disponíveis: crap"):
            obter_formula("inexistente")

    def test_obter_formula_recusa_nome_que_nao_e_texto(self):
        with pytest.raises(ValueError, match="desconhecida"):
            obter_formula(7)

    def test_obter_formula_traduz_fabrica_que_explode(self, monkeypatch):
        from jev_crap.metrica import risco

        def explode():
            raise RuntimeError("faltou dependência")

        monkeypatch.setitem(risco._REGISTRO, "quebrada", explode)
        with pytest.raises(ValueError, match="a fábrica da fórmula 'quebrada' falhou"):
            obter_formula("quebrada")

    def test_obter_formula_recusa_objeto_que_nao_cumpre_o_protocolo(self, monkeypatch):
        from jev_crap.metrica import risco

        monkeypatch.setitem(risco._REGISTRO, "torta", lambda: object())
        with pytest.raises(ValueError, match="não cumpre o protocolo Formula"):
            obter_formula("torta")

    def test_obter_formula_devolve_instancia_nova_a_cada_chamada(self):
        assert obter_formula() is not obter_formula()


@pytest.fixture
def formula_temporaria():
    """Registra e desfaz: `_REGISTRO` é global e sobrevive ao teste."""
    from jev_crap.metrica import risco

    registrar_formula("temporaria", CrapClassico)
    yield
    risco._REGISTRO.pop("temporaria", None)


class TestRegistroDeFormulas:
    def test_nome_repetido_e_recusado(self):
        """Duas fórmulas com o mesmo nome fariam relatórios antigos mudarem de
        significado sem aviso."""
        with pytest.raises(ValueError, match="já existe"):
            registrar_formula("crap", lambda: None)

    def test_nome_desconhecido_lista_os_disponiveis(self):
        with pytest.raises(ValueError, match="disponíveis"):
            obter_formula("inventada")


class TestInsumos:
    @pytest.mark.parametrize("cobertura", [-0.5, 1.5])
    def test_cobertura_fora_da_faixa_e_erro_e_nao_corte_silencioso(self, cobertura):
        """Cobertura acima de 1 quase sempre é bug de leitura do relatório;
        cortar em silêncio esconderia o defeito que interessa descobrir."""
        with pytest.raises(ValueError, match="fração"):
            Insumos(5, cobertura, None, 10)

    def test_complexidade_zero_e_erro(self):
        with pytest.raises(ValueError, match="começa em 1"):
            Insumos(0, 0.5, None, 10)

    def test_branch_e_preferida_quando_existe(self):
        """Branch está na mesma unidade da complexidade: caminhos."""
        assert Insumos(5, 0.9, 0.3, 10).cobertura_preferida == 0.3
        assert Insumos(5, 0.9, None, 10).cobertura_preferida == 0.9


class TestLimiarPadraoRecusaCalcularTorto:
    """O limiar sai de uma fórmula registrada de fora: ele confere o que recebe."""

    def uma_formula_que_devolve(self, valor):
        class Torta(Formula):
            nome = "torta"

            def calcular(self, i: Insumos) -> float:
                return valor

        return Torta()

    def test_limiar_padrao_recusa_nan(self):
        with pytest.raises(ValueError, match="não finito"):
            self.uma_formula_que_devolve(float("nan")).limiar_padrao()

    def test_limiar_padrao_recusa_infinito(self):
        with pytest.raises(ValueError, match="não finito"):
            self.uma_formula_que_devolve(float("inf")).limiar_padrao()

    def test_limiar_padrao_recusa_texto(self):
        with pytest.raises(TypeError, match="precisa\n?.*ser número|precisa ser número"):
            self.uma_formula_que_devolve("trinta").limiar_padrao()

    def test_limiar_padrao_recusa_none(self):
        with pytest.raises(TypeError):
            self.uma_formula_que_devolve(None).limiar_padrao()

    def test_limiar_padrao_nomeia_a_formula_culpada(self):
        with pytest.raises(ValueError, match="torta"):
            self.uma_formula_que_devolve(float("nan")).limiar_padrao()

    def test_limiar_padrao_converte_inteiro_em_float(self):
        assert isinstance(self.uma_formula_que_devolve(30).limiar_padrao(), float)


class TestProtocoloFormula:
    """`calcular` é obrigatório; `limiar_padrao` e `interpretar` têm padrão."""

    def test_calcular_do_protocolo_levanta_not_implemented(self):
        with pytest.raises(NotImplementedError, match="calcular"):
            SemCalcular().calcular(insumos())

    def test_a_mensagem_nomeia_a_classe_incompleta(self):
        with pytest.raises(NotImplementedError, match="SemCalcular"):
            SemCalcular().calcular(insumos())

    def test_a_mensagem_diz_que_os_outros_dois_tem_padrao(self):
        with pytest.raises(NotImplementedError, match="implementação padrão"):
            SemCalcular().calcular(insumos())

    def test_limiar_padrao_do_protocolo_deriva_da_formula(self):
        assert Metade().limiar_padrao() == COMPLEXIDADE_DE_REFERENCIA * 2.0

    def test_limiar_padrao_do_protocolo_propaga_o_calcular_que_falta(self):
        with pytest.raises(NotImplementedError):
            SemCalcular().limiar_padrao()

    def test_interpretar_do_protocolo_compara_com_o_limiar(self):
        assert "abaixo do limiar" in Metade().interpretar(1.0)

    def test_interpretar_do_protocolo_aponta_acima_do_limiar(self):
        assert "acima do limiar" in Metade().interpretar(999.0)

    def test_interpretar_do_protocolo_cita_o_nome_da_formula(self):
        assert "metade" in Metade().interpretar(1.0)

    def test_interpretar_do_protocolo_recusa_nan(self):
        assert "não pode ser lida" in Metade().interpretar(float("nan"))

    def test_interpretar_do_protocolo_recusa_nao_numero(self):
        assert "não numérico" in Metade().interpretar("alto")
