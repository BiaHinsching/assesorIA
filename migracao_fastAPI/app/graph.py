import operator
from typing import Annotated, TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, MessagesState, END

import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from app.tools.financeiro import TOOLS
from app.tools.faq import faq_retriever
from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
)

from app.guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada, desanonimizar_saida
from langchain_core.messages import RemoveMessage

load_dotenv()

# ==============================================================================
# MODELOS E AGENTES  (sem checkpointer — a memória fica no grafo)
# ==============================================================================
llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    api_key=os.getenv("GEMINI_API_KEY"),
)

llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=os.getenv("GROQ_API_KEY"),
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])

llm_rapido = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    api_key=os.getenv("GROQ_API_KEY"),
)

router_app = create_agent(
    model=llm_rapido,
    system_prompt=ROUTER_PROMPT_COMPLETO,
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    system_prompt=AGENDA_PROMPT_COMPLETO,
)

orquestrador_app = create_agent(
    model=llm_rapido,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

faq_app = create_agent(
    model=llm_rapido,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT_COMPLETO,
)


# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(MessagesState):                                  # ID da sessão
    agentes_chamados:   Annotated[list[str], operator.add]  # acumula entre nós
    rota: str
    mapa_pii: dict

# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado) -> dict:
    saida = router_app.invoke({"messages": list(estado["messages"])})
    texto = saida["messages"][-1].text

    if "ROUTE=" not in texto:
        return {
            "agentes_chamados": ["roteador"],
            "rota": "fim",
            "messages": [{"role": "assistant", "content": texto}],
        }
    rota = "fim"
    for linha in texto.splitlines():
        if linha.startswith("ROUTE="):
            rota = linha.split("=", 1)[1].strip()
            break
        
    return {
        "agentes_chamados": ["roteador", rota],
        "rota": rota,
    }

def no_orquestrador(estado: Estado) -> dict:
    ultima_especialiasta = ""
    for mensagem in reversed(estado["messages"]):
        if mensagem.type == "ai" and mensagem.content:
            ultima_especialiasta = mensagem.content
            break
        
    saida = orquestrador_app.invoke({
        "messages": [estado["messages"][-1]],
    })
    
    return {
        "agentes_chamados": ["orquestrador"],
        "messages": [{"role": "assistant", "content": saida["messages"][-1].text}],
    }

# ==============================================================================
# NÓS DE GUARDRAIL
# ==============================================================================
def no_guardrail_entrada(estado: Estado) -> dict:
    texto_original = estado["messages"][-1].content
    texto_anonimizado, mapa = anonimizar_entrada(texto_original)
    resultado = guardrail_entrada(texto_anonimizado)

    if resultado["bloqueado"]:
        return {
            "agentes_chamados": ["guardrail_entrada"],
            "mapa_pii": mapa,
            "messages": [{"role": "assistant", "content": resultado["mensagem"]}],
        }

    return {
        "agentes_chamados": ["guardrail_entrada"],
        "mapa_pii": mapa,
    }

def no_guardrail_saida(estado: Estado) -> dict:
    resposta = estado["messages"][-1].content
    resultado = guardrail_saida(resposta, estado["mapa_pii"])

    return {
        "agentes_chamados": ["guardrail_saida"],
        "messages": [{"role": "assistant", "content": resultado["conteudo"]}],
    }

# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    """Se o guardrail bloqueou, a última mensagem virou 'ai'; senão continua humana."""
    return "fim" if estado["messages"][-1].type == "ai" else "roteador"

def decidir_especialista(estado: Estado) -> str:
    """Lê a rota decidida pelo roteador e devolve o nome do próximo nó."""
    rota = estado["rota"]
    return rota if rota in ("financeiro", "agenda", "faq") else "fim"


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro",   financeiro_app)
grafo.add_node("agenda",       agenda_app)
grafo.add_node("faq",          faq_app)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("guardrail_saida", no_guardrail_saida)

grafo.set_entry_point("guardrail_entrada")

grafo.add_conditional_edges("guardrail_entrada", decidir_pos_guardrail_entrada, {
    "roteador":"roteador",
    "fim": END
    }
)

grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "agenda":     "agenda",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro",   "orquestrador")
grafo.add_edge("agenda",       "orquestrador")
grafo.add_edge("orquestrador", "guardrail_saida")
grafo.add_edge("guardrail_saida", END)
grafo.add_edge("faq",          END)   # FAQ bypassa o orquestrador

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo_assessor(pergunta_usuario: str, session_id: str) -> str:
    estado_inicial = {
        "messages": [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados":   [],
        "rota": "",
        "mapa_pii": {},
    }

    estado_final = fluxo_agentes.invoke(
        estado_inicial,
        config={"configurable": {"thread_id": session_id}},
    )

    print(f"[debug] agentes chamados: {estado_final['agentes_chamados']}")
    return estado_final["messages"][-1].content


# ==============================================================================
# LOOP DE CONVERSA
# ==============================================================================
while True:
    try:
        user_input = input("> ")
        if user_input.lower() in ("sair", "end", "fim", "tchau", "bye"):
            print("Encerrando a conversa.")
            break

        resposta = executar_fluxo_assessor(
            pergunta_usuario=user_input,
            session_id="id_usuario_mas_agora_não_importa",
        )
        print(resposta)

    except Exception as e:
        print("Erro ao consumir a API:", e)
        continue
