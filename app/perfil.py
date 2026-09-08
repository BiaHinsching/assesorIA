"""
Perfil financeiro do usuário — dado de APOIO do especialista financeiro.

Dois tipos de dado, dois armazéns, uma única porta de escrita:

  - estruturado (renda_mensal, objetivo, tolerancia_risco) → MongoDB, collection
    "perfis", _id = user_id. Consulta direta por usuário.
  - preferências (texto livre) → Qdrant, collection "perfil_preferencias".
    Uma frase por ponto, para busca SEMÂNTICA: "não quero nada agressivo"
    precisa ser encontrada numa pergunta sobre "cripto", sem a palavra aparecer.

salvar_perfil() escreve nos DOIS a partir do mesmo ponto (a rota POST /perfil).
O índice do Qdrant NUNCA é populado sob demanda numa consulta — só aqui.

Re-salvar com o mesmo user_id SUBSTITUI: o doc do Mongo é trocado inteiro e
todos os pontos antigos do usuário no Qdrant são apagados antes de inserir os
novos. Nada do texto antigo sobrevive ao lado do novo.
"""

import re
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_URI, MONGODB_DB_NAME
from app.vectorstore import qdrant, gerar_embedding, garantir_collection

_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
col_perfis = _client[MONGODB_DB_NAME]["perfis"]

COLLECTION_PERFIL = "perfil_preferencias"


def _preparar_qdrant() -> None:
    """Garante a collection e o índice de payload por user_id (o Qdrant exige
    índice para filtrar num delete). Idempotente — seguro chamar toda vez."""
    garantir_collection(COLLECTION_PERFIL)
    try:
        qdrant.create_payload_index(
            collection_name=COLLECTION_PERFIL,
            field_name="user_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # índice já existe


# Prepara no import do módulo (o servidor sobe → collection já existe, nunca é
# criada sob demanda numa consulta). Em ambiente sem Qdrant (testes) o import
# não pode quebrar — salvar_perfil() chama _preparar_qdrant() de novo.
try:
    _preparar_qdrant()
except Exception:
    pass


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _frases(texto: str) -> list[str]:
    """Quebra o texto livre em frases — cada uma vira um ponto no Qdrant, para
    que "não quero investimento agressivo" seja encontrável sozinho, sem
    depender do resto do parágrafo."""
    partes = re.split(r"[.;\n]+", texto)
    return [p.strip() for p in partes if p.strip()]


def salvar_perfil(
    user_id: str,
    renda_mensal: float,
    objetivo: str,
    tolerancia_risco: str,
    preferencias: str,
) -> dict:
    """Grava/atualiza o perfil nos dois armazéns e devolve o que ficou gravado."""
    _preparar_qdrant()
    agora = _agora()

    doc = {
        "_id": user_id,
        "user_id": user_id,
        "renda_mensal": renda_mensal,
        "objetivo": objetivo,
        "tolerancia_risco": tolerancia_risco,
        "preferencias": preferencias,
        "atualizado_em": agora,
    }
    col_perfis.replace_one({"_id": user_id}, doc, upsert=True)

    # Qdrant: apaga TUDO que era deste usuário antes de reinserir.
    qdrant.delete(
        collection_name=COLLECTION_PERFIL,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
            )
        ),
    )
    frases = _frases(preferencias)
    if frases:
        vetores = [gerar_embedding(f) for f in frases]
        qdrant.upsert(
            collection_name=COLLECTION_PERFIL,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=v,
                    payload={"user_id": user_id, "texto": f},
                )
                for f, v in zip(frases, vetores)
            ],
        )

    doc["atualizado_em"] = agora.isoformat()
    return doc


def buscar_perfil_estruturado(user_id: str) -> dict | None:
    """Lookup direto do dado estruturado. None se o usuário não tem perfil."""
    return col_perfis.find_one({"_id": user_id})


def buscar_preferencias(user_id: str, consulta: str, limite: int = 3) -> list[str]:
    """Busca SEMÂNTICA nas preferências DESTE usuário (filtro por user_id no
    payload). Devolve as frases mais próximas do assunto consultado."""
    if not qdrant.collection_exists(COLLECTION_PERFIL):
        return []
    resultados = qdrant.query_points(
        collection_name=COLLECTION_PERFIL,
        query=gerar_embedding(consulta),
        query_filter=models.Filter(
            must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
        ),
        limit=limite,
    )
    return [p.payload["texto"] for p in resultados.points]
