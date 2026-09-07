"""
app/graph.py — funções de decisão de rota e o parsing do roteador.

Os agentes (router_app etc.) são objetos LangGraph pesados; aqui só mockamos
`router_app.invoke` para testar o parsing de `no_roteador`. As funções de
decisão são puras.
"""

from types import SimpleNamespace


def _msg(text, tipo="ai"):
    return SimpleNamespace(text=text, content=text, type=tipo)


# ── decisões puras ───────────────────────────────────────────────────────────

def test_decidir_pos_guardrail_bloqueado_vai_pro_fim():
    from app.graph import decidir_pos_guardrail_entrada

    estado = {"messages": [_msg("bloqueado", "ai")]}
    assert decidir_pos_guardrail_entrada(estado) == "fim"


def test_decidir_pos_guardrail_liberado_vai_pro_roteador():
    from app.graph import decidir_pos_guardrail_entrada

    estado = {"messages": [_msg("pergunta do user", "human")]}
    assert decidir_pos_guardrail_entrada(estado) == "roteador"


def test_decidir_especialista_rotas_validas():
    from app.graph import decidir_especialista

    for rota in ("financeiro", "agenda", "faq"):
        assert decidir_especialista({"rota": rota}) == rota


def test_decidir_especialista_rota_desconhecida_vai_pro_fim():
    from app.graph import decidir_especialista

    assert decidir_especialista({"rota": "outra_coisa"}) == "fim"
    assert decidir_especialista({"rota": ""}) == "fim"


# ── parsing do roteador ──────────────────────────────────────────────────────

def test_no_roteador_extrai_rota(monkeypatch):
    import app.graph as g

    saida = {"messages": [_msg("ROUTE=financeiro\nPERGUNTA_ORIGINAL=quanto gastei?")]}
    monkeypatch.setattr(g.router_app, "invoke", lambda *a, **k: saida)

    out = g.no_roteador({"messages": []}, config={})
    assert out["rota"] == "financeiro"
    assert out["agentes_chamados"] == ["roteador", "financeiro"]


def test_no_roteador_resposta_direta_sem_route(monkeypatch):
    import app.graph as g

    saida = {"messages": [_msg("Olá! Como posso ajudar?")]}
    monkeypatch.setattr(g.router_app, "invoke", lambda *a, **k: saida)

    out = g.no_roteador({"messages": []}, config={})
    assert out["rota"] == "fim"
    assert out["messages"][0]["content"] == "Olá! Como posso ajudar?"


def test_no_roteador_repassa_o_config(monkeypatch):
    import app.graph as g

    capturado = {}

    def fake_invoke(payload, config=None):
        capturado["config"] = config
        return {"messages": [_msg("ROUTE=agenda\n")]}

    monkeypatch.setattr(g.router_app, "invoke", fake_invoke)
    cfg = {"configurable": {"thread_id": "s1", "user_id": "u1"}}
    g.no_roteador({"messages": []}, config=cfg)
    assert capturado["config"] == cfg
