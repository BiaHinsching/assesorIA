"""
app/schemas.py e app/vectorstore.py — contratos pequenos.
"""

import pytest
from pydantic import ValidationError


# ── ChatRequest ──────────────────────────────────────────────────────────────

def test_chatrequest_user_id_tem_default():
    from app.schemas import ChatRequest

    req = ChatRequest(session_id="s1", pergunta="oi")
    assert req.user_id == "usuario_teste"


def test_chatrequest_aceita_user_id_explicito():
    from app.schemas import ChatRequest

    req = ChatRequest(session_id="s1", pergunta="oi", user_id="alice")
    assert req.user_id == "alice"


def test_chatrequest_pergunta_obrigatoria_e_nao_vazia():
    from app.schemas import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(session_id="s1", pergunta="")
    with pytest.raises(ValidationError):
        ChatRequest(session_id="s1")


def test_chatresponse_agentes_default_lista_vazia():
    from app.schemas import ChatResponse

    assert ChatResponse(resposta="ok").agentes_chamados == []


# ── vectorstore ──────────────────────────────────────────────────────────────

def test_gerar_embedding_pede_768_dimensoes(monkeypatch):
    import app.vectorstore as vs

    capturado = {}

    class FakeEmb:
        def embed_query(self, texto, **kwargs):
            capturado.update(kwargs)
            return [0.0] * kwargs.get("output_dimensionality", 3072)

        def embed_documents(self, textos, **kwargs):
            capturado.update(kwargs)
            return [[0.0] * kwargs["output_dimensionality"] for _ in textos]

    monkeypatch.setattr(vs, "_embeddings", FakeEmb())

    v = vs.gerar_embedding("teste")
    assert capturado["output_dimensionality"] == vs.EMBEDDING_DIM == 768
    assert len(v) == 768


def test_gerar_embeddings_batch(monkeypatch):
    import app.vectorstore as vs

    class FakeEmb:
        def embed_documents(self, textos, **kwargs):
            return [[0.1] * kwargs["output_dimensionality"] for _ in textos]

    monkeypatch.setattr(vs, "_embeddings", FakeEmb())
    out = vs.gerar_embeddings_batch(["a", "b", "c"])
    assert len(out) == 3 and all(len(v) == 768 for v in out)


def test_collections_tem_nomes_puros():
    from app.vectorstore import COLLECTION_FAQ, COLLECTION_MEMORIA

    assert COLLECTION_FAQ == "faq_chunks"
    assert COLLECTION_MEMORIA == "memoria_resumos"
