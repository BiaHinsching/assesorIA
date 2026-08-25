from langchain_core.callbacks import BaseCallbackHandler
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.config import GEMINI_API_KEY, GROQ_API_KEY


class _FallbackErrorLogger(BaseCallbackHandler):
    """Loga o erro de cada modelo tentado, já que with_fallbacks só reergue o do primeiro."""

    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        print(f"[llm_fallback] {type(error).__name__}: {error}")

llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    api_key=GEMINI_API_KEY,
    max_retries=0,
    timeout=15,
)

llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=GROQ_API_KEY,
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq]).with_config(
    callbacks=[_FallbackErrorLogger()]
)

llm_rapido = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0.0,
    api_key=GROQ_API_KEY,
)
