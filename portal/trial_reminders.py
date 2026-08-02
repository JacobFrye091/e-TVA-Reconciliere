"""Emailuri de avertizare pentru firmele a caror perioada de proba (vezi
TRIAL_ZILE in portal/db.py) se apropie de expirare fara sa fi ales inca un
ciclu de facturare, plus arhivarea automata a celor care ajung la capatul
perioadei tot fara sa fi ales un ciclu.

Rulat fie in proces (fir de fundal, vezi start_scheduler - pornit doar din
run.py, la fel ca portal/backup.py), fie manual din panoul master
(/master/remindere-trial/trimite si /arhiveaza), fie ambele -
verifica_si_trimite e idempotenta prin firms.trial_reminder_ultim_prag, iar
arhiveaza_firme_neplatitoare prin firms.arhivata_la, deci a rula de mai
multe ori in aceeasi zi nu retrimite acelasi email si nu incearca sa
re-arhiveze o firma deja arhivata.
"""
import threading
import time
import traceback
from datetime import datetime, timezone

from portal import db as pdb

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
RETRY_INTERVAL_SECONDS = 30 * 60


def zile_ramase_trial(trial_expira_la: str) -> int:
    """Zile intregi ramase pana la trial_expira_la (folosit si de ruta master
    de listare, nu doar intern de verifica_si_trimite)."""
    expira = datetime.fromisoformat(trial_expira_la)
    return max(0, (expira - datetime.now(timezone.utc)).days)


def _prag_de_trimis(zile_ramase: int, ultim_prag_trimis: "int | None") -> "int | None":
    """Primul prag din TRIAL_REMINDER_PRAGURI_ZILE (parcurs de la cel mai
    putin urgent la cel mai urgent) atins deja de zile_ramase si netrimis
    inca - niciodata mai mult de un prag pe apel, ca o firma sa treaca prin
    toate pragurile in ordine chiar daca verificarea a ratat cateva zile
    intre doua rulari, in loc sa sara direct la cel mai urgent. Foloseste
    ultim_prag_trimis (None inainte de primul email) ca sa nu retrimita un
    prag deja notificat."""
    for prag in pdb.TRIAL_REMINDER_PRAGURI_ZILE:
        if zile_ramase <= prag and (ultim_prag_trimis is None or prag < ultim_prag_trimis):
            return prag
    return None


def _continut_email(nume_firma: str, zile_ramase: int) -> "tuple[str, str]":
    """Textul foloseste zile_ramase (numarul real, curent) - nu pragul care a
    declansat trimiterea - ca sa nu afirme niciodata un numar de zile gresit
    daca verificarea prinde pragul cu intarziere (ex: serverul a fost oprit
    cateva zile si repornit direct sub un prag mai vechi)."""
    if zile_ramase <= 0:
        subiect = "Perioada ta de proba e-TVA Reconciliere s-a incheiat"
        motiv = f"perioada gratuita de proba pentru firma {nume_firma} s-a incheiat"
    else:
        zi = "zi" if zile_ramase == 1 else "zile"
        subiect = f"Mai ai {zile_ramase} {zi} din perioada de proba e-TVA Reconciliere"
        motiv = (f"perioada gratuita de proba pentru firma {nume_firma} se "
                 f"incheie in {zile_ramase} {zi}")
    continut = (
        f"Buna ziua,\n\n"
        f"Va informam ca {motiv}.\n\n"
        f"Pentru a continua sa folositi platforma fara intrerupere, alegeti "
        f"un plan de facturare din contul dvs.:\n"
        f"https://ereconciliere.ro/panou/plan\n\n"
        f"Va multumim,\nEchipa e-TVA Reconciliere")
    return subiect, continut


def _email_admin_firma(conn, firm_id: int) -> "str | None":
    row = conn.execute(
        "SELECT u.email FROM user_firms uf "
        "JOIN users u ON u.id = uf.user_id "
        "WHERE uf.firm_id=? AND uf.role='admin' AND u.email IS NOT NULL "
        "LIMIT 1", (firm_id,)).fetchone()
    return row["email"] if row else None


