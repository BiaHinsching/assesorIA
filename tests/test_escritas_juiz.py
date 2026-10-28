"""
O contrato do "Juiz Tardio", sem rede e sem Postgres:

    turno recusado  → nenhuma escrita do turno permanece
    turno aprovado  → a escrita acontece, uma vez

Três camadas: (1) as tools com a unidade de trabalho, (2) o veredito do juiz como
dado, (3) o turno inteiro por executar_fluxo_assessor, com o grafo real de
duas etapas (escreve → juiz) e o juiz real falando com um JEV falso.
"""

import datetime
import urllib.error

import pytest
from langchain_core.runnables import RunnableConfig

from helpers_juiz import FabricaDeConexoes, jev_falso, montar_fluxo

TS = datetime.datetime(2026, 10, 27, 12, 0)
LINHA_UPDATE = (7, TS, 50.0, "EXPENSES", "lazer", "cinema", "pix", "gastei 50")


@pytest.fixture
def fabrica(monkeypatch):
    import app.db as db

    f = FabricaDeConexoes()
    monkeypatch.setattr(db, "get_conn", f)
    return f


@pytest.fixture
def uow():
    from app.db import UnidadeDeTrabalho

    return UnidadeDeTrabalho()


def _cfg(uow):
    return {"configurable": {"uow": uow}}


# ══ 1 · as tools ═════════════════════════════════════════════════════════════

def test_add_transaction_no_turno_nao_commita_nem_fecha(fabrica, uow):
    from app.tools.financeiro import add_transaction

    fabrica.kwargs["respostas"] = [(7, TS)]
    out = add_transaction.invoke({"amount": 50, "source_text": "gastei 50"}, config=_cfg(uow))
    assert out["status"] == "ok" and out["id"] == 7
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and not conn.closed


def test_update_transaction_no_turno_nao_commita_nem_fecha(fabrica, uow):
    from app.tools.financeiro import update_transaction

    fabrica.kwargs["respostas"] = [LINHA_UPDATE]
    out = update_transaction.invoke({"id": 7, "amount": 50}, config=_cfg(uow))
    assert out["status"] == "ok" and out["updated"]["id"] == 7
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and not conn.closed


def test_add_event_no_turno_nao_commita_nem_fecha(fabrica, uow):
    from app.tools.agenda import add_event

    fabrica.kwargs["respostas"] = [(3, TS, None)]
    out = add_event.invoke(
        {"title": "dentista", "source_text": "dentista", "start_time": "2026-10-28T15:00:00-03:00"},
        config=_cfg(uow),
    )
    assert out["status"] == "ok" and out["id"] == 3
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and not conn.closed


def test_sem_uow_as_tools_continuam_commitando_como_antes(fabrica):
    """Caminho do mcp_server.py e de scripts: sem config, comportamento de sempre."""
    from app.tools.financeiro import add_transaction

    fabrica.kwargs["respostas"] = [(7, TS)]
    out = add_transaction.invoke({"amount": 50, "source_text": "gastei 50"})
    assert out["status"] == "ok"
    conn = fabrica.conexoes[0]
    assert conn.commits == 1 and conn.closed


def test_leituras_no_turno_usam_a_conexao_do_turno_e_nao_a_fecham(fabrica, uow):
    from app.tools.agenda import query_events
    from app.tools.financeiro import saldo_diario, saldo_total, search_transactions

    fabrica.kwargs["respostas"] = [(10.0,), (5.0,)]
    search_transactions.invoke({"text": "cafe"}, config=_cfg(uow))
    saldo_total.invoke({}, config=_cfg(uow))
    saldo_diario.invoke({"date_local": "2026-10-27"}, config=_cfg(uow))
    query_events.invoke({"date_local": "2026-10-27"}, config=_cfg(uow))
    assert len(fabrica.conexoes) == 1
    assert not fabrica.conexoes[0].closed


