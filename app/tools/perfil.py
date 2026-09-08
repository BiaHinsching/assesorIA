"""
Tools de PERFIL para o especialista financeiro.

Duas tools, porque os dois tipos de dado do perfil têm padrão de acesso
diferente (o próprio enunciado os separa):

  - consultar_perfil     → dado ESTRUTURADO (renda, objetivo, risco). Lookup
    direto, sem argumento: o modelo só quer os fatos.
  - buscar_preferencias  → TEXTO LIVRE. Busca semântica, então o modelo passa
    o ASSUNTO da pergunta ("cripto", "investimento agressivo"). A restrição é
    achada mesmo sem a palavra exata estar escrita.

Uma tool só forçaria o modelo a sempre inventar uma query mesmo quando quer
apenas a renda, e misturaria um lookup determinístico com uma busca vetorial.

O user_id NÃO é argumento — o modelo não escolhe de quem é o perfil. Vem do
`config["configurable"]["user_id"]`, o mesmo identificador que o chat usa
(padrão idêntico ao de buscar_historico).

Estas tools SÓ LEEM. Não existe tool de escrita de perfil: mudança de perfil
é só pela tela.
"""

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.perfil import (
    buscar_perfil_estruturado as _perfil_estruturado_db,
    buscar_preferencias as _preferencias_db,
)

_SEM_PERFIL = (
    "PERFIL_NAO_CADASTRADO. O usuário ainda não preencheu o perfil. "
    "NÃO invente renda, objetivo ou tolerância a risco — oriente o usuário a "
    "abrir a tela Perfil e cadastrar."
)


def _user_id(config: RunnableConfig) -> str | None:
    configuravel = (config or {}).get("configurable", {})
    return configuravel.get("user_id") or configuravel.get("thread_id")


@tool
def consultar_perfil(config: RunnableConfig) -> str:
    """Dados estruturados do perfil financeiro do usuário: renda mensal,
    objetivo declarado e tolerância a risco (baixa/media/alta).

    Use SEMPRE que for aconselhar sobre quanto guardar, onde alocar dinheiro,
    metas ou orçamento — o conselho tem que estar ancorado nestes dados, não
    em chute. Se não houver perfil, oriente a usar a tela Perfil.
    """
    user_id = _user_id(config)
    if not user_id:
        return "Não foi possível identificar o usuário."

    perfil = _perfil_estruturado_db(user_id)
    if not perfil:
        return _SEM_PERFIL

    return (
        f"renda_mensal={perfil['renda_mensal']}; "
        f"objetivo={perfil['objetivo']}; "
        f"tolerancia_risco={perfil['tolerancia_risco']}"
    )


@tool
def buscar_preferencias(consulta: str, config: RunnableConfig) -> str:
    """Busca semântica nas PREFERÊNCIAS em texto livre do perfil (planos,
    restrições e gostos que o usuário escreveu).

    Passe em `consulta` o assunto da pergunta do usuário — ex.: "cripto",
    "investimento agressivo", "reserva de emergência". A busca acha a
    preferência relevante mesmo que a palavra não apareça literalmente no
    texto cadastrado.

    Args:
        consulta: assunto/tema a procurar nas preferências.
    """
    user_id = _user_id(config)
    if not user_id:
        return "Não foi possível identificar o usuário."

    frases = _preferencias_db(user_id, consulta)
    if not frases:
        return "Nenhuma preferência cadastrada relevante para essa consulta."

    return "\n".join(f"- {f}" for f in frases)


TOOLS_PERFIL = [consultar_perfil, buscar_preferencias]
