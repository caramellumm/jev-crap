"""O transporte: o que é enviado, o que é lido de volta, e como a falha degrada.

Nenhum teste aqui toca a rede. O `httpx.MockTransport` responde no lugar da API,
e o relógio e o sorteio do jitter entram pelo construtor — então o teste de
retentativa não dorme de verdade.
"""

from __future__ import annotations

import httpx
import pytest

from jev_crap.julgamento.jev import (
    STATUS_TRANSITORIOS,
    JulgadorDesligado,
    JulgadorFake,
    JulgadorJev,
    extrair_respostas,
    montar_estado,
    obter_julgador,
)


def resposta_da_api(**respostas):
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 100, "output_tokens": 10},
        "answers": respostas or {
            "complexidade_cognitiva": {"type": "score", "score": 1.5, "confidence": 0.8},
            "injecao": {"type": "noul", "noul": 0.03},
        },
    }


def cliente_que_responde(corpo, status=200, registro=None):
    def responder(requisicao: httpx.Request) -> httpx.Response:
        if registro is not None:
            registro.append(requisicao)
        codigo = status.pop(0) if isinstance(status, list) else status
        return httpx.Response(codigo, json=corpo)

    return httpx.Client(transport=httpx.MockTransport(responder))


class TestMontarEstado:
    def test_leva_codigo_linguagem_testes_e_cobertura(self):
        estado = montar_estado("def f(): ...", "python", ["def test(): ..."], 0.5)
        assert estado["codigo"] == "def f(): ..."
        assert estado["linguagem"] == "python"
        assert estado["testes"] == ["def test(): ..."]
        assert estado["cobertura_branch"] == 0.5

    def test_nao_leva_complexidade_ciclomatica(self):
        """Mandar o ccn ancoraria o julgamento no número que nós mesmos enviamos.

        A graça de ter dois eixos é que o segundo seja independente do primeiro;
        esta é a regressão que passaria despercebida olhando só o resultado.
        """
        estado = montar_estado("def f(): ...", "python")
        texto = " ".join(str(chave) for chave in estado)
        assert "ccn" not in texto
        assert "complexidade" not in texto

    def test_testes_vazios_nao_criam_o_campo(self):
        """O campo ausente é o que faz a pergunta sobre teste não ser feita."""
        assert "testes" not in montar_estado("x", "python", [])

    def test_cobertura_ausente_nao_vira_zero(self):
        assert "cobertura_branch" not in montar_estado("x", "python", None, None)

    def test_funcao_grande_e_truncada_com_aviso_dentro_do_estado(self):
        """Sem o aviso, o modelo julga casos-limite de um pedaço achando que viu tudo."""
        estado = montar_estado("linha\n" * 500, "python", max_linhas=400)
        assert len(estado["codigo"].splitlines()) == 400
        assert "500 linhas" in estado["aviso"]
        assert "400 primeiras" in estado["aviso"]


class TestExtrairRespostas:
    def test_score_normaliza_pela_quantidade_de_niveis(self, rubrica):
        respostas = extrair_respostas(
            resposta_da_api(
                complexidade_cognitiva={"type": "score", "score": 1.43, "confidence": 0.35}
            ),
            rubrica,
        )
        nota = respostas["complexidade_cognitiva"]
        assert nota.bruto == 1.43
        assert nota.normalizado == pytest.approx(0.715)
        assert nota.confianca == 0.35

    def test_noul_nao_ganha_confianca_inventada(self, rubrica):
        """A API não manda `confidence` em noul, e a documentação explica por quê:
        a distribuição tem só dois desfechos, então o próprio número a descreve.
        Inventar 1.0 faria um 0.5 — "não me decidi" — virar certeza absoluta."""
        respostas = extrair_respostas(
            resposta_da_api(injecao={"type": "noul", "noul": 0.5}), rubrica
        )
        assert respostas["injecao"].confianca is None
        assert respostas["injecao"].dispersa is False

    def test_score_com_confianca_baixa_e_marcado_como_disperso(self, rubrica):
        respostas = extrair_respostas(
            resposta_da_api(
                manutenibilidade={"type": "score", "score": 1.0, "confidence": 0.2}
            ),
            rubrica,
        )
        assert respostas["manutenibilidade"].dispersa is True

    def test_uma_resposta_torta_nao_descarta_as_outras(self, rubrica):
        respostas = extrair_respostas(
            resposta_da_api(
                complexidade_cognitiva={"type": "score", "score": "texto"},
                injecao={"type": "noul", "noul": 0.1},
            ),
            rubrica,
        )
        assert "complexidade_cognitiva" not in respostas
        assert "injecao" in respostas

    def test_pergunta_fora_da_regua_e_ignorada(self, rubrica):
        """Régua e resposta podem divergir quando o arquivo de perguntas muda.
        Travar nisso derrubaria a avaliação inteira por um campo a mais."""
        respostas = extrair_respostas(
            resposta_da_api(inventada={"type": "noul", "noul": 0.9}), rubrica
        )
        assert respostas == {}

    def test_booleano_nao_vira_numero(self, rubrica):
        """`isinstance(True, int)` é verdadeiro em Python; aceitar viraria 1.0."""
        respostas = extrair_respostas(
            resposta_da_api(injecao={"type": "noul", "noul": True}), rubrica
        )
        assert respostas == {}

    @pytest.mark.parametrize("dados", [None, [], "texto", {}, {"answers": []}])
    def test_resposta_fora_do_contrato_devolve_vazio(self, dados, rubrica):
        assert extrair_respostas(dados, rubrica) == {}


