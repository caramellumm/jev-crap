"""A régua: o que ela aceita, o que ela recusa e como ela normaliza.

A validação é testada com o mesmo cuidado do resto porque uma régua inválida
não produz erro visível: ela produz uma nota plausível e errada.
"""

from __future__ import annotations

import json

import pytest

from jev_crap.julgamento.rubrica import Rubrica, RubricaInvalida, carregar_rubrica

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


class TestNormalizacao:
    def test_score_de_tres_niveis_vai_de_zero_a_dois(self, rubrica):
        """Dividir por 1 aqui daria nota acima de 100 sem ninguém perceber."""
        assert rubrica.normalizar("complexidade_cognitiva", 0.0) == 0.0
        assert rubrica.normalizar("complexidade_cognitiva", 2.0) == 1.0
        assert rubrica.normalizar("complexidade_cognitiva", 1.0) == 0.5

    def test_noul_passa_inteiro(self, rubrica):
        assert rubrica.normalizar("injecao", 0.63) == 0.63


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
