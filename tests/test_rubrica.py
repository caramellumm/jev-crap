"""A régua: o que ela aceita, o que ela recusa e como ela normaliza.

A validação é testada com o mesmo cuidado do resto porque uma régua inválida
não produz erro visível: ela produz uma nota plausível e errada.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from jev_crap.julgamento.rubrica import (
    GRUPOS,
    Rubrica,
    RubricaInvalida,
    _conferir_pesos,
    _ler_dimensoes,
    _texto_de,
    carregar_rubrica,
)

MINIMA = {
    "versao": "teste",
    "dimensoes": {
        "legivel": {
            "grupo": "qualidade",
            "peso": 1.0,
            "sentido": "maior_melhor",
            "pergunta": {"type": "score", "instructions": "q", "criteria": ["ruim", "bom"]},
        }
    },
}


def regua(**mudancas):
    corpo = json.loads(json.dumps(MINIMA))
    corpo["dimensoes"].update(mudancas)
    return corpo


class TestRiscoGrave:
    """Grupo vazio aqui é régua sem gate: tudo passaria a sair como aprovar."""

    def test_risco_grave_traz_os_gates_de_bloqueio(self, rubrica):
        assert "injecao" in rubrica.risco_grave

    def test_risco_grave_levanta_quando_o_grupo_some(self, rubrica, monkeypatch):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        with pytest.raises(RubricaInvalida, match="ao menos uma dimensão"):
            _ = rubrica.risco_grave

    def test_risco_grave_e_risco_atencao_nao_se_sobrepoem(self, rubrica):
        assert not set(rubrica.risco_grave) & set(rubrica.risco_atencao)


class TestNiveis:
    """`niveis` devolve 0 para noul: ele já chega em 0..1 e não se divide."""

    def test_niveis_conta_os_criterios_de_um_score(self, rubrica):
        assert rubrica.dimensoes["complexidade_cognitiva"].niveis == 3

    def test_niveis_e_zero_para_noul(self, rubrica):
        assert rubrica.dimensoes["injecao"].niveis == 0

    def test_niveis_e_zero_quando_criteria_nao_e_lista(self, rubrica):
        dimensao = replace(
            rubrica.dimensoes["complexidade_cognitiva"],
            pergunta={"type": "score", "criteria": "ruim"},
        )
        assert dimensao.niveis == 0

    def test_niveis_e_zero_sem_criteria(self, rubrica):
        dimensao = replace(
            rubrica.dimensoes["complexidade_cognitiva"], pergunta={"type": "score"}
        )
        assert dimensao.niveis == 0

    def test_niveis_nunca_levanta(self, rubrica):
        for dimensao in rubrica.dimensoes.values():
            assert dimensao.niveis >= 0


class TestQualidade:
    """`qualidade` é o único grupo que forma a nota — vazio, a nota não é média de nada."""

    def test_qualidade_traz_as_dimensoes_compensaveis(self, rubrica):
        assert "complexidade_cognitiva" in rubrica.qualidade

    def test_qualidade_traz_so_dimensoes_com_peso(self, rubrica):
        assert all(d.peso > 0 for d in rubrica.qualidade.values())

    def test_qualidade_levanta_quando_o_grupo_some(self, rubrica, monkeypatch):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        with pytest.raises(RubricaInvalida, match="não haveria nota"):
            _ = rubrica.qualidade

    def test_qualidade_nunca_devolve_dicionario_vazio(self, rubrica):
        assert rubrica.qualidade


class TestReguaQueVemComOPacote:
    def test_carrega_e_valida(self, rubrica):
        assert rubrica.versao
        assert len(rubrica.dimensoes) == 11

    def test_os_quatro_grupos_existem_e_nao_se_misturam(self, rubrica):
        assert set(rubrica.qualidade) == {
            "complexidade_cognitiva",
            "teste_verifica",
            "manutenibilidade",
            "tratamento_de_erros",
        }
        assert set(rubrica.risco_grave) == {"exec_dinamica", "injecao"}
        assert set(rubrica.risco_atencao) == {
            "entrada_nao_validada",
            "retorno_inconsistente",
            "caso_limite_nao_tratado",
        }
        # Nenhuma dimensão em dois grupos: um gate que também pesasse na nota
        # seria risco compensado por legibilidade, que é o que o desenho proíbe.
        todos = [
            *rubrica.qualidade,
            *rubrica.contexto,
            *rubrica.risco_grave,
            *rubrica.risco_atencao,
        ]
        assert len(todos) == len(set(todos)) == len(rubrica.dimensoes)

    def test_so_qualidade_tem_peso(self, rubrica):
        assert all(d.peso == 0.0 for d in rubrica.dimensoes.values() if d.grupo != "qualidade")
        assert round(sum(rubrica.pesos.values()), 6) == 1.0

    def test_consequencia_de_falha_declara_que_alto_e_ma_noticia(self, rubrica):
        """A direção é dado, não convenção implícita na ordem dos níveis.

        Inverter esta leitura despriorizaria justamente o código perigoso, e o
        número continuaria plausível — é o erro mais caro que este arquivo pode
        ter e o mais difícil de notar num relatório.
        """
        assert rubrica.dimensoes["consequencia_de_falha"].sentido == "maior_mais_em_jogo"
        niveis = rubrica.dimensoes["consequencia_de_falha"].descrever_niveis()
        assert "cosmético" in niveis[0].lower()
        assert "corrompe" in niveis[-1].lower()

    def test_gates_graves_tem_criteria_de_fronteira(self, rubrica):
        """Gate que barra precisa de fronteira explícita, não só da pergunta.

        A documentação do modelo recomenda `criteria` quando a fronteira é
        ambígua; num gate de bloqueio automático ela nunca pode ser ambígua.
        """
        for dimensao in rubrica.risco_grave.values():
            criteria = dimensao.pergunta.get("criteria")
            assert isinstance(criteria, dict)
            assert criteria["true"].strip() and criteria["false"].strip()

    def test_niveis_de_score_descrevem_situacao_e_nao_grau(self, rubrica):
        """"Moderado" não dá ao modelo nada contra o que comparar.

        A checagem é grosseira de propósito: ela pega a regressão óbvia (alguém
        substituir a descrição por um advérbio de grau) sem tentar julgar texto.
        """
        proibidos = ("moderado", "médio grau", "razoável", "mais ou menos")
        for dimensao in rubrica.dimensoes.values():
            for nivel in dimensao.descrever_niveis():
                assert not any(p in nivel.lower() for p in proibidos), nivel


class TestValidacao:
    def test_peso_que_nao_soma_um_e_recusado(self):
        corpo = regua(
            outra={
                "grupo": "qualidade",
                "peso": 0.3,
                "sentido": "maior_melhor",
                "pergunta": {"type": "score", "instructions": "q", "criteria": ["a", "b"]},
            }
        )
        with pytest.raises(RubricaInvalida, match="somam"):
            Rubrica(corpo)

    def test_peso_fora_do_grupo_qualidade_e_recusado(self):
        corpo = regua(
            risco={
                "grupo": "risco_grave",
                "peso": 0.5,
                "sentido": "maior_pior",
                "pergunta": {"type": "noul", "instructions": "q"},
            }
        )
        with pytest.raises(RubricaInvalida, match="só dimensão do grupo"):
            Rubrica(corpo)

    def test_grupo_desconhecido_e_recusado(self):
        corpo = regua(
            x={
                "grupo": "inventado",
                "sentido": "maior_melhor",
                "pergunta": {"type": "noul", "instructions": "q"},
            }
        )
        with pytest.raises(RubricaInvalida, match="grupo"):
            Rubrica(corpo)

    def test_score_com_um_nivel_so_e_recusado(self):
        """Um nível só faria a normalização dividir por zero.

        Recusar na carga é o que impede isso de virar `ZeroDivisionError` no
        meio de uma batelada já paga.
        """
        corpo = regua(
            x={
                "grupo": "contexto",
                "sentido": "maior_melhor",
                "pergunta": {"type": "score", "instructions": "q", "criteria": ["único"]},
            }
        )
        with pytest.raises(RubricaInvalida, match="dois níveis"):
            Rubrica(corpo)

    def test_instrucao_vazia_e_recusada(self):
        corpo = regua(
            x={
                "grupo": "contexto",
                "sentido": "maior_melhor",
                "pergunta": {"type": "noul", "instructions": "   "},
            }
        )
        with pytest.raises(RubricaInvalida, match="instructions"):
            Rubrica(corpo)

    def test_arquivo_inexistente_diz_onde_procurou(self, tmp_path):
        with pytest.raises(RubricaInvalida, match="não encontrada"):
            carregar_rubrica(tmp_path / "nao-existe.json")

    def test_json_torto_nao_vira_traceback_de_json(self, tmp_path):
        arquivo = tmp_path / "r.json"
        arquivo.write_text("{ não é json", encoding="utf-8")
        with pytest.raises(RubricaInvalida, match="não é JSON"):
            carregar_rubrica(arquivo)


class TestPerguntaCondicional:
    def test_sem_testes_a_pergunta_sobre_teste_nao_e_feita(self, rubrica):
        perguntas = rubrica.perguntas_para({"codigo": "x", "testes": []})
        assert "teste_verifica" not in perguntas
        assert len(perguntas) == len(rubrica.dimensoes) - 1

    def test_com_testes_todas_as_perguntas_vao(self, rubrica):
        perguntas = rubrica.perguntas_para({"codigo": "x", "testes": ["def test(): ..."]})
        assert set(perguntas) == set(rubrica.dimensoes)

    def test_o_peso_da_ausente_e_redistribuido_e_nao_zerado(self, rubrica):
        """Nota sobre três dimensões é honesta; sobre quatro com uma inventada, não."""
        observadas = {n: object() for n in rubrica.pesos if n != "teste_verifica"}
        pesos = rubrica.pesos_observados(observadas)
        assert "teste_verifica" not in pesos
        assert round(sum(pesos.values()), 6) == 1.0
        # A proporção entre as que ficaram é preservada.
        assert pesos["complexidade_cognitiva"] > pesos["manutenibilidade"]

    def test_nenhuma_dimensao_observada_devolve_vazio_em_vez_de_dividir_por_zero(self, rubrica):
        assert rubrica.pesos_observados({}) == {}

    def test_a_pergunta_enviada_nao_leva_metadado_nosso(self, rubrica):
        """`grupo`, `peso` e `sentido` são para o nosso código, não para o modelo.

        Mandá-los inflaria o estado com conteúdo que não decide nada — e a
        documentação do modelo é explícita sobre acurácia cair com isso.
        """
        for pergunta in rubrica.perguntas_para({"codigo": "x", "testes": ["t"]}).values():
            assert set(pergunta) <= {"type", "instructions", "criteria"}


class TestNormalizar:
    """A escala: um score de três níveis vai de 0 a 2, não de 0 a 1.

    Uma asserção por teste, e cada uma sobre um aspecto — o valor convertido,
    a borda do `noul` que já chega pronto, e a régua cuja escala não divide.
    """

    def test_normalizar_divide_o_score_pela_quantidade_de_niveis_menos_um(self, rubrica):
        """Resultado: dividir por 1 daria nota acima de 100% e pareceria plausível."""
        assert rubrica.normalizar("complexidade_cognitiva", 2.0) == 1.0

    def test_normalizar_deixa_o_noul_passar_inteiro(self, rubrica):
        """Borda: `noul` já chega em 0..1, dividir o descaracterizaria."""
        assert rubrica.normalizar("injecao", 0.37) == 0.37

    def test_normalizar_recusa_score_de_um_nivel_so(self, rubrica):
        """Erro: dividir por zero viraria nota infinita no meio do relatório."""
        de_um_nivel = replace(
            rubrica.dimensoes["complexidade_cognitiva"],
            pergunta={"type": "score", "criteria": ["único"]},
        )
        so_um = type("R", (), {"dimensoes": {"so_um": de_um_nivel}})()
        with pytest.raises(RubricaInvalida, match="ao menos dois níveis"):
            Rubrica.normalizar(so_um, "so_um", 1.0)

    def test_normalizar_leva_o_meio_da_escala_para_meio(self, rubrica):
        assert rubrica.normalizar("complexidade_cognitiva", 1.0) == 0.5

    def test_normalizar_recusa_dimensao_desconhecida(self, rubrica):
        with pytest.raises(KeyError):
            rubrica.normalizar("inventada", 1.0)


class TestDoGrupo:
    """`do_grupo` recusa nome que não existe: `{}` silencioso some da nota."""

    def test_do_grupo_devolve_as_dimensoes_do_grupo(self, rubrica):
        assert all(d.grupo == "qualidade" for d in rubrica.do_grupo("qualidade").values())

    def test_do_grupo_aceita_todos_os_grupos_declarados(self, rubrica):
        for grupo in GRUPOS:
            assert isinstance(rubrica.do_grupo(grupo), dict)

    def test_do_grupo_recusa_nome_desconhecido(self, rubrica):
        with pytest.raises(RubricaInvalida, match="desconhecido"):
            rubrica.do_grupo("qualiadde")

    def test_do_grupo_lista_os_grupos_validos_no_erro(self, rubrica):
        with pytest.raises(RubricaInvalida, match="qualidade"):
            rubrica.do_grupo("inventado")

    def test_do_grupo_recusa_grupo_vazio_como_nome(self, rubrica):
        with pytest.raises(RubricaInvalida):
            rubrica.do_grupo("")


class TestRiscoAtencao:
    """Pode estar vazio: sem gate de atenção o relatório degrada, não mente."""

    def test_risco_atencao_traz_as_dimensoes_de_revisao(self, rubrica):
        assert "caso_limite_nao_tratado" in rubrica.risco_atencao

    def test_risco_atencao_nao_levanta_com_grupo_vazio(self, rubrica, monkeypatch):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        assert rubrica.risco_atencao == {}

    def test_risco_atencao_registra_no_log_quando_vazio(self, rubrica, monkeypatch, caplog):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        with caplog.at_level(logging.INFO, logger="jev_crap.julgamento.rubrica"):
            _ = rubrica.risco_atencao
        assert "linha de revisão" in caplog.text


class TestContexto:
    """Também pode estar vazio: degrada a prioridade, não o veredito."""

    def test_contexto_traz_as_dimensoes_de_prioridade(self, rubrica):
        assert "consequencia_de_falha" in rubrica.contexto

    def test_contexto_nao_levanta_com_grupo_vazio(self, rubrica, monkeypatch):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        assert rubrica.contexto == {}

    def test_contexto_registra_no_log_quando_vazio(self, rubrica, monkeypatch, caplog):
        monkeypatch.setattr(rubrica, "dimensoes", {})
        with caplog.at_level(logging.INFO, logger="jev_crap.julgamento.rubrica"):
            _ = rubrica.contexto
        assert "prioridade" in caplog.text

    def test_contexto_nao_tem_peso(self, rubrica):
        assert all(d.peso == 0 for d in rubrica.contexto.values())


class TestDimensoesDeRisco:
    """`dimensoes_de_risco` junta os dois grupos; um nome nos dois seria ambíguo e perigoso."""

    def test_dimensoes_de_risco_junta_grave_e_atencao(self, rubrica):
        dos_dois = set(rubrica.risco_grave) | set(rubrica.risco_atencao)
        assert set(rubrica.dimensoes_de_risco) == dos_dois

    def test_dimensoes_de_risco_inclui_os_gates_de_bloqueio(self, rubrica):
        assert "injecao" in rubrica.dimensoes_de_risco

    def test_dimensoes_de_risco_inclui_as_de_atencao(self, rubrica):
        assert "caso_limite_nao_tratado" in rubrica.dimensoes_de_risco

    def test_dimensoes_de_risco_recusa_repetida_nos_dois_grupos(self, rubrica, monkeypatch):
        """Barrar e aconselhar não podem depender da ordem em que os grupos se unem."""
        grave = next(iter(rubrica.risco_grave.values()))
        monkeypatch.setattr(
            rubrica,
            "do_grupo",
            lambda grupo: {grave.nome: grave} if grupo.startswith("risco") else {},
        )
        with pytest.raises(RubricaInvalida, match="aparece nos dois grupos"):
            _ = rubrica.dimensoes_de_risco


class TestPesos:
    """`pesos` é o divisor da nota: somando 0.9 ela sai alta, somando 1.1 sai baixa."""

    def test_pesos_traz_uma_entrada_por_dimensao_de_qualidade(self, rubrica):
        assert set(rubrica.pesos) == set(rubrica.qualidade)

    def test_pesos_somam_um(self, rubrica):
        assert round(sum(rubrica.pesos.values()), 6) == 1.0

    def test_pesos_levanta_quando_a_soma_nao_fecha(self, rubrica, monkeypatch):
        torta = {n: replace(d, peso=d.peso / 2) for n, d in rubrica.qualidade.items()}
        monkeypatch.setattr(rubrica, "dimensoes", {**rubrica.dimensoes, **torta})
        with pytest.raises(RubricaInvalida, match="não 1.0"):
            _ = rubrica.pesos

    def test_pesos_sao_todos_positivos(self, rubrica):
        assert all(p > 0 for p in rubrica.pesos.values())


class TestParaRubrica:
    def test_para_rubrica_traz_a_versao(self, rubrica):
        assert rubrica.para_rubrica()["versao"] == rubrica.versao

    def test_para_rubrica_traz_uma_entrada_por_dimensao(self, rubrica):
        assert set(rubrica.para_rubrica()["dimensoes"]) == set(rubrica.dimensoes)

    def test_para_rubrica_anula_o_peso_fora_de_qualidade(self, rubrica):
        corpo = rubrica.para_rubrica()["dimensoes"]
        assert corpo["injecao"]["peso"] is None

    def test_para_rubrica_e_serializavel(self, rubrica):
        assert json.dumps(rubrica.para_rubrica())

    def test_para_rubrica_traz_a_instrucao_de_cada_pergunta(self, rubrica):
        corpo = rubrica.para_rubrica()["dimensoes"]
        assert corpo["injecao"]["pergunta"]


class TestLerDimensoes:
    def test_ler_dimensoes_recusa_regua_sem_o_campo(self):
        with pytest.raises(RubricaInvalida, match="dimensoes"):
            _ler_dimensoes({"versao": "x"})

    def test_ler_dimensoes_recusa_dimensoes_vazias(self):
        with pytest.raises(RubricaInvalida, match="não vazio"):
            _ler_dimensoes({"dimensoes": {}})

    def test_ler_dimensoes_recusa_dimensao_que_nao_e_objeto(self):
        with pytest.raises(RubricaInvalida, match="precisa ser um objeto"):
            _ler_dimensoes({"dimensoes": {"x": "não é objeto"}})

    def test_ler_dimensoes_recusa_sentido_desconhecido(self):
        crua = {**MINIMA["dimensoes"]["legivel"], "sentido": "para_cima"}
        with pytest.raises(RubricaInvalida, match="sentido"):
            _ler_dimensoes({"dimensoes": {"legivel": crua}})

    def test_ler_dimensoes_recusa_tipo_desconhecido(self):
        crua = {**MINIMA["dimensoes"]["legivel"]}
        crua["pergunta"] = {**crua["pergunta"], "type": "estrelas"}
        with pytest.raises(RubricaInvalida, match="tipo"):
            _ler_dimensoes({"dimensoes": {"legivel": crua}})

    def test_ler_dimensoes_aceita_a_regua_minima(self):
        assert "legivel" in _ler_dimensoes(MINIMA)


class TestConferirPesos:
    def test_conferir_pesos_aceita_soma_um(self, rubrica):
        assert _conferir_pesos(rubrica.dimensoes) is None

    def test_conferir_pesos_recusa_soma_diferente(self, rubrica):
        torta = {n: replace(d, peso=d.peso / 2) for n, d in rubrica.qualidade.items()}
        with pytest.raises(RubricaInvalida, match="não 1.0"):
            _conferir_pesos({**rubrica.dimensoes, **torta})

    def test_conferir_pesos_recusa_ausencia_de_qualidade(self):
        with pytest.raises(RubricaInvalida, match="não haveria nota"):
            _conferir_pesos({})

    def test_conferir_pesos_diz_quanto_somou(self, rubrica):
        torta = {n: replace(d, peso=d.peso / 2) for n, d in rubrica.qualidade.items()}
        with pytest.raises(RubricaInvalida, match="0.5"):
            _conferir_pesos({**rubrica.dimensoes, **torta})


class TestCarregarRubrica:
    def test_carregar_rubrica_le_a_que_vem_no_pacote(self):
        assert carregar_rubrica().dimensoes

    def test_carregar_rubrica_le_um_arquivo_apontado(self, tmp_path):
        alvo = tmp_path / "regua.json"
        alvo.write_text(json.dumps(MINIMA), encoding="utf-8")
        assert carregar_rubrica(alvo).versao == "teste"

    def test_carregar_rubrica_diz_onde_procurou(self, tmp_path):
        with pytest.raises(RubricaInvalida, match="não encontrada"):
            carregar_rubrica(tmp_path / "nao_existe.json")

    def test_carregar_rubrica_traduz_json_torto(self, tmp_path):
        alvo = tmp_path / "regua.json"
        alvo.write_text("{não é json", encoding="utf-8")
        with pytest.raises(RubricaInvalida, match="não é JSON válido"):
            carregar_rubrica(alvo)

    def test_carregar_rubrica_aceita_caminho_em_texto(self, tmp_path):
        alvo = tmp_path / "regua.json"
        alvo.write_text(json.dumps(MINIMA), encoding="utf-8")
        assert carregar_rubrica(str(alvo)).versao == "teste"


class TestPerguntasPara:
    def test_perguntas_para_traz_as_incondicionais(self, rubrica):
        assert "complexidade_cognitiva" in rubrica.perguntas_para({"codigo": "x"})

    def test_perguntas_para_omite_a_condicional_sem_evidencia(self, rubrica):
        assert "teste_verifica" not in rubrica.perguntas_para({"codigo": "x"})

    def test_perguntas_para_inclui_a_condicional_com_evidencia(self, rubrica):
        pedidas = rubrica.perguntas_para({"codigo": "x", "testes": ["def test_x(): ..."]})
        assert "teste_verifica" in pedidas

    def test_perguntas_para_trata_lista_vazia_como_ausencia(self, rubrica):
        assert "teste_verifica" not in rubrica.perguntas_para({"codigo": "x", "testes": []})

    def test_perguntas_para_devolve_copia_da_pergunta(self, rubrica):
        pedidas = rubrica.perguntas_para({"codigo": "x"})
        pedidas["injecao"]["type"] = "adulterado"
        assert rubrica.dimensoes["injecao"].pergunta["type"] == "noul"


class TestPesosObservados:
    def test_pesos_observados_renormaliza_para_somar_um(self, rubrica):
        parciais = dict(list(rubrica.pesos.items())[:2])
        observados = rubrica.pesos_observados(parciais)
        assert round(sum(observados.values()), 6) == 1.0

    def test_pesos_observados_ignora_quem_nao_respondeu(self, rubrica):
        observados = rubrica.pesos_observados({"complexidade_cognitiva": 1})
        assert set(observados) == {"complexidade_cognitiva"}

    def test_pesos_observados_sem_resposta_e_vazio(self, rubrica):
        assert rubrica.pesos_observados({}) == {}

    def test_pesos_observados_com_tudo_devolve_os_pesos_originais(self, rubrica):
        observados = rubrica.pesos_observados(rubrica.pesos)
        assert observados == pytest.approx(rubrica.pesos)


class TestDescreverNiveis:
    def test_descrever_niveis_lista_os_criterios_de_um_score(self, rubrica):
        assert len(rubrica.dimensoes["complexidade_cognitiva"].descrever_niveis()) == 3

    def test_descrever_niveis_de_noul_traz_nao_e_sim(self, rubrica):
        descritos = rubrica.dimensoes["injecao"].descrever_niveis()
        assert descritos == [] or descritos[0].startswith("não:")

    def test_descrever_niveis_sem_criteria_e_vazio(self, rubrica):
        dimensao = replace(
            rubrica.dimensoes["complexidade_cognitiva"], pergunta={"type": "score"}
        )
        assert dimensao.descrever_niveis() == []

    def test_descrever_niveis_nunca_levanta(self, rubrica):
        for dimensao in rubrica.dimensoes.values():
            assert isinstance(dimensao.descrever_niveis(), list)


class TestTextoDe:
    """A régua vem de arquivo do usuário: explicar critérios não pode falhar."""

    def test_texto_de_preserva_texto(self):
        assert _texto_de("instrução") == "instrução"

    def test_texto_de_converte_numero(self):
        assert _texto_de(3) == "3"

    def test_texto_de_converte_none(self):
        assert _texto_de(None) == "None"

    def test_texto_de_marca_o_que_nao_vira_texto(self):
        class Explosiva:
            def __str__(self):
                raise RuntimeError("não vira texto")

        assert "não textualizável" in _texto_de(Explosiva())

    def test_texto_de_nunca_levanta(self):
        class Explosiva:
            def __str__(self):
                raise RecursionError

        assert isinstance(_texto_de(Explosiva()), str)
