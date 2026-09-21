"""O que o registro de episódios faz quando o mundo não colabora.

O histórico é opcional por desenho: nenhuma falha aqui pode impedir uma
avaliação de acontecer. Os testes deste arquivo são quase todos sobre isso —
disco sem permissão, linha corrompida, carimbo fora de formato, `notas` que não
vira JSON.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path

import pytest

from jev_crap.aprendizado.episodio import (
    NOME_ARQUIVO,
    NOME_DIRETORIO,
    TAMANHO_DO_CARIMBO,
    VARIAVEL_CAMINHO,
    Episodio,
    Repositorio,
    _cwd_ou_ponto,
    _expandir,
    agora_iso,
    caminho_padrao,
)
from jev_crap.situacoes import SituacaoConhecida


def episodio(**mudancas) -> Episodio:
    base = dict(
        id="e1", em="2026-01-01T00:00:00Z", arquivo="src/a.py", funcao="f",
        risco=40.0, formula="crap", limiar_vigente=30.0, complexidade=8,
        cobertura_linha=0.5, cobertura_branch=0.4,
    )
    return Episodio(**{**base, **mudancas})


def _relogio_que_devolve(texto: str):
    """Um substituto de `datetime` cujo `isoformat` devolve exatamente `texto`."""

    class Momento:
        @staticmethod
        def isoformat(timespec: str = "seconds") -> str:
            return texto

    class Relogio:
        @staticmethod
        def now(_tz=None) -> Momento:
            return Momento()

    return Relogio


class TestAgoraIso:
    def test_agora_iso_termina_em_z(self):
        assert agora_iso().endswith("Z")

    def test_agora_iso_tem_largura_fixa(self):
        assert len(agora_iso()) == TAMANHO_DO_CARIMBO

    def test_agora_iso_nao_traz_offset_numerico(self):
        assert "+" not in agora_iso()

    def test_agora_iso_ordena_lexicograficamente_como_no_tempo(self):
        antigo = "2020-01-01T00:00:00Z"
        assert antigo < agora_iso()

    def test_agora_iso_e_parseavel_como_iso(self):
        assert datetime.fromisoformat(agora_iso().replace("Z", "+00:00"))

    def test_agora_iso_recusa_carimbo_com_offset_que_nao_e_utc(self, monkeypatch):
        """O `replace('+00:00','Z')` não faria nada, e a string ordenaria antes de tudo."""
        monkeypatch.setattr(
            "jev_crap.aprendizado.episodio.datetime",
            _relogio_que_devolve("2026-01-01T00:00:00+05:00"),
        )
        with pytest.raises(ValueError, match="largura fixa terminada em Z"):
            agora_iso()

    def test_agora_iso_recusa_carimbo_com_microssegundos(self, monkeypatch):
        """Termina em Z mas ordena diferente de um carimbo sem microssegundos."""
        monkeypatch.setattr(
            "jev_crap.aprendizado.episodio.datetime",
            _relogio_que_devolve("2026-01-01T00:00:00.000001+00:00"),
        )
        with pytest.raises(ValueError, match="fora do formato esperado"):
            agora_iso()


class TestParaLinhaDeHistorico:
    def test_para_linha_de_historico_traz_todos_os_campos(self):
        corpo = episodio().para_linha_de_historico()
        assert corpo["arquivo"] == "src/a.py"
        assert corpo["risco"] == 40.0

    def test_para_linha_de_historico_e_serializavel(self):
        assert json.dumps(episodio().para_linha_de_historico())

    def test_para_linha_de_historico_marca_notas_nao_copiaveis(self):
        ciclica: dict = {}
        ciclica["eu"] = ciclica
        ep = episodio()
        ep.notas = ciclica
        assert "_ilegivel" in ep.para_linha_de_historico()["notas"]

    def test_para_linha_de_historico_preserva_escalares_quando_notas_falha(self):
        ciclica: dict = {}
        ciclica["eu"] = ciclica
        ep = episodio()
        ep.notas = ciclica
        assert ep.para_linha_de_historico()["risco"] == 40.0


class TestDeDict:
    def test_de_dict_ignora_chave_desconhecida(self):
        bruto = {**episodio().para_linha_de_historico(), "campo_do_futuro": 1}
        assert Episodio.de_dict(bruto).arquivo == "src/a.py"

    def test_de_dict_recusa_lista(self):
        with pytest.raises(TypeError, match="objeto JSON"):
            Episodio.de_dict([1, 2])

    def test_de_dict_recusa_numero(self):
        with pytest.raises(TypeError, match="veio int"):
            Episodio.de_dict(3)

    def test_de_dict_reconstroi_o_que_para_linha_de_historico_produziu(self):
        original = episodio()
        assert Episodio.de_dict(original.para_linha_de_historico()) == original


class TestExpandir:
    def test_expandir_resolve_o_til(self):
        assert not str(_expandir("~/x")).startswith("~")

    def test_expandir_preserva_caminho_absoluto(self):
        assert _expandir("/tmp/x") == Path("/tmp/x")

    def test_expandir_devolve_literal_quando_nao_ha_home(self, monkeypatch):
        def explode(_self):
            raise RuntimeError("sem home")

        monkeypatch.setattr(Path, "expanduser", explode)
        assert _expandir("~/x") == Path("~/x")


class TestCwdOuPonto:
    def test_cwd_ou_ponto_devolve_o_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _cwd_ou_ponto() == Path.cwd()

    def test_cwd_ou_ponto_cai_no_ponto_quando_o_cwd_sumiu(self, monkeypatch):
        def explode():
            raise FileNotFoundError("workspace apagado")

        monkeypatch.setattr(Path, "cwd", explode)
        assert _cwd_ou_ponto() == Path(".")


class TestCaminhoPadrao:
    def test_caminho_padrao_usa_a_raiz_do_projeto(self, tmp_path, monkeypatch):
        monkeypatch.delenv(VARIAVEL_CAMINHO, raising=False)
        assert caminho_padrao(tmp_path) == tmp_path / NOME_DIRETORIO / NOME_ARQUIVO

    def test_caminho_padrao_respeita_a_variavel_de_ambiente(self, tmp_path, monkeypatch):
        monkeypatch.setenv(VARIAVEL_CAMINHO, str(tmp_path / "outro.jsonl"))
        assert caminho_padrao(tmp_path) == tmp_path / "outro.jsonl"

    def test_caminho_padrao_ignora_variavel_vazia(self, tmp_path, monkeypatch):
        monkeypatch.setenv(VARIAVEL_CAMINHO, "   ")
        assert caminho_padrao(tmp_path).name == NOME_ARQUIVO

    def test_caminho_padrao_nao_levanta_sem_cwd(self, monkeypatch):
        monkeypatch.delenv(VARIAVEL_CAMINHO, raising=False)

        def explode():
            raise OSError("sumiu")

        monkeypatch.setattr(Path, "cwd", explode)
        assert caminho_padrao().name == NOME_ARQUIVO

    def test_caminho_padrao_nao_cria_nada_em_disco(self, tmp_path, monkeypatch):
        monkeypatch.delenv(VARIAVEL_CAMINHO, raising=False)
        caminho_padrao(tmp_path)
        assert not (tmp_path / NOME_DIRETORIO).exists()


class TestRepositorioInit:
    """O contrato de `Repositorio.__init__`: escolhe o lugar, não toca em disco."""

    def test_init_aceita_caminho_explicito(self, tmp_path):
        alvo = tmp_path / "h.jsonl"
        assert Repositorio.__init__ and Repositorio(alvo).caminho == alvo

    def test_init_cai_no_padrao_sem_caminho(self, tmp_path, monkeypatch):
        monkeypatch.delenv(VARIAVEL_CAMINHO, raising=False)
        assert Repositorio(raiz_projeto=tmp_path).caminho.name == NOME_ARQUIVO

    def test_init_recusa_caminho_vazio(self):
        with pytest.raises(ValueError, match="não pode ser vazio"):
            Repositorio("   ")

    def test_init_nao_toca_em_disco(self, tmp_path):
        Repositorio(tmp_path / "sub" / "h.jsonl")
        assert not (tmp_path / "sub").exists()

    def test_init_comeca_sem_linhas_invalidas(self, tmp_path):
        assert Repositorio(tmp_path / "h.jsonl").linhas_invalidas == 0

    def test_init_recebe_caminho_e_raiz_projeto(self):
        assinatura = inspect.signature(Repositorio.__init__)
        assert list(assinatura.parameters) == ["self", "caminho", "raiz_projeto"]

    def test_init_exige_raiz_projeto_como_nomeado(self):
        assinatura = inspect.signature(Repositorio.__init__)
        parametro = assinatura.parameters["raiz_projeto"]
        assert parametro.kind is inspect.Parameter.KEYWORD_ONLY

    def test_init_pode_ser_chamado_sem_argumento_nenhum(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(VARIAVEL_CAMINHO, raising=False)
        assert Repositorio().caminho.name == NOME_ARQUIVO


class TestAnexar:
    """`_anexar` é a única função do módulo que escreve em disco."""

    def test_anexar_cria_o_diretorio_na_primeira_gravacao(self, tmp_path):
        repo = Repositorio(tmp_path / "sub" / "h.jsonl")
        repo._anexar(episodio())
        assert (tmp_path / "sub" / "h.jsonl").exists()

    def test_anexar_escreve_uma_linha_por_episodio(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        repo._anexar(episodio(id="a"))
        repo._anexar(episodio(id="b"))
        assert len(repo.caminho.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_anexar_escreve_json_com_chaves_ordenadas(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        repo._anexar(episodio())
        corpo = json.loads(repo.caminho.read_text(encoding="utf-8"))
        assert list(corpo) == sorted(corpo)

    def test_anexar_preserva_acento_sem_escapar(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        repo._anexar(episodio(conselho="refatoração"))
        assert "refatoração" in repo.caminho.read_text(encoding="utf-8")

    def test_anexar_nunca_reescreve_linha_anterior(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        repo._anexar(episodio(id="a", risco=10.0))
        primeira = repo.caminho.read_text(encoding="utf-8").splitlines()[0]
        repo._anexar(episodio(id="a", risco=99.0))
        assert repo.caminho.read_text(encoding="utf-8").splitlines()[0] == primeira

    def test_anexar_traduz_disco_sem_permissao(self, tmp_path, monkeypatch):
        repo = Repositorio(tmp_path / "h.jsonl")

        def explode(*_a, **_k):
            raise PermissionError("somente leitura")

        monkeypatch.setattr(Path, "open", explode)
        with pytest.raises(SituacaoConhecida, match="historico_nao_gravavel"):
            repo._anexar(episodio())

    def test_a_situacao_de_disco_diz_como_resolver(self, tmp_path, monkeypatch):
        repo = Repositorio(tmp_path / "h.jsonl")

        def explode(*_a, **_k):
            raise OSError("cheio")

        monkeypatch.setattr(Path, "open", explode)
        with pytest.raises(SituacaoConhecida) as erro:
            repo._anexar(episodio())
        assert VARIAVEL_CAMINHO in erro.value.como_resolver

    def test_anexar_traduz_episodio_nao_serializavel(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        ep = episodio()
        ep.notas = {"x": {"objeto": object()}}
        with pytest.raises(SituacaoConhecida, match="episodio_nao_serializavel"):
            repo._anexar(ep)

    def test_anexar_nao_deixa_arquivo_pela_metade_ao_falhar(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        ep = episodio()
        ep.notas = {"x": {"objeto": object()}}
        with pytest.raises(SituacaoConhecida):
            repo._anexar(ep)
        assert not repo.caminho.exists()
