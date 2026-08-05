"""Restaurarea bazei Postgres a mediului testare, ceruta din /master/backup.

Aplicatia ruleaza sub NoNewPrivileges cu CapabilityBoundingSet gol - nu
poate rula pg_dump/psql ca postgres, nu poate opri propriul serviciu si
nu poate citi /root/backup-pg (0700 root, contine dumpuri necriptate).
Foloseste deci acelasi mecanism ca portal/pipeline.py: scrie un fisier-
semnal in data_dir, o unitate systemd .path detinuta de root ruleaza
/usr/local/sbin/etva-restore-pg.sh si lasa rezultatul intr-un fisier de
stare, citit inapoi la urmatoarea incarcare a paginii (read_status din
pipeline.py, acelasi format 'stare|moment|mesaj' - nu duplicat aici).

Spre deosebire de restul semnalelor (a caror simpla existenta e tot
semnalul), acesta poarta si o sarcina utila: de unde se restaureaza.
Format, doua linii:

    <moment ISO 8601 UTC>
    local:<YYYY-MM-DD>   sau   upload

Prima linie ramane identica cu a celorlalte trigger-e, ca 'head -1' sa
functioneze uniform peste toate. Sursa e validata si aici, si in
scriptul root - aplicatia e componenta mai putin privilegiata, si o
compromitere a ei nu trebuie sa devina o citire arbitrara de fisiere ca
root (de-asta glob-ul din script, nu doar regex-ul de-aici, e ce conteaza
cu adevarat pentru siguranta).

Nu exista niciun echivalent al acestui mecanism pentru mediul productie -
restaurarea Postgres e disponibila STRICT pe testare (vezi verificarile
din portal/app.py si refuzul explicit din scriptul root daca ruleaza pe
alt server).
"""
import pathlib
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

RESTORE_TRIGGER_NAME = "restaurare-pg.trigger"
RESTORE_STATUS_NAME = "restaurare-pg.status"
RESTORE_UPLOAD_NAME = "restaurare-pg-incarcat.sql.gz"
MANIFEST_NAME = "backup-pg.manifest"

SURSA_UPLOAD = "upload"
_SURSA_LOCALA_RE = re.compile(r"^local:\d{4}-\d{2}-\d{2}$")
_GZIP_MAGIC = b"\x1f\x8b"


class RestoreError(Exception):
    pass


def nume_baza(dsn: "str | None") -> str:
    """Numele bazei din DATABASE_URL (ex. 'etva_testare'), sau valoarea
    implicita daca DSN-ul lipseste/e nevalid. E si fraza de confirmare pe
    care masterul trebuie sa o scrie inainte de restaurare - derivata din
    DSN, nu hardcodata, ca sa ramana o singura sursa de adevar si sa fie
    usor de suprascris in teste."""
    if dsn:
        parsat = urlparse(dsn)
        # Fara schema (ex. un string oarecare, nu un DSN), urlparse pune
        # tot continutul in .path - de-asta scheme e verificat explicit,
        # nu doar path-ul nevid.
        if parsat.scheme:
            nume = parsat.path.lstrip("/")
            if nume:
                return nume
    return "etva_testare"


def list_local_backups(data_dir: str) -> list[dict]:
    """Backup-urile locale disponibile, cele mai noi primele. Citeste
    manifestul scris de etva-backup-pg.sh (root:root 0644) - aplicatia nu
    are acces la /root/backup-pg unde stau dumpurile reale, necriptate.
    Linii stricate sunt ignorate silentios (aceeasi filozofie indulgenta
    ca pipeline.read_status); lista goala daca manifestul lipseste inca
    (primul backup nu a rulat)."""
    out = []
    try:
        with open(pathlib.Path(data_dir, MANIFEST_NAME), encoding="utf-8") as f:
            linii = f.read().splitlines()
    except OSError:
        return out
    for linie in linii:
        try:
            data, octeti = linie.split("|", 1)
            out.append({"data": data, "marime_mb": round(int(octeti) / 1_048_576, 2)})
        except ValueError:
            continue
    out.sort(key=lambda b: b["data"], reverse=True)
    return out


def manifest_updated_at(data_dir: str) -> "datetime | None":
    """Cand a fost scris ultima oara manifestul - ca sa se vada in UI daca
    lista e proaspata sau invechita. None daca nu exista inca."""
    try:
        stamp = pathlib.Path(data_dir, MANIFEST_NAME).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


def save_uploaded_dump(data_dir: str, fisier) -> pathlib.Path:
    """Salveaza un dump .sql.gz incarcat manual, sub un nume FIX -
    niciodata numele trimis de client (fisier.filename) - asta e toata
    apararea impotriva path traversal pe partea aplicatiei; validarea
    reala de continut (chiar e un dump Postgres) se face in scriptul
    root, care nu are de unde sti daca poate avea incredere in aplicatie.
    Verifica doar magic number-ul gzip aici - ieftin, si suficient ca sa
    resping imediat un fisier evident gresit fara sa mai declansam
    scriptul root deloc."""
    inceput = fisier.read(2)
    fisier.seek(0)
    if inceput != _GZIP_MAGIC:
        raise RestoreError("Fisierul nu e o arhiva gzip valida (.sql.gz).")
    dest = pathlib.Path(data_dir, RESTORE_UPLOAD_NAME)
    fisier.save(dest)
    return dest


def sterge_incarcare(data_dir: str) -> None:
    """Sterge un upload ramas pe disc - apelat pe orice cale de refuz (ca
    un dump necriptat sa nu zaca in data_dir, de unde create_backup l-ar
    matura in urmatorul zip SQLite) si de scriptul root la final,
    indiferent de rezultat."""
    pathlib.Path(data_dir, RESTORE_UPLOAD_NAME).unlink(missing_ok=True)


def request_restore(data_dir: str, sursa: str) -> None:
    """Cere restaurarea bazei Postgres din sursa data - 'local:<data>' sau
    'upload' - vezi docstring-ul modulului pentru mecanism. Ridica
    RestoreError daca sursa nu are un format recunoscut (aplicatia
    refuza inainte sa scrie orice, ruta apelanta nu trebuie sa lase sa
    treaca un format neasteptat pana aici)."""
    if sursa != SURSA_UPLOAD and not _SURSA_LOCALA_RE.match(sursa):
        raise RestoreError(f"Sursa de restaurare invalida: {sursa!r}")
    continut = datetime.now(timezone.utc).isoformat() + "\n" + sursa + "\n"
    pathlib.Path(data_dir, RESTORE_TRIGGER_NAME).write_text(continut, encoding="utf-8")
