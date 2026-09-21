"""Testes do pacote raiz e do re-export preguiçoso dos subpacotes.

Duas garantias vivem aqui: `diagnostico` nunca levanta (é chamado quando algo
já deu errado) e `conferir_mapa` recusa um `__all__` que divergiu do mapa de
origens — divergência que, sem a conferência, só apareceria no `import *` de
outra pessoa.
"""

from __future__ import annotations

import builtins
from importlib import metadata

import pytest

import jev_crap
from jev_crap import (
    AUSENTE,
    DEPENDENCIAS,
    VARIAVEL_DA_CHAVE,
    __version__,
    _versao_instalada,
    diagnostico,
)
from jev_crap._reexport import (
    MapaDeReexportInconsistente,
    conferir_mapa,
    importar_publicado,
)


class TestDiagnostico:
    def test_diagnostico_traz_a_versao_do_pacote(self):
        assert diagnostico()["jev_crap"] == __version__

    def test_diagnostico_traz_python_e_plataforma(self):
        relatorio = diagnostico()
        assert relatorio["python"]
        assert relatorio["plataforma"]

    def test_diagnostico_lista_as_tres_dependencias(self):
        relatorio = diagnostico()
        for nome in DEPENDENCIAS:
            assert nome in relatorio

    def test_diagnostico_diz_sim_quando_a_chave_existe(self, monkeypatch):
        monkeypatch.setenv(VARIAVEL_DA_CHAVE, "sk-qualquer-coisa")
        assert diagnostico()["chave_definida"] == "sim"

    def test_diagnostico_diz_nao_quando_a_chave_falta(self, monkeypatch):
        monkeypatch.delenv(VARIAVEL_DA_CHAVE, raising=False)
        assert diagnostico()["chave_definida"] == "nao"

    def test_diagnostico_nunca_expoe_o_valor_da_chave(self, monkeypatch):
        monkeypatch.setenv(VARIAVEL_DA_CHAVE, "sk-segredo-de-verdade")
        assert "segredo" not in str(diagnostico())

    def test_diagnostico_devolve_so_texto(self):
        assert all(isinstance(v, str) for v in diagnostico().values())

    def test_diagnostico_nao_levanta_com_metadado_quebrado(self, monkeypatch):
        def explode(_nome):
            raise RuntimeError("metadado corrompido")

        monkeypatch.setattr(metadata, "version", explode)
        assert diagnostico()["httpx"].startswith(AUSENTE)


class TestVersaoInstalada:
    def test_versao_instalada_devolve_a_versao_de_um_pacote_presente(self):
        assert _versao_instalada("pytest") == metadata.version("pytest")

    def test_versao_instalada_devolve_ausente_para_pacote_inexistente(self):
        assert _versao_instalada("pacote-que-nao-existe-mesmo") == AUSENTE

    def test_versao_instalada_traduz_metadado_ilegivel(self, monkeypatch):
        def explode(_nome):
            raise OSError("disco")

        monkeypatch.setattr(metadata, "version", explode)
        assert _versao_instalada("httpx") == f"{AUSENTE} (metadado ilegível)"

    def test_versao_instalada_nunca_propaga(self, monkeypatch):
        def explode(_nome):
            raise KeyboardInterrupt

        monkeypatch.setattr(metadata, "version", explode)
        with pytest.raises(KeyboardInterrupt):
            _versao_instalada("httpx")


class TestConferirMapa:
    def test_conferir_mapa_devolve_vazio_quando_mapa_e_all_batem(self):
        assert conferir_mapa("pacote", {"ler": "cobertura"}, ["ler"]) == ()

    def test_conferir_mapa_aponta_nome_publicado_sem_origem(self):
        with pytest.warns(MapaDeReexportInconsistente):
            problemas = conferir_mapa("pacote", {}, ["orfao"])
        assert "não tem submódulo de origem" in problemas[0]

    def test_conferir_mapa_aponta_origem_sem_publicacao(self):
        with pytest.warns(MapaDeReexportInconsistente):
            problemas = conferir_mapa("pacote", {"escondido": "sub"}, [])
        assert "não está em __all__" in problemas[0]

    def test_conferir_mapa_nomeia_o_divergente(self):
        with pytest.warns(MapaDeReexportInconsistente):
            problemas = conferir_mapa("pacote", {}, ["orfao"])
        assert "orfao" in problemas[0]

    def test_conferir_mapa_relata_as_duas_divergencias_de_uma_vez(self):
        with pytest.warns(MapaDeReexportInconsistente):
            problemas = conferir_mapa("pacote", {"so_origem": "sub"}, ["so_all"])
        assert len(problemas) == 2

    def test_conferir_mapa_nunca_levanta(self):
        with pytest.warns(MapaDeReexportInconsistente):
            assert isinstance(conferir_mapa("pacote", {"a": "s"}, ["b"]), tuple)

    def test_conferir_mapa_aceita_os_dois_vazios_sem_avisar(self, recwarn):
        assert conferir_mapa("pacote", {}, []) == ()
        assert not [a for a in recwarn if a.category is MapaDeReexportInconsistente]

    @pytest.mark.parametrize(
        "pacote",
        ["jev_crap.metrica", "jev_crap.julgamento", "jev_crap.aprendizado"],
    )
    def test_conferir_mapa_nao_acha_divergencia_nos_pacotes_reais(self, pacote):
        """A conferência que, em produção, só avisa. Aqui ela barra de verdade."""
        modulo = builtins.__import__(pacote, fromlist=["*"])
        assert conferir_mapa(pacote, modulo._ORIGEM, modulo.__all__) == ()


