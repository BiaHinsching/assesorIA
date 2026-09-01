"""
Memória de LONGO PRAZO — o que sobrevive ao fechar o navegador.

Não confundir com a memória CURTA: aquela é o MemorySaver do grafo, vive em
`estado["messages"]` (RAM), morre quando o servidor reinicia e chega sozinha no
prompt dos agentes. Esta aqui mora no MongoDB, vive para sempre e só é acessada
por quem chamar — na prática, a tool `buscar_historico` (app/tools/memoria.py).

O ciclo de vida de uma sessão tem quatro momentos:

    nasce    → iniciar_sessao()   cria o documento
    vive     → salvar_mensagem()  empilha cada turno no array `mensagens`
    encerra  → encerrar_sessao()  pede um resumo ao LLM e grava no campo `resumo`
    recupera → recuperar_historico() acha o resumo numa conversa FUTURA

O terceiro momento é o que costuma faltar: sem alguém chamar encerrar_sessao(),
o campo `resumo` fica vazio — e recuperar_historico() só enxerga documentos COM
resumo. A conversa fica guardada e invisível ao mesmo tempo. Quem dispara esse
momento é o POST /sessions/{id}/encerrar, em app/routes/sessions.py.
"""

import re
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_URI, MONGODB_DB_NAME
from app.llm import llm_rapido
from app.vectorstore import qdrant, gerar_embedding, COLLECTION_MEMORIA

_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
col_sessoes = _client[MONGODB_DB_NAME]["sessoes"]

# Cache session_id → _id do documento aberto. Vive na RAM do processo, ou seja,
# some a cada --reload do uvicorn. _doc_id_da_sessao() cobre esse buraco.
_sessoes_ativas: dict[str, str] = {}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# Tokens que o guardrail de entrada deixa no lugar do dado pessoal, no formato
# [PII_CPF_a3f9c1]. O sufixo é um UUID sorteado por requisição, e o mapa que o
# traduz de volta morre junto com ela — um resumo de março com [PII_CPF_a3f9c1]
# seria ilegível para sempre. Por isso trocamos o token por um rótulo genérico
# antes de gravar: o resumo continua compreensível sem carregar dado pessoal.
_TOKEN_PII = re.compile(r"\[PII_([A-Z]+)_[0-9a-f]+\]")


def _limpar_tokens_pii(texto: str) -> str:
    return _TOKEN_PII.sub(lambda m: f"[{m.group(1).lower()} omitido]", texto)


def _doc_id_da_sessao(session_id: str) -> str | None:
    """
    Descobre o documento da sessão EM ANDAMENTO deste usuário, ou None.

    Olha primeiro o cache em memória (_sessoes_ativas). Se não achar, procura
    no MongoDB a sessão mais recente que ainda não foi encerrada — isto é, com
    resumo vazio.

    Essa segunda tentativa existe porque _sessoes_ativas vive na RAM do
    processo: um --reload do uvicorn no meio da conversa esvazia o dicionário.
    Sem ela, iniciar_sessao() criaria um documento novo para a mesma conversa a
    cada reinício, e encerrar_sessao() não acharia nada para resumir — sem erro
    nenhum, apenas silêncio.
    """
    doc_id = _sessoes_ativas.get(session_id)
    if doc_id:
        return doc_id

    doc = col_sessoes.find_one(
        {"session_id": session_id, "resumo": {"$in": ["", None]}},
        {"_id": 1},
        sort=[("iniciada_em", -1)],
    )
    if not doc:
        return None

    _sessoes_ativas[session_id] = doc["_id"]   # repovoa o cache
    return doc["_id"]


