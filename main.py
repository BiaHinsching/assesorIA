from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from pg_tools import TOOLS

import os
from datetime import datetime

from prompts import (ROUTER_PROMPT_COMPLETO, FINANCEIRO_PROMPT_COMPLETO, AGENDA_PROMPT_COMPLETO, ORQUESTRADOR_PROMPT_COMPLETO, FAQ_PROMPT_COMPLETO)

agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    google_api_key=os.getenv("GEMINI_API_KEY")
)

llm_groq = ChatGroq(
  model="openai/gpt-oss-120b",
  #model="qwen/qwen3-32b"
  temperature=0.7,
  api_key=os.getenv("GROQ_API_KEY")
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])  # Tenta Gemini primeiro, depois Groq se necessário
llm_rapido = ChatGroq(
  model="llama-3.3-70b-versatile",
  temperature=0.0,
  api_key=os.getenv("GROQ_API_KEY")
)

# =============================================================================
# SYSTEM PROMPT
# =============================================================================

router_memory = MemorySaver()
router_app = create_agent(
  model=llm_groq,
  system_prompt=ROUTER_PROMPT_COMPLETO,
  checkpointer=router_memory,
)

financeiro_app = create_agent(
  model=llm_especialista,
  tools=TOOLS,
  system_prompt=FINANCEIRO_PROMPT_COMPLETO
)

agenda_app = create_agent(
  model=llm_especialista,
  system_prompt=AGENDA_PROMPT_COMPLETO
)

orquestrador_app = create_agent(
  model= llm_rapido,
  system_prompt=ORQUESTRADOR_PROMPT_COMPLETO
)

faq_app = create_agent(
  model = llm_rapido,
  system_prompt=FAQ_PROMPT_COMPLETO
)

def extrair_texto(resultado) -> str:
    """Extrai o texto puro da resposta do agente, independente do formato."""
    ultima = resultado['messages'][-1]
    
    # Formato objeto com .content
    if hasattr(ultima, 'content'):
        conteudo = ultima.content
    # Formato dict
    elif isinstance(ultima, dict):
        conteudo = ultima.get('content', '')
    else:
        conteudo = str(ultima)
    
    # Formato lista de blocos [{'type': 'text', 'text': '...'}]
    if isinstance(conteudo, list):
        conteudo = next(
            (bloco['text'] for bloco in conteudo if bloco.get('type') == 'text'),
            ''
        )
    
    # Remove markdown de código se vier ```json ... ```
    conteudo = conteudo.strip()
    if conteudo.startswith("```"):
        conteudo = conteudo.split("\n", 1)[-1]
        conteudo = conteudo.rsplit("```", 1)[0].strip()
    
    return conteudo


def rotear_conversa(pergunta: str, session_id: str) -> str:
    # 1. Roteador decide a rota
    resultado_roteador = router_app.invoke(
        {"messages": [{"role": "human", "content": pergunta}]},
        config={"configurable": {"thread_id": session_id}},
    )
    texto_roteador = extrair_texto(resultado_roteador)

    # 2. Se não há rota, é resposta direta (saudação, fora de escopo, etc.)
    if "ROUTE=" not in texto_roteador:
        return texto_roteador

    rota = texto_roteador.split("ROUTE=")[1].strip().split("\n")[0].upper()

    # 3. Agente especialista
    if rota == "FINANCEIRO":
        resultado_especialista = financeiro_app.invoke(
            {"messages": [{"role": "human", "content": pergunta}]}
        )
    elif rota == "AGENDA":
        resultado_especialista = agenda_app.invoke(
            {"messages": [{"role": "human", "content": pergunta}]}
        )
    else:
        return "Desculpe, não consegui identificar a rota correta para sua pergunta."

    json_especialista = extrair_texto(resultado_especialista)

    # 4. Orquestrador formata a resposta final
    resultado_final = orquestrador_app.invoke(
        {"messages": [{"role": "human", "content": f"ESPECIALISTA_JSON={json_especialista}"}]}
    )

    return extrair_texto(resultado_final)
  
while True:
  user_input = input("> ")
  if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
    print("Encerrando a conversa. Até mais!")
    break
  try:
    resposta = rotear_conversa(user_input, session_id="meu_id_de_sessao")
    print(resposta)
  except Exception as e:
    print("Erro ao consumir a API:", e)