def test_erro_sql_de_uma_tool_nao_envenena_as_outras_do_turno(monkeypatch, uow):
    """
    O modelo manda uma data inválida e a tool devolve erro (como hoje); a
    correção na volta seguinte do loop precisa funcionar na MESMA transação.
    """
    import app.db as db
    from app.tools.agenda import add_event

    quebrar = {"ativo": True}
    f = FabricaDeConexoes(
        respostas=[(3, TS, None)],
        falhar_quando=lambda sql: quebrar["ativo"] and sql.startswith("INSERT INTO events"),
    )
    monkeypatch.setattr(db, "get_conn", f)

    args = {"title": "x", "source_text": "x", "start_time": "ontem"}
    ruim = add_event.invoke(args, config=_cfg(uow))
    assert ruim["status"] == "error"

    quebrar["ativo"] = False
    bom = add_event.invoke({**args, "start_time": "2026-10-28T15:00:00-03:00"}, config=_cfg(uow))
    assert bom["status"] == "ok"
    log = f.conexoes[0].log
    assert "ROLLBACK TO SAVEPOINT sp_1" in log and "RELEASE SAVEPOINT sp_2" in log
    assert f.conexoes[0].rollbacks == 0


# ── Google: adiado até o veredito ────────────────────────────────────────────

@pytest.fixture
def google(monkeypatch):
    import app.tools.calendario_google as cg

    chamadas = []

    def falso(title, start_time, end_time, location, description):
        chamadas.append(title)
        return {"status": "ok", "id": "g1"}

    monkeypatch.setattr(cg, "_criar_evento_google", falso)
    return chamadas


ARGS_GOOGLE = {"title": "dentista", "start_time": "2026-10-28T15:00:00-03:00"}


def test_google_no_turno_so_registra_a_intencao(uow, google):
    from app.tools.calendario_google import add_google_event

    out = add_google_event.invoke(ARGS_GOOGLE, config=_cfg(uow))
    assert out["status"] == "pendente_aprovacao"
    assert google == []                      # o Google ainda não foi chamado


def test_google_no_turno_recusado_nunca_e_chamado(fabrica, uow, google):
    from app.tools.calendario_google import add_google_event

    add_google_event.invoke(ARGS_GOOGLE, config=_cfg(uow))
    uow.finalizar(False, "resp")
    uow.encerrar()
    assert google == []


def test_google_no_turno_aprovado_e_chamado_uma_vez(fabrica, uow, google):
    from app.tools.calendario_google import add_google_event

    add_google_event.invoke(ARGS_GOOGLE, config=_cfg(uow))
    assert uow.finalizar(True, "resp") == "resp"
    assert google == ["dentista"]


def test_google_valida_o_iso_na_hora_para_o_modelo_poder_corrigir(uow, google):
    from app.tools.calendario_google import add_google_event

    out = add_google_event.invoke({"title": "x", "start_time": "amanhã às 15h"}, config=_cfg(uow))
    assert out["status"] == "error"
    assert "start_time" in out["message"]
    uow.finalizar(True, "resp")
    assert google == []


def test_google_sem_uow_continua_imediato(google):
    from app.tools.calendario_google import add_google_event

    out = add_google_event.invoke(ARGS_GOOGLE)
    assert out["status"] == "ok"
    assert google == ["dentista"]


# ══ 2 · o veredito como dado ═════════════════════════════════════════════════

def _estado_lc(resposta="Registrei o gasto."):
    from langchain_core.messages import AIMessage, HumanMessage

    return {"messages": [HumanMessage(content="gastei 50"), AIMessage(content=resposta)]}


def test_juiz_aprova_so_com_apoiada_e_noul_acima_do_limiar(monkeypatch):
    import app.juiz as j

    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(j.urllib.request, "urlopen", jev_falso("apoiada", 0.95))
    assert j.no_juiz(_estado_lc())["juiz_aprovado"] is True


@pytest.mark.parametrize("choice,noul", [
    ("apoiada", 0.40),      # abaixo do limiar
    ("parcial", 0.99),
    ("sem_apoio", 0.99),
])
def test_juiz_recusa(monkeypatch, choice, noul):
    import app.juiz as j

    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(j.urllib.request, "urlopen", jev_falso(choice, noul))
    assert j.no_juiz(_estado_lc())["juiz_aprovado"] is False


