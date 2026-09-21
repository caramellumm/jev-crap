"""O transporte: o que é enviado, o que é lido de volta, e como a falha degrada.

Nenhum teste aqui toca a rede. O `httpx.MockTransport` responde no lugar da API,
e o relógio e o sorteio do jitter entram pelo construtor — então o teste de
retentativa não dorme de verdade.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from jev_crap.julgamento.jev import (
    ESPERA_MAXIMA_SEGUNDOS,
    STATUS_TRANSITORIOS,
    Julgador,
    JulgadorDesligado,
    JulgadorFake,
    JulgadorJev,
    _como_numero,
    _para_resposta,
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


    def test_montar_estado_recusa_codigo_que_nao_e_texto(self):
        with pytest.raises(TypeError, match="precisa ser texto"):
            montar_estado(["def f(): pass"], "python")

    def test_montar_estado_recusa_max_linhas_zero(self):
        with pytest.raises(ValueError, match="ao menos 1"):
            montar_estado("def f(): pass", "python", max_linhas=0)

    def test_montar_estado_ignora_cobertura_nan(self):
        """NaN no JSON não é JSON válido: a requisição voltaria com HTTP 400."""
        assert "cobertura_branch" not in montar_estado("x", "python", cobertura_branch=float("nan"))

    def test_montar_estado_ignora_cobertura_booleana(self):
        assert "cobertura_branch" not in montar_estado("x", "python", cobertura_branch=True)

    def test_montar_estado_usa_desconhecida_para_linguagem_vazia(self):
        assert montar_estado("x", "")["linguagem"] == "desconhecida"


def _escala_degenerada(_nome, _valor):
    """Uma régua cuja conversão de escala estoura — dimensão descartada, não o resto."""
    raise ZeroDivisionError("régua com escala degenerada")


def _explode_ao_fechar(_self) -> None:
    """Um `close` que levanta — acontece com transporte já morto."""
    raise RuntimeError("socket já morreu")


class _Incompleto(Julgador):
    """Herda o protocolo e não implementa nada — o caso que o corpo dele cobre."""

    ativo = True


class TestContratoDeJulgar:
    """O método do protocolo, visto nas quatro implementações lado a lado.

    Elas compartilham um contrato só: devolver o dicionário de respostas ou
    `{}`, **nunca** levantar. Testar cada uma no seu canto esconde justamente o
    que importa — que as quatro respondem a mesma coisa para a mesma falha.

    Cada teste percorre `todas()`, então nenhuma implementação fica sem a
    verificação que as outras têm.
    """

    ESTADO = {"codigo": "def f(): return 1", "linguagem": "python"}

    def test_toda_implementacao_devolve_dicionario_e_o_protocolo_levanta(self, rubrica):
        ativo = JulgadorJev("sk-chave", cliente=cliente_que_responde(resposta_da_api()))
        assert isinstance(ativo.julgar(self.ESTADO, rubrica), dict)
        assert isinstance(JulgadorFake().julgar(self.ESTADO, rubrica), dict)
        assert JulgadorDesligado().julgar(self.ESTADO, rubrica) == {}
        assert JulgadorFake(erro=True).julgar(self.ESTADO, rubrica) == {}
        with pytest.raises(NotImplementedError, match="_Incompleto"):
            _Incompleto().julgar(self.ESTADO, rubrica)

    def test_nenhuma_implementacao_levanta_quando_a_rede_cai(self, rubrica, caplog):
        caido = JulgadorJev(
            "sk-chave", cliente=cliente_que_responde({}, status=500), dormir=lambda _s: None
        )
        assert caido.julgar(self.ESTADO, rubrica) == {}
        assert JulgadorFake(erro=True).julgar(self.ESTADO, rubrica) == {}
        # O desligado devolve {} mesmo com estado torto, mas registra o aviso —
        # é o caminho que roda em todo CI sem chave.
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            assert JulgadorDesligado().julgar({"sem_codigo": 1}, rubrica) == {}
        assert "sem `codigo`" in caplog.text
        with pytest.raises(NotImplementedError):
            _Incompleto().julgar(self.ESTADO, rubrica)

    def test_so_o_transporte_ativo_traz_respostas(self, rubrica, caplog):
        ativo = JulgadorJev("sk-chave", cliente=cliente_que_responde(resposta_da_api()))
        assert ativo.julgar(self.ESTADO, rubrica)["respostas"]
        assert JulgadorFake().julgar(self.ESTADO, rubrica)["modelo"] == "jev-fake"
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            assert JulgadorDesligado().julgar(self.ESTADO, rubrica) == {}
        assert caplog.text == ""  # estado bom: o desligado não avisa nada
        assert JulgadorFake(erro=True).julgar(self.ESTADO, rubrica) == {}
        with pytest.raises(NotImplementedError, match=r"virar \{\}"):
            _Incompleto().julgar(self.ESTADO, rubrica)

    def test_o_corpo_do_protocolo_levanta_em_vez_de_devolver_none(self, rubrica):
        with pytest.raises(NotImplementedError, match="_Incompleto"):
            _Incompleto().julgar(self.ESTADO, rubrica)

    def test_o_corpo_do_protocolo_diz_que_toda_falha_deve_virar_vazio(self, rubrica):
        with pytest.raises(NotImplementedError, match=r"virar \{\}"):
            _Incompleto().julgar(self.ESTADO, rubrica)

    def test_o_fake_registra_o_estado_recebido(self, rubrica):
        fake = JulgadorFake()
        fake.julgar(self.ESTADO, rubrica)
        assert fake.chamadas == [self.ESTADO]

    def test_o_fake_pode_estourar_para_exercitar_quem_chama(self, rubrica):
        with pytest.raises(RuntimeError, match="caiu"):
            JulgadorFake(levanta=RuntimeError("caiu")).julgar(self.ESTADO, rubrica)

    def test_o_fake_falha_so_nas_chamadas_escolhidas(self, rubrica):
        fake = JulgadorFake(falhar_nas=(1,))
        with pytest.raises(RuntimeError, match="chamada 1"):
            fake.julgar(self.ESTADO, rubrica)
        assert fake.julgar(self.ESTADO, rubrica)["modelo"] == "jev-fake"

    def test_o_fake_registra_o_estado_antes_de_estourar(self, rubrica):
        fake = JulgadorFake(levanta=RuntimeError("caiu"))
        with pytest.raises(RuntimeError):
            fake.julgar(self.ESTADO, rubrica)
        assert fake.chamadas == [self.ESTADO]

    def test_o_transporte_ativo_devolve_vazio_quando_a_regua_nao_monta(
        self, rubrica, monkeypatch
    ):
        def explode(_estado):
            raise RuntimeError("régua inconsistente")

        monkeypatch.setattr(rubrica, "perguntas_para", explode)
        ativo = JulgadorJev("sk-chave", cliente=cliente_que_responde(resposta_da_api()))
        assert ativo.julgar(self.ESTADO, rubrica) == {}



class TestPedir:
    """`_pedir` só fecha o cliente que ele mesmo abriu."""

    def test_pedir_usa_o_cliente_injetado(self, rubrica):
        cliente = cliente_que_responde(resposta_da_api())
        julgador = JulgadorJev("sk-chave", cliente=cliente)
        assert julgador._pedir({"model": "m", "state": {}, "questions": {}})["model"]

    def test_pedir_nao_fecha_o_cliente_injetado(self, rubrica):
        cliente = cliente_que_responde(resposta_da_api())
        julgador = JulgadorJev("sk-chave", cliente=cliente)
        julgador._pedir({"model": "m", "state": {}, "questions": {}})
        assert not cliente.is_closed

    def test_pedir_nao_fecha_o_injetado_nem_quando_falha(self, rubrica):
        cliente = cliente_que_responde({}, status=401)
        julgador = JulgadorJev("sk-chave", cliente=cliente)
        with pytest.raises(httpx.HTTPStatusError):
            julgador._pedir({"model": "m", "state": {}, "questions": {}})
        assert not cliente.is_closed

    def test_pedir_nao_troca_o_erro_real_por_erro_de_encerramento(self, monkeypatch):
        """`close` que levanta dentro do `finally` apagaria a causa original."""
        pronto = cliente_que_responde({}, status=401)
        monkeypatch.setattr(type(pronto), "close", _explode_ao_fechar)
        monkeypatch.setattr(httpx, "Client", lambda **_k: pronto)
        with pytest.raises(httpx.HTTPStatusError):
            JulgadorJev("sk-chave")._pedir({"model": "m", "state": {}, "questions": {}})

    def test_pedir_nao_levanta_quando_so_o_fechamento_falha(self, monkeypatch):
        pronto = cliente_que_responde(resposta_da_api())
        monkeypatch.setattr(type(pronto), "close", _explode_ao_fechar)
        monkeypatch.setattr(httpx, "Client", lambda **_k: pronto)
        assert JulgadorJev("sk-chave")._pedir({"model": "m", "state": {}, "questions": {}})

    def test_pedir_fecha_o_cliente_que_ele_criou(self, monkeypatch):
        # O cliente é construído antes do monkeypatch: `cliente_que_responde`
        # também chama `httpx.Client`, e substituí-lo primeiro faria a fábrica
        # chamar a si mesma.
        pronto = cliente_que_responde(resposta_da_api())
        monkeypatch.setattr(httpx, "Client", lambda **_k: pronto)
        JulgadorJev("sk-chave")._pedir({"model": "m", "state": {}, "questions": {}})
        assert pronto.is_closed


class TestPedirComEspera:
    """O retry: recua só no que a API documenta como transitório."""

    def test_pedir_com_espera_devolve_o_json_no_primeiro_sucesso(self):
        julgador = JulgadorJev("sk-chave")
        cliente = cliente_que_responde(resposta_da_api())
        assert julgador._pedir_com_espera(cliente, {"model": "m"})["model"]

    def test_pedir_com_espera_repete_status_transitorio(self):
        esperas = []
        julgador = JulgadorJev("sk-chave", dormir=esperas.append, sortear=lambda: 0.0)
        cliente = cliente_que_responde(resposta_da_api(), status=[429, 200])
        assert julgador._pedir_com_espera(cliente, {"model": "m"})["model"]
        assert len(esperas) == 1

    def test_pedir_com_espera_nao_repete_status_definitivo(self):
        esperas = []
        julgador = JulgadorJev("sk-chave", dormir=esperas.append)
        cliente = cliente_que_responde({}, status=[401, 200])
        with pytest.raises(httpx.HTTPStatusError):
            julgador._pedir_com_espera(cliente, {"model": "m"})
        assert esperas == []

    def test_pedir_com_espera_nao_dorme_antes_de_desistir(self):
        """Dormir depois de decidir desistir só atrasa a resposta."""
        esperas = []
        julgador = JulgadorJev("sk-chave", max_tentativas=1, dormir=esperas.append)
        cliente = cliente_que_responde({}, status=429)
        with pytest.raises(httpx.HTTPStatusError):
            julgador._pedir_com_espera(cliente, {"model": "m"})
        assert esperas == []

    def test_pedir_com_espera_manda_a_chave_no_cabecalho(self):
        registro = []
        julgador = JulgadorJev("sk-chave")
        cliente = cliente_que_responde(resposta_da_api(), registro=registro)
        julgador._pedir_com_espera(cliente, {"model": "m"})
        assert registro[0].headers["Authorization"] == "Bearer sk-chave"

    def test_pedir_com_espera_respeita_o_teto_de_tentativas(self):
        esperas = []
        julgador = JulgadorJev(
            "sk-chave", max_tentativas=3, dormir=esperas.append, sortear=lambda: 0.0
        )
        cliente = cliente_que_responde({}, status=[429, 429, 429])
        with pytest.raises(httpx.HTTPStatusError):
            julgador._pedir_com_espera(cliente, {"model": "m"})
        assert len(esperas) == 2


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


class TestComoNumero:
    """`_como_numero` é a fronteira entre o JSON da API e a aritmética da nota."""

    def test_como_numero_converte_inteiro(self):
        assert _como_numero(2) == 2.0

    def test_como_numero_preserva_float(self):
        assert _como_numero(1.5) == 1.5

    def test_como_numero_recusa_booleano(self):
        """`true` viraria nota 1.0 — a melhor possível — escondendo contrato quebrado."""
        assert _como_numero(True) is None

    def test_como_numero_recusa_texto(self):
        assert _como_numero("1.5") is None

    def test_como_numero_recusa_none(self):
        assert _como_numero(None) is None

    def test_como_numero_recusa_nan(self):
        assert _como_numero(float("nan")) is None

    def test_como_numero_recusa_infinito(self):
        assert _como_numero(float("inf")) is None


class TestParaResposta:
    """`_para_resposta` devolve None em vez de levantar: perde a dimensão, não o resto."""

    def test_para_resposta_converte_um_score(self, rubrica):
        bruta = {"type": "score", "score": 1.5, "confidence": 0.8}
        assert _para_resposta(bruta, rubrica, "complexidade_cognitiva").bruto == 1.5

    def test_para_resposta_converte_um_noul(self, rubrica):
        assert _para_resposta({"type": "noul", "noul": 0.2}, rubrica, "injecao").bruto == 0.2

    def test_para_resposta_recusa_o_que_nao_e_mapa(self, rubrica):
        assert _para_resposta([1, 2], rubrica, "injecao") is None

    def test_para_resposta_recusa_dimensao_fora_da_regua(self, rubrica):
        assert _para_resposta({"noul": 0.2}, rubrica, "inventada") is None

    def test_para_resposta_recusa_valor_ausente(self, rubrica):
        assert _para_resposta({"type": "noul"}, rubrica, "injecao") is None

    def test_para_resposta_nao_inventa_confianca_para_noul(self, rubrica):
        bruta = {"type": "noul", "noul": 0.2, "confidence": 0.9}
        assert _para_resposta(bruta, rubrica, "injecao").confianca is None

    def test_para_resposta_descarta_a_dimensao_se_a_escala_falhar(self, rubrica, monkeypatch):
        monkeypatch.setattr(rubrica, "normalizar", _escala_degenerada)
        assert _para_resposta({"noul": 0.2}, rubrica, "injecao") is None

    def test_para_resposta_guarda_as_probabilidades_quando_vierem(self, rubrica):
        bruta = {"score": 1.0, "confidence": 0.5, "probabilities": {"0": 0.5, "2": 0.5}}
        convertida = _para_resposta(bruta, rubrica, "complexidade_cognitiva")
        assert convertida.probabilidades == {"0": 0.5, "2": 0.5}


class TestEspera:
    """A espera entre tentativas: exponencial, com jitter, mas com teto e piso."""

    def julgador(self, sortear=lambda: 0.0):
        return JulgadorJev("sk-chave", sortear=sortear, dormir=lambda _s: None)

    def test_espera_cresce_exponencialmente(self):
        julgador = self.julgador()
        assert julgador._espera(1) > julgador._espera(0)

    def test_espera_soma_o_jitter(self):
        com = self.julgador(sortear=lambda: 1.0)._espera(0)
        sem = self.julgador(sortear=lambda: 0.0)._espera(0)
        assert com > sem

    def test_espera_nunca_passa_do_teto(self):
        assert self.julgador()._espera(50) == ESPERA_MAXIMA_SEGUNDOS

    def test_espera_nunca_e_negativa(self):
        """`time.sleep` de valor negativo levanta, e apareceria como erro de rede."""
        assert self.julgador(sortear=lambda: -1000.0)._espera(0) >= 0.0

    def test_espera_com_sorteio_nao_finito_cai_no_teto(self):
        assert self.julgador(sortear=lambda: float("inf"))._espera(0) == ESPERA_MAXIMA_SEGUNDOS


class TestObterJulgadorNaoLevanta:
    def test_obter_julgador_com_chave_invalida_desliga_com_motivo(self, monkeypatch):
        monkeypatch.setattr(
            "jev_crap.julgamento.jev.JulgadorJev",
            _fabrica_que_recusa,
        )
        julgador = obter_julgador(ambiente={"TYPESAFE_API_KEY": "sk-x"})
        assert julgador.ativo is False
        assert "inválida" in julgador.motivo

    def test_obter_julgador_nunca_levanta(self, monkeypatch):
        monkeypatch.setattr("jev_crap.julgamento.jev.JulgadorJev", _fabrica_que_recusa)
        assert obter_julgador(ambiente={"TYPESAFE_API_KEY": "sk-x"}) is not None


def _fabrica_que_recusa(*_a, **_k):
    raise ValueError("chave com formato inesperado")


class TestJulgadorDesligadoConfere:
    """O eixo desligado é o caminho mais exercitado: ele não pode ser um buraco."""

    ESTADO = {"codigo": "def f(): return 1", "linguagem": "python"}

    def test_desligado_devolve_vazio_com_estado_bom(self, rubrica):
        assert JulgadorDesligado().julgar(self.ESTADO, rubrica) == {}

    def test_desligado_avisa_no_log_sobre_estado_sem_codigo(self, rubrica, caplog):
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            JulgadorDesligado().julgar({"linguagem": "python"}, rubrica)
        assert "sem `codigo`" in caplog.text

    def test_desligado_avisa_sobre_estado_que_nao_e_mapa(self, rubrica, caplog):
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            JulgadorDesligado().julgar(["codigo"], rubrica)
        assert "sem `codigo`" in caplog.text

    def test_desligado_avisa_sobre_regua_sem_perguntas_para(self, caplog):
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            JulgadorDesligado().julgar(self.ESTADO, object())
        assert "perguntas_para" in caplog.text

    def test_desligado_nao_levanta_com_nada_disso(self, rubrica):
        assert JulgadorDesligado().julgar(None, None) == {}

    def test_desligado_nao_avisa_quando_esta_tudo_certo(self, rubrica, caplog):
        with caplog.at_level(logging.WARNING, logger="jev_crap.julgamento.jev"):
            JulgadorDesligado().julgar(self.ESTADO, rubrica)
        assert caplog.text == ""
