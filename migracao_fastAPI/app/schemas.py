from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    session_id: str = Field(..., examples=["id_usario"])
    pergunta: str = Field(..., examples=["Gastei 50 reais no mercado, quanto eu tenho?"])

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
