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

# O roteador usa o 120b, e não o llm_rapido, por um motivo medido: o gpt-oss-20b
# vaza o próprio canal de raciocínio na saída quando tem tools ligadas e a
# conversa já tem histórico. O sintoma são erros 400 do Groq com o token
# <|channel|>commentary dentro do NOME da função, ou o raciocínio cru no lugar
# da resposta ("User wants to economize on furniture. That's finance.").
# Num teste de 3 turnos: 2 falhas com o 20b, 0 com o 120b.
#
# Não é o llm_especialista (Gemini) porque cada roteamento consumiria uma das
# 20 requisições diárias da cota gratuita — e roteamento acontece em TODA
# mensagem. O 120b usa a mesma chave Groq do resto.
llm_roteador = llm_groq
