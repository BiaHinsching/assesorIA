from app.config import FRONTEND_DIR, validar_config

for _problema in validar_config():
    print(f"[config] ATENÇÃO: {_problema}")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.routes import chat, sessions

app = FastAPI(
    title = "AssesorIA",
    description = "Assessor financeiro e de agenda com Langchain e LangGraph",
    version = "0.1.0",
)

# ==============================================================================
# CORS
# Quando o frontend é aberto pelo próprio FastAPI (http://localhost:8000) nada
# disto é necessário — é a mesma origem. Existe para o outro caso: abrir o
# index.html pelo Live Server ou por outra porta, quando o navegador passa a
# tratar a API como um domínio externo e bloqueia a resposta sem estes headers.
#
# O regex limita a liberação a localhost/127.0.0.1 em qualquer porta. É uma
# conveniência de desenvolvimento: em produção, troque por a lista exata de
# origens que devem poder falar com a API.
# ==============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

@app.get("/health")
def health() -> dict:
    problemas = validar_config()
    return {
        "status": "ok" if not problemas else "atencao",
        "problemas_de_configuracao": problemas,
    }

# ==============================================================================
# ROTAS
# Cada arquivo de app/routes/ expõe um `router`, e é aqui que ele entra na
# aplicação. A ordem importa por causa do mount em "/" logo abaixo: qualquer
# rota declarada DEPOIS dele nunca seria alcançada — o mount engole a
# requisição e devolve 404 de arquivo não encontrado.
# ==============================================================================
app.include_router(chat.router)
app.include_router(sessions.router)

if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/", tags =["infra"])
    def raiz() -> dict:
        return {
            "mensagem": "API do Assessor no ar. O frotend ainda não foi criado"
        }