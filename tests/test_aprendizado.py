"""O histórico e o que ele autoriza concluir.

A regra que organiza o módulo: `agregar` descreve, `propor` sugere, e nada é
aplicado. Os testes aqui cobrem principalmente o que o módulo **se recusa** a
concluir — que é onde uma ferramenta de calibração costuma mentir.
"""

from __future__ import annotations

import pytest

from jev_crap.aprendizado import (
    MINIMO_EPISODIOS,
    Episodio,
    Repositorio,
    agregar,
    propor,
)


def episodio(**mudancas) -> Episodio:
    base = dict(
        id="", em="", arquivo="src/a.py", funcao="f", risco=40.0, formula="crap",
        limiar_vigente=30.0, complexidade=8, cobertura_linha=0.5, cobertura_branch=0.4,
        notas={}, nota=70.0, conselho="escrever teste", veredito="revisar",
    )
    return Episodio(**{**base, **mudancas})


def muitos(n=MINIMO_EPISODIOS, **mudancas):
    return [
        episodio(id=str(i), em=f"2026-09-{i % 28 + 1:02d}T00:00:00Z", **mudancas)
        for i in range(n)
    ]


class TestRepositorio:
    def test_grava_e_le(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        gravado = repo.registrar(episodio())
        assert gravado.id and gravado.em
        assert len(repo.carregar()) == 1

    def test_linha_corrompida_nao_derruba_o_historico(self, tmp_path):
        """O arquivo é escrito por processos que podem ser interrompidos no meio;
        um episódio corrompido custa um episódio, não o arquivo inteiro."""
        caminho = tmp_path / "h.jsonl"
        repo = Repositorio(caminho)
        repo.registrar(episodio(id="bom"))
        with caminho.open("a", encoding="utf-8") as f:
            f.write("{ isto não é json\n")
        repo.registrar(episodio(id="outro"))
        assert len(repo.carregar()) == 2
        assert repo.linhas_invalidas == 1

    def test_campo_desconhecido_e_ignorado_em_vez_de_quebrar(self, tmp_path):
        """Arquivo escrito por versão mais nova precisa continuar legível:
        perder um campo é aceitável, perder o histórico por um TypeError não."""
        caminho = tmp_path / "h.jsonl"
        caminho.write_text(
            '{"id":"1","em":"2026-01-01T00:00:00Z","arquivo":"a.py","funcao":"f",'
            '"risco":10,"formula":"crap","limiar_vigente":30,"complexidade":1,'
            '"cobertura_linha":0.5,"cobertura_branch":null,"campo_do_futuro":42}\n',
            encoding="utf-8",
        )
        assert len(Repositorio(caminho).carregar()) == 1

    def test_desfecho_preserva_a_medicao_da_epoca(self, tmp_path):
        repo = Repositorio(tmp_path / "h.jsonl")
        criado = repo.registrar(episodio(risco=42.0, nota=71.0))
        atualizado = repo.registrar_desfecho(criado.id, defeito=True)
        assert atualizado.risco == 42.0
        assert atualizado.nota == 71.0
        assert atualizado.defeito is True
        assert len(repo.carregar()) == 1

    def test_desfecho_em_id_inexistente_levanta(self, tmp_path):
        with pytest.raises(KeyError):
            Repositorio(tmp_path / "h.jsonl").registrar_desfecho("nao-existe", defeito=True)

    def test_argumento_nulo_preserva_o_valor_atual(self, tmp_path):
        """É assim que marcar um defeito meses depois não apaga a decisão da época."""
        repo = Repositorio(tmp_path / "h.jsonl")
        criado = repo.registrar(episodio(aceita=False, acao="ignorei"))
        atualizado = repo.registrar_desfecho(criado.id, defeito=True)
        assert atualizado.aceita is False
        assert atualizado.acao == "ignorei"


class TestAgregar:
    def test_metrica_ausente_e_nula_e_nao_zero(self):
        """"Nenhuma sugestão foi aceita" e "nenhuma teve decisão registrada" são
        fatos diferentes, e confundi-los produz a conclusão errada."""
        metricas = agregar(muitos(5))
        assert metricas["taxa_aceitacao"] is None
        assert metricas["cobertura_de_risco"] is None
        assert len(metricas["avisos"]) == 2

    def test_taxa_conta_so_quem_teve_decisao(self):
        eps = [episodio(id="1", aceita=True), episodio(id="2", aceita=False),
               episodio(id="3", aceita=None)]
        assert agregar(eps)["taxa_aceitacao"] == 0.5

    def test_cobertura_de_risco_so_olha_quem_deu_defeito(self):
        """É o número mais honesto do conjunto: só pode ser calculado com
        informação que chegou depois e fora do controle de quem escreveu a régua."""
        eps = [
            episodio(id="1", defeito=True, risco=50.0, limiar_vigente=30.0),   # pego
            episodio(id="2", defeito=True, risco=10.0, limiar_vigente=30.0),   # escapou
            episodio(id="3", defeito=None, risco=99.0),
        ]
        assert agregar(eps)["cobertura_de_risco"] == 0.5

    def test_formulas_misturadas_viram_aviso(self):
        """Risco 12 pela fórmula clássica e risco 12 por outra não são a mesma
        coisa: são escalas diferentes com o mesmo nome."""
        eps = [episodio(id="1", formula="crap"), episodio(id="2", formula="outra")]
        assert any("mais de uma fórmula" in a for a in agregar(eps)["avisos"])

    def test_le_o_valor_normalizado_e_nao_a_confianca(self):
        """Confiança mede o quanto o modelo se decidiu, não a posição na escala;
        pegá-la por engano produziria uma série que parece certa e mede outra coisa."""
        eps = muitos(10, notas={"legivel": {"normalizado": 0.8, "confianca": 0.2}})
        assert agregar(eps)["variacao_dimensoes"]["legivel"]["media"] == 0.8


class TestPropor:
    def test_abaixo_do_minimo_nao_conclui_e_diz_por_que(self):
        propostas = propor(muitos(5))
        assert propostas == []
        assert "ainda não há base" in propostas.motivo

    def test_um_defeito_abaixo_do_limiar_ja_autoriza_baixar(self):
        """Diferente do incômodo, que precisa de repetição, o defeito que escapou
        já é a evidência completa."""
        eps = muitos(risco=10.0)
        eps[0].defeito = True
        propostas = propor(eps)
        assert [p["tipo"] for p in propostas if p["tipo"] == "baixar_limiar"]

    def test_faixa_estreita_majoritariamente_ignorada_autoriza_subir(self):
        """Ignorar um alerta de risco 12 quando o limiar é 10 sugere limiar
        baixo; ignorar um de risco 90 sugere outra coisa."""
        eps = muitos(risco=32.0, aceita=False, acao="ignorei")
        assert any(p["tipo"] == "subir_limiar" for p in propor(eps))

    def test_um_defeito_na_faixa_derruba_a_proposta_de_subir(self):
        """Subir o limiar esconderia justamente o caso que a ferramenta acertou,
        e falso negativo custa mais caro que incômodo."""
        eps = muitos(risco=32.0, aceita=False, acao="ignorei")
        eps[0].defeito = True
        assert not any(p["tipo"] == "subir_limiar" for p in propor(eps))

    def test_risco_muito_acima_do_limiar_ignorado_nao_mexe_na_regua(self):
        eps = muitos(risco=500.0, aceita=False, acao="ignorei")
        assert not any(p["tipo"] == "subir_limiar" for p in propor(eps))

    def test_dimensao_que_nao_varia_e_candidata_a_remocao(self):
        """Ela só acrescenta latência, custo de chamada e uma coluna a mais."""
        eps = muitos(notas={"parada": {"normalizado": 0.5}})
        propostas = [p for p in propor(eps) if p["tipo"] == "remover_dimensao"]
        assert propostas and propostas[0]["alvo"] == "parada"

    def test_toda_proposta_carrega_a_evidencia(self):
        """Proposta sem evidência é palpite com aparência de método, e quem vai
        aplicar precisa poder discordar do número, não da conclusão."""
        eps = muitos(risco=10.0)
        eps[0].defeito = True
        for proposta in propor(eps):
            assert proposta["evidencia"]
            assert proposta["motivo"]

    def test_formulas_diferentes_nao_se_somam_para_atingir_o_minimo(self):
        """Um limiar calibrado sobre a mistura não vale para nenhuma das duas."""
        eps = muitos(15, formula="crap") + muitos(15, formula="outra")
        for i, ep in enumerate(eps):
            ep.id = str(i)
        assert "não podem ser somados" in propor(eps).motivo

    def test_lista_vazia_explica_a_diferenca_entre_sem_base_e_sem_ajuste(self):
        """"Ainda não há base" quer mais uso; "não indica ajuste" quer que se
        deixe como está. As duas pedem reações opostas."""
        assert "não indica ajuste" in propor(muitos(aceita=True, acao="fiz")).motivo

    def test_propor_nao_tem_efeito_colateral(self):
        eps = muitos(risco=10.0)
        eps[0].defeito = True
        antes = [ep.limiar_vigente for ep in eps]
        propor(eps)
        assert [ep.limiar_vigente for ep in eps] == antes
