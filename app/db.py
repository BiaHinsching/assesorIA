import threading
from contextlib import contextmanager
from typing import Callable, Optional

import psycopg2

from app.config import DATABASE_URL

# Quanto uma tool espera por um lock de linha dentro da transação do turno. Sem
# teto, um UPDATE na mesma linha de outro turno em andamento esperaria até esse
# turno terminar (incluindo o juiz, que tem timeout de 30s).
LOCK_TIMEOUT_MS = 5000


def get_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


class UnidadeDeTrabalho:
    """
    A transação do turno: tudo que as tools gravam no Postgres fica pendente
    até o veredito do juiz. Aprovado → commit. Qualquer outra coisa → rollback.

    Viaja em config["configurable"]["uow"]. Valores que não são str/int/float/
    bool ficam fora dos metadados do checkpoint, então o MemorySaver não tenta
    serializar a conexão.

    O padrão é desfazer: se ninguém chamar finalizar(aprovado=True), encerrar()
    faz rollback. Um crash do processo também desfaz, porque o servidor reverte
    a transação quando a conexão cai.

    As tools de um mesmo passo do agente rodam em threads. O RLock serializa o
    corpo de cada tool: sem ele, o ROLLBACK TO SAVEPOINT de uma tool desfaria
    escritas feitas por outra no meio do caminho.
    """

    def __init__(self) -> None:
        self._conn = None
        self._lock = threading.RLock()
        self._contador_savepoint = 0
        self._adiadas: list[tuple[Callable[[], dict], str]] = []
        self.finalizada = False

    # -- conexão -------------------------------------------------------------
    def conexao(self):
        with self._lock:
            if self._conn is None:
                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"SET LOCAL lock_timeout = {int(LOCK_TIMEOUT_MS)}")
                except Exception:
                    conn.close()
                    raise
                self._conn = conn
            return self._conn

    @contextmanager
    def trecho(self):
        """
        Uma tool dentro do turno. O SAVEPOINT mantém um erro SQL local a esta
        chamada: sem ele, a transação inteira ficaria abortada e o COMMIT final
        viraria rollback sem avisar, perdendo escritas de outras tools já
        aprovadas pelo juiz.
        """
        with self._lock:
            conn = self.conexao()
            self._contador_savepoint += 1
            nome = f"sp_{self._contador_savepoint}"
            with conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {nome}")
            try:
                yield conn
            except BaseException:
                with conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {nome}")
                raise
            else:
                with conn.cursor() as cur:
                    cur.execute(f"RELEASE SAVEPOINT {nome}")

    # -- efeitos fora do Postgres -------------------------------------------
    def adiar(self, acao: Callable[[], dict], aviso_se_falhar: str) -> None:
        """Guarda um efeito externo para depois do veredito (ex.: Google Calendar)."""
        with self._lock:
            self._adiadas.append((acao, aviso_se_falhar))

    # -- fim do turno --------------------------------------------------------
    def finalizar(self, aprovado: bool, resposta: str) -> str:
        """
        Aprovado: commit, depois os efeitos adiados. Se o commit falhar, a
        exceção sobe (o turno vira erro e nada foi gravado). Recusado: rollback
        e os efeitos adiados são descartados sem nunca terem rodado.
        Devolve a resposta, com um aviso fixo anexado se um efeito adiado falhar.
        """
        with self._lock:
            if self.finalizada:
                return resposta
            if not aprovado:
                self._desfazer()
                return resposta
            if self._conn is not None:
                self._conn.commit()
            self.finalizada = True
            avisos = []
            for acao, aviso in self._adiadas:
                try:
                    resultado = acao()
                    falhou = not isinstance(resultado, dict) or resultado.get("status") != "ok"
                except Exception:
                    falhou = True
                if falhou:
                    avisos.append(aviso)
            self._adiadas.clear()
            self._fechar()
            return resposta if not avisos else f"{resposta}\n\n" + " ".join(avisos)

    def encerrar(self) -> None:
        """Rede de segurança do `finally`: desfaz o que não foi finalizado e fecha."""
        with self._lock:
            if not self.finalizada:
                self._desfazer()
            self._fechar()

    def _desfazer(self) -> None:
        self.finalizada = True
        self._adiadas.clear()
        if self._conn is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def _fechar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def uow_do_config(config: Optional[dict]) -> Optional[UnidadeDeTrabalho]:
    return ((config or {}).get("configurable") or {}).get("uow")


@contextmanager
def transacao(config: Optional[dict] = None):
    """
    A conexão que uma tool deve usar.

    Com unidade de trabalho no config (turno do grafo): usa a conexão do turno,
    dentro de um SAVEPOINT, sem commitar nem fechar. Quem decide é o juiz.

    Sem ela (mcp_server.py, scripts): comportamento de sempre — conexão própria,
    commit ao sair sem erro, rollback se houver erro, e fecha.
    """
    uow = uow_do_config(config)
    if uow is not None:
        with uow.trecho() as conn:
            yield conn
        return

    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
