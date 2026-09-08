from langchain_core.callbacks import BaseCallbackHandler
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.config import GEMINI_API_KEY, GROQ_API_KEY


class _FallbackErrorLogger(BaseCallbackHandler):
    """Loga o erro de cada modelo tentado, já que with_fallbacks só reergue o do primeiro."""

    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        print(f"[llm_fallback] {type(error).__name__}: {error}")


# O callback é anexado AQUI, em cada ChatModel, e NÃO com um `.with_config()`
# em volta do `.with_fallbacks(...)`. Motivo: dentro do create_agent o framework
# chama `.bind_tools()` no objeto que recebe. Num
# `with_fallbacks(...).with_config(...)` o bind_tools era proxyado só para o
# modelo primário — o fallback ficava SEM as tools, e a queda pra ele estourava
# ("tool call validation failed"). Com o callback em cada modelo, o objeto
# exposto é um RunnableWithFallbacks "puro", cujo bind_tools se propaga para
# os dois lados.
_LOGGER = [_FallbackErrorLogger()]

# ── ESPECIALISTA (financeiro / agenda) ────────────────────────────────────────
# Primário: Gemini 2.5-flash. Bom em seguir o contrato de saída JSON dos
# especialistas, mas o free tier são 20 req/dia — quando acaba, cai pro Qwen.
# max_retries=1 é CRÍTICO: com 0, o langchain-google-genai monta
# HttpRetryOptions(attempts=0), que o SDK trata como "retry padrão" e fica ~18s
# insistindo num 429 de cota antes de desistir. Com 1 = uma tentativa só, o 429
# volta em ~0.3s e o fallback assume na hora.
# timeout=10 é o MÍNIMO que o Gemini aceita ("Minimum allowed deadline is 10s").
llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-flash-latest",
    temperature=0.7,
    top_p=0.95,
    api_key=GEMINI_API_KEY,
    max_retries=1,
    timeout=10,
    callbacks=_LOGGER,
)

# Fallback do especialista: qwen3.8-27b no Groq. Escolhido em teste:
#   - o openai/gpt-oss-120b, num prompt de saída JSON, tenta "chamar" uma tool
#     inexistente chamada `json` e o Groq rejeita com 400;
#   - o qwen devolve o JSON direto, com tool calling funcionando.
# reasoning_effort="none" + max_tokens: o qwen é um modelo de raciocínio e por
# padrão gasta MUITOS tokens de saída — o suficiente pra estourar o limite de
# output-tokens-por-minuto do Groq free tier (OTPM 1000). Sem reasoning ele
# responde curto e não bate no limite.
llm_especialista_fallback = ChatGroq(
    model="qwen/qwen3.8-27b",
    temperature=0.3,
    max_tokens=900,          # abaixo do OTPM 1000 do Groq free tier
    reasoning_effort="none",
    max_retries=1,
    api_key=GROQ_API_KEY,
    callbacks=_LOGGER,
)

llm_especialista = llm_gemini.with_fallbacks([llm_especialista_fallback])

# ── RÁPIDO (orquestrador / guardrail / resumo de sessão) ──────────────────────
llm_rapido = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0.0,
    api_key=GROQ_API_KEY,
)

# ── ROTEADOR ─────────────────────────────────────────────────────────────────
# O roteador usa o 120b, e não o llm_rapido, por um motivo medido: o gpt-oss-20b
# vaza o próprio canal de raciocínio na saída quando tem tools ligadas e a
# conversa já tem histórico. O sintoma são erros 400 do Groq com o token
# <|channel|>commentary dentro do NOME da função, ou o raciocínio cru no lugar
# da resposta ("User wants to economize on furniture. That's finance.").
# Num teste de 3 turnos: 2 falhas com o 20b, 0 com o 120b.
#
# Não é o Gemini porque cada roteamento consumiria uma das 20 requisições
# diárias da cota gratuita — e roteamento acontece em TODA mensagem.
llm_roteador = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=GROQ_API_KEY,
    callbacks=_LOGGER,
)
