"""
Rota de escrita do perfil financeiro — a única que a tela Perfil consome.

Só existe escrita: não há leitura, listagem nem exclusão. A tela envia o
formulário, o POST devolve o perfil gravado e é isso que confirma o salvamento.

Fino de propósito (como chat.py e sessions.py): valida o contrato, chama quem
sabe persistir (app.perfil) e devolve. A gravação nos DOIS armazéns (Mongo +
Qdrant) acontece dentro de salvar_perfil, disparada aqui — nunca sob demanda
numa consulta.
"""

from fastapi import APIRouter

from app.perfil import salvar_perfil
from app.schemas import PerfilRequest, PerfilResponse

router = APIRouter(tags=["perfil"])


@router.post("/perfil", response_model=PerfilResponse)
def salvar(requisicao: PerfilRequest) -> PerfilResponse:
    """Grava/atualiza o perfil do usuário. Dado inválido é recusado pelo
    PerfilRequest (422) antes de chegar aqui."""
    perfil = salvar_perfil(
        user_id=requisicao.user_id,
        renda_mensal=requisicao.renda_mensal,
        objetivo=requisicao.objetivo,
        tolerancia_risco=requisicao.tolerancia_risco,
        preferencias=requisicao.preferencias,
    )
    return PerfilResponse(**perfil)
