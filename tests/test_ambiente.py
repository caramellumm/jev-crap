"""O `.env`: quem vence quem, e o que é formato do arquivo versus valor."""

from __future__ import annotations

import pytest

from jev_crap.ambiente import carregar_env


@pytest.fixture
def limpo(monkeypatch):
    for nome in ("CHAVE_A", "CHAVE_B", "JA_EXISTE", "EXPORTADA"):
        monkeypatch.delenv(nome, raising=False)
    return monkeypatch


def test_o_ambiente_vence_o_arquivo(tmp_path, limpo):
    """No CI a chave chega por secret; se o arquivo sobrescrevesse, um `.env`
    esquecido no disco trocaria silenciosamente a chave da organização pela de
    alguém. É também o que torna seguro chamar isto dentro do servidor MCP."""
    limpo.setenv("JA_EXISTE", "do-ambiente")
    (tmp_path / ".env").write_text("JA_EXISTE=do-arquivo\n", encoding="utf-8")
    carregar_env(tmp_path)
    import os

    assert os.environ["JA_EXISTE"] == "do-ambiente"


def test_procura_subindo_os_diretorios_pais(tmp_path, limpo):
    """Rodar de dentro de `src/` é comum e o `.env` mora na raiz; olhar só o
    diretório atual falharia dizendo "defina a variável", que é o diagnóstico
    errado."""
    (tmp_path / ".env").write_text("CHAVE_A=valor\n", encoding="utf-8")
    fundo = tmp_path / "src" / "pacote"
    fundo.mkdir(parents=True)
    assert carregar_env(fundo) == tmp_path / ".env"


def test_aspas_sao_do_formato_e_nao_do_segredo(tmp_path, limpo):
    """Sem tirá-las, a chave vai para o cabeçalho com as aspas juntas e a API
    responde 401 sem dizer por quê."""
    (tmp_path / ".env").write_text('CHAVE_A="com-aspas"\n', encoding="utf-8")
    carregar_env(tmp_path)
    import os

    assert os.environ["CHAVE_A"] == "com-aspas"


def test_cerquilha_no_meio_do_valor_nao_vira_comentario(tmp_path, limpo):
    """`#` é caractere válido dentro de uma chave; cortar ali corromperia o valor."""
    (tmp_path / ".env").write_text("CHAVE_B=abc#def\n", encoding="utf-8")
    carregar_env(tmp_path)
    import os

    assert os.environ["CHAVE_B"] == "abc#def"


def test_prefixo_export_e_aceito(tmp_path, limpo):
    (tmp_path / ".env").write_text("export EXPORTADA=sim\n", encoding="utf-8")
    carregar_env(tmp_path)
    import os

    assert os.environ["EXPORTADA"] == "sim"


def test_sem_arquivo_devolve_nulo_em_vez_de_levantar(tmp_path):
    """A diferença importa na hora de explicar a falta da chave: "não existe
    `.env` nenhum" e "existe um e ele não tem a chave" pedem ações opostas."""
    assert carregar_env(tmp_path) is None
