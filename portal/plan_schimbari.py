"""Aplicarea programata a schimbarilor de plan self-service (upgrade cu
intrare in vigoare la finalul perioadei curente, sau orice downgrade -
niciodata imediat, vezi portal/app.py::schimba_plan) - genereaza automat
contractul nou abia acum, la momentul aplicarii, nu la momentul cererii
(randul plan_schimbari_programate ramane fara contract_id pana atunci). Un
contract nesemnat generat prematur ar deveni gresit "contractul curent" al
firmei (vezi _contract_curent) si i-ar bloca platile pe planul VECHI, inca
valabil, cat timp schimbarea era doar programata.

Rulat in proces (fir de fundal, vezi start_scheduler), la fel ca
portal/trial_reminders.py si portal/risk_alerts.py.
"""
import threading
import time
import traceback
from datetime import datetime, timezone

from portal import db as pdb

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
RETRY_INTERVAL_SECONDS = 30 * 60


def aplica_schimbari_programate(conn, aplica_una_fn) -> int:
    """Parcurge schimbarile programate a caror aplica_la a trecut deja si
    cheama aplica_una_fn(row) (injectat din portal/app.py - genereaza si
    trimite contractul nou, apoi actualizeaza firms) pentru fiecare. Randul
    e marcat 'aplicata' doar daca aplica_una_fn a reusit - o eroare izolata
    (ANAF/eSemneaza indisponibile) lasa randul 'in_asteptare', reincercat la
    urmatorul tick, fara sa opreasca procesarea celorlalte randuri.
    Returneaza numarul de schimbari aplicate cu succes."""
    acum = datetime.now(timezone.utc).isoformat()
    randuri = conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE stare=? AND aplica_la<=?",
        (pdb.PLAN_SCHIMBARE_STARE_IN_ASTEPTARE, acum)).fetchall()
    # Vezi comentariul identic din trial_reminders.verifica_si_trimite -
    # aceeasi problema de conexiune "idle in transaction" cand randuri e gol.
    conn.commit()
    n_aplicate = 0
    for row in randuri:
        try:
            contract_id = aplica_una_fn(row)
        except Exception:
            traceback.print_exc()
            continue
        conn.execute(
            "UPDATE plan_schimbari_programate SET stare=?, aplicata_la=?, "
            "contract_id=? WHERE id=?",
            (pdb.PLAN_SCHIMBARE_STARE_APLICATA, acum, contract_id, row["id"]))
        conn.commit()
        n_aplicate += 1
    return n_aplicate


def start_scheduler(conn, aplica_una_fn, lock) -> None:
    """Fir de fundal care aplica periodic schimbarile de plan programate
    scadente, la fel ca portal/trial_reminders.py. Verifica imediat la
    pornire (recupereaza aplicari ratate cat serverul a fost oprit), apoi la
    fiecare CHECK_INTERVAL_SECONDS."""
    def _loop():
        while True:
            try:
                with lock:
                    aplica_schimbari_programate(conn, aplica_una_fn)
                time.sleep(CHECK_INTERVAL_SECONDS)
            except Exception:
                traceback.print_exc()
                time.sleep(RETRY_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
