"""
app/memory.py — ciclo de vida da sessão e recuperação de histórico.

Mongo → mongomock (fixture mongo_col). Qdrant, embeddings e o LLM de resumo
são todos mockados. Zero rede.
"""

import pytest


@pytest.fixture
def memory(mongo_col, fake_embedding, monkeypatch):
    """app.memory com Mongo/Qdrant/LLM falsos."""
    import app.memory as m

    class FakeQdrant:
        def __init__(self):
            self.upserts = []
            self.query_result_points = []
            self.raise_on_query = None

        def upsert(self, collection_name, points):
            self.upserts.append((collection_name, points))

        def query_points(self, **kwargs):
            if self.raise_on_query:
                raise self.raise_on_query
            self._last_query = kwargs

            class _R:
                points = self.query_result_points

            return _R()

    fake_q = FakeQdrant()
    monkeypatch.setattr(m, "qdrant", fake_q)

    class _Resumo:
        content = "  O usuário fez X e pediu Y.  "

    monkeypatch.setattr(m, "llm_rapido", type("L", (), {"invoke": lambda self, p: _Resumo()})())

    m._fake_qdrant = fake_q
    return m


def test_iniciar_sessao_cria_doc_com_user_id(memory, mongo_col):
    doc_id = memory.iniciar_sessao("sess-1", user_id="alice")
    doc = mongo_col.find_one({"_id": doc_id})
    assert doc["session_id"] == "sess-1"
    assert doc["user_id"] == "alice"
    assert doc["resumo"] == ""
    assert doc["mensagens"] == []


def test_iniciar_sessao_e_idempotente(memory, mongo_col):
    d1 = memory.iniciar_sessao("sess-1", user_id="alice")
    d2 = memory.iniciar_sessao("sess-1", user_id="alice")
    assert d1 == d2
    assert mongo_col.count_documents({"session_id": "sess-1"}) == 1


def test_iniciar_sessao_backfilla_user_id(memory, mongo_col):
    doc_id = memory.iniciar_sessao("sess-1")  # sem user_id
    assert mongo_col.find_one({"_id": doc_id})["user_id"] is None
    memory._sessoes_ativas.clear()  # simula --reload
    memory.iniciar_sessao("sess-1", user_id="bob")
    assert mongo_col.find_one({"_id": doc_id})["user_id"] == "bob"


def test_salvar_mensagem_empilha(memory, mongo_col):
    memory.salvar_mensagem("s", "user", "oi", user_id="alice")
    memory.salvar_mensagem("s", "assistant", "olá", user_id="alice")
    doc = mongo_col.find_one({"session_id": "s"})
    assert [m["content"] for m in doc["mensagens"]] == ["oi", "olá"]


def test_encerrar_sessao_gera_resumo_e_indexa_no_qdrant(memory, mongo_col):
    memory.salvar_mensagem("s", "user", "gastei 50", user_id="alice")
    resumo = memory.encerrar_sessao("s")

    assert resumo == "O usuário fez X e pediu Y."  # .strip() aplicado
    assert mongo_col.find_one({"session_id": "s"})["resumo"] == resumo

    assert len(memory._fake_qdrant.upserts) == 1
    _, points = memory._fake_qdrant.upserts[0]
    assert points[0].payload["user_id"] == "alice"
    assert points[0].payload["resumo"] == resumo


def test_encerrar_sessao_vazia_nao_faz_nada(memory):
    memory.iniciar_sessao("s", user_id="alice")
    assert memory.encerrar_sessao("s") == ""
    assert memory._fake_qdrant.upserts == []


def test_encerrar_sessao_inexistente_devolve_vazio(memory):
    assert memory.encerrar_sessao("nao-existe") == ""


def test_falha_no_qdrant_nao_derruba_encerramento(memory, mongo_col):
    memory._fake_qdrant.upsert = lambda **kw: (_ for _ in ()).throw(RuntimeError("qdrant down"))

    def boom(collection_name, points):
        raise RuntimeError("qdrant down")

    memory._fake_qdrant.upsert = boom
    memory.salvar_mensagem("s", "user", "oi", user_id="alice")
    resumo = memory.encerrar_sessao("s")
    assert resumo  # o resumo saiu mesmo com o Qdrant fora
    assert mongo_col.find_one({"session_id": "s"})["resumo"] == resumo


# ── recuperar_historico ──────────────────────────────────────────────────────

def test_recuperar_historico_sem_busca_usa_mongo(memory, mongo_col):
    mongo_col.insert_one({
        "_id": "d1", "session_id": "x", "user_id": "alice",
        "resumo": "resumo antigo", "iniciada_em": __import__("datetime").datetime(2026, 1, 1),
    })
    out = memory.recuperar_historico("alice")
    assert out[0]["resumo"] == "resumo antigo"
    assert out[0]["doc_id"] == "d1"


def test_recuperar_historico_ignora_sessao_em_andamento(memory, mongo_col):
    import datetime
    mongo_col.insert_many([
        {"_id": "d1", "user_id": "alice", "resumo": "", "iniciada_em": datetime.datetime(2026, 1, 2)},
        {"_id": "d2", "user_id": "alice", "resumo": "fechada", "iniciada_em": datetime.datetime(2026, 1, 1)},
    ])
    out = memory.recuperar_historico("alice")
    assert [d["doc_id"] for d in out] == ["d2"]


def test_recuperar_historico_com_busca_usa_qdrant(memory):
    class _P:
        id = "d9"
        payload = {"resumo": "viagem para Salvador", "iniciada_em": "2026-08-01"}

    memory._fake_qdrant.query_result_points = [_P()]
    out = memory.recuperar_historico("alice", busca="viajar")
    assert out == [{"doc_id": "d9", "iniciada_em": "2026-08-01", "resumo": "viagem para Salvador"}]
    # filtro por user_id foi aplicado
    flt = memory._fake_qdrant._last_query["query_filter"]
    assert flt.must[0].key == "user_id"


def test_recuperar_historico_cai_no_mongo_se_qdrant_falha(memory, mongo_col):
    import datetime
    memory._fake_qdrant.raise_on_query = RuntimeError("qdrant timeout")
    mongo_col.insert_one({
        "_id": "d1", "user_id": "alice", "resumo": "backup do mongo",
        "iniciada_em": datetime.datetime(2026, 1, 1),
    })
    out = memory.recuperar_historico("alice", busca="qualquer")
    assert out[0]["resumo"] == "backup do mongo"


def test_recuperar_historico_respeita_o_limite(memory):
    memory._fake_qdrant.query_result_points = []
    memory.recuperar_historico("alice", busca="x", limite=7)
    assert memory._fake_qdrant._last_query["limit"] == 7


# ── limpeza de tokens PII ────────────────────────────────────────────────────

def test_limpar_tokens_pii(memory):
    txt = "O usuário informou [PII_CPF_a3f9c1] e [PII_EMAIL_9910bb]."
    assert memory._limpar_tokens_pii(txt) == "O usuário informou [cpf omitido] e [email omitido]."