def test_juiz_sem_chave_recusa(monkeypatch):
    import app.juiz as j

    monkeypatch.setattr(j, "TYPESAFE_API_KEY", None)
    assert j.no_juiz(_estado_lc())["juiz_aprovado"] is False


@pytest.mark.parametrize("erro", [
    urllib.error.HTTPError("u", 500, "x", {}, None),
    urllib.error.URLError("sem rede"),
    TimeoutError("30s"),
    ValueError("json inválido"),
])
def test_juiz_com_falha_recusa(monkeypatch, erro):
    import app.juiz as j

    def quebrado(*a, **k):
        raise erro

    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(j.urllib.request, "urlopen", quebrado)
    assert j.no_juiz(_estado_lc())["juiz_aprovado"] is False


def test_juiz_sem_resposta_recusa():
    import app.juiz as j

    assert j.no_juiz({"messages": []})["juiz_aprovado"] is False


# ══ 3 · o turno inteiro ══════════════════════════════════════════════════════

@pytest.fixture
def turno(monkeypatch, fabrica, google):
    """executar_fluxo_assessor com o grafo real de duas etapas e o Mongo desligado."""
    import app.graph as g
    import app.juiz as j
    from app.tools.agenda import add_event
    from app.tools.calendario_google import add_google_event
    from app.tools.financeiro import add_transaction, update_transaction

    gravadas = []
    monkeypatch.setattr(g, "salvar_mensagem", lambda *a, **k: gravadas.append(a))
    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")
    # cada conexão do turno recebe uma cópia destas respostas, na ordem das consultas
    fabrica.kwargs["respostas"] = [(7, TS), LINHA_UPDATE, (3, TS, None)]

    def escrever(estado, config: RunnableConfig):
        add_transaction.invoke({"amount": 50, "source_text": "QA-1"}, config=config)
        update_transaction.invoke({"id": 7, "amount": 60}, config=config)
        add_event.invoke(
            {"title": "QA", "source_text": "QA-1", "start_time": "2026-10-28T15:00:00-03:00"},
            config=config,
        )
        add_google_event.invoke({"title": "QA", "start_time": "2026-10-28T15:00:00-03:00"}, config=config)
        return {"messages": [{"role": "assistant", "content": "Registrei tudo."}]}

    class Ctx:
        pass

    ctx = Ctx()
    ctx.g = g
    ctx.gravadas = gravadas
    ctx.conexoes = fabrica.conexoes
    ctx.fabrica = fabrica
    ctx.google = google
    ctx.monkeypatch = monkeypatch
    ctx.fluxo = lambda f=escrever: monkeypatch.setattr(g, "fluxo_agentes", montar_fluxo(f))
    ctx.fluxo()
    return ctx


def _jev(ctx, choice, noul=0.95):
    import app.juiz as j

    ctx.monkeypatch.setattr(j.urllib.request, "urlopen", jev_falso(choice, noul))


def test_turno_aprovado_grava_uma_vez(turno):
    _jev(turno, "apoiada")
    out = turno.g.executar_fluxo_assessor("gastei 50", "s1")
    assert out["resposta"] == "Registrei tudo."
    assert len(turno.conexoes) == 1                 # uma conexão por turno, não uma por tool
    conn = turno.conexoes[0]
    assert conn.commits == 1 and conn.rollbacks == 0 and conn.closed
    assert turno.google == ["QA"]                   # Google depois do veredito, uma vez


def test_turno_recusado_nao_deixa_escrita(turno):
    _jev(turno, "sem_apoio")
    out = turno.g.executar_fluxo_assessor("gastei 50", "s1")
    assert out["resposta"] == "Não tenho evidência para afirmar isso."
    conn = turno.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1 and conn.closed
    assert turno.google == []
    # as escritas chegaram a ser executadas (INSERT/UPDATE), mas só pendentes:
    assert any(s.startswith("INSERT INTO transactions") for s in conn.log)
    assert any(s.startswith("UPDATE transactions") for s in conn.log)
    assert any(s.startswith("INSERT INTO events") for s in conn.log)


