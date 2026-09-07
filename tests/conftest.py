"""
Configuração compartilhada dos testes.

Objetivo: rodar TUDO offline. Nenhum teste bate em Gemini, Groq, Postgres,
MongoDB ou Qdrant de verdade. As variáveis de ambiente abaixo são setadas
ANTES de qualquer `import app.*` para que:

  - app.config.validar_config() passe (as chaves existem, mesmo que falsas);
  - os clientes (ChatGoogleGenerativeAI, ChatGroq, QdrantClient, MongoClient)
    construam sem erro. Eles só falhariam ao fazer a primeira chamada de rede —
    e nenhum teste chega lá: cada um mocka o método que usaria.

load_dotenv() roda com override=False, então estes valores vencem o .env real.
"""

import os

os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", "assessoria_test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("QDRANT_API_KEY", "test-qdrant-key")

import pytest


@pytest.fixture
def mongo_col(monkeypatch):
    """Substitui app.memory.col_sessoes por uma coleção mongomock em memória."""
    import mongomock

    import app.memory as memory

    client = mongomock.MongoClient()
    col = client["assessoria_test"]["sessoes"]
    monkeypatch.setattr(memory, "col_sessoes", col)
    memory._sessoes_ativas.clear()
    yield col
    memory._sessoes_ativas.clear()


class FakeEmbedding:
    """Embedding determinístico: vetor de 768 floats derivado do texto."""

    def __call__(self, texto: str) -> list[float]:
        h = abs(hash(texto))
        return [((h >> (i % 32)) & 1) * 0.01 + 0.001 for i in range(768)]


@pytest.fixture
def fake_embedding(monkeypatch):
    fake = FakeEmbedding()
    import app.memory as memory
    import app.vectorstore as vs

    monkeypatch.setattr(vs, "gerar_embedding", fake)
    monkeypatch.setattr(memory, "gerar_embedding", fake)
    return fake


class FakeLLM:
    """Stub de chat model: devolve sempre o mesmo texto, registra as chamadas."""

    def __init__(self, resposta="RESPOSTA_FAKE"):
        self.resposta = resposta
        self.chamadas = []

    def invoke(self, entrada, *args, **kwargs):
        self.chamadas.append(entrada)

        class _Msg:
            def __init__(self, content):
                self.content = content
                self.text = content

        return _Msg(self.resposta)


@pytest.fixture
def fake_llm_rapido(monkeypatch):
    fake = FakeLLM(resposta="CATEGORIA: APROVADO\nJUSTIFICATIVA: ok")
    import app.guardrail as guardrail

    monkeypatch.setattr(guardrail, "llm", fake)
    return fake