class TestJulgadorJev:
    def test_envia_a_regua_como_questions_e_o_estado_como_state(self, rubrica):
        registro: list[httpx.Request] = []
        julgador = JulgadorJev(
            "chave", cliente=cliente_que_responde(resposta_da_api(), registro=registro)
        )
        estado = montar_estado("def f(): ...", "python", ["def test(): ..."])
        julgador.julgar(estado, rubrica)

        import json

        corpo = json.loads(registro[0].content)
        assert corpo["model"] == "jev-latest"
        assert corpo["state"]["codigo"] == "def f(): ..."
        assert set(corpo["questions"]) == set(rubrica.dimensoes)
        assert registro[0].headers["authorization"] == "Bearer chave"

    def test_devolve_modelo_e_usage_junto_das_respostas(self, rubrica):
        julgador = JulgadorJev("k", cliente=cliente_que_responde(resposta_da_api()))
        resultado = julgador.julgar(montar_estado("x", "python"), rubrica)
        assert resultado["modelo"] == "jev-1.13.0"
        assert resultado["usage"]["input_tokens"] == 100

    @pytest.mark.parametrize("status", sorted(STATUS_TRANSITORIOS))
    def test_status_transitorio_e_repetido_com_espera_crescente(self, status, rubrica):
        esperas: list[float] = []
        julgador = JulgadorJev(
            "k",
            cliente=cliente_que_responde(resposta_da_api(), status=[status, status, 200]),
            dormir=esperas.append,
            sortear=lambda: 0.0,
        )
        resultado = julgador.julgar(montar_estado("x", "python"), rubrica)
        assert resultado["respostas"]
        assert esperas == [1.0, 2.0]

    def test_o_jitter_entra_na_espera(self, rubrica):
        """Sem jitter, as requisições que tomaram 429 juntas voltam juntas e
        recriam o pico que causou o 429."""
        esperas: list[float] = []
        julgador = JulgadorJev(
            "k",
            cliente=cliente_que_responde(resposta_da_api(), status=[429, 200]),
            dormir=esperas.append,
            sortear=lambda: 1.0,
        )
        julgador.julgar(montar_estado("x", "python"), rubrica)
        assert esperas == [1.5]

    def test_status_definitivo_nao_e_repetido(self, rubrica):
        esperas: list[float] = []
        julgador = JulgadorJev(
            "k",
            cliente=cliente_que_responde({"erro": "chave inválida"}, status=401),
            dormir=esperas.append,
        )
        assert julgador.julgar(montar_estado("x", "python"), rubrica) == {}
        assert esperas == []

    def test_falha_degrada_para_vazio_em_vez_de_levantar(self, rubrica):
        """Indisponibilidade de terceiro não pode virar falha da ferramenta inteira:
        o eixo contável continua valendo sozinho."""

        def explodir(_):
            raise httpx.ConnectError("sem rede")

        julgador = JulgadorJev("k", cliente=httpx.Client(transport=httpx.MockTransport(explodir)))
        assert julgador.julgar(montar_estado("x", "python"), rubrica) == {}


class TestEscolhaDoJulgador:
    def test_sem_chave_devolve_desligado_com_motivo(self):
        julgador = obter_julgador(ambiente={})
        assert isinstance(julgador, JulgadorDesligado)
        assert julgador.ativo is False
        assert "TYPESAFE_API_KEY" in julgador.motivo

    def test_chave_so_de_espaco_conta_como_ausente(self):
        assert obter_julgador(ambiente={"TYPESAFE_API_KEY": "   "}).ativo is False

    def test_com_chave_devolve_o_de_verdade(self):
        assert isinstance(obter_julgador(ambiente={"TYPESAFE_API_KEY": "k"}), JulgadorJev)

    def test_modelo_pode_ser_fixado_pelo_ambiente(self):
        julgador = obter_julgador(
            ambiente={"TYPESAFE_API_KEY": "k", "JEV_CRAP_MODELO": "jev-1.13.0"}
        )
        assert julgador._modelo == "jev-1.13.0"


class TestJulgadorFake:
    def test_guarda_o_estado_recebido(self, rubrica):
        julgador = JulgadorFake({})
        julgador.julgar({"codigo": "x", "linguagem": "python"}, rubrica)
        assert julgador.chamadas == [{"codigo": "x", "linguagem": "python"}]

    def test_respeita_a_pergunta_condicional(self, rubrica):
        """O falso precisa mentir igual ao verdadeiro: sem testes no estado,
        `teste_verifica` não volta — senão a suíte valida um caminho que a API
        nunca produz."""
        from tests.conftest import RESPOSTAS_BOAS

        julgador = JulgadorFake(RESPOSTAS_BOAS)
        sem = julgador.julgar({"codigo": "x", "linguagem": "python"}, rubrica)
        com = julgador.julgar({"codigo": "x", "linguagem": "python", "testes": ["t"]}, rubrica)
        assert "teste_verifica" not in sem["respostas"]
        assert "teste_verifica" in com["respostas"]
