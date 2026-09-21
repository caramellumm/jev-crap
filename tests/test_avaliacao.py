"""As regras de decisão. É aqui que o projeto acerta ou erra.

Cada teste desta suíte corresponde a uma frase do cabeçalho de
`jev_crap.avaliacao`. Se uma regra mudar, um teste daqui precisa mudar junto —
e é isso que impede que uma delas seja "simplificada" sem que ninguém perceba o
que foi perdido.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import RESPOSTAS_BOAS, RESPOSTAS_REAIS, noul, score

from jev_crap.avaliacao import (
    FuncaoMedida,
    avaliar,
    decidir,
    faixa_da_nota,
    julgar_trecho,
    medir,
)
from jev_crap.config import Config
from jev_crap.julgamento.jev import JulgadorDesligado, JulgadorFake
from jev_crap.metrica.cobertura import SEM_DADOS
from jev_crap.situacoes import SituacaoConhecida


def decidida(medida, respostas, config, rubrica, limiar=30.0):
    return decidir(medida, respostas, rubrica=rubrica, config=config, limiar=limiar)


class TestRegra1QueDaParaContarNaoViraPergunta:
    def test_complexidade_e_tamanho_saem_da_medicao_e_nao_do_modelo(self, projeto):
        """O número vem do lizard e do arquivo, nunca de uma resposta do modelo."""
        medicao = medir([str(projeto / "src")], config=Config(raiz=projeto))
        divide = next(f for f in medicao.funcoes if f.nome == "divide")
        assert divide.complexidade == 2          # contado, não perguntado
        assert divide.tamanho == 4               # linhas do arquivo, não opinião
        assert divide.risco == pytest.approx(6.0)

    def test_a_regua_nao_tem_pergunta_sobre_numero_contavel(self, rubrica):
        """Perguntar trocaria certeza por distribuição de probabilidade."""
        for dimensao in rubrica.dimensoes.values():
            texto = dimensao.pergunta["instructions"].lower()
            assert "ciclomátic" not in texto
            assert "quantas linhas" not in texto


class TestRegra3RiscoViraGateEFicaForaDaNota:
    def test_gate_grave_barra_mesmo_com_nota_alta(self, medida, rubrica, config):
        """É a tese do projeto: uma função com injeção e nota 95 continua sendo
        uma função com injeção. Se este teste cair, o desenho caiu junto."""
        avaliada = decidida(medida, {**RESPOSTAS_BOAS, "injecao": noul(0.95)}, config, rubrica)
        assert avaliada.nota == 90.0
        assert avaliada.faixa == "sólido"
        assert avaliada.veredito == "bloquear"
        assert any("injecao" in g for g in avaliada.graves)

    def test_o_gate_nao_entra_na_media(self, medida, rubrica, config):
        """A nota é a mesma com e sem o gate disparado — é o que significa
        'fora da nota'."""
        limpa = decidida(medida, RESPOSTAS_BOAS, config, rubrica)
        barrada = decidida(medida, {**RESPOSTAS_BOAS, "injecao": noul(0.99)}, config, rubrica)
        assert limpa.nota == barrada.nota

    def test_risco_de_atencao_nunca_barra(self, medida, rubrica, config):
        """"Existe entrada plausível que quebraria isto?" é verdade em quase
        todo código real; como gate de bloqueio não separaria nada."""
        avaliada = decidida(
            medida, {**RESPOSTAS_BOAS, "caso_limite_nao_tratado": noul(0.99)}, config, rubrica
        )
        assert avaliada.graves == ()
        assert avaliada.veredito == "revisar"
        assert any("caso_limite" in d for d in avaliada.duvidas)

    def test_gate_nao_respondido_nao_conta_como_nao(self, medida, rubrica, config):
        """"A pergunta não foi feita" e "a resposta foi não" são coisas
        diferentes; tratá-las igual é como um gate deixa de valer."""
        parcial = {k: v for k, v in RESPOSTAS_BOAS.items() if k != "injecao"}
        avaliada = decidida(medida, parcial, config, rubrica)
        assert avaliada.veredito == "revisar"
        assert any("gate não respondido" in d for d in avaliada.duvidas)

    def test_tamanho_e_gate_contavel_e_zera_a_nota(self, rubrica, config, medida):
        gigante = FuncaoMedida(
            **{**medida.__dict__, "linha_inicio": 1, "linha_fim": config.limite_tamanho + 1}
        )
        avaliada = decidida(gigante, RESPOSTAS_BOAS, config, rubrica)
        assert avaliada.nota == 0.0
        assert avaliada.veredito == "bloquear"
        assert "limite" in avaliada.graves[0]

    def test_no_limite_de_tamanho_a_nota_sobrevive(self, rubrica, config, medida):
        """Exatamente no limite não barra — e o julgamento sobre trecho parcial
        vira dúvida, não bloqueio: barrar aqui puniria tamanho duas vezes."""
        no_limite = FuncaoMedida(
            **{**medida.__dict__, "linha_inicio": 1, "linha_fim": config.limite_tamanho}
        )
        avaliada = decidida(no_limite, RESPOSTAS_BOAS, config, rubrica)
        assert avaliada.graves == ()
        assert avaliada.nota == 90.0
        assert avaliada.veredito == "revisar"
        assert any("primeiras de" in d for d in avaliada.duvidas)


class TestRegra4E5AusenciaDeDadoNaoEZero:
    def test_sem_trecho_de_teste_o_peso_e_redistribuido(self, medida, rubrica, config):
        """Perguntar sem evidência devolveria "não há teste" para função testada
        indiretamente — e cobraria por isso um quarto da nota."""
        sem_teste = {k: v for k, v in RESPOSTAS_BOAS.items() if k != "teste_verifica"}
        avaliada = decidida(medida, sem_teste, config, rubrica)
        assert avaliada.nota == 90.0  # a mesma das outras três, não 67.5
        assert avaliada.nao_observadas == ("teste_verifica",)
        assert any("teste não localizado" in d for d in avaliada.duvidas)

    def test_cobertura_ausente_vira_nulo_no_relatorio_e_nao_menos_um(
        self, medida, rubrica, config
    ):
        sem_dados = FuncaoMedida(
            **{**medida.__dict__, "cobertura_linha": SEM_DADOS, "cobertura_branch": SEM_DADOS}
        )
        corpo = decidida(sem_dados, RESPOSTAS_BOAS, config, rubrica).para_avaliacao()
        assert corpo["cobertura_linha"] is None
        assert corpo["cobertura_branch"] is None


class TestNota:
    def test_e_a_media_ponderada_das_dimensoes_de_qualidade(self, medida, rubrica, config):
        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_cognitiva": score(2.0),   # 1.0 × 0.30
            "teste_verifica": score(0.0),           # 0.0 × 0.25
            "manutenibilidade": score(1.0),         # 0.5 × 0.25
            "tratamento_de_erros": score(2.0),      # 1.0 × 0.20
        }
        assert decidida(medida, respostas, config, rubrica).nota == pytest.approx(62.5)

    def test_sem_nenhuma_dimensao_a_nota_e_nula_e_nao_zero(self, medida, rubrica, config):
        """Zero significaria "péssimo"; None significa "não sei", que é o fato."""
        so_risco = {k: v for k, v in RESPOSTAS_BOAS.items() if k not in rubrica.pesos}
        assert decidida(medida, so_risco, config, rubrica).nota is None

    @pytest.mark.parametrize(
        "nota,esperada",
        [(100.0, "sólido"), (80.0, "sólido"), (79.9, "aceitável"), (60.0, "aceitável"),
         (59.9, "frágil"), (40.0, "frágil"), (39.9, "ruim"), (0.0, "ruim"), (None, "sem nota")],
    )
    def test_a_faixa_e_a_unidade_real_da_resposta(self, nota, esperada):
        """A calibração numérica de um score é fraca: 72.4 e 75.1 são o mesmo
        "aceitável", e tratar a diferença como informação é ler precisão que o
        modelo não tem."""
        assert faixa_da_nota(nota) == esperada


class TestConselho:
    def casos(self):
        return {
            "refatorar e testar": (score(0.0), noul(0.1), score(0.0)),
            "só simplificar": (score(0.0), noul(0.1), score(2.0)),
            "só testar": (score(2.0), noul(0.9), score(0.0)),
            "nada": (score(2.0), noul(0.9), score(2.0)),
        }

    @pytest.mark.parametrize(
        "cenario,esperado",
        [
            ("refatorar e testar", "refatorar e só depois testar"),
            ("só simplificar", "simplificar a forma"),
            ("só testar", "escrever teste, não refatorar"),
            ("nada", "nada obrigatório"),
        ],
    )
    def test_os_quatro_quadrantes(self, cenario, esperado, medida, rubrica, config):
        cognitiva, essencial, teste = self.casos()[cenario]
        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_cognitiva": cognitiva,
            "complexidade_essencial": essencial,
            "teste_verifica": teste,
        }
        assert esperado in decidida(medida, respostas, config, rubrica).conselho

    def test_complexidade_essencial_impede_o_conselho_de_refatorar(
        self, medida, rubrica, config
    ):
        """Quando a complexidade vem do domínio, simplificar apaga casos reais —
        e o número melhora, porque a métrica não sabe a diferença."""
        respostas = {
            **RESPOSTAS_BOAS,
            "complexidade_cognitiva": score(0.0),
            "complexidade_essencial": noul(0.95),
            "teste_verifica": score(0.0),
        }
        conselho = decidida(medida, respostas, config, rubrica).conselho
        assert "escrever teste, não refatorar" in conselho

    def test_sem_julgamento_sobre_teste_quem_responde_e_a_cobertura(
        self, medida, rubrica, config
    ):
        """A regra 1 aplicada onde ela sempre deveria ter valido."""
        sem_teste = {
            k: v for k, v in RESPOSTAS_BOAS.items() if k != "teste_verifica"
        } | {"complexidade_cognitiva": score(2.0), "complexidade_essencial": noul(0.9)}
        descoberta = FuncaoMedida(**{**medida.__dict__, "cobertura_linha": 0.2})
        coberta = FuncaoMedida(**{**medida.__dict__, "cobertura_linha": 0.95})
        assert "escrever teste" in decidida(descoberta, sem_teste, config, rubrica).conselho
        assert "nada obrigatório" in decidida(coberta, sem_teste, config, rubrica).conselho

    def test_sem_cobertura_e_sem_teste_nao_inventa_conselho(self, medida, rubrica, config):
        """Ausência de dado dos dois lados não vira "está tudo bem"."""
        sem_teste = {
            k: v for k, v in RESPOSTAS_BOAS.items() if k != "teste_verifica"
        } | {"complexidade_cognitiva": score(2.0), "complexidade_essencial": noul(0.9)}
        sem_dados = FuncaoMedida(**{**medida.__dict__, "cobertura_linha": SEM_DADOS})
        assert decidida(sem_dados, sem_teste, config, rubrica).conselho == "nada obrigatório"

    def test_sem_julgamento_nenhum_o_conselho_diz_isso(self, medida, rubrica, config):
        conselho = decidida(medida, {}, config, rubrica).conselho
        assert "sem julgamento" in conselho
        assert "mesmo número" in conselho


class TestPrioridade:
    def test_consequencia_alta_sobe_a_prioridade(self, medida, rubrica, config):
        """A direção da escala é o erro mais caro possível neste arquivo:
        invertê-la despriorizaria justamente o código perigoso."""
        cosmetica = decidida(
            medida, {**RESPOSTAS_BOAS, "consequencia_de_falha": score(0.0)}, config, rubrica
        )
        corrompe = decidida(
            medida, {**RESPOSTAS_BOAS, "consequencia_de_falha": score(2.0)}, config, rubrica
        )
        assert cosmetica.prioridade == "media"
        assert corrompe.prioridade == "alta"

    def test_gate_grave_e_sempre_prioridade_alta(self, medida, rubrica, config):
        avaliada = decidida(
            medida,
            {**RESPOSTAS_BOAS, "consequencia_de_falha": score(0.0), "injecao": noul(0.99)},
            config,
            rubrica,
        )
        assert avaliada.prioridade == "alta"

    def test_abaixo_do_limiar_e_sem_duvida_a_prioridade_e_baixa(self, medida, rubrica, config):
        tranquila = FuncaoMedida(**{**medida.__dict__, "risco": 5.0, "complexidade": 2})
        assert decidida(tranquila, RESPOSTAS_BOAS, config, rubrica).prioridade == "baixa"


class TestVeredito:
    def test_nota_abaixo_do_minimo_manda_revisar(self, medida, rubrica, config):
        respostas = {**RESPOSTAS_BOAS, **{n: score(0.5) for n in rubrica.pesos}}
        avaliada = decidida(medida, respostas, config, rubrica)
        assert avaliada.nota < config.nota_minima
        assert avaliada.veredito == "revisar"

    def test_confianca_baixa_vira_duvida_e_nao_veredito(self, medida, rubrica, config):
        """Distribuição espalhada é informação real sobre um caso ambíguo;
        apagá-la transfere a quem lê uma certeza que ninguém teve."""
        respostas = {**RESPOSTAS_BOAS, "manutenibilidade": score(1.8, confianca=0.2)}
        avaliada = decidida(medida, respostas, config, rubrica)
        assert avaliada.veredito == "revisar"
        assert any("disperso" in d for d in avaliada.duvidas)

    def test_eixo_desligado_nao_vira_aprovacao(self, medida, rubrica, config):
        """O que não foi julgado não foi aprovado."""
        assert decidida(medida, {}, config, rubrica).veredito == "sem_julgamento"

    def test_complexidade_acima_do_viavel_vira_duvida(self, medida, rubrica, config):
        complexa = FuncaoMedida(**{**medida.__dict__, "complexidade": config.limite_ccn + 1})
        avaliada = decidida(complexa, RESPOSTAS_BOAS, config, rubrica)
        assert avaliada.veredito == "revisar"
        assert any("complexidade" in d for d in avaliada.duvidas)


class TestMedirComProjetoEmDisco:
    def test_mede_e_cruza_com_a_cobertura(self, projeto):
        config = Config(raiz=projeto)
        medicao = medir(
            [str(projeto / "src")], str(projeto / "lcov.info"), config=config
        )
        nomes = {f.nome for f in medicao.funcoes}
        assert nomes == {"soma", "divide"}
        divide = next(f for f in medicao.funcoes if f.nome == "divide")
        assert divide.cobertura_linha == 1.0
        assert divide.cobertura_branch == 1.0

    def test_arquivo_de_teste_nao_e_avaliado(self, projeto):
        """Avaliar o próprio teste polui o relatório e gasta token sem responder nada."""
        medicao = medir([str(projeto)], config=Config(raiz=projeto))
        relativos = [str(Path(f.arquivo).relative_to(projeto)) for f in medicao.funcoes]
        assert relativos == ["src/calculo.py", "src/calculo.py"]

    def test_projeto_que_mora_em_pasta_chamada_test_nao_some_inteiro(self, tmp_path):
        """A armadilha: relativizar contra a raiz analisada é o que impede que
        `~/dev/meu-test/` classifique todo o código como teste e devolva zero
        função sem explicar por quê. O diretório do pytest já se chama assim."""
        raiz = tmp_path / "projeto-de-teste" / "src"
        raiz.mkdir(parents=True)
        (raiz / "app.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
        medicao = medir([str(raiz)], config=Config(raiz=raiz))
        assert [f.nome for f in medicao.funcoes] == ["f"]

    def test_sem_cobertura_o_aviso_explica_por_que_tudo_ficou_vermelho(self, projeto):
        medicao = medir([str(projeto / "src")], config=Config(raiz=projeto))
        assert any("nenhum relatório de cobertura" in a for a in medicao.avisos)
        assert all(f.cobertura_linha == SEM_DADOS for f in medicao.funcoes)

    def test_relatorio_que_nao_casa_com_nada_e_denunciado(self, projeto, tmp_path):
        """Quase tudo acima do limiar costuma ser isto, não projeto ruim."""
        outro = tmp_path / "outro.info"
        outro.write_text("TN:x\nSF:/build/nada/a-ver.py\nDA:1,1\nend_of_record\n")
        medicao = medir([str(projeto / "src")], str(outro), config=Config(raiz=projeto))
        assert any("nenhum arquivo analisado casou" in a for a in medicao.avisos)

    def test_caminho_inexistente_vira_situacao_com_saida(self, config):
        with pytest.raises(SituacaoConhecida) as erro:
            medir(["nao/existe/mesmo"], config=config)
        assert erro.value.situacao == "caminho_inexistente"
        assert erro.value.como_resolver

    def test_sem_caminhos_recusa_em_vez_de_medir_o_mundo(self, config):
        with pytest.raises(SituacaoConhecida) as erro:
            medir([], config=config)
        assert erro.value.situacao == "sem_caminhos"

    def test_cobertura_inexistente_ensina_como_gerar(self, projeto):
        with pytest.raises(SituacaoConhecida) as erro:
            medir([str(projeto / "src")], "nao-existe.info", config=Config(raiz=projeto))
        assert erro.value.situacao == "cobertura_inexistente"
        assert "--cov" in erro.value.como_resolver

    def test_cobertura_em_formato_errado_e_recusada(self, projeto, tmp_path):
        ruim = tmp_path / "relatorio.json"
        ruim.write_text('{"nao": "e lcov"}')
        with pytest.raises(SituacaoConhecida) as erro:
            medir([str(projeto / "src")], str(ruim), config=Config(raiz=projeto))
        assert erro.value.situacao == "cobertura_ilegivel"


class TestAvaliarCompleto:
    def test_so_julga_o_que_passou_do_limiar(self, projeto, rubrica):
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=julgador,
            rubrica=rubrica,
            limiar=1000.0,
        )
        assert len(julgador.chamadas) == 0
        assert relatorio["resumo"]["julgadas"] == 0
        assert relatorio["resumo"]["resultado"] == "sem_julgamento"

    def test_limiar_zero_julga_tudo(self, projeto, rubrica):
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=julgador,
            rubrica=rubrica,
            limiar=0.0,
        )
        assert len(julgador.chamadas) == 2

    def test_o_preco_do_recorte_esta_dito_e_nao_escondido(self, projeto, rubrica):
        """Filtrar por risco cega o caso mais interessante: complexidade baixa
        com código ilegível."""
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica,
            limiar=1000.0,
        )
        assert any("abaixo do limiar não foram julgadas" in a for a in relatorio["avisos"])

    def test_o_teto_de_julgamentos_e_denunciado(self, projeto, rubrica):
        config = Config(raiz=projeto, max_julgamentos=1)
        relatorio = avaliar(
            [str(projeto / "src")],
            config=config,
            julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica,
            limiar=0.0,
        )
        assert relatorio["resumo"]["julgadas"] == 1
        assert any("teto de 1 julgamentos" in a for a in relatorio["avisos"])

    def test_uma_falha_nao_derruba_a_batelada(self, projeto, rubrica, monkeypatch):
        """Com `map`, uma exceção descartava tudo que já tinha sido pago."""
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        chamadas = {"n": 0}
        original = julgador.julgar

        def as_vezes_falha(estado, regua):
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                raise RuntimeError("a primeira falhou")
            return original(estado, regua)

        monkeypatch.setattr(julgador, "julgar", as_vezes_falha)
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=julgador,
            rubrica=rubrica,
            limiar=0.0,
        )
        assert len(relatorio["falhas"]) == 1
        assert relatorio["resumo"]["julgadas"] == 2
        assert any(f["veredito"] != "sem_julgamento" for f in relatorio["funcoes"])

    def test_falha_parcial_nao_sai_como_aprovar(self, projeto, rubrica, monkeypatch):
        """O exit code é o que um CI lê; silêncio não pode virar sinal verde."""
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        monkeypatch.setattr(
            julgador, "julgar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caiu"))
        )
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=julgador,
            rubrica=rubrica,
            limiar=0.0,
        )
        assert relatorio["resumo"]["resultado"] != "aprovar"

    def test_eixo_desligado_e_dito_no_relatorio(self, projeto, rubrica):
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=JulgadorDesligado(),
            rubrica=rubrica,
        )
        eixo = relatorio["eixo_semantico"]
        assert eixo["ligado"] is False
        assert "TYPESAFE_API_KEY" in eixo["motivo"]
        assert relatorio["resumo"]["resultado"] == "sem_julgamento"

    def test_os_trechos_de_teste_chegam_ao_modelo(self, projeto, rubrica):
        julgador = JulgadorFake(RESPOSTAS_BOAS)
        avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=julgador,
            rubrica=rubrica,
            limiar=0.0,
            pasta_testes=str(projeto / "tests"),
        )
        com_teste = [c for c in julgador.chamadas if c.get("testes")]
        assert com_teste, "nenhum trecho de teste foi enviado"
        trecho = com_teste[0]["testes"][0]
        # O corpo do teste, e não a linha de import: recortar do começo do
        # arquivo alcançava só o import, e o modelo respondia "não há teste"
        # corretamente, sobre o material errado.
        assert "def test_" in trecho
        assert "from src.calculo import" not in trecho

    def test_o_veredito_da_rodada_e_o_pior_de_suas_funcoes(self, projeto, rubrica):
        relatorio = avaliar(
            [str(projeto / "src")],
            config=Config(raiz=projeto),
            julgador=JulgadorFake({**RESPOSTAS_BOAS, "injecao": noul(0.99)}),
            rubrica=rubrica,
            limiar=0.0,
        )
        assert relatorio["resumo"]["resultado"] == "bloquear"


class TestJulgarTrecho:
    CODIGO = (
        "def cobrar(valor, cliente):\n"
        "    if valor <= 0:\n"
        "        raise ValueError('valor inválido')\n"
        "    return valor * cliente.taxa\n"
    )

    def test_mede_a_complexidade_do_proprio_texto(self, rubrica, config):
        resultado = julgar_trecho(
            self.CODIGO,
            config=config,
            julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica,
            arquivo="cobranca.py",
        )
        funcao = resultado["funcao"]
        assert funcao["funcao"] == "cobrar"
        assert funcao["complexidade"] == 2
        assert funcao["linguagem"] == "python"

    def test_escolhe_a_mais_complexa_quando_ha_varias(self, rubrica, config):
        """Quem cola um trecho com uma auxiliar de duas linhas no topo quer a
        resposta sobre a outra."""
        codigo = "def trivial():\n    return 1\n\n\n" + self.CODIGO
        resultado = julgar_trecho(
            codigo, config=config, julgador=JulgadorFake(RESPOSTAS_BOAS), rubrica=rubrica
        )
        assert resultado["funcao"]["funcao"] == "cobrar"
        assert any("2 funções" in a for a in resultado["avisos"])

    def test_respeita_a_funcao_pedida_pelo_nome(self, rubrica, config):
        codigo = "def trivial():\n    return 1\n\n\n" + self.CODIGO
        resultado = julgar_trecho(
            codigo,
            config=config,
            julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica,
            funcao="trivial",
        )
        assert resultado["funcao"]["funcao"] == "trivial"

    def test_trecho_vazio_e_recusado_com_instrucao(self, rubrica, config):
        with pytest.raises(SituacaoConhecida) as erro:
            julgar_trecho("   ", config=config, julgador=JulgadorFake(RESPOSTAS_BOAS))
        assert erro.value.situacao == "trecho_vazio"
        assert "avaliar_arquivos" in erro.value.como_resolver

    def test_sem_chave_diz_o_que_fazer_em_vez_de_falhar_mudo(self, rubrica, config):
        with pytest.raises(SituacaoConhecida) as erro:
            julgar_trecho(self.CODIGO, config=config, julgador=JulgadorDesligado())
        assert erro.value.situacao == "eixo_semantico_desligado"
        assert "medir_risco" in erro.value.como_resolver

    def test_julgamento_vazio_nao_vira_relatorio_falso(self, rubrica, config):
        with pytest.raises(SituacaoConhecida) as erro:
            julgar_trecho(
                self.CODIGO, config=config, julgador=JulgadorFake({}, erro=True), rubrica=rubrica
            )
        assert erro.value.situacao == "julgamento_indisponivel"

    def test_linguagem_desconhecida_avisa_em_vez_de_inventar_complexidade(
        self, rubrica, config
    ):
        resultado = julgar_trecho(
            "qualquer coisa",
            config=config,
            julgador=JulgadorFake(RESPOSTAS_BOAS),
            rubrica=rubrica,
            arquivo="trecho.qqcoisa",
        )
        assert any("nenhuma função foi reconhecida" in a for a in resultado["avisos"])
        assert resultado["funcao"]["complexidade"] == 1

    def test_avisa_que_o_numero_nao_e_comparavel_sem_cobertura(self, rubrica, config):
        resultado = julgar_trecho(
            self.CODIGO, config=config, julgador=JulgadorFake(RESPOSTAS_BOAS), rubrica=rubrica
        )
        assert any("sem cobertura informada" in a for a in resultado["avisos"])
        assert any("nenhum trecho de teste" in a for a in resultado["avisos"])


class TestRespostaRealCongelada:
    def test_a_resposta_real_produz_numeros_plausiveis(self, medida, rubrica, config):
        """Guarda contra o erro de escala: se alguém trocar a normalização, a
        nota sai de 45.7 e este teste diz exatamente quanto."""
        avaliada = decidida(medida, RESPOSTAS_REAIS, config, rubrica)
        assert avaliada.nota == pytest.approx(45.7, abs=0.1)
        assert avaliada.faixa == "frágil"
        assert avaliada.graves == ()
        assert avaliada.veredito == "revisar"
