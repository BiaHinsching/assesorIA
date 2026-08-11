from fastapi import FastAPI
from app.routes.chat import router as chat_router
app = FastAPI(
    title = "AssesorIA",
    description = "Assessor financeiro e de agenda com Langchain e LangGraph",
    version = "0.1.0",
)

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(chat_router)