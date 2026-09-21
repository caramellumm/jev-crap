"""Os auxiliares do servidor MCP, exercitados fora do transporte.

O que eles têm em comum é o destinatário: quem lê a resposta de uma tool é um
modelo decidindo o próximo passo, e não uma pessoa olhando um traceback. Por
isso nenhum deles deixa subir exceção de biblioteca, e todos preferem uma
resposta parcial e explicada a nenhuma resposta.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp.exceptions import ToolError

from jev_crap.aprendizado.episodio import Episodio, Repositorio
from jev_crap.config import Config
from jev_crap.server import (
    ERRO_DE_CONFIGURACAO,
    SEM_CONTAGEM,
    _anexar_desfecho,
    _confirmacao,
    _consultar,
    _erro,
    _gravar_episodio_novo,
    _normalizar_notas,
    _registrar,
    main,
)
from jev_crap.situacoes import SituacaoConhecida


def episodio(**mudancas) -> Episodio:
    base = dict(
        id="e1", em="2026-01-01T00:00:00Z", arquivo="src/a.py", funcao="f",
        risco=40.0, formula="crap", limiar_vigente=30.0, complexidade=8,
        cobertura_linha=0.5, cobertura_branch=0.4,
    )
    return Episodio(**{**base, **mudancas})


def _montagem_com_regua_ilegivel():
    """Uma montagem que falha por configuração — régua ilegível é o caso comum."""
    raise SituacaoConhecida("regua_ilegivel", "o JSON não abre", "conserte a régua")


def _montagem_que_explode():
    """Uma montagem que falha por defeito nosso, não por configuração."""
    raise RuntimeError("dependência faltando")


def _carregar_que_explode(*_a, **_k):
    """Um `carregar` que falha por disco — o que acontece depois da gravação."""
    raise PermissionError("histórico ilegível")


class TestErro:
    """`_erro` traduz exceção em texto acionável; ninguém do outro lado lê traceback."""

    def test_erro_traduz_situacao_conhecida_preservando_o_como_resolver(self):
        conhecida = SituacaoConhecida("cobertura_inexistente", "não há relatório", "rode --cov")
        assert "rode --cov" in str(_erro(conhecida))

    def test_erro_traduz_situacao_conhecida_preservando_o_nome(self):
        conhecida = SituacaoConhecida("cobertura_inexistente", "x", "y")
        assert "cobertura_inexistente" in str(_erro(conhecida))

    def test_erro_devolve_tool_error(self):
        assert isinstance(_erro(SituacaoConhecida("x", "y", "z")), ToolError)

    def test_erro_assume_a_culpa_por_excecao_inesperada(self):
        """Defeito nosso chega dizendo que é defeito nosso, não da configuração."""
        assert "defeito do jev-crap" in str(_erro(RuntimeError("caiu")))

    def test_erro_nomeia_o_tipo_da_excecao_inesperada(self):
        assert "RuntimeError" in str(_erro(RuntimeError("caiu")))

    def test_erro_registra_o_traceback_do_inesperado(self, caplog):
        with caplog.at_level(logging.ERROR, logger="jev_crap.server"):
            _erro(RuntimeError("caiu"))
        assert "falha inesperada" in caplog.text

    def test_erro_nao_registra_traceback_de_situacao_conhecida(self, caplog):
        with caplog.at_level(logging.ERROR, logger="jev_crap.server"):
            _erro(SituacaoConhecida("x", "y", "z"))
        assert caplog.text == ""


class TestNormalizarNotas:
    """As notas chegam do cliente MCP em dois formatos, e saem num só."""

    def test_normalizar_notas_preserva_o_bloco_do_relatorio(self):
        bruto = {"teste_verifica": {"normalizado": 0.4, "confianca": 0.9}}
        assert _normalizar_notas(bruto)["teste_verifica"]["normalizado"] == 0.4

    def test_normalizar_notas_aceita_numero_solto(self):
        assert _normalizar_notas({"teste_verifica": 0.4}) == {
            "teste_verifica": {"normalizado": 0.4, "confianca": None}
        }

    def test_normalizar_notas_de_none_e_vazio(self):
        assert _normalizar_notas(None) == {}

    def test_normalizar_notas_de_lista_e_vazio(self):
        """Recusar o registro inteiro perderia risco, limiar e desfecho."""
        assert _normalizar_notas([1, 2]) == {}

    def test_normalizar_notas_descarta_booleano(self):
        assert _normalizar_notas({"x": True}) == {}

    def test_normalizar_notas_descarta_texto(self):
        assert _normalizar_notas({"x": "alta"}) == {}

    def test_normalizar_notas_descarta_nan(self):
        assert _normalizar_notas({"x": float("nan")}) == {}

    def test_normalizar_notas_descarta_infinito(self):
        assert _normalizar_notas({"x": float("inf")}) == {}

    def test_normalizar_notas_nunca_levanta(self):
        for bruto in (None, [1], "x", 3, {"a": object()}, {"a": float("nan")}):
            assert isinstance(_normalizar_notas(bruto), dict)


class TestConfirmacao:
    """A gravação já aconteceu: nada aqui pode fazer a tool responder erro."""

    def repo(self, tmp_path) -> Repositorio:
        return Repositorio(tmp_path / "historico.jsonl")

    def test_confirmacao_devolve_o_id_para_anexar_o_desfecho(self, tmp_path):
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())
        assert _confirmacao(repo, gravado, "episodio_registrado")["id_episodio"] == "e1"

    def test_confirmacao_conta_o_historico(self, tmp_path):
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())
        assert _confirmacao(repo, gravado, "x")["historico"]["episodios"] == 1

    def test_confirmacao_diz_quantos_faltam_para_propor(self, tmp_path):
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())
        assert _confirmacao(repo, gravado, "x")["historico"]["faltam_para_propor"] > 0

    def test_confirmacao_nao_falha_quando_o_historico_fica_ilegivel(
        self, tmp_path, monkeypatch
    ):
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())

        def explode():
            raise PermissionError("permissão trocada depois da escrita")

        monkeypatch.setattr(repo, "carregar", explode)
        assert _confirmacao(repo, gravado, "x")["id_episodio"] == "e1"

    def test_confirmacao_marca_a_contagem_como_indisponivel(self, tmp_path, monkeypatch):
        """Zero seria lido como 'histórico vazio', o oposto de 'não consegui contar'."""
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())
        monkeypatch.setattr(repo, "carregar", _carregar_que_explode)
        assert _confirmacao(repo, gravado, "x")["historico"]["episodios"] == SEM_CONTAGEM

    def test_confirmacao_registra_no_log_quando_nao_releu(self, tmp_path, monkeypatch, caplog):
        repo = self.repo(tmp_path)
        gravado = repo.registrar(episodio())
        monkeypatch.setattr(repo, "carregar", _carregar_que_explode)
        with caplog.at_level(logging.WARNING, logger="jev_crap.server"):
            _confirmacao(repo, gravado, "x")
        assert "gravado" in caplog.text


class TestConsultar:
    def test_consultar_sem_historico_diz_que_ainda_nao_ha_base(self, tmp_path):
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        assert _consultar(config)["situacao"] == "ainda_sem_base"

    def test_consultar_explica_por_que_nao_ha_base(self, tmp_path):
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        assert "mínimo" in _consultar(config)["explicacao"]

    def test_consultar_devolve_as_metricas_mesmo_sem_base(self, tmp_path):
        """Esconder os números convidaria a registrar episódios só para destravar."""
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        assert "metricas" in _consultar(config)

    def test_consultar_nao_aplica_nada_sozinho(self, tmp_path):
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        assert isinstance(_consultar(config)["propostas"], list)

    def test_consultar_nao_cria_o_historico(self, tmp_path):
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "sub" / "h.jsonl")
        _consultar(config)
        assert not (tmp_path / "sub").exists()

    def test_consultar_nao_falha_com_historico_ilegivel(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Repositorio, "carregar", _carregar_que_explode)
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        assert _consultar(config)["situacao"] == "ainda_sem_base"

    def test_consultar_registra_no_log_o_historico_ilegivel(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setattr(Repositorio, "carregar", _carregar_que_explode)
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        with caplog.at_level(logging.WARNING, logger="jev_crap.server"):
            _consultar(config)
        assert "ilegível" in caplog.text

    def test_consultar_conta_as_linhas_invalidas_como_aviso(self, tmp_path):
        alvo = tmp_path / "h.jsonl"
        alvo.write_text('{"id": "a"}\nnão é json\n', encoding="utf-8")
        config = Config(raiz=tmp_path, caminho_episodios=alvo)
        assert any("ilegíveis" in a for a in _consultar(config)["avisos"])


class TestMain:
    """O cliente MCP descarta o que um processo filho imprime sem estrutura."""

    def test_main_sobe_o_servidor(self, monkeypatch):
        rodou = {"sim": False}

        class Servidor:
            def run(self, **_k):
                rodou["sim"] = True

        monkeypatch.setattr("jev_crap.server.criar_servidor", lambda: Servidor())
        main()
        assert rodou["sim"]

    def test_main_traduz_situacao_conhecida_na_montagem(self, monkeypatch, capsys):
        monkeypatch.setattr("jev_crap.server.criar_servidor", _montagem_com_regua_ilegivel)
        with pytest.raises(SystemExit) as saida:
            main()
        assert saida.value.code == ERRO_DE_CONFIGURACAO
        assert "conserte a régua" in capsys.readouterr().err

    def test_main_nao_morre_calado_com_erro_inesperado(self, monkeypatch, capsys):
        monkeypatch.setattr("jev_crap.server.criar_servidor", _montagem_que_explode)
        with pytest.raises(SystemExit):
            main()
        assert "não subiu" in capsys.readouterr().err

    def test_main_aponta_o_diagnostico_no_erro_inesperado(self, monkeypatch, capsys):
        monkeypatch.setattr("jev_crap.server.criar_servidor", _montagem_que_explode)
        with pytest.raises(SystemExit):
            main()
        assert "--diagnostico" in capsys.readouterr().err

    def test_main_nao_imprime_nada_em_stdout(self, monkeypatch, capsys):
        """stdout é o canal do protocolo MCP: escrever lá corrompe a conversa."""

        monkeypatch.setattr("jev_crap.server.criar_servidor", _montagem_que_explode)
        with pytest.raises(SystemExit):
            main()
        assert capsys.readouterr().out == ""



class TestRegistrar:
    """O despacho entre os dois verbos: o id é o que separa um do outro."""

    def campos(self, **mudancas) -> dict:
        base = dict(
            arquivo="src/a.py", funcao="f", risco=40.0, conselho="escrever teste",
            veredito="revisar", nota=70.0, acao=None, aceita=None, complexidade=8,
            cobertura_linha=0.5, cobertura_branch=0.4, limiar=30.0, formula="crap",
            notas=None, risco_depois=None, defeito=None, id_episodio="",
        )
        return {**base, **mudancas}

    def config(self, tmp_path) -> Config:
        return Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")

    def test_registrar_grava_um_episodio_novo(self, tmp_path):
        resposta = _registrar(self.config(tmp_path), **self.campos())
        assert resposta["situacao"] == "episodio_registrado"

    def test_registrar_devolve_o_id_gerado(self, tmp_path):
        assert _registrar(self.config(tmp_path), **self.campos())["id_episodio"]

    def test_registrar_anexa_o_desfecho_de_um_episodio_existente(self, tmp_path):
        config = self.config(tmp_path)
        criado = _registrar(config, **self.campos())
        desfecho = _registrar(
            config, **self.campos(id_episodio=criado["id_episodio"], aceita=True)
        )
        assert desfecho["situacao"] == "desfecho_registrado"

    def test_registrar_desfecho_preserva_o_mesmo_id(self, tmp_path):
        """Duas linhas com o mesmo id: a última vence, a primeira fica no arquivo."""
        config = self.config(tmp_path)
        criado = _registrar(config, **self.campos())
        desfecho = _registrar(
            config, **self.campos(id_episodio=criado["id_episodio"], defeito=True)
        )
        assert desfecho["id_episodio"] == criado["id_episodio"]

    def test_registrar_recusa_id_que_nao_existe(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="episodio_desconhecido"):
            _registrar(self.config(tmp_path), **self.campos(id_episodio="inexistente"))

    def test_a_recusa_de_id_diz_onde_procurou(self, tmp_path):
        with pytest.raises(SituacaoConhecida) as erro:
            _registrar(self.config(tmp_path), **self.campos(id_episodio="inexistente"))
        assert "h.jsonl" in erro.value.como_resolver + str(erro.value.detalhes)

    def test_registrar_recusa_episodio_novo_sem_arquivo(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="episodio_incompleto"):
            _registrar(self.config(tmp_path), **self.campos(arquivo=""))

    def test_registrar_recusa_episodio_novo_sem_funcao(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="episodio_incompleto"):
            _registrar(self.config(tmp_path), **self.campos(funcao=""))

    def test_registrar_recusa_episodio_novo_sem_risco(self, tmp_path):
        with pytest.raises(SituacaoConhecida, match="episodio_incompleto"):
            _registrar(self.config(tmp_path), **self.campos(risco=None))

    def test_a_recusa_de_incompleto_aponta_os_dois_caminhos(self, tmp_path):
        with pytest.raises(SituacaoConhecida) as erro:
            _registrar(self.config(tmp_path), **self.campos(arquivo=""))
        assert "id_episodio" in erro.value.como_resolver

    def test_registrar_normaliza_as_notas_recebidas(self, tmp_path):
        config = self.config(tmp_path)
        _registrar(config, **self.campos(notas={"teste_verifica": 0.4}))
        gravado = config.repositorio().carregar()[0]
        assert gravado.notas["teste_verifica"]["normalizado"] == 0.4

    def test_registrar_nao_perde_o_registro_anterior(self, tmp_path):
        config = self.config(tmp_path)
        _registrar(config, **self.campos())
        _registrar(config, **self.campos(funcao="g"))
        assert len(config.repositorio().carregar()) == 2


class TestAnexarDesfecho:
    """Só os quatro campos de desfecho mudam; a medição da época fica como estava."""

    def repo_com_episodio(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        return repo, repo.registrar(episodio())

    def test_anexar_desfecho_marca_a_aceitacao(self, tmp_path):
        repo, gravado = self.repo_com_episodio(tmp_path)
        resposta = _anexar_desfecho(
            repo, gravado.id, acao="refatorei", aceita=True, risco_depois=12.0, defeito=None
        )
        assert resposta["aceita"] is True

    def test_anexar_desfecho_devolve_a_situacao_certa(self, tmp_path):
        repo, gravado = self.repo_com_episodio(tmp_path)
        resposta = _anexar_desfecho(
            repo, gravado.id, acao=None, aceita=False, risco_depois=None, defeito=None
        )
        assert resposta["situacao"] == "desfecho_registrado"

    def test_anexar_desfecho_preserva_a_medicao_original(self, tmp_path):
        """Medição não se corrige com informação que ainda não existia."""
        repo, gravado = self.repo_com_episodio(tmp_path)
        _anexar_desfecho(
            repo, gravado.id, acao=None, aceita=None, risco_depois=None, defeito=True
        )
        atual = next(ep for ep in repo.carregar() if ep.id == gravado.id)
        assert atual.risco == gravado.risco
        assert atual.limiar_vigente == gravado.limiar_vigente

    def test_anexar_desfecho_nao_reescreve_a_linha_anterior(self, tmp_path):
        repo, gravado = self.repo_com_episodio(tmp_path)
        antes = repo.caminho.read_text(encoding="utf-8").splitlines()[0]
        _anexar_desfecho(
            repo, gravado.id, acao=None, aceita=None, risco_depois=None, defeito=True
        )
        assert repo.caminho.read_text(encoding="utf-8").splitlines()[0] == antes

    def test_anexar_desfecho_recusa_id_desconhecido(self, tmp_path):
        repo, _ = self.repo_com_episodio(tmp_path)
        with pytest.raises(SituacaoConhecida, match="episodio_desconhecido"):
            _anexar_desfecho(
                repo, "de-outro-projeto", acao=None, aceita=None,
                risco_depois=None, defeito=None,
            )

    def test_a_recusa_traz_o_arquivo_onde_procurou(self, tmp_path):
        """O histórico é por repositório: id de outro projeto não existe aqui."""
        repo, _ = self.repo_com_episodio(tmp_path)
        with pytest.raises(SituacaoConhecida) as erro:
            _anexar_desfecho(
                repo, "x", acao=None, aceita=None, risco_depois=None, defeito=None
            )
        assert "h.jsonl" in str(erro.value.detalhes)


class TestGravarEpisodioNovo:
    """A fotografia de uma avaliação, com o que faltou preenchido da configuração."""

    def campos(self, **mudancas) -> dict:
        base = dict(
            arquivo="src/a.py", funcao="f", risco=40.0, conselho="escrever teste",
            veredito="revisar", nota=70.0, acao=None, aceita=None, complexidade=8,
            cobertura_linha=0.5, cobertura_branch=0.4, limiar=None, formula="",
            notas=None, risco_depois=None, defeito=None,
        )
        return {**base, **mudancas}

    def montar(self, tmp_path):
        config = Config(raiz=tmp_path, caminho_episodios=tmp_path / "h.jsonl")
        return config, config.repositorio()

    def test_gravar_episodio_novo_devolve_a_situacao_certa(self, tmp_path):
        config, repo = self.montar(tmp_path)
        resposta = _gravar_episodio_novo(config, repo, **self.campos())
        assert resposta["situacao"] == "episodio_registrado"

    def test_gravar_episodio_novo_preenche_o_limiar_da_configuracao(self, tmp_path):
        """Episódio sem limiar não diz se a função estava acima da régua da época."""
        config, repo = self.montar(tmp_path)
        _gravar_episodio_novo(config, repo, **self.campos(limiar=None))
        assert repo.carregar()[0].limiar_vigente > 0

    def test_gravar_episodio_novo_respeita_o_limiar_informado(self, tmp_path):
        config, repo = self.montar(tmp_path)
        _gravar_episodio_novo(config, repo, **self.campos(limiar=12.0))
        assert repo.carregar()[0].limiar_vigente == 12.0

    def test_gravar_episodio_novo_preenche_a_formula_da_configuracao(self, tmp_path):
        config, repo = self.montar(tmp_path)
        _gravar_episodio_novo(config, repo, **self.campos(formula=""))
        assert repo.carregar()[0].formula == config.formula

    def test_gravar_episodio_novo_eleva_complexidade_zero_a_um(self, tmp_path):
        """Zero indicaria função não medida — a mais segura de todas no histórico."""
        config, repo = self.montar(tmp_path)
        _gravar_episodio_novo(config, repo, **self.campos(complexidade=0))
        assert repo.carregar()[0].complexidade == 1

    def test_gravar_episodio_novo_recusa_sem_arquivo(self, tmp_path):
        config, repo = self.montar(tmp_path)
        with pytest.raises(SituacaoConhecida, match="episodio_incompleto"):
            _gravar_episodio_novo(config, repo, **self.campos(arquivo=""))

    def test_gravar_episodio_novo_recusa_sem_risco(self, tmp_path):
        config, repo = self.montar(tmp_path)
        with pytest.raises(SituacaoConhecida, match="episodio_incompleto"):
            _gravar_episodio_novo(config, repo, **self.campos(risco=None))

    def test_gravar_episodio_novo_normaliza_as_notas(self, tmp_path):
        config, repo = self.montar(tmp_path)
        _gravar_episodio_novo(config, repo, **self.campos(notas={"teste_verifica": 0.4}))
        assert repo.carregar()[0].notas["teste_verifica"]["normalizado"] == 0.4

    def test_gravar_episodio_novo_nao_grava_nada_ao_recusar(self, tmp_path):
        config, repo = self.montar(tmp_path)
        with pytest.raises(SituacaoConhecida):
            _gravar_episodio_novo(config, repo, **self.campos(funcao=""))
        assert not repo.caminho.exists()