def test_turno_com_juiz_sem_chave_nao_deixa_escrita(turno):
    import app.juiz as j

    turno.monkeypatch.setattr(j, "TYPESAFE_API_KEY", None)
    turno.g.executar_fluxo_assessor("gastei 50", "s1")
    conn = turno.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1
    assert turno.google == []


def test_turno_com_juiz_em_timeout_nao_deixa_escrita(turno):
    import app.juiz as j

    def estoura(*a, **k):
        raise TimeoutError("30s")

    turno.monkeypatch.setattr(j.urllib.request, "urlopen", estoura)
    turno.g.executar_fluxo_assessor("gastei 50", "s1")
    conn = turno.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1
    assert turno.google == []


def test_excecao_num_no_do_grafo_desfaz_tudo_e_fecha(turno):
    from app.tools.financeiro import add_transaction

    def quebra_depois_de_gravar(estado, config: RunnableConfig):
        add_transaction.invoke({"amount": 50, "source_text": "QA-1"}, config=config)
        raise RuntimeError("Groq caiu")

    turno.fluxo(quebra_depois_de_gravar)
    with pytest.raises(RuntimeError):
        turno.g.executar_fluxo_assessor("gastei 50", "s1")
    conn = turno.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1 and conn.closed


def test_google_que_falha_depois_da_aprovacao_avisa_sem_desfazer_o_banco(turno):
    import app.tools.calendario_google as cg

    turno.monkeypatch.setattr(cg, "_criar_evento_google", lambda *a, **k: {"status": "error", "message": "403"})
    _jev(turno, "apoiada")
    out = turno.g.executar_fluxo_assessor("gastei 50", "s1")
    assert out["resposta"].startswith("Registrei tudo.")
    assert cg.AVISO_GOOGLE_FALHOU in out["resposta"]
    assert turno.conexoes[0].commits == 1


def test_falha_no_commit_depois_da_aprovacao_vira_erro_e_nao_chama_o_google(turno):
    import app.db as db

    original = db.get_conn

    def get_conn_com_commit_quebrado():
        conn = original()

        def quebrado():
            raise RuntimeError("conexão caiu")

        conn.commit = quebrado
        return conn

    turno.monkeypatch.setattr(db, "get_conn", get_conn_com_commit_quebrado)
    _jev(turno, "apoiada")
    with pytest.raises(RuntimeError):
        turno.g.executar_fluxo_assessor("gastei 50", "s1")
    assert turno.conexoes[0].rollbacks == 1
    assert turno.google == []


def test_mongo_recebe_o_texto_final_depois_da_decisao(turno):
    _jev(turno, "sem_apoio")
    turno.g.executar_fluxo_assessor("gastei 50", "s1")
    assert turno.gravadas[-1][1:3] == ("assistant", "Não tenho evidência para afirmar isso.")


def test_veredito_de_um_turno_nao_vaza_para_o_seguinte_na_mesma_sessao(turno):
    """
    O MemorySaver guarda o Estado entre turnos. O 2º turno não passa pelo juiz,
    então nada o sobrescreve: se o `juiz_aprovado = True` do 1º vazasse, as
    escritas do 2º seriam commitadas sem veredito nenhum.
    """
    import app.graph as g
    from app.tools.financeiro import add_transaction

    def escrever(estado, config: RunnableConfig):
        add_transaction.invoke({"amount": 50, "source_text": "QA-1"}, config=config)
        return {"messages": [{"role": "assistant", "content": "Registrei."}]}

    def sem_juiz(estado):
        humanas = [m for m in estado["messages"] if m.type == "human"]
        return "sem juiz" in humanas[-1].content

    turno.monkeypatch.setattr(g, "fluxo_agentes", montar_fluxo(escrever, pular_juiz=sem_juiz))
    turno.fabrica.kwargs["respostas"] = [(7, TS)]
    _jev(turno, "apoiada")
    g.executar_fluxo_assessor("gastei 50", "s1")
    g.executar_fluxo_assessor("gastei 70 sem juiz", "s1")
    primeiro, segundo = turno.conexoes
    assert primeiro.commits == 1
    assert segundo.commits == 0 and segundo.rollbacks == 1