def iniciar_sessao(session_id: str, user_id: str | None = None) -> str:
    """
    Garante que existe um documento de sessão aberto para este session_id
    e devolve o _id dele. Se já houver um (em memória ou no Mongo), não cria
    outro — é seguro chamar a cada turno.

    O `user_id` identifica o DONO da conversa de forma estável entre sessões
    (o session_id muda a cada "nova sessão"; o user_id não). É por ele que
    recuperar_historico() acha as conversas passadas. Se a sessão já existia
    sem user_id, ele é preenchido agora.
    """
    doc_id = _doc_id_da_sessao(session_id)
    if doc_id:
        if user_id:
            col_sessoes.update_one(
                {"_id": doc_id, "user_id": {"$in": ["", None]}},
                {"$set": {"user_id": user_id}},
            )
        return doc_id

    # _id string em vez do ObjectId padrão: o doc_id atravessa camadas (tool,
    # rota, futuramente o Qdrant) e um str simples evita arrastar o bson junto.
    doc_id = uuid.uuid4().hex
    col_sessoes.insert_one({
        "_id":          doc_id,
        "session_id":   session_id,
        "user_id":      user_id,
        "iniciada_em":  _agora(),
        "encerrada_em": None,
        "mensagens":    [],
        "resumo":       "",      # vazio = sessão em andamento
    })
    _sessoes_ativas[session_id] = doc_id
    return doc_id


def salvar_mensagem(
    session_id: str, role: str, content: str, user_id: str | None = None
) -> None:
    """
    Acrescenta uma mensagem ao array `mensagens` da sessão em andamento.

    O `content` que chega aqui já passou pelo guardrail de entrada, ou seja, é
    a versão ANONIMIZADA da pergunta. É por isso que a chamada mora dentro do
    grafo, depois do guardrail — invertê-la persistiria o CPF do usuário em
    texto puro no MongoDB.
    """
    doc_id = iniciar_sessao(session_id, user_id)

    col_sessoes.update_one(
        {"_id": doc_id},
        {"$push": {"mensagens": {"role": role, "content": content, "em": _agora()}}},
    )


_PROMPT_RESUMO = """\
Resuma a conversa abaixo entre um usuário e seu assessor pessoal de finanças e agenda.

REGRAS:
- 2 a 4 frases, em português, em texto corrido (sem listas, sem markdown).
- Guarde o que o assessor precisaria lembrar meses depois: decisões, preferências,
  metas, valores relevantes, planos e compromissos assumidos.
- Descarte o ruído: saudações, "ok", "obrigado", confirmações vazias.
- Escreva em terceira pessoa, começando por "O usuário".
- Se aparecerem rótulos como [cpf omitido], mantenha-os como estão — são dados
  pessoais que foram removidos de propósito.
- Responda SOMENTE com o resumo, sem introdução e sem comentários.

CONVERSA:
{conversa}
"""


def encerrar_sessao(session_id: str) -> str:
    """
    Encerra a sessão: condensa a conversa em um resumo via LLM e grava no
    documento. Devolve o resumo, ou "" quando não havia nada a encerrar.

    O resumo é o que torna a conversa visível para o futuro: recuperar_historico()
    filtra por resumo não-vazio. As mensagens literais continuam no mesmo
    documento — o resumo é o índice, as mensagens são o conteúdo.

    Custa uma chamada de LLM. Chame uma vez por conversa, nunca por turno.
    """
    doc_id = _doc_id_da_sessao(session_id)
    if not doc_id:
        return ""

    doc = col_sessoes.find_one(
        {"_id": doc_id},
        {"mensagens": 1, "user_id": 1, "iniciada_em": 1},
    )
    mensagens = (doc or {}).get("mensagens") or []
    if not mensagens:
        # Sessão aberta e vazia: não há o que resumir. Devolver "" (e não erro)
        # deixa o documento disponível para ser reaproveitado no próximo turno.
        return ""

    conversa = "\n".join(
        f"{'Usuário' if m['role'] == 'user' else 'Assessor'}: {m['content']}"
        for m in mensagens
    )

    # Limpa os tokens ANTES do LLM, para ele não copiar [PII_CPF_a3f9c1] cru
    # para dentro do resumo, e DEPOIS, caso algum escape mesmo assim.
    conversa = _limpar_tokens_pii(conversa)
    resumo = llm_rapido.invoke(_PROMPT_RESUMO.format(conversa=conversa)).content.strip()
    resumo = _limpar_tokens_pii(resumo)

    if not resumo:
        # Acontece de verdade: o gpt-oss-20b é um modelo de raciocínio e de vez
        # em quando devolve tudo em reasoning_content, deixando o content vazio.
        # Gravar "" aqui seria o pior desfecho possível — a sessão ficaria com
        # encerrada_em preenchido mas resumo vazio, ou seja, invisível para o
        # recuperar_historico() E ainda parecendo aberta para o
        # _doc_id_da_sessao(). A conversa se perderia sem nada dar erro.
        # Deixando a sessão aberta, uma nova tentativa ainda pode salvá-la.
        print(f"[memory] resumo vazio para a sessão {session_id}: sessão mantida aberta")
        return ""

    col_sessoes.update_one(
        {"_id": doc_id},
        {"$set": {"resumo": resumo, "encerrada_em": _agora()}},
    )

    # Índice de busca semântica no Qdrant. O Mongo continua sendo o armazém
    # (mensagens + metadados); o Qdrant guarda só o vetor do resumo e um payload
    # mínimo, e devolve o doc_id que aponta de volta pro documento completo.
    #
    # O filtro de recuperação usa user_id (estável entre sessões), não
    # session_id. Uma falha aqui não pode derrubar o encerramento — o resumo já
    # está salvo no Mongo e o recuperar_historico() tem fallback pra ele.
    _indexar_resumo_qdrant(doc_id, doc, resumo, session_id)

    _sessoes_ativas.pop(session_id, None)

    return resumo


