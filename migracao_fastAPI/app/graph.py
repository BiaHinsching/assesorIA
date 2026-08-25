import operator
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, MessagesState, END

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from app.llm import llm_especialista, llm_rapido, llm_roteador
from app.tools.financeiro import TOOLS
from app.tools.faq import faq_retriever
from app.tools.memoria import TOOLS_MEMORIA

# Só salvar_mensagem entra aqui. iniciar_sessao() é chamada por ela mesma, e
# encerrar_sessao() mudou de camada: virou POST /sessions/{id}/encerrar, em
# app/routes/sessions.py. O critério é a natureza do evento — salvar_mensagem()
# acontece a cada turno e precisa rodar aqui dentro porque grava a versão
# ANONIMIZADA da pergunta, que só existe depois do guardrail de entrada.
# Já encerrar_sessao() acontece uma vez por conversa, disparado pelo usuário, e
# não tem nada a ver com processar uma pergunta. Um é turno, o outro é ciclo de
# vida — por isso moram em camadas diferentes.
from app.memory import salvar_mensagem
from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
)

from app.guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada, desanonimizar_saida
from langchain_core.messages import RemoveMessage

# ==============================================================================
# AGENTES  (sem checkpointer — a memória fica no grafo)
# ==============================================================================

# TOOLS_MEMORIA (buscar_historico) vai para o roteador E para os especialistas,
# porque os dois usam a memória para coisas diferentes:
#
#   roteador     → perguntas que SÓ dependem do passado ("o que eu te falei
#                  sobre a viagem?"). Não existe especialista para isso; sem a
#                  tool aqui, a pergunta cairia em fora_escopo.
#   especialista → perguntas em que o passado é insumo da RESPOSTA ("lembrando
#                  da viagem, quanto preciso separar?"). O resultado da tool
#                  nasce dentro do contexto dele e alimenta o JSON de saída.
#
# O FAQ fica de fora de propósito: ele responde sobre as regras do Assessor.AI
# a partir do PDF, não sobre o que ESTE usuário conversou. Dar a tool a ele só
# faria o modelo confundir "as regras do sistema" com "o que você me disse".
router_app = create_agent(
    model=llm_roteador,   # 120b: o 20b quebra com tools + histórico (ver app/llm.py)
    tools=TOOLS_MEMORIA,
    system_prompt=ROUTER_PROMPT_COMPLETO,
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS + TOOLS_MEMORIA,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

# Ainda não existe app/tools/agenda.py, então a agenda tem só a memória por
# enquanto — ela monta o JSON do evento no texto, sem persistir nada.
agenda_app = create_agent(
    model=llm_especialista,
    tools=TOOLS_MEMORIA,
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
def no_roteador(estado: Estado, config: RunnableConfig) -> dict:
    # O `config` é injetado pelo LangGraph nos nós que o declaram na assinatura,
    # e precisa ser repassado à mão no .invoke(). Diferente de financeiro/agenda,
    # que são nós do grafo (add_node) e herdam o config sozinhos por serem
    # subgrafos, o router_app é chamado dentro de uma função Python comum — nada
    # propaga o config para lá. Faltando qualquer uma das duas pontas, a tool
    # buscar_historico não recebe o thread_id/user_id e responde "não foi
    # possível identificar o usuário", sem nada apontar para o config.
    saida = router_app.invoke({"messages": list(estado["messages"])}, config=config)
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
def no_guardrail_entrada(estado: Estado, config: RunnableConfig) -> dict:
    ultima = estado["messages"][-1]
    texto_anonimizado, mapa = anonimizar_entrada(ultima.content)
    resultado = guardrail_entrada(texto_anonimizado)

    # Reescreve a mensagem do usuário com a versão anonimizada. O reducer
    # add_messages substitui a mensagem existente quando o `id` é o mesmo — sem
    # isso, anonimizar_entrada() calculava o texto limpo e ninguém o usava: os
    # agentes recebiam o CPF em texto puro, e o salvar_mensagem() abaixo o
    # persistiria assim no MongoDB.
    mensagens = [{"role": "human", "content": texto_anonimizado, "id": ultima.id}]

    if resultado["bloqueado"]:
        mensagens.append({"role": "assistant", "content": resultado["mensagem"]})

    # A gravação mora aqui, e não antes, porque é este o primeiro ponto do fluxo
    # em que existe uma versão sem dado pessoal da pergunta. Perguntas bloqueadas
    # também são gravadas: o resumo deve refletir a conversa que houve.
    session_id = (config or {}).get("configurable", {}).get("thread_id")
    if session_id:
        salvar_mensagem(session_id, "user", texto_anonimizado)

    return {
        "agentes_chamados": ["guardrail_entrada"],
        "mapa_pii": mapa,
        "messages": mensagens,
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
def executar_fluxo_assessor(pergunta_usuario: str, session_id: str) -> dict:
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

    resposta = estado_final["messages"][-1].content

    # A resposta é gravada aqui, e não dentro de um nó, porque existem três
    # saídas diferentes do grafo — guardrail_saida, resposta direta do roteador
    # e bloqueio na entrada — e só este ponto está depois de todas elas. A
    # pergunta é gravada no no_guardrail_entrada, onde nasce a versão anonimizada.
    salvar_mensagem(session_id, "assistant", resposta)

    return {
        "resposta": resposta,
        "agentes_chamados": estado_final["agentes_chamados"],
    }
