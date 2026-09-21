"""O servidor visto de fora, por um cliente MCP de verdade.

O `Client` do FastMCP fala o protocolo inteiro em memória: listagem de tools,
schema de entrada, anotações, chamada e erro. É o mesmo caminho que um cliente
MCP externo percorre, sem subprocesso e sem rede — então o que passa aqui é o
que o agente vai encontrar.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from tests.conftest import RESPOSTAS_BOAS

from jev_crap.config import Config
from jev_crap.julgamento.jev import JulgadorDesligado, JulgadorFake
from jev_crap.server import criar_servidor

TOOLS = {
    "avaliar_arquivos",
    "avaliar_trecho",
    "medir_risco",
    "explicar_criterios",
    "registrar_episodio",
    "consultar_aprendizado",
}


@pytest.fixture
def servidor(config, rubrica):
    return criar_servidor(config, JulgadorFake(RESPOSTAS_BOAS), rubrica)


@pytest.fixture
async def cliente(servidor):
    async with Client(servidor) as c:
        yield c


class TestCriarServidor:
    async def test_criar_servidor_registra_as_seis_tools(self, config, rubrica):
        """Conferido no objeto devolvido, não através do protocolo.

        `TestSuperficie` faz a mesma pergunta ao `Client`, e as duas camadas
        valem: uma tool esquecida no registro é indistinguível de uma tool que o
        cliente não expõe, e só a asserção sobre o servidor recém-montado separa
        as duas — `is not None` não separava nenhuma.
        """
        servidor = criar_servidor(config, JulgadorDesligado(), rubrica)
        assert {tool.name for tool in await servidor.list_tools()} == TOOLS

    def test_criar_servidor_aceita_os_tres_por_injecao(self, config, rubrica):
        assert criar_servidor(config, JulgadorFake(), rubrica) is not None

    def test_criar_servidor_monta_sozinho_sem_argumento(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        assert criar_servidor() is not None

    def test_criar_servidor_nao_toca_em_disco(self, config, rubrica, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        criar_servidor(config, JulgadorDesligado(), rubrica)
        assert list(tmp_path.iterdir()) == []


class TestSuperficie:
    async def test_expoe_exatamente_as_seis_tools(self, cliente):
        assert {t.name for t in await cliente.list_tools()} == TOOLS

    async def test_toda_tool_tem_descricao_que_diz_quando_usar(self, cliente):
        """Quem lê é um modelo decidindo o próximo passo, não alguém procurando
        referência: sem o "quando", a tool é escolhida por adivinhação."""
        for tool in await cliente.list_tools():
            assert tool.description
            assert "Use " in tool.description, tool.name

    async def test_todo_parametro_tem_descricao(self, cliente):
        """O schema de entrada é como o agente acerta a chamada na primeira
        tentativa; parâmetro sem descrição vira argumento chutado."""
        for tool in await cliente.list_tools():
            for nome, campo in tool.input_schema.get("properties", {}).items():
                assert campo.get("description"), f"{tool.name}.{nome}"

    async def test_as_anotacoes_separam_leitura_de_escrita(self, cliente):
        """Uma tool de leitura marcada como escrita ensina o usuário a confirmar
        no automático, que é como a confirmação deixa de proteger."""
        por_nome = {t.name: t.annotations for t in await cliente.list_tools()}
        assert por_nome["registrar_episodio"].read_only_hint is False
        for nome in TOOLS - {"registrar_episodio"}:
            assert por_nome[nome].read_only_hint is True, nome

    async def test_as_anotacoes_separam_quem_chama_api_paga(self, cliente):
        por_nome = {t.name: t.annotations for t in await cliente.list_tools()}
        assert por_nome["avaliar_arquivos"].open_world_hint is True
        assert por_nome["avaliar_trecho"].open_world_hint is True
        assert por_nome["medir_risco"].open_world_hint is False
        assert por_nome["explicar_criterios"].open_world_hint is False

    async def test_registrar_episodio_nao_se_declara_idempotente(self, cliente):
        """Duas chamadas iguais produzem dois episódios, e proporções calculadas
        sobre repetição enviesam a régua."""
        por_nome = {t.name: t.annotations for t in await cliente.list_tools()}
        assert por_nome["registrar_episodio"].idempotent_hint is False
        assert por_nome["registrar_episodio"].destructive_hint is False


class TestMedirRisco:
    async def test_devolve_o_eixo_contavel(self, cliente, projeto):
        r = await cliente.call_tool("medir_risco", {"caminhos": [str(projeto / "src")]})
        assert r.data["resumo"]["funcoes_medidas"] == 2
        assert all("interpretacao" in f for f in r.data["funcoes"])

    async def test_diz_o_que_nao_responde(self, cliente, projeto):
        """É o campo que impede a tool barata de ser confundida com a completa."""
        r = await cliente.call_tool("medir_risco", {"caminhos": [str(projeto / "src")]})
        assert "escrever teste e refatorar" in r.data["o_que_isto_nao_responde"]

    async def test_nao_chama_o_julgador(self, config, rubrica, projeto):
        """É a tool que precisa custar zero: CI e varredura ampla dependem disso."""
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        async with Client(criar_servidor(config, julgador, rubrica)) as c:
            await c.call_tool("medir_risco", {"caminhos": [str(projeto / "src")]})
        assert julgador.chamadas == []

    async def test_funciona_sem_chave_de_api(self, config, rubrica, projeto):
        async with Client(criar_servidor(config, JulgadorDesligado(), rubrica)) as c:
            r = await c.call_tool("medir_risco", {"caminhos": [str(projeto / "src")]})
        assert r.data["resumo"]["funcoes_medidas"] == 2


class TestAvaliarArquivos:
    async def test_cruza_os_dois_eixos(self, cliente, projeto):
        r = await cliente.call_tool(
            "avaliar_arquivos",
            {"caminhos": [str(projeto / "src")], "cobertura": str(projeto / "lcov.info"),
             "testes": str(projeto / "tests"), "limiar": 0},
        )
        assert r.data["resumo"]["julgadas"] == 2
        assert r.data["eixo_semantico"]["ligado"] is True
        assert all(f["nota"] is not None for f in r.data["funcoes"])

    async def test_com_julgamento_falso_nao_chama_a_api(self, config, rubrica, projeto):
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        async with Client(criar_servidor(config, julgador, rubrica)) as c:
            r = await c.call_tool(
                "avaliar_arquivos",
                {"caminhos": [str(projeto / "src")], "com_julgamento": False},
            )
        assert julgador.chamadas == []
        assert r.data["eixo_semantico"]["ligado"] is False

    async def test_o_relatorio_ensina_a_ler_a_si_mesmo(self, cliente, projeto):
        r = await cliente.call_tool("avaliar_arquivos", {"caminhos": [str(projeto / "src")]})
        assert set(r.data["como_ler"]) >= {"risco", "nota", "graves", "duvidas", "conselho"}

    async def test_nao_publica_nota_agregada_do_projeto(self, cliente, projeto):
        """Ninguém conserta uma média; conserta-se uma função. E média vira meta
        de planilha, que se persegue pelo caminho mais barato — o pior deles."""
        r = await cliente.call_tool("avaliar_arquivos", {"caminhos": [str(projeto / "src")]})
        texto = json.dumps(r.data, ensure_ascii=False).lower()
        assert "nota_media" not in texto
        assert "nota_do_projeto" not in texto
        assert "saude" not in texto


class TestAvaliarTrecho:
    CODIGO = "def cobrar(v, c):\n    if v <= 0:\n        raise ValueError()\n    return v * c\n"

    async def test_julga_o_texto_recebido(self, cliente):
        r = await cliente.call_tool("avaliar_trecho", {"codigo": self.CODIGO,
                                                       "arquivo": "cobranca.py"})
        assert r.data["funcao"]["funcao"] == "cobrar"
        assert r.data["funcao"]["complexidade"] == 2

    async def test_sem_chave_devolve_erro_que_ensina_a_alternativa(
        self, config, rubrica
    ):
        async with Client(criar_servidor(config, JulgadorDesligado(), rubrica)) as c:
            with pytest.raises(ToolError) as erro:
                await c.call_tool("avaliar_trecho", {"codigo": self.CODIGO})
        assert "eixo_semantico_desligado" in str(erro.value)
        assert "medir_risco" in str(erro.value)


class TestExplicarCriterios:
    async def test_mostra_a_regua_inteira(self, cliente, rubrica):
        r = await cliente.call_tool("explicar_criterios", {})
        assert set(r.data["rubrica"]["dimensoes"]) == set(rubrica.dimensoes)

    async def test_explica_o_que_cada_grupo_faz_com_o_resultado(self, cliente):
        assert set((await cliente.call_tool("explicar_criterios", {})).data["grupos"]) == {
            "qualidade", "contexto", "risco_grave", "risco_atencao"
        }

    async def test_publica_os_limiares_em_vigor(self, cliente):
        limiares = (await cliente.call_tool("explicar_criterios", {})).data["limiares"]
        assert limiares["bloqueio"] == 0.8
        assert limiares["limiar_de_risco"] == 30.0


class TestAprendizado:
    async def test_registra_e_devolve_o_id(self, cliente):
        r = await cliente.call_tool(
            "registrar_episodio",
            {"arquivo": "src/a.py", "funcao": "f", "risco": 45.0,
             "conselho": "escrever teste", "veredito": "revisar", "nota": 62.0,
             "aceita": False},
        )
        assert r.data["situacao"] == "episodio_registrado"
        assert r.data["id_episodio"]
        assert r.data["historico"]["episodios"] == 1

    async def test_episodio_incompleto_diz_o_que_falta(self, cliente):
        with pytest.raises(ToolError) as erro:
            await cliente.call_tool("registrar_episodio", {"funcao": "f"})
        assert "episodio_incompleto" in str(erro.value)
        assert "arquivo, funcao e risco" in str(erro.value)

    async def test_desfecho_preserva_os_numeros_medidos_na_epoca(self, cliente):
        """Medição não se corrige com informação que ainda não existia."""
        criado = await cliente.call_tool(
            "registrar_episodio",
            {"arquivo": "src/a.py", "funcao": "f", "risco": 45.0, "nota": 62.0},
        )
        r = await cliente.call_tool(
            "registrar_episodio",
            {"id_episodio": criado.data["id_episodio"], "defeito": True, "aceita": False},
        )
        assert r.data["situacao"] == "desfecho_registrado"
        assert r.data["defeito"] is True
        assert r.data["historico"]["episodios"] == 1  # o mesmo episódio, não um novo

    async def test_id_desconhecido_nao_cria_episodio_orfao(self, cliente):
        with pytest.raises(ToolError) as erro:
            await cliente.call_tool("registrar_episodio", {"id_episodio": "nao-existe"})
        assert "episodio_desconhecido" in str(erro.value)

    async def test_historico_curto_recusa_concluir(self, cliente):
        """Com 5 avaliações, "60% foram ignoradas" quer dizer "3 de 5"."""
        for i in range(5):
            await cliente.call_tool(
                "registrar_episodio",
                {"arquivo": f"a{i}.py", "funcao": "f", "risco": 40.0, "aceita": False},
            )
        r = await cliente.call_tool("consultar_aprendizado", {})
        assert r.data["situacao"] == "ainda_sem_base"
        assert r.data["propostas"] == []
        assert r.data["metricas"]["total"] == 5  # as métricas vêm mesmo assim

    async def test_defeito_abaixo_do_limiar_autoriza_baixar(self, cliente):
        """É a única evidência que revela falso negativo — e basta uma."""
        for i in range(30):
            criado = await cliente.call_tool(
                "registrar_episodio",
                {"arquivo": f"a{i}.py", "funcao": "f", "risco": 10.0, "limiar": 30.0,
                 "formula": "crap", "aceita": True, "acao": "nada"},
            )
            if i == 0:
                await cliente.call_tool(
                    "registrar_episodio",
                    {"id_episodio": criado.data["id_episodio"], "defeito": True},
                )
        r = await cliente.call_tool("consultar_aprendizado", {})
        tipos = {p["tipo"] for p in r.data["propostas"]}
        assert "baixar_limiar" in tipos
        proposta = next(p for p in r.data["propostas"] if p["tipo"] == "baixar_limiar")
        assert proposta["evidencia"]["defeitos_abaixo_do_limiar"] == 1

    async def test_nada_e_aplicado_sozinho(self, cliente, config):
        """Uma ferramenta que recalibra os próprios critérios acaba provando que
        está certa contra um alvo que ela mesma moveu."""
        antes = config.limiar_efetivo(config.obter_formula())
        for i in range(30):
            await cliente.call_tool(
                "registrar_episodio",
                {"arquivo": f"a{i}.py", "funcao": "f", "risco": 10.0, "aceita": True},
            )
        await cliente.call_tool("consultar_aprendizado", {})
        assert config.limiar_efetivo(config.obter_formula()) == antes


class TestErros:
    async def test_situacao_conhecida_vira_erro_com_como_resolver(self, cliente):
        with pytest.raises(ToolError) as erro:
            await cliente.call_tool("medir_risco", {"caminhos": ["/nao/existe/mesmo"]})
        texto = str(erro.value)
        assert "caminho_inexistente" in texto
        assert "Como resolver:" in texto

    async def test_nenhuma_excecao_crua_atravessa_a_fronteira(self, cliente, monkeypatch):
        """Exceção que sobe vira erro de protocolo sem texto útil, e quem está
        do outro lado fica com uma tool que "não funciona" e nenhuma pista."""
        from jev_crap import avaliacao

        monkeypatch.setattr(
            avaliacao, "medir", lambda *a, **k: (_ for _ in ()).throw(ZeroDivisionError("ops"))
        )
        with pytest.raises(ToolError) as erro:
            await cliente.call_tool("medir_risco", {"caminhos": ["."]})
        assert "falha_inesperada" in str(erro.value)
        assert "defeito do jev-crap" in str(erro.value)

    async def test_parametro_invalido_e_barrado_pelo_schema(self, cliente):
        """Validação no schema é mais barata que validação no corpo da tool, e o
        agente recebe o erro antes de a chamada custar qualquer coisa."""
        with pytest.raises(ToolError, match="too_short|Invalid|validation"):
            await cliente.call_tool("avaliar_arquivos", {"caminhos": []})


class TestInjecaoParaTeste:
    async def test_o_servidor_nao_le_o_ambiente_quando_recebe_configuracao(
        self, monkeypatch, rubrica, tmp_path
    ):
        """Variável de ambiente é estado global; teste que a altera passa a
        depender da ordem em que roda."""
        monkeypatch.setenv("JEV_CRAP_LIMIAR", "999")
        config = Config(raiz=tmp_path, limiar=12.0)
        async with Client(criar_servidor(config, JulgadorFake(RESPOSTAS_BOAS), rubrica)) as c:
            r = await c.call_tool("explicar_criterios", {})
        assert r.data["limiares"]["limiar_de_risco"] == 12.0
