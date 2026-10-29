"""
O contrato do "Juiz Tardio" contra um PostgreSQL de verdade.

    turno recusado  → nenhuma escrita do turno permanece
    turno aprovado  → a escrita acontece, uma vez

Só roda com TEST_DATABASE_URL apontando para um banco DESCARTÁVEL (nunca o do
.env). Cada execução cria um schema próprio, com as tabelas abaixo, e o derruba
inteiro no fim — nenhuma linha é apagada à mão. O DDL é o que as queries de
app/tools/ exigem; o schema real do projeto não está no repositório, então
constraints e índices de produção NÃO estão sendo testados aqui.

    1. suba um Postgres 16 descartável (ex.: um contêiner Docker com --rm) e crie um banco vazio;
    2. TEST_DATABASE_URL=<url do banco descartável> pytest tests/test_juiz_tardio_postgres.py

Cada caso usa um marcador único (QA-xxxxxxxx) e confere o estado de ANTES e de
DEPOIS por uma conexão independente — o que outra sessão enxerga.
"""

import os
import threading
import time
import uuid

import pytest
from langchain_core.runnables import RunnableConfig

from helpers_juiz import jev_falso, montar_fluxo

URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="defina TEST_DATABASE_URL (banco descartável)")

DDL = """
CREATE TABLE transaction_types (id int PRIMARY KEY, type text NOT NULL);
INSERT INTO transaction_types VALUES (1,'INCOME'),(2,'EXPENSES'),(3,'TRANSFER');
CREATE TABLE categories (id int PRIMARY KEY, name text NOT NULL);
INSERT INTO categories VALUES (1,'comida'),(12,'outros');
CREATE TABLE transactions (
    id serial PRIMARY KEY, amount numeric NOT NULL,
    type int REFERENCES transaction_types(id), category_id int REFERENCES categories(id),
    description text, payment_method text,
    occurred_at timestamptz NOT NULL DEFAULT now(), source_text text);
CREATE TABLE events (
    id serial PRIMARY KEY, title text NOT NULL, start_time timestamptz NOT NULL,
    end_time timestamptz, location text, notes text, source_text text,
    recorded_at timestamptz NOT NULL DEFAULT now());
"""


def _conectar(url):
    import psycopg2

    return psycopg2.connect(url)


