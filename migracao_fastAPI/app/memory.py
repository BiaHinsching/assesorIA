from pymongo import MongoClient

from app.config import MONGODB_URI, MONGODB_DB_NAME

_client = MongoClient(MONGODB_URI)
col_sessoes = _client[MONGODB_DB_NAME]["sessoes"]


def recuperar_historico(session_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    """
    Recupera resumos de sessões ANTERIORES (já encerradas) de um usuário.

    Estratégia: olha primeiro os resumos. Se houver termo de busca, filtra
    por ele; senão, traz as sessões mais recentes. As mensagens completas
    NÃO vêm aqui — para isso use recuperar_mensagens(doc_id).

    session_id : identifica o usuário (hoje fixo, depois dinâmico)
    busca      : termo opcional para filtrar resumos relevantes
    limite     : máximo de sessões retornadas (mais recentes primeiro)
    """
    # só sessões DESTE usuário que já têm resumo (= já encerradas)
    filtro = {"session_id": session_id}

    # se houver termo de busca, filtra resumos que o contenham (case-insensitive)
    if busca:
        filtro["resumo"] = {"$regex": busca, "$options": "i"}

    docs = (
        col_sessoes
        .find(filtro, {"resumo": 1, "iniciada_em": 1})  # projeção: sem mensagens
        .sort("iniciada_em", -1)                          # mais recentes primeiro
        .limit(limite)
    )

    return [
        {"doc_id": d["_id"], "iniciada_em": d["iniciada_em"], "resumo": d["resumo"]}
        for d in docs
    ]


def recuperar_mensagens(doc_id: str) -> list[dict]:
    """
    Busca o array completo de mensagens de um documento específico, pelo _id.
    Usada no passo 2 — só quando o resumo deu match e você precisa do detalhe
    literal da conversa. No futuro, o doc_id virá do Qdrant.
    """
    doc = col_sessoes.find_one({"_id": doc_id}, {"mensagens": 1})
    return doc["mensagens"] if doc else []
