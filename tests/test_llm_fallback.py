"""
Regressão do bug do fallback do especialista (app/llm.py).

Dois bugs foram corrigidos e estes testes travam os dois:

  1. ESTRUTURAL — `with_fallbacks(...).with_config(callbacks=...)` fazia o
     `create_agent` chamar `.bind_tools()` só no modelo primário; o fallback
     era invocado SEM tools e estourava "tool call validation failed".
     Fix: callback anexado por-modelo, sem wrapper.

  2. MODELO — o fallback era `openai/gpt-oss-120b`, que num prompt de saída
     JSON tenta chamar uma tool inexistente chamada `json`.
     Fix: fallback passou a ser `qwen/qwen3.8-27b`.

Nenhum destes testes faz chamada de rede — são só sobre a ESTRUTURA do objeto.
"""

from langchain_core.runnables import RunnableWithFallbacks
from langchain_core.tools import tool


@tool
def _ferramenta_exemplo(x: int) -> int:
    """dobra x."""
    return x * 2


def _tools_bound(runnable) -> bool:
    """True se este runnable tem tools amarradas nos kwargs (via bind_tools)."""
    kwargs = getattr(runnable, "kwargs", {})
    return "tools" in kwargs


def _model_id(chat_model) -> str:
    """ChatGroq guarda em model_name; ChatGoogleGenerativeAI em model."""
    return getattr(chat_model, "model_name", None) or getattr(chat_model, "model", "") or ""


def test_especialista_e_runnable_with_fallbacks():
    from app.llm import llm_especialista

    assert isinstance(llm_especialista, RunnableWithFallbacks), (
        "llm_especialista precisa ser um RunnableWithFallbacks 'puro' — "
        "sem .with_config() por cima, senão bind_tools não propaga."
    )


def test_bind_tools_propaga_para_primario_e_fallback():
    """O bug 1: as tools tem que chegar nos DOIS modelos."""
    from app.llm import llm_especialista

    bound = llm_especialista.bind_tools([_ferramenta_exemplo])

    assert isinstance(bound, RunnableWithFallbacks)
    assert _tools_bound(bound.runnable), "primário (Gemini) ficou sem tools"
    assert bound.fallbacks, "não há fallback configurado"
    for fb in bound.fallbacks:
        assert _tools_bound(fb), "fallback ficou sem tools — bug 1 voltou"


def test_fallback_do_especialista_nao_e_gpt_oss():
    """O bug 2: gpt-oss-* inventa a tool `json` em prompt de saída JSON."""
    from app.llm import llm_especialista

    for fb in llm_especialista.fallbacks:
        assert "gpt-oss" not in _model_id(fb), (
            f"fallback do especialista é {_model_id(fb)!r} — gpt-oss quebra em saída JSON"
        )


def test_primario_do_especialista_e_gemini():
    from app.llm import llm_especialista

    assert "gemini" in _model_id(llm_especialista.runnable).lower()


def test_roteador_e_instancia_propria_nao_alias_do_fallback():
    """llm_roteador deve ser um ChatGroq próprio (120b), não o mesmo objeto
    usado no fallback do especialista."""
    from app.llm import llm_especialista, llm_roteador

    assert _model_id(llm_roteador) == "openai/gpt-oss-120b"
    for fb in llm_especialista.fallbacks:
        assert fb is not llm_roteador


def test_gemini_nao_retenta_429():
    """O SDK google-genai só deve retentar erros 5xx, nunca 429 de cota."""
    from app.llm import llm_especialista

    primario = llm_especialista.runnable
    retry = primario.client_args["http_options"]["retry_options"]
    assert 429 not in retry.get("http_status_codes", [])
