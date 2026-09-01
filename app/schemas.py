from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""

    session_id: str = Field(
        ...,
        description="Identifica a conversa (UUID gerado pelo front a cada sessão).",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    user_id: str = Field(
        default="usuario_teste",
        description="Identifica o usuário de forma estável entre sessões. "
                    "É o que permite a memória de longo prazo funcionar.",
        examples=["usuario_teste"],
    )
    pergunta: str = Field(
        ...,
        min_length=1,
        description="A mensagem do usuário — o que antes vinha do input().",
        examples=["gastei 50 reais no mercado hoje"],
    )

class ChatResponse(BaseModel):
    resposta: str = Field(..., examples=["Você tem 100 reais."])
    agentes_chamados: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """Resposta das rotas de ciclo de vida da sessão (app/routes/sessions.py).

    `resumo` é None quando não havia nada a encerrar — sessão inexistente ou
    sem nenhuma mensagem. Isso é resultado normal, não erro.
    """
    session_id: str = Field(..., examples=["aluno01"])
    resumo: str | None = Field(
        default=None,
        examples=["O usuário registrou um gasto de R$ 120 com uma cadeira de escritório."],
    )
