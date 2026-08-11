from fastapi import FastAPI

app = FastAPI(
    title = "AssesorIA",
    description = "Assessor financeiro e de agenda com Langchain e LangGraph",
    version = "0.1.0",
)

@app.get("/health")
def health():
    return {"status": "ok"}