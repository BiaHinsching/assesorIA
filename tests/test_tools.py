"""
app/tools/memoria.py e app/tools/faq.py — só o formato de entrada/saída.
recuperar_historico e o Qdrant são mockados.
"""

import datetime

import pytest


# ── buscar_historico (tool de memória) ───────────────────────────────────────

@pytest.fixture
def buscar_historico(monkeypatch):
    import app.tools.memoria as tm

    chamadas = {}

    def fake_recuperar(user_id, busca="", limite=3):
        chamadas["args"] = (user_id, busca, limite)
        return chamadas.get("retorno", [])

    monkeypatch.setattr(tm, "recuperar_historico", fake_recuperar)
    tm.buscar_historico._chamadas = chamadas
    return tm.buscar_historico


def _call(tool, busca, configurable):
    return tool.invoke({"busca": busca}, config={"configurable": configurable})


def test_buscar_historico_usa_user_id_do_config(buscar_historico):
    _call(buscar_historico, "viagem", {"user_id": "alice", "thread_id": "sess-1"})
    assert buscar_historico._chamadas["args"][0] == "alice"


def test_buscar_historico_cai_no_thread_id_sem_user_id(buscar_historico):
    _call(buscar_historico, "viagem", {"thread_id": "sess-1"})
    assert buscar_historico._chamadas["args"][0] == "sess-1"


def test_buscar_historico_sem_identificador(buscar_historico):
    out = _call(buscar_historico, "viagem", {})
    assert "não foi possível identificar" in out.lower()


def test_buscar_historico_sem_resultados(buscar_historico):
    buscar_historico._chamadas["retorno"] = []
    out = _call(buscar_historico, "viagem", {"user_id": "alice"})
    assert out == "Nenhuma conversa anterior relevante encontrada."


def test_buscar_historico_formata_data_datetime(buscar_historico):
    buscar_historico._chamadas["retorno"] = [
        {"doc_id": "d1", "iniciada_em": datetime.datetime(2026, 8, 9), "resumo": "viagem a Salvador"}
    ]
    out = _call(buscar_historico, "viagem", {"user_id": "alice"})
    assert out == "[09/08/2026] viagem a Salvador"


def test_buscar_historico_formata_data_string_iso(buscar_historico):
    """Vindo do Qdrant a data chega como string ISO — não pode quebrar."""
    buscar_historico._chamadas["retorno"] = [
        {"doc_id": "d1", "iniciada_em": "2026-08-09T12:00:00+00:00", "resumo": "curso de espanhol"}
    ]
    out = _call(buscar_historico, "estudo", {"user_id": "alice"})
    assert out == "[2026-08-09] curso de espanhol"


# ── faq_retriever ────────────────────────────────────────────────────────────

@pytest.fixture
def faq(monkeypatch):
    import app.tools.faq as tf

    class FakeQdrant:
        def query_points(self, **kwargs):
            self.last = kwargs

            class _R:
                points = getattr(self, "_points", [])

            _R.points = self._points
            return _R()

    fq = FakeQdrant()
    fq._points = []
    monkeypatch.setattr(tf, "qdrant", fq)
    monkeypatch.setattr(tf, "gerar_embedding", lambda t: [0.0] * 768)
    tf._fake = fq
    return tf


def test_faq_retriever_junta_page_content(faq):
    faq._fake._points = [
        type("P", (), {"payload": {"page_content": "trecho A"}}),
        type("P", (), {"payload": {"page_content": "trecho B"}}),
    ]
    out = faq.faq_retriever.invoke({"question": "como funciona?"})
    assert out == "trecho A\n\ntrecho B"
    assert faq._fake.last["collection_name"] == faq.COLLECTION_FAQ
    assert faq._fake.last["limit"] == 6


def test_faq_retriever_sem_resultado(faq):
    faq._fake._points = []
    out = faq.faq_retriever.invoke({"question": "xyz"})
    assert out == "Nenhum trecho relevante encontrado no FAQ."
