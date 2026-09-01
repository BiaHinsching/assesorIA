"""
A rota de conversa — o coração da API.

No modo script, o `while True` lia a pergunta do teclado e chamava
executar_fluxo_assessor(). Aqui é a mesma ideia: cada POST /chat é um turno da
conversa. O laço virou "o navegador manda outra requisição".

Este arquivo é fino de propósito (igual ao sessions.py): recebe o pedido, chama
quem sabe fazer o trabalho (app.graph) e devolve o resultado. Toda a lógica —
guardrail, roteamento, especialistas, orquestrador e a gravação no MongoDB —
mora dentro de executar_fluxo_assessor().
"""

from fastapi import APIRouter

from app.graph import executar_fluxo_assessor
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(requisicao: ChatRequest) -> ChatResponse:
    """
    Processa um turno da conversa.

    O `session_id` vem do navegador (UUID sorteado por conversa) e serve de
    thread_id do checkpointer E de chave da sessão no Mongo. A `pergunta` entra
    crua e é anonimizada pelo guardrail de entrada dentro do grafo, antes de ser
    persistida.
    """
    resultado = executar_fluxo_assessor(
        requisicao.pergunta, requisicao.session_id, requisicao.user_id
    )
    return ChatResponse(
        resposta=resultado["resposta"],
        agentes_chamados=resultado["agentes_chamados"],
    )
