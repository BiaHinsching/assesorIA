import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from app.tools.financeiro import (
    add_transaction as _add_transaction,
    saldo_diario as _saldo_diario,
    search_transactions as _search_transactions,
    saldo_total as _saldo_total,
    update_transaction as _update_transaction,
)

mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions="Ferramentas financeiras do Assessor.AI...",
)

LEITURA = ToolAnnotations(readOnlyHint=True)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Saldo de todo o histórico: receitas menos despesas.",
    annotations=LEITURA,
)
def total_balance() -> dict[str, Any]:
    return _saldo_total.invoke({})


@mcp.tool(
    name="daily_balance",
    title="Saldo diário",
    description="Saldo (receitas menos despesas) de um dia local (America/Sao_Paulo).",
    annotations=LEITURA,
)
def daily_balance(
    date_local: Annotated[str, Field(description="Data local no formato YYYY-MM-DD.")],
) -> dict[str, Any]:
    return _saldo_diario.invoke({"date_local": date_local})


@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description="Registra uma transação na tabela transactions.",
    annotations=ESCRITA,
)
def add_transaction(
    amount: Annotated[float, Field(description="Valor (sempre positivo).")],
    source_text: Annotated[str, Field(description="Texto original.")],
    occurred_at: Annotated[Optional[str], Field(description="Timestamp ISO 8601; se ausente, usa NOW().")] = None,
    type_id: Annotated[Optional[int], Field(description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).")] = None,
    type_name: Annotated[Optional[str], Field(description="INCOME | EXPENSES | TRANSFER")] = None,
    category_id: Annotated[Optional[int], Field(description="FK de categories.")] = None,
    category_name: Annotated[Optional[str], Field(description="Categoria em pt-BR.")] = None,
    description: Annotated[Optional[str], Field(description="Descrição (opcional).")] = None,
    payment_method: Annotated[Optional[str], Field(description="Forma de pagamento (opcional).")] = None,
) -> dict[str, Any]:
    return _add_transaction.invoke({
        "amount": amount,
        "source_text": source_text,
        "occurred_at": occurred_at,
        "type_id": type_id,
        "type_name": type_name,
        "category_id": category_id,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
    })


@mcp.tool(
    name="search_transactions",
    title="Buscar transações",
    description="Consulta transações com filtros por texto, tipo, categoria e datas.",
    annotations=LEITURA,
)
def search_transactions(
    text: Annotated[str, Field(description="Filtro por texto (description/source_text).")],
    start_date: Annotated[Optional[datetime], Field(description="Data inicial do intervalo (opcional).")] = None,
    end_date: Annotated[Optional[datetime], Field(description="Data final do intervalo (opcional).")] = None,
    category_id: Annotated[Optional[int], Field(description="FK de categories (opcional).")] = None,
    category_name: Annotated[Optional[str], Field(description="Categoria em pt-BR (opcional).")] = None,
    type_id: Annotated[Optional[int], Field(description="ID em transaction_types (opcional).")] = None,
    type_name: Annotated[Optional[str], Field(description="INCOME | EXPENSES | TRANSFER (opcional).")] = None,
) -> dict[str, Any]:
    return _search_transactions.invoke({
        "text": text,
        "start_date": start_date,
        "end_date": end_date,
        "category_id": category_id,
        "category_name": category_name,
        "type_id": type_id,
        "type_name": type_name,
    })


@mcp.tool(
    name="update_transaction",
    title="Atualizar transação",
    description="Atualiza uma transação existente por ID, ou por (match_text + date_local).",
    annotations=ESCRITA,
)
def update_transaction(
    id: Annotated[Optional[int], Field(description="ID da transação a atualizar.")] = None,
    match_text: Annotated[Optional[str], Field(description="Texto para localizar a transação quando id não for informado.")] = None,
    date_local: Annotated[Optional[str], Field(description="Data local (YYYY-MM-DD) usada junto de match_text quando id ausente.")] = None,
    amount: Annotated[Optional[float], Field(description="Novo valor.")] = None,
    type_id: Annotated[Optional[int], Field(description="Novo type_id (1/2/3).")] = None,
    type_name: Annotated[Optional[str], Field(description="Novo type_name: INCOME | EXPENSES | TRANSFER.")] = None,
    category_id: Annotated[Optional[int], Field(description="Nova categoria (id).")] = None,
    category_name: Annotated[Optional[str], Field(description="Nova categoria (nome).")] = None,
    description: Annotated[Optional[str], Field(description="Nova descrição.")] = None,
    payment_method: Annotated[Optional[str], Field(description="Novo meio de pagamento.")] = None,
    occurred_at: Annotated[Optional[str], Field(description="Novo timestamp ISO 8601.")] = None,
) -> dict[str, Any]:
    return _update_transaction.invoke({
        "id": id,
        "match_text": match_text,
        "date_local": date_local,
        "amount": amount,
        "type_id": type_id,
        "type_name": type_name,
        "category_id": category_id,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
        "occurred_at": occurred_at,
    })