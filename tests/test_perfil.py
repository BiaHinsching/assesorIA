"""
Perfil financeiro — persistência (app/perfil.py) e tools (app/tools/perfil.py).

Mongo → mongomock. Qdrant e embeddings → fakes. Zero rede.
"""

import pytest


@pytest.fixture
def perfil(monkeypatch, mongo_col, fake_embedding):
    import mongomock

    import app.perfil as p

    col = mongomock.MongoClient()["t"]["perfis"]
    monkeypatch.setattr(p, "col_perfis", col)

    class FakeQdrant:
        def __init__(self):
            self.pontos = []          # (user_id, texto, vetor)
            self.deletes = []

        def collection_exists(self, nome):
            return True

        def delete(self, collection_name, points_selector):
            uid = points_selector.filter.must[0].match.value
            self.deletes.append(uid)
            self.pontos = [x for x in self.pontos if x[0] != uid]

        def upsert(self, collection_name, points):
            for pt in points:
                self.pontos.append((pt.payload["user_id"], pt.payload["texto"], pt.vector))

        def query_points(self, collection_name, query, query_filter, limit):
            uid = query_filter.must[0].match.value
            meus = [x for x in self.pontos if x[0] == uid]
            # "similaridade": distância euclidiana simples ao vetor da consulta
            meus.sort(key=lambda x: sum((a - b) ** 2 for a, b in zip(x[2], query)))

            class _R:
                points = [type("P", (), {"payload": {"user_id": u, "texto": t}}) for u, t, _ in meus[:limit]]

            return _R()

    fq = FakeQdrant()
    monkeypatch.setattr(p, "qdrant", fq)
    monkeypatch.setattr(p, "_preparar_qdrant", lambda: None)
    p._fake_qdrant = fq
    return p


def _salvar(p, **over):
    dados = dict(
        user_id="alice",
        renda_mensal=4200.0,
        objetivo="viagem em dezembro",
        tolerancia_risco="baixa",
        preferencias="quero juntar para uma viagem; não quero investimento agressivo.",
    )
    dados.update(over)
    return p.salvar_perfil(**dados)


def test_salvar_grava_estruturado_no_mongo(perfil):
    _salvar(perfil)
    doc = perfil.col_perfis.find_one({"_id": "alice"})
    assert doc["renda_mensal"] == 4200.0
    assert doc["tolerancia_risco"] == "baixa"


def test_salvar_quebra_preferencias_em_frases_no_qdrant(perfil):
    _salvar(perfil)
    textos = [t for _, t, _ in perfil._fake_qdrant.pontos]
    assert "quero juntar para uma viagem" in textos
    assert "não quero investimento agressivo" in textos


def test_resave_substitui_nos_dois_bancos(perfil):
    _salvar(perfil)
    _salvar(perfil, renda_mensal=9000.0, preferencias="agora aceito renda variável e cripto.")

    assert perfil.col_perfis.count_documents({"_id": "alice"}) == 1
    assert perfil.col_perfis.find_one({"_id": "alice"})["renda_mensal"] == 9000.0

    textos = [t for _, t, _ in perfil._fake_qdrant.pontos]
    assert "não quero investimento agressivo" not in textos  # texto antigo sumiu
    assert any("cripto" in t for t in textos)
    assert perfil._fake_qdrant.deletes  # apagou antes de reinserir


def test_buscar_estruturado_sem_perfil_devolve_none(perfil):
    assert perfil.buscar_perfil_estruturado("ninguem") is None


def test_buscar_preferencias_filtra_por_usuario(perfil):
    _salvar(perfil, user_id="alice")
    _salvar(perfil, user_id="bob", preferencias="quero day trade agressivo.")

    fake = perfil._fake_embedding = None  # noqa
    r = perfil.buscar_preferencias("alice", "investimento agressivo")
    assert r  # achou algo
    # nada do bob vazou
    assert all("day trade" not in frase for frase in r)


# ── tools ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tools_perfil(perfil, monkeypatch):
    import app.tools.perfil as tp

    monkeypatch.setattr(tp, "_perfil_estruturado_db", perfil.buscar_perfil_estruturado)
    monkeypatch.setattr(tp, "_preferencias_db", perfil.buscar_preferencias)
    return tp


def _cfg(uid):
    return {"configurable": {"user_id": uid}}


def test_consultar_perfil_sem_cadastro_orienta_tela(tools_perfil):
    out = tools_perfil.consultar_perfil.invoke({}, config=_cfg("ninguem"))
    assert "PERFIL_NAO_CADASTRADO" in out


def test_consultar_perfil_devolve_campos(tools_perfil, perfil):
    _salvar(perfil, user_id="alice")
    out = tools_perfil.consultar_perfil.invoke({}, config=_cfg("alice"))
    assert "renda_mensal=4200" in out
    assert "tolerancia_risco=baixa" in out


def test_buscar_preferencias_tool_usa_user_id_do_config(tools_perfil, perfil):
    _salvar(perfil, user_id="alice")
    out = tools_perfil.buscar_preferencias.invoke(
        {"consulta": "cripto"}, config=_cfg("alice")
    )
    assert "agressivo" in out


def test_tools_sem_user_id_no_config(tools_perfil):
    out = tools_perfil.consultar_perfil.invoke({}, config={"configurable": {}})
    assert "não foi possível identificar" in out.lower()
