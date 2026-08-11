from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    session_id: str = Field(..., examples=["id_usario"])
    pergunta: str = Field(..., examples=["Gastei 50 reais no mercado, quanto eu tenho?"])

class ChatResponse(BaseModel):
    resposta: str = Field(..., examples=["Você tem 100 reais."])
    agentes_chamados: list[str] = Field(default_factory=list)
