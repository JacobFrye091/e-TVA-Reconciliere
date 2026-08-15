"""Alerte automate cand o evaluare de risc fiscal (etva/risc_fiscal.py +
etva/risc_fiscal_store.py) e clasificata 'ridicat' - fie din scorul propriu
(indicatorii 1-5 din Anexa 2 ANAF), fie din override-ul Sectiunii B
(declarat inactiv, insolventa etc - vezi etva/risc_fiscal.py::FLAGURI_SECTIUNE_B).

Acelasi tipar ca portal/trial_reminders.py: fir de fundal pornit din
create_app, verifica_si_alerteaza idempotenta printr-un tabel per-firma
(risc_fiscal_alerte), reutilizabila manual. Diferenta structurala fata de
trial_reminders: verificarea are DOUA nivele - firme active cu
risc_fiscal_nivel setat (din portal.db), apoi per-firma toate perioadele
lor cu risc 'ridicat' (din baza per-firma, vezi firm_conn_fn) - de aceea
start_scheduler primeste si closure-ul firm_conn din create_app, nu doar
conexiunea portalului.
"""
import threading
import time
import traceback
from datetime import datetime, timezone

from etva import risc_fiscal_store
from portal.trial_reminders import _email_admin_firma

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
RETRY_INTERVAL_SECONDS = 30 * 60


def _semnatura(perioada: dict) -> str:
    """Semnatura starii de risc curente a unei perioade - o resubmisie cu
    exact aceeasi clasificare/scor/flaguri active NU retrimite alerta; o
    schimbare reala (scor diferit, flag nou de Sectiunea B aparut) da o
    semnatura noua, deci alerteaza din nou (vezi risc_fiscal_alerte)."""
    flaguri_active = sorted(
        k for k, v in (perioada.get("flaguri_sectiune_b") or {}).items() if v)
    return f"{perioada['clasificare']}|{perioada['scor_afisat']}|{','.join(flaguri_active)}"


def _continut_email(client_nume: str, perioada: dict) -> "tuple[str, str]":
    # Prefixul de stadiu ramane in SUBIECT cat timp modulul e in dezvoltare:
    # alerta pleaca automat, fara ca cineva sa o citeasca inainte, deci
    # destinatarul trebuie sa vada din inbox ca e un semnal de test.
    subiect = (f"[IN DEZVOLTARE] Risc fiscal ridicat detectat - "
               f"{client_nume} ({perioada['perioada']})")
    continut = (
        f"Buna ziua,\n\n"
        f"ATENTIE: modulul de Risc Fiscal este inca IN DEZVOLTARE. Aceasta "
        f"alerta e generata pentru testare - nu o folositi ca baza in relatia "
        f"cu clientul sau cu autoritatile.\n\n"
        f"Evaluarea de risc fiscal pentru {client_nume}, perioada "
        f"{perioada['perioada']}, a fost clasificata RISC RIDICAT "
        f"(scor {perioada['scor_afisat']}/100).\n\n"
        f"Aceasta clasificare foloseste o metodologie proprie, inspirata de "
        f"Anexa 2 ANAF (Fisa indicatorilor de risc fiscal) - vezi raportul "
        f"PDF din aplicatie, tab-ul Risc Fiscal, pentru detalii complete si "
        f"disclaimer-ul de nonechivalenta cu clasificarea oficiala ANAF.\n\n"
        f"Va multumim,\nEchipa e-TVA Reconciliere")
    return subiect, continut


def _alerteaza_firma(fc, firma_nume: str, email: str, trimite_email_fn) -> int:
    n_trimise = 0
    for perioada in risc_fiscal_store.perioade_cu_risc_ridicat(fc):
        semnatura = _semnatura(perioada)
        deja_alertat = fc.execute(
            "SELECT 1 FROM risc_fiscal_alerte WHERE perioada_id=? AND semnatura=?",
            (perioada["id"], semnatura)).fetchone()
        if deja_alertat:
            continue
        client_nume = perioada.get("client_name") or perioada.get("client_cui") or firma_nume
        subiect, continut = _continut_email(client_nume, perioada)
        trimite_email_fn(email, subiect, continut)
        fc.execute(
            "INSERT INTO risc_fiscal_alerte(perioada_id, semnatura, trimis_la) "
            "VALUES(?,?,?)",
            (perioada["id"], semnatura, datetime.now(timezone.utc).isoformat()))
        fc.commit()
        n_trimise += 1
    return n_trimise


def verifica_si_alerteaza(portal_conn, firm_conn_fn, trimite_email_fn) -> int:
    """Parcurge firmele active cu modulul Risc Fiscal activat, si pentru
    fiecare, toate perioadele ei (orice client) clasificate 'ridicat' care
    nu au fost deja alertate cu semnatura curenta. Returneaza numarul de
    emailuri trimise."""
    firme = portal_conn.execute(
        "SELECT id, name FROM firms WHERE active=TRUE "
        "AND risc_fiscal_nivel IS NOT NULL AND arhivata_la IS NULL").fetchall()
    # commit imediat: vezi comentariul identic din
    # trial_reminders.verifica_si_trimite (idle-in-transaction blocheaza
    # DDL-uri viitoare pana la urmatorul ciclu al schedulerului).
    portal_conn.commit()
    n_trimise = 0
    for firma in firme:
        email = _email_admin_firma(portal_conn, firma["id"])
        if not email:
            continue
        fc = firm_conn_fn(firma["id"])
        n_trimise += _alerteaza_firma(fc, firma["name"], email, trimite_email_fn)
    return n_trimise


def start_scheduler(portal_conn, firm_conn_fn, lock, trimite_email_fn) -> None:
    """Fir de fundal care verifica periodic evaluarile de risc fiscal, la
    fel ca portal/trial_reminders.py. Verifica imediat la pornire, apoi la
    fiecare CHECK_INTERVAL_SECONDS."""
    def _loop():
        while True:
            try:
                with lock:
                    verifica_si_alerteaza(portal_conn, firm_conn_fn, trimite_email_fn)
                time.sleep(CHECK_INTERVAL_SECONDS)
            except Exception:
                traceback.print_exc()
                time.sleep(RETRY_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
