import os
from dotenv import load_dotenv
import psycopg2
from typing import Optional
from langchain.tools import tool
from pydantic import BaseModel, Field
from datetime import datetime

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


# Essa classe garante que o objeto de Python passe todos esses campos
class AddTransactionArgs(BaseModel):
    amount: float = Field(..., description="Valor da transação (use positivo).")
    source_text: str = Field(..., description="Texto original do usuário.")
    occurred_at: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601; se ausente, usa NOW() no banco."
    )
    type_id: Optional[int] = Field(default=None, description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).")
    type_name: Optional[str] = Field(default=None, description="Nome do tipo: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=12, description="FK de categories (opcional). (1=comida, 2=besteira, 5=transporte, 6=moradia, 7= saúde, 8=lazer)")

    category_name: Optional[str] = Field(default=None, description="Nome da categoria (opcional e para e gerenciamento de gastos e entradas). (As opções disponíveis são: comida, besteira, transporte, moradia, saúde, lazer).")

    description: Optional[str] = Field(default=None, description="Descrição (opcional).")
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento (opcional).")

class QueryTransactionArgs(BaseModel):
    text: str = Field(..., description="Filtro por texto (description/source_text), datas, locais, categorias de gasto.")
    start_date: Optional[datetime]= Field(default=None, description='Data inicial para filtro por data (opcional).')
    end_date: Optional[datetime] = Field(default=None, description='Data final para filtro por data (opcional).')
    category_id: Optional[int] = Field(default=None, description="FK de categories (opcional). (1=comida, 2=besteira, 5=transporte, 6=moradia, 7= saúde, 8=lazer)")

    category_name: Optional[str] = Field(default=None, description="Nome da categoria (opcional e para e gerenciamento de gastos e entradas). (As opções disponíveis são: comida, besteira, transporte, moradia, saúde, lazer).")

    type_id: Optional[int] = Field(default=None, description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).")
    type_name: Optional[str] = Field(default=None, description="Nome do tipo: INCOME | EXPENSES | TRANSFER.")

#Garante que o campo type da tabela transactions receba um id válido (1=INCOME, 2=EXPENSES, 3=TRANSFER    
TYPE_ALIASES = {
    "INCOME": "INCOME", "ENTRADA": "INCOME", "RECEITA": "INCOME",
    "SALÁRIO": "INCOME", "EXPENSES": "EXPENSES", "DESPESA": "EXPENSES", "GASTO": "EXPENSES",
    "TRANSFER": "TRANSFER", "TRANSFERÊNCIA": "TRANSFER", "TRANSFERENCIA": "TRANSFER"
}

def _resolve_type_id(cur, type_id: Optional[int], type_name: Optional[str]) -> Optional[int]:
    if type_name:
        t = type_name.strip().upper()
        if t in TYPE_ALIASES:
            t = TYPE_ALIASES[t]
        cur.execute("SELECT id FROM transaction_types WHERE UPPER(type)=%s LIMIT 1;", (t,))
        row = cur.fetchone()
        if row: return row[0]
    
    if type_id is not None:
        return int(type_id)
    return 2

def _resolve_category_id(cur, category_id: Optional[int], category_name: Optional[str]) -> Optional[int]:
    if category_name:
        c = category_name.strip().lower()
        cur.execute("SELECT id FROM categories WHERE LOWER(name)=%s LIMIT 1;", (c,))
        row = cur.fetchone()
        if row: return row[0]
    
    if category_id is not None:
        return int(category_id)
    return 12


# Tool: add_transaction
@tool("add_transaction", args_schema=AddTransactionArgs)
def add_transaction(
    amount: float,
    source_text: str,
    occurred_at: Optional[str] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> dict:
    """Insere uma transação financeira no banco de dados Postgres.""" # docstring obrigatório da @tools do langchain (estranho, mas legal né?)
    conn = get_conn()
    cur = conn.cursor()
    try:
        resolved_type_id = _resolve_type_id(cur, type_id, type_name)
        resolved_category_id = _resolve_category_id(cur, category_id, category_name)
        if not resolved_type_id:
            return {"status": "error", "message": "Tipo inválido (use type_id ou type_name: INCOME/EXPENSES/TRANSFER)."}

        if occurred_at:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, resolved_category_id, description, payment_method, occurred_at, source_text),
            )
        else:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, NOW(), %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, resolved_category_id, description, payment_method, source_text),
            )

        new_id, occurred = cur.fetchone()
        conn.commit()
        return {"status": "ok", "id": new_id, "occurred_at": str(occurred)}

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

@tool('search-transactions', args_schema=QueryTransactionArgs)
def search_transactions(
    text: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
) -> dict:
    """Consulta transações com filtros por texto (source_text/description), tipo e datas locais (America/Sao_Paulo).
    Os dados devem vir na seguinte ordem:
    
    Intervalo (date_from_local/date_to_local): ASC (cronológico).
    
    Caso contrário: DESC (mais recentes primeiro)."""

    conn = get_conn()
    cur = conn.cursor()

    try:
        conditions = []
        params = []

        if text:
            conditions.append("(source_text ~* %s OR description ~* %s)")
            params.extend([text, text])

        if start_date and end_date:
            conditions.append("occurred_at BETWEEN %s AND %s")
            params.extend([start_date, end_date])
            order_by = "ASC"
        else:
            order_by = "DESC"

        if category_id is not None or category_name is not None:
            resolved_category_id = _resolve_category_id(cur, category_id, category_name)
            if resolved_category_id:
                conditions.append("category_id = %s")
                params.append(resolved_category_id)

        if type_id is not None or type_name is not None:
            resolved_type_id = _resolve_type_id(cur, type_id, type_name)
            if resolved_type_id:
                conditions.append("type = %s")
                params.append(resolved_type_id)

        query = "SELECT * FROM transactions"

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += f" ORDER BY occurred_at {order_by}"

        cur.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return {"status": "ok", "results": [dict(zip(columns, row)) for row in rows]}

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

# Exporta a lista de tools
TOOLS = [add_transaction, search_transactions]