def test_turno_que_nao_passa_pelo_juiz_nao_abre_conexao_nem_commita(monkeypatch, fabrica):
    """Bloqueio de entrada ou resposta direta do roteador: sem escrita, sem conexão."""
    import app.graph as g
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    sg = StateGraph(g.Estado)
    sg.add_node("direto", lambda estado: {"messages": [{"role": "assistant", "content": "Olá!"}]})
    sg.set_entry_point("direto")
    sg.add_edge("direto", END)
    monkeypatch.setattr(g, "fluxo_agentes", sg.compile(checkpointer=MemorySaver()))
    monkeypatch.setattr(g, "salvar_mensagem", lambda *a, **k: None)
    assert g.executar_fluxo_assessor("oi", "s1")["resposta"] == "Olá!"
    assert fabrica.conexoes == []


# ══ 4 · o agente de verdade (create_agent + ToolNode) ════════════════════════

class _ModeloFalso:
    """Chat model que devolve respostas roteirizadas e aceita bind_tools."""

    @staticmethod
    def criar(respostas):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

        class Modelo(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        return Modelo(responses=respostas)


@pytest.mark.parametrize("veredito,commits,rollbacks,google_chamado", [
    ("apoiada", 1, 0, ["t"]),
    ("sem_apoio", 0, 1, []),
])
def test_agente_real_com_tool_calls_paralelas(monkeypatch, fabrica, google, veredito, commits, rollbacks, google_chamado):
    """
    O caminho de produção: um agente create_agent, como financeiro_app/agenda_app,
    emite três tool calls no mesmo passo (o ToolNode as roda em threads). A unidade
    de trabalho precisa chegar às três pelo config, usar UMA conexão, e o
    veredito decidir o destino de tudo — inclusive do Google.
    """
    from langchain.agents import create_agent
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    import app.graph as g
    import app.juiz as j
    from app.juiz import no_juiz
    from app.tools.agenda import add_event
    from app.tools.calendario_google import add_google_event
    from app.tools.financeiro import add_transaction

    fabrica.kwargs["respostas"] = [(7, TS), (3, TS, None)]
    monkeypatch.setattr(g, "salvar_mensagem", lambda *a, **k: None)
    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(j.urllib.request, "urlopen", jev_falso(veredito, 0.95))

    modelo = _ModeloFalso.criar([
        AIMessage(content="", tool_calls=[
            {"name": "add_transaction", "args": {"amount": 5, "source_text": "x"}, "id": "c1"},
            {"name": "add_event", "args": {"title": "t", "source_text": "x", "start_time": "2026-10-28T15:00:00-03:00"}, "id": "c2"},
            {"name": "add_google_event", "args": {"title": "t", "start_time": "2026-10-28T15:00:00-03:00"}, "id": "c3"},
        ]),
        AIMessage(content="Registrei tudo."),
    ])
    agente = create_agent(model=modelo, tools=[add_transaction, add_event, add_google_event], system_prompt="x")
    sg = StateGraph(g.Estado)
    sg.add_node("especialista", agente)
    sg.add_node("juiz", no_juiz)
    sg.set_entry_point("especialista")
    sg.add_edge("especialista", "juiz")
    sg.add_edge("juiz", END)
    monkeypatch.setattr(g, "fluxo_agentes", sg.compile(checkpointer=MemorySaver()))

    g.executar_fluxo_assessor("gastei 5", "s-agente")

    assert len(fabrica.conexoes) == 1
    conn = fabrica.conexoes[0]
    assert conn.commits == commits and conn.rollbacks == rollbacks and conn.closed
    assert google == google_chamado
    # as duas escritas aconteceram na mesma transação, cada uma no seu savepoint
    assert sum(s.startswith("INSERT INTO") for s in conn.log) == 2
    assert conn.log.count("RELEASE SAVEPOINT sp_1") == 1 and conn.log.count("RELEASE SAVEPOINT sp_2") == 1
