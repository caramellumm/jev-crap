"""A skill e o servidor precisam descrever a mesma ferramenta.

Documentação que descreve um servidor que não existe mais é pior do que
documentação nenhuma: ela é lida com confiança e manda o agente chamar uma tool
que foi renomeada, ou ler um campo que sumiu. Estes testes prendem os dois — se
alguém mexer no servidor sem mexer na skill, a suíte diz qual dos dois ficou
para trás.

O acoplamento é de propósito e é barato: são nomes, não prosa. Nada aqui tenta
julgar se o texto está bem escrito.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastmcp import Client

from jev_crap.avaliacao import FAIXAS
from jev_crap.config import (
    BLOQUEIO_PADRAO,
    MAX_JULGAMENTOS_PADRAO,
    SUSPEITA_PADRAO,
    VARIAVEIS,
)
from jev_crap.julgamento.jev import VARIAVEL_DA_CHAVE
from jev_crap.server import criar_servidor

SKILL = Path(__file__).parents[1] / "skills" / "jev-crap" / "SKILL.md"


@pytest.fixture(scope="module")
def texto() -> str:
    return SKILL.read_text(encoding="utf-8")


@pytest.fixture
async def nomes_das_tools(config, rubrica):
    from jev_crap.julgamento.jev import JulgadorDesligado

    async with Client(criar_servidor(config, JulgadorDesligado(), rubrica)) as c:
        return {t.name for t in await c.list_tools()}


class TestFrontmatter:
    def test_tem_name_e_description(self, texto):
        assert texto.startswith("---\n")
        cabecalho = texto.split("---", 2)[1]
        assert re.search(r"^name:\s*jev-crap\s*$", cabecalho, re.M)
        assert re.search(r"^description:\s*\S", cabecalho, re.M)

    def test_a_descricao_lista_os_gatilhos_em_linguagem_de_gente(self, texto):
        """A descrição é o que decide se a skill é acionada. Se ela só falasse em
        "CRAP" e "complexidade ciclomática", nunca dispararia com "isso aqui tá
        bom?", que é como a pergunta chega na prática."""
        descricao = texto.split("---", 2)[1]
        for gatilho in ("refatorar", "melhorar", "antes de abrir PR", "tá bom"):
            assert gatilho in descricao, gatilho


class TestSincroniaComOServidor:
    async def test_a_skill_documenta_exatamente_as_tools_que_existem(
        self, texto, nomes_das_tools
    ):
        tabela = texto.split("## Por que dois eixos")[0]
        citadas = set(re.findall(r"`(\w+)`", tabela))
        documentadas = citadas & nomes_das_tools
        assert documentadas == nomes_das_tools, (
            f"faltam na skill: {nomes_das_tools - documentadas}"
        )

    async def test_a_skill_nao_cita_tool_que_nao_existe(self, texto, nomes_das_tools):
        """O sintoma clássico de skill desatualizada: a tool foi renomeada e o
        agente continua chamando o nome antigo até tomar erro."""
        candidatas = set(re.findall(r"`([a-z_]{6,})\(", texto))
        inventadas = candidatas - nomes_das_tools
        assert not inventadas, f"a skill cita tools inexistentes: {inventadas}"

    async def test_as_dimensoes_citadas_existem_na_regua(self, texto, rubrica):
        citadas = set(re.findall(r"`(\w+)`", texto)) & {
            n for n in rubrica.dimensoes
        }
        assert citadas == set(rubrica.dimensoes), (
            f"dimensões não documentadas: {set(rubrica.dimensoes) - citadas}"
        )

    def test_as_variaveis_de_ambiente_citadas_existem(self, texto):
        citadas = set(re.findall(r"`(JEV_CRAP_\w+)`", texto))
        assert citadas <= set(VARIAVEIS), f"variáveis inexistentes: {citadas - set(VARIAVEIS)}"
        assert citadas, "a skill não menciona nenhuma variável de ajuste"

    def test_os_pesos_na_tabela_batem_com_a_regua(self, texto, rubrica):
        """Uma tabela de pesos errada faz quem lê o relatório calcular outra nota
        na cabeça e concluir que a ferramenta está com defeito."""
        for nome, peso in rubrica.pesos.items():
            assert re.search(rf"`{nome}`\s*\|\s*{peso:.2f}\s*\|", texto), nome

    def test_os_limiares_citados_batem_com_os_padroes(self, texto):
        assert f"acima de {BLOQUEIO_PADRAO:.2f}" in texto
        assert str(SUSPEITA_PADRAO) in texto
        assert "JEV_CRAP_MAX_JULGAMENTOS" in texto
        assert MAX_JULGAMENTOS_PADRAO == 20  # o texto fala em "no máximo ... delas"

    def test_as_faixas_da_nota_batem(self, texto):
        for piso, nome in FAIXAS:
            if piso:
                assert f"`{nome}` (≥{piso:.0f})" in texto, nome

    def test_a_variavel_da_chave_e_citada_com_o_nome_certo(self, texto):
        assert VARIAVEL_DA_CHAVE in texto


class TestOQueASkillPrecisaDizer:
    @pytest.mark.parametrize(
        "assunto,marca",
        [
            ("quando não rodar", "## 0. Quando não rodar"),
            ("nota ordena e não mede", "ordena, não mede"),
            ("gates ficam fora da nota", "ficam fora da nota"),
            ("noul não tem confiança", "não têm confiança"),
            ("nada agregado", "Nunca publique um número agregado"),
            ("avaliar não é editar", "não é autorização para editar"),
            ("registrar quando ignorou", "aceita=false"),
            ("nada é aplicado sozinho", "recalibra os"),
            ("sem chave ainda funciona", "## Sem `TYPESAFE_API_KEY`"),
        ],
    )
    def test_os_avisos_que_evitam_uso_errado_estao_la(self, texto, assunto, marca):
        assert marca in texto, assunto

    def test_ensina_a_gerar_cobertura_de_branch(self, texto):
        """Sem branch, o relatório declara coberto o ramo que nunca rodou."""
        assert "--cov-branch" in texto
        assert "coverageReporters=lcov" in texto