@pytest.fixture(scope="module")
def url_do_schema():
    schema = f"qa_{uuid.uuid4().hex[:10]}"
    admin = _conectar(URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path = {schema}")
        cur.execute(DDL)
    sep = "&" if "?" in URL else "?"
    yield f"{URL}{sep}options=-csearch_path%3D{schema}"
    with admin.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    admin.close()


@pytest.fixture
def pg(monkeypatch, url_do_schema):
    import app.db as db

    monkeypatch.setattr(db, "DATABASE_URL", url_do_schema)
    return url_do_schema


@pytest.fixture
def outra_sessao(pg):
    """Uma sessão independente: só enxerga o que foi confirmado."""
    conn = _conectar(pg)
    conn.autocommit = True
    yield conn
    conn.close()


def _um(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _marcador():
    return f"QA-{uuid.uuid4().hex[:8]}"


def _n_transacoes(conn, m):
    return _um(conn, "SELECT count(*) FROM transactions WHERE source_text LIKE %s", (f"%{m}%",))


def _n_eventos(conn, m):
    return _um(conn, "SELECT count(*) FROM events WHERE source_text LIKE %s", (f"%{m}%",))


def _foto(conn, id_):
    return _um(conn, "SELECT md5(t::text) FROM transactions t WHERE id = %s", (id_,))


def _semear_transacao(pg, m):
    conn = _conectar(pg)
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO transactions (amount, type, category_id, description, source_text) "
            "VALUES (10, 2, 12, 'original', %s) RETURNING id",
            (m,),
        )
        id_ = cur.fetchone()[0]
    conn.close()
    return id_


# ── um turno completo (grafo real de duas etapas + juiz real + JEV falso) ────

@pytest.fixture
def rodar_turno(monkeypatch, pg):
    import app.graph as g
    import app.juiz as j

    monkeypatch.setattr(g, "salvar_mensagem", lambda *a, **k: None)
    monkeypatch.setattr(j, "TYPESAFE_API_KEY", "k")

    def rodar(escrever, veredito="apoiada", sessao=None, chave="k"):
        monkeypatch.setattr(j, "TYPESAFE_API_KEY", chave)
        monkeypatch.setattr(j.urllib.request, "urlopen", jev_falso(veredito, 0.95))
        monkeypatch.setattr(g, "fluxo_agentes", montar_fluxo(escrever))
        return g.executar_fluxo_assessor("pedido", sessao or uuid.uuid4().hex)

    return rodar


def _escreve_transacao(m):
    from app.tools.financeiro import add_transaction

    def escrever(estado, config: RunnableConfig):
        add_transaction.invoke(
            {"amount": 37.13, "source_text": m, "type_name": "EXPENSES", "category_name": "comida"},
            config=config,
        )
        return {"messages": [{"role": "assistant", "content": "Registrei o gasto."}]}

    return escrever


def _escreve_evento(m):
    from app.tools.agenda import add_event

    def escrever(estado, config: RunnableConfig):
        add_event.invoke(
            {"title": m, "source_text": m, "start_time": "2026-10-28T15:00:00-03:00"}, config=config
        )
        return {"messages": [{"role": "assistant", "content": "Marquei o compromisso."}]}

    return escrever


def _escreve_alteracao(id_, m):
    from app.tools.financeiro import update_transaction

    def escrever(estado, config: RunnableConfig):
        update_transaction.invoke({"id": id_, "amount": 99.99, "description": m}, config=config)
        return {"messages": [{"role": "assistant", "content": "Alterei o gasto."}]}

    return escrever


# ══ criação e alteração: recusa não deixa rastro, aprovação grava uma vez ════

def test_add_transaction_recusado_nao_deixa_linha(rodar_turno, outra_sessao):
    m = _marcador()
    assert _n_transacoes(outra_sessao, m) == 0
    rodar_turno(_escreve_transacao(m), veredito="sem_apoio")
    assert _n_transacoes(outra_sessao, m) == 0


def test_add_transaction_aprovado_grava_uma_vez(rodar_turno, outra_sessao):
    m = _marcador()
    rodar_turno(_escreve_transacao(m), veredito="apoiada")
    assert _n_transacoes(outra_sessao, m) == 1


def test_update_transaction_recusado_nao_altera_a_linha(rodar_turno, outra_sessao, pg):
    m = _marcador()
    id_ = _semear_transacao(pg, m)
    antes = _foto(outra_sessao, id_)
    rodar_turno(_escreve_alteracao(id_, m), veredito="parcial")
    assert _foto(outra_sessao, id_) == antes


def test_update_transaction_aprovado_altera_a_linha(rodar_turno, outra_sessao, pg):
    m = _marcador()
    id_ = _semear_transacao(pg, m)
    rodar_turno(_escreve_alteracao(id_, m), veredito="apoiada")
    assert float(_um(outra_sessao, "SELECT amount FROM transactions WHERE id = %s", (id_,))) == 99.99
    assert _um(outra_sessao, "SELECT description FROM transactions WHERE id = %s", (id_,)) == m


def test_add_event_recusado_nao_deixa_linha(rodar_turno, outra_sessao):
    m = _marcador()
    rodar_turno(_escreve_evento(m), veredito="sem_apoio")
    assert _n_eventos(outra_sessao, m) == 0


def test_add_event_aprovado_grava_uma_vez(rodar_turno, outra_sessao):
    m = _marcador()
    rodar_turno(_escreve_evento(m), veredito="apoiada")
    assert _n_eventos(outra_sessao, m) == 1


def test_turno_recusado_com_as_tres_escritas_desfaz_todas(rodar_turno, outra_sessao, pg):
    from app.tools.agenda import add_event
    from app.tools.financeiro import add_transaction, update_transaction

    m = _marcador()
    id_ = _semear_transacao(pg, m)
    antes = _foto(outra_sessao, id_)

    def escrever(estado, config: RunnableConfig):
        add_transaction.invoke({"amount": 1, "source_text": m, "type_id": 2}, config=config)
        update_transaction.invoke({"id": id_, "amount": 5}, config=config)
        add_event.invoke({"title": m, "source_text": m, "start_time": "2026-10-28T15:00:00-03:00"}, config=config)
        return {"messages": [{"role": "assistant", "content": "Feito."}]}

    rodar_turno(escrever, veredito="sem_apoio")
    assert _n_transacoes(outra_sessao, m) == 1       # só a semente
    assert _n_eventos(outra_sessao, m) == 0
    assert _foto(outra_sessao, id_) == antes


def test_excecao_no_meio_do_turno_nao_deixa_escrita(rodar_turno, outra_sessao):
    from app.tools.financeiro import add_transaction

    m = _marcador()

    def escrever(estado, config: RunnableConfig):
        add_transaction.invoke({"amount": 1, "source_text": m, "type_id": 2}, config=config)
        raise RuntimeError("Groq caiu")

    with pytest.raises(RuntimeError):
        rodar_turno(escrever)
    assert _n_transacoes(outra_sessao, m) == 0


def test_juiz_sem_chave_nao_deixa_escrita(rodar_turno, outra_sessao):
    m = _marcador()
    rodar_turno(_escreve_transacao(m), chave=None)
    assert _n_transacoes(outra_sessao, m) == 0


# ══ a transação do turno ═════════════════════════════════════════════════════

def test_leitura_no_turno_enxerga_a_propria_escrita_e_outra_sessao_nao(pg, outra_sessao):
    from app.db import UnidadeDeTrabalho
    from app.tools.financeiro import add_transaction, search_transactions

    m = _marcador()
    uow = UnidadeDeTrabalho()
    cfg = {"configurable": {"uow": uow}}
    try:
        add_transaction.invoke({"amount": 5, "source_text": m, "type_id": 2}, config=cfg)
        visto = search_transactions.invoke({"text": m}, config=cfg)
        assert len(visto["results"]) == 1                   # o turno enxerga
        assert _n_transacoes(outra_sessao, m) == 0          # os outros não
    finally:
        uow.encerrar()


def test_erro_sql_numa_tool_nao_perde_a_escrita_de_outra_aprovada(pg, outra_sessao):
    from app.db import UnidadeDeTrabalho
    from app.tools.agenda import add_event
    from app.tools.financeiro import add_transaction

    m = _marcador()
    uow = UnidadeDeTrabalho()
    cfg = {"configurable": {"uow": uow}}
    try:
        assert add_transaction.invoke({"amount": 5, "source_text": m, "type_id": 2}, config=cfg)["status"] == "ok"
        ruim = add_event.invoke({"title": m, "source_text": m, "start_time": "ontem"}, config=cfg)
        assert ruim["status"] == "error"
        bom = add_event.invoke({"title": m, "source_text": m, "start_time": "2026-10-28T15:00:00-03:00"}, config=cfg)
        assert bom["status"] == "ok"                        # a correção do modelo funciona
        uow.finalizar(True, "ok")
    finally:
        uow.encerrar()
    assert _n_transacoes(outra_sessao, m) == 1
    assert _n_eventos(outra_sessao, m) == 1


def test_queda_da_conexao_antes_do_veredito_nao_deixa_escrita(pg, outra_sessao):
    """Equivale a matar o processo entre a escrita e o juiz: o servidor reverte."""
    from app.db import UnidadeDeTrabalho
    from app.tools.financeiro import add_transaction

    m = _marcador()
    uow = UnidadeDeTrabalho()
    cfg = {"configurable": {"uow": uow}}
    add_transaction.invoke({"amount": 5, "source_text": m, "type_id": 2}, config=cfg)
    pid = _um(uow.conexao(), "SELECT pg_backend_pid()")
    with outra_sessao.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
    time.sleep(0.3)
    uow.encerrar()                                          # não pode levantar
    assert _n_transacoes(outra_sessao, m) == 0
    assert _um(outra_sessao, "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,)) == 0


def test_sem_uow_a_tool_continua_confirmando_como_antes(pg, outra_sessao):
    """Caminho do mcp_server.py: sem config, commit imediato."""
    from app.tools.financeiro import add_transaction

    m = _marcador()
    assert add_transaction.invoke({"amount": 5, "source_text": m, "type_id": 2})["status"] == "ok"
    assert _n_transacoes(outra_sessao, m) == 1


# ══ concorrência na mesma linha ══════════════════════════════════════════════

def test_segundo_turno_espera_o_primeiro_e_estoura_o_lock_timeout(pg, monkeypatch):
    import app.db as db
    from app.db import UnidadeDeTrabalho
    from app.tools.financeiro import update_transaction

    monkeypatch.setattr(db, "LOCK_TIMEOUT_MS", 300)
    id_ = _semear_transacao(pg, _marcador())
    a, b = UnidadeDeTrabalho(), UnidadeDeTrabalho()
    try:
        update_transaction.invoke({"id": id_, "amount": 11}, config={"configurable": {"uow": a}})
        inicio = time.perf_counter()
        out = update_transaction.invoke({"id": id_, "amount": 22}, config={"configurable": {"uow": b}})
        espera = time.perf_counter() - inicio
        assert out["status"] == "error" and "lock timeout" in out["message"]
        assert 0.25 < espera < 3          # esperou o teto, não ficou pendurado
    finally:
        a.encerrar()
        b.encerrar()


def test_segundo_turno_prossegue_quando_o_primeiro_e_recusado(pg, outra_sessao):
    from app.db import UnidadeDeTrabalho
    from app.tools.financeiro import update_transaction

    id_ = _semear_transacao(pg, _marcador())
    a, b = UnidadeDeTrabalho(), UnidadeDeTrabalho()
    resultado = {}

    def segundo():
        resultado["b"] = update_transaction.invoke(
            {"id": id_, "amount": 22}, config={"configurable": {"uow": b}}
        )

    try:
        update_transaction.invoke({"id": id_, "amount": 11}, config={"configurable": {"uow": a}})
        t = threading.Thread(target=segundo)
        t.start()
        time.sleep(0.4)                   # o segundo está esperando o lock
        a.finalizar(False, "x")           # primeiro turno recusado
        t.join(5)
        assert resultado["b"]["status"] == "ok"
        b.finalizar(True, "x")
    finally:
        a.encerrar()
        b.encerrar()
    assert float(_um(outra_sessao, "SELECT amount FROM transactions WHERE id = %s", (id_,))) == 22


# ══ o que NÃO é garantido (documentado) ══════════════════════════════════════

def test_o_mesmo_pedido_aprovado_duas_vezes_grava_duas_linhas(rodar_turno, outra_sessao):
    """
    Sem request_id (a API não pode mudar) nem chave única, repetir o pedido é
    indistinguível de um segundo pedido legítimo igual. Este teste registra o
    comportamento real; ele deixa de valer se a idempotência entre requisições
    for adotada.
    """
    m = _marcador()
    rodar_turno(_escreve_transacao(m))
    rodar_turno(_escreve_transacao(m))
    assert _n_transacoes(outra_sessao, m) == 2