class TestImportarPublicado:
    def test_importar_publicado_devolve_o_objeto_do_submodulo(self):
        assert importar_publicado("jev_crap.metrica", "cobertura", "ler").__name__ == "ler"

    def test_importar_publicado_culpa_o_mapa_quando_o_nome_sumiu(self):
        with pytest.raises(AttributeError, match="não define esse nome"):
            importar_publicado("jev_crap.metrica", "cobertura", "sumiu")

    def test_importar_publicado_cita_submodulo_e_nome_na_mensagem(self):
        with pytest.raises(AttributeError, match="'cobertura'"):
            importar_publicado("jev_crap.metrica", "cobertura", "sumiu")

    def test_importar_publicado_propaga_erro_de_submodulo_inexistente(self):
        with pytest.raises(ModuleNotFoundError):
            importar_publicado("jev_crap.metrica", "nao_existe", "x")


class TestGetattrDosPacotes:
    """O `__getattr__` que cada subpacote define, exercitado diretamente."""

    @pytest.mark.parametrize(
        ("pacote", "nome"),
        [
            ("jev_crap.metrica", "ler"),
            ("jev_crap.julgamento", "Rubrica"),
            ("jev_crap.aprendizado", "propor"),
        ],
    )
    def test_getattr_resolve_nome_publicado(self, pacote, nome):
        modulo = builtins.__import__(pacote, fromlist=["*"])
        assert modulo.__getattr__(nome) is not None

    @pytest.mark.parametrize(
        "pacote",
        ["jev_crap.metrica", "jev_crap.julgamento", "jev_crap.aprendizado"],
    )
    def test_getattr_recusa_nome_de_fora_listando_os_validos(self, pacote):
        modulo = builtins.__import__(pacote, fromlist=["*"])
        with pytest.raises(AttributeError, match="não publica 'xpto'"):
            modulo.__getattr__("xpto")

    @pytest.mark.parametrize(
        "pacote",
        ["jev_crap.metrica", "jev_crap.julgamento", "jev_crap.aprendizado"],
    )
    def test_getattr_cita_o_pacote_na_mensagem(self, pacote):
        modulo = builtins.__import__(pacote, fromlist=["*"])
        with pytest.raises(AttributeError, match=pacote):
            modulo.__getattr__("xpto")

    def test_getattr_do_dunder_pedido_pelo_python_nao_explode_o_import(self):
        modulo = builtins.__import__("jev_crap.metrica", fromlist=["*"])
        with pytest.raises(AttributeError):
            modulo.__getattr__("__path__xyz")


class TestReexportDosPacotes:
    """Cada nome de `__all__` resolve de verdade — é o que o `import *` faria."""

    @pytest.mark.parametrize(
        "pacote",
        ["jev_crap.metrica", "jev_crap.julgamento", "jev_crap.aprendizado"],
    )
    def test_todo_nome_publicado_resolve(self, pacote):
        modulo = builtins.__import__(pacote, fromlist=["*"])
        for nome in modulo.__all__:
            assert getattr(modulo, nome) is not None

    @pytest.mark.parametrize(
        "pacote",
        ["jev_crap.metrica", "jev_crap.julgamento", "jev_crap.aprendizado"],
    )
    def test_nome_fora_do_all_levanta_attribute_error(self, pacote):
        modulo = builtins.__import__(pacote, fromlist=["*"])
        ausente = "nome_que_nao_existe"
        with pytest.raises(AttributeError, match=ausente):
            getattr(modulo, ausente)

    def test_pacote_raiz_publica_apenas_versao_e_diagnostico(self):
        assert set(jev_crap.__all__) == {"__version__", "diagnostico"}