def _indexar_resumo_qdrant(doc_id: str, doc: dict, resumo: str, session_id: str) -> None:
    try:
        iniciada_em = doc.get("iniciada_em")
        qdrant.upsert(
            collection_name=COLLECTION_MEMORIA,
            points=[
                models.PointStruct(
                    id=doc_id,
                    vector=gerar_embedding(resumo),
                    payload={
                        "user_id":     doc.get("user_id"),
                        "session_id":  session_id,
                        "resumo":      resumo,
                        "iniciada_em": iniciada_em.isoformat()
                                       if hasattr(iniciada_em, "isoformat")
                                       else str(iniciada_em),
                    },
                )
            ],
        )
    except Exception as e:
        print(f"[memory] falha ao indexar resumo no Qdrant ({session_id}): {e}")


def recuperar_historico(user_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    """
    Recupera resumos de sessões ANTERIORES (já encerradas) deste usuário.

    Com termo de busca → busca SEMÂNTICA no Qdrant (embedding do termo vs.
    embedding dos resumos), então "viajar" encontra "viagem para Salvador".
    Sem termo, ou se o Qdrant falhar → cai no Mongo e traz as mais recentes.
    As mensagens completas NÃO vêm aqui — para isso use recuperar_mensagens(doc_id).

    user_id : identificador estável do usuário (não muda entre sessões)
    busca   : termo opcional para filtrar resumos relevantes
    limite  : máximo de sessões retornadas
    """
    if busca:
        try:
            resultados = qdrant.query_points(
                collection_name=COLLECTION_MEMORIA,
                query=gerar_embedding(busca),
                query_filter=models.Filter(must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id),
                    )
                ]),
                limit=limite,
            )
            if resultados.points:
                return [
                    {
                        "doc_id":      p.id,
                        "iniciada_em": p.payload.get("iniciada_em", ""),
                        "resumo":      p.payload["resumo"],
                    }
                    for p in resultados.points
                ]
        except Exception as e:
            print(f"[memory] busca semântica falhou, caindo no Mongo: {e}")

    # Fallback (sem busca, Qdrant vazio ou Qdrant fora do ar): sessões DESTE
    # usuário que já têm resumo, mais recentes primeiro. O $nin descarta a
    # sessão em andamento, cujo resumo ainda está vazio.
    docs = (
        col_sessoes
        .find(
            {"user_id": user_id, "resumo": {"$nin": ["", None]}},
            {"resumo": 1, "iniciada_em": 1},
        )
        .sort("iniciada_em", -1)
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
