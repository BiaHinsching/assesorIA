from app.config import validar_config    

for _problema in validar_config():
    print(f"[config] ATENÇÃO: {_problema}")

from fastapi import FastAPI
from app.routes.chat import router as chat_router
app = FastAPI(
    title = "AssesorIA",
    description = "Assessor financeiro e de agenda com Langchain e LangGraph",
    version = "0.1.0",
)

@app.get("/health")
def health() -> dict:
    problemas = validar_config()
    return {
        "status": "ok" if not problemas else "atencao",
        "problemas_de_configuracao": problemas,
    }

app.include_router(chat_router)