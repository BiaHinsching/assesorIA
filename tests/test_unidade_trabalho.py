"""
app/db.py — a unidade de trabalho do turno. Conexão falsa: o que se prova aqui
é QUANDO se commita, se desfaz e se fecha, não o SQL em si.
"""

import threading

import pytest

from helpers_juiz import FabricaDeConexoes


@pytest.fixture
def fabrica(monkeypatch):
    import app.db as db

    f = FabricaDeConexoes()
    monkeypatch.setattr(db, "get_conn", f)
    return f


@pytest.fixture
def uow():
    from app.db import UnidadeDeTrabalho

    return UnidadeDeTrabalho()


# ── ciclo de vida: o padrão é desfazer ───────────────────────────────────────

def test_aprovado_commita_e_fecha(fabrica, uow):
    with uow.trecho():
        pass
    assert uow.finalizar(True, "ok") == "ok"
    conn = fabrica.conexoes[0]
    assert conn.commits == 1 and conn.rollbacks == 0
    assert conn.closed


def test_recusado_desfaz_e_nao_commita(fabrica, uow):
    with uow.trecho():
        pass
    uow.finalizar(False, "ok")
    uow.encerrar()
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1
    assert conn.closed


def test_sem_finalizar_o_encerrar_desfaz(fabrica, uow):
    """Exceção no meio do turno: ninguém chamou finalizar()."""
    with uow.trecho():
        pass
    uow.encerrar()
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1 and conn.closed


def test_turno_sem_escrita_nao_abre_conexao(fabrica, uow):
    assert uow.finalizar(True, "ok") == "ok"
    uow.encerrar()
    assert fabrica.conexoes == []


def test_encerrar_e_idempotente(fabrica, uow):
    with uow.trecho():
        pass
    uow.encerrar()
    uow.encerrar()
    assert fabrica.conexoes[0].rollbacks == 1


def test_finalizar_depois_de_finalizado_nao_repete(fabrica, uow):
    with uow.trecho():
        pass
    uow.finalizar(True, "a")
    uow.finalizar(False, "a")
    assert fabrica.conexoes[0].commits == 1
    assert fabrica.conexoes[0].rollbacks == 0


def test_falha_no_commit_sobe_e_o_encerrar_desfaz(monkeypatch, uow):
    import app.db as db
    from helpers_juiz import ConexaoFalsa

    conn = ConexaoFalsa()

    def commit_quebrado():
        raise RuntimeError("conexão caiu")

    conn.commit = commit_quebrado
    monkeypatch.setattr(db, "get_conn", lambda: conn)
    executou = []
    with uow.trecho():
        pass
    uow.adiar(lambda: executou.append(1) or {"status": "ok"}, "aviso")
    with pytest.raises(RuntimeError):
        uow.finalizar(True, "ok")
    uow.encerrar()
    assert conn.rollbacks == 1 and conn.closed
    assert executou == []   # o efeito externo não roda se a escrita não foi confirmada


# ── conexão ──────────────────────────────────────────────────────────────────

def test_abre_a_conexao_com_lock_timeout(fabrica, uow):
    with uow.trecho():
        pass
    assert any(s.startswith("SET LOCAL lock_timeout") for s in fabrica.conexoes[0].log)


def test_duas_threads_na_primeira_vez_abrem_uma_conexao_so(fabrica, uow):
    barreira = threading.Barrier(8)

    def usar():
        barreira.wait()
        with uow.trecho():
            pass

    threads = [threading.Thread(target=usar) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(fabrica.conexoes) == 1


# ── SAVEPOINT: um erro SQL fica local à tool ─────────────────────────────────

def test_savepoint_liberado_no_sucesso(fabrica, uow):
    with uow.trecho():
        pass
    log = fabrica.conexoes[0].log
    assert "SAVEPOINT sp_1" in log and "RELEASE SAVEPOINT sp_1" in log


def test_erro_desfaz_so_o_savepoint_e_re_levanta(fabrica, uow):
    with pytest.raises(ValueError):
        with uow.trecho():
            raise ValueError("boom")
    conn = fabrica.conexoes[0]
    assert "ROLLBACK TO SAVEPOINT sp_1" in conn.log
    assert conn.rollbacks == 0          # a transação do turno continua viva
    with uow.trecho():                  # e a próxima tool funciona
        pass
    assert "RELEASE SAVEPOINT sp_2" in conn.log


def test_tools_em_threads_nao_entrelacam_savepoints(fabrica, uow):
    """Sem o RLock, o ROLLBACK TO de uma tool desfaria escritas da outra."""
    fabrica.kwargs["atraso"] = 0.002
    def usar(i):
        try:
            with uow.trecho() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"INSERT {i}")
                    cur.execute(f"INSERT {i}")
                if i % 2:
                    raise ValueError
        except ValueError:
            pass

    threads = [threading.Thread(target=usar, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    log = [s for s in fabrica.conexoes[0].log if s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK TO", "INSERT"))]
    # cada trecho tem de ser contíguo: SAVEPOINT, 2 INSERT, RELEASE|ROLLBACK TO
    for i in range(0, len(log), 4):
        bloco = log[i:i + 4]
        assert bloco[0].startswith("SAVEPOINT")
        assert bloco[1] == bloco[2] and bloco[1].startswith("INSERT")
        assert bloco[3].split()[-1] == bloco[0].split()[-1]


# ── efeitos fora do Postgres (Google) ────────────────────────────────────────

def test_efeito_adiado_nao_roda_se_recusado(fabrica, uow):
    rodou = []
    uow.adiar(lambda: rodou.append(1) or {"status": "ok"}, "aviso")
    uow.finalizar(False, "resp")
    uow.encerrar()
    assert rodou == []


def test_efeito_adiado_roda_depois_do_commit(fabrica, uow):
    ordem = []
    with uow.trecho() as conn:
        pass
    conn_falsa = fabrica.conexoes[0]
    uow.adiar(lambda: ordem.append(("google", conn_falsa.commits)) or {"status": "ok"}, "aviso")
    assert uow.finalizar(True, "resp") == "resp"
    assert ordem == [("google", 1)]     # já havia commit quando o Google foi chamado


@pytest.mark.parametrize("acao", [
    lambda: {"status": "error", "message": "403"},
    lambda: (_ for _ in ()).throw(RuntimeError("rede")),
    lambda: None,
])
def test_efeito_adiado_que_falha_anexa_o_aviso_e_nao_desfaz_o_commit(fabrica, uow, acao):
    with uow.trecho():
        pass
    uow.adiar(acao, "AVISO FIXO")
    resposta = uow.finalizar(True, "Registrei.")
    assert resposta == "Registrei.\n\nAVISO FIXO"
    assert fabrica.conexoes[0].commits == 1


# ── transacao(): caminho com e sem unidade ───────────────────────────────────

def test_transacao_com_uow_nao_commita_nem_fecha(fabrica, uow):
    from app.db import transacao

    with transacao({"configurable": {"uow": uow}}):
        pass
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and not conn.closed


def test_transacao_sem_uow_commita_e_fecha(fabrica):
    from app.db import transacao

    with transacao(None):
        pass
    conn = fabrica.conexoes[0]
    assert conn.commits == 1 and conn.closed


def test_transacao_sem_uow_desfaz_e_fecha_no_erro(fabrica):
    from app.db import transacao

    with pytest.raises(ValueError):
        with transacao({}):
            raise ValueError
    conn = fabrica.conexoes[0]
    assert conn.commits == 0 and conn.rollbacks == 1 and conn.closed