def verifica_si_trimite(conn, trimite_email_fn) -> int:
    """Parcurge firmele active, inca in proba (fara ciclu_facturare ales),
    trimite reminderul pentru cel mai urgent prag netrimis inca al fiecareia
    si marcheaza pragul ca trimis. Returneaza numarul de emailuri trimise -
    folosit atat de fir-ul de fundal, cat si de butonul manual din master."""
    firme = conn.execute(
        "SELECT id, name, trial_expira_la, trial_reminder_ultim_prag "
        "FROM firms WHERE active=TRUE AND ciclu_facturare IS NULL "
        "AND trial_expira_la IS NOT NULL").fetchall()
    # commit imediat: randurile sunt deja materializate in Python (fetchall),
    # deci nu mai e nevoie sa tinem deschisa tranzactia implicita a acestui
    # SELECT. Fara asta, cand firme e goala (sau toate randurile sunt sarite
    # mai jos), conexiunea ramane "idle in transaction" pana la urmatorul
    # ciclu (6h) - descoperit direct: a blocat CREATE INDEX CONCURRENTLY pe
    # testare si productie dupa 5+ ore agatata.
    conn.commit()
    n_trimise = 0
    for firma in firme:
        zile_ramase = zile_ramase_trial(firma["trial_expira_la"])
        prag = _prag_de_trimis(zile_ramase, firma["trial_reminder_ultim_prag"])
        if prag is None:
            continue
        email = _email_admin_firma(conn, firma["id"])
        if not email:
            continue
        subiect, continut = _continut_email(firma["name"], zile_ramase)
        trimite_email_fn(email, subiect, continut)
        conn.execute(
            "UPDATE firms SET trial_reminder_ultim_prag=? WHERE id=?",
            (prag, firma["id"]))
        conn.commit()
        n_trimise += 1
    return n_trimise


def arhiveaza_firme_neplatitoare(conn) -> int:
    """Arhiveaza firmele a caror perioada de proba s-a incheiat complet
    (zile_ramase_trial == 0) fara sa fi ales un ciclu de facturare - adica
    firma nu a decis sa continue. Nu e o stergere: datele raman intacte,
    doar accesul la aplicatia de reconciliere se blocheaza (vezi
    current_identity in portal/app.py) pana firma alege un ciclu si master
    valideaza o plata, moment in care valideaza_plata sterge arhivata_la.
    Idempotenta prin arhivata_la IS NULL in filtru - o firma deja arhivata
    nu mai e reprocesata. Returneaza numarul de firme arhivate."""
    acum = datetime.now(timezone.utc).isoformat()
    firme = conn.execute(
        "SELECT id, trial_expira_la FROM firms WHERE active=TRUE "
        "AND ciclu_facturare IS NULL AND arhivata_la IS NULL "
        "AND trial_expira_la IS NOT NULL").fetchall()
    # Vezi comentariul din verifica_si_trimite mai sus - acelasi motiv.
    conn.commit()
    n_arhivate = 0
    for firma in firme:
        if zile_ramase_trial(firma["trial_expira_la"]) > 0:
            continue
        conn.execute(
            "UPDATE firms SET arhivata_la=? WHERE id=?", (acum, firma["id"]))
        conn.commit()
        n_arhivate += 1
    return n_arhivate


def start_scheduler(conn, lock, trimite_email_fn) -> None:
    """Fir de fundal care verifica periodic firmele in proba, la fel ca
    portal/backup.py - reutilizeaza conexiunea si lock-ul deja partajate de
    toate cererile Flask (nu deschide o a doua conexiune la portal.db).
    Verifica imediat la pornire (recupereaza remindere/arhivari ratate cat
    serverul a fost oprit), apoi la fiecare CHECK_INTERVAL_SECONDS."""
    def _loop():
        while True:
            try:
                with lock:
                    verifica_si_trimite(conn, trimite_email_fn)
                    arhiveaza_firme_neplatitoare(conn)
                time.sleep(CHECK_INTERVAL_SECONDS)
            except Exception:
                traceback.print_exc()
                time.sleep(RETRY_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
