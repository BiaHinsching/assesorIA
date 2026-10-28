"""
Peças compartilhadas pelos testes do "Juiz Tardio": conexão falsa, veredito do
JEV falso e um grafo de duas etapas (escreve → juiz) que usa o `Estado` e o
`no_juiz` reais.
"""

import json
import time


class CursorFalso:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 1
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).split())
        if self.conn.atraso:
            time.sleep(self.conn.atraso)   # força a troca de thread no meio da tool
        self.conn.log.append(texto)
        if self.conn.falhar_quando and self.conn.falhar_quando(texto):
            raise RuntimeError("falha SQL simulada")

    def fetchone(self):
        return self.conn.respostas.pop(0) if self.conn.respostas else None

    def fetchall(self):
        return []

    def close(self):
        pass


class ConexaoFalsa:
    """Registra tudo que a unidade de trabalho e as tools fazem na conexão."""

    def __init__(self, respostas=None, falhar_quando=None, atraso=0):
        self.atraso = atraso
        self.log = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.respostas = list(respostas or [])
        self.falhar_quando = falhar_quando

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1
        self.log.append("COMMIT")

    def rollback(self):
        self.rollbacks += 1
        self.log.append("ROLLBACK")

    def close(self):
        self.closed = True


class FabricaDeConexoes:
    """Substitui app.db.get_conn: cada chamada devolve uma conexão falsa nova."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.conexoes = []

    def __call__(self):
        conn = ConexaoFalsa(**self.kwargs)
        self.conexoes.append(conn)
        return conn


class _RespostaHTTP:
    def __init__(self, corpo):
        self._corpo = corpo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._corpo).encode()


def jev_falso(choice="apoiada", noul=0.95):
    """Troca o urlopen do juiz por um veredito fixo do JEV."""
    corpo = {"answers": {"veredito": {"choice": choice}, "apoiada": {"noul": noul}}}

    def _urlopen(pedido, timeout=None):
        return _RespostaHTTP(corpo)

    return _urlopen


def montar_fluxo(escrever, pular_juiz=None):
    """
    Grafo mínimo: `escrever` (as tools de verdade, chamadas com o config do nó)
    → `no_juiz` real → END. Usa o Estado real e um MemorySaver, como o grafo
    de produção, para o veredito atravessar o checkpointer.

    `pular_juiz(estado) -> bool` desvia direto para END, simulando um caminho
    do grafo que não passa pelo juiz.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    import app.graph as g
    from app.juiz import no_juiz

    sg = StateGraph(g.Estado)
    sg.add_node("escrever", escrever)
    sg.add_node("juiz", no_juiz)
    sg.set_entry_point("escrever")
    if pular_juiz is None:
        sg.add_edge("escrever", "juiz")
    else:
        sg.add_conditional_edges(
            "escrever", lambda estado: "fim" if pular_juiz(estado) else "juiz", {"fim": END, "juiz": "juiz"}
        )
    sg.add_edge("juiz", END)
    return sg.compile(checkpointer=MemorySaver())
