# Restaurare Postgres (mediul testare) - procedura completa

Adaugat 2026-08-04. Documenteaza partea din acest feature care traieste
**in afara repo-ului** (scripturi + unitati systemd instalate direct pe
server) - repo-ul (`portal/backup_pg.py`, ruta `restaureaza_backup_postgres`
din `portal/app.py`, cardul din `master_backup.html`) e documentat in
`planning/harta-functii.md` §3m si §6.7b. Fara acest fisier, artefactele
de mai jos n-ar avea nicio urma versionata - nu exista alt loc unde sa fie
scrise.

Ruta ramane exclusiv pe mediul **testare** - vezi decizia din
`planning/harta-functii.md` §3m si precedentul deja existent la
restaurarea SQLite (`restaureaza_backup`), care blocheaza productia in
acelasi fel.

## 1. Artefacte de instalat, in ordinea asta (obligatoriu)

Daca se instaleaza codul aplicatiei inaintea scripturilor+unitatilor,
butonul exista in UI dar scrie un trigger pe care nimeni nu-l asculta -
esec silentios, fara mesaj de eroare.

1. `/usr/local/sbin/etva-restore-pg.sh` (nou, `700 root:root`)
2. `/etc/systemd/system/etva-testare-restore-pg.path` + `.service`
3. Adaosul de manifest in `/usr/local/sbin/etva-backup-pg.sh`
4. `systemctl daemon-reload && systemctl enable --now etva-testare-restore-pg.path`
5. `systemctl start etva-backup-pg.service` o data manual (ca manifestul
   sa existe imediat, nu doar la 03:30 noaptea)
6. Abia acum: deploy codul aplicatiei prin `dev` -> testare (butonul
   "Actualizeaza testare din GitHub")

## 2. `/usr/local/sbin/etva-restore-pg.sh` - logica, pas cu pas

Mod `700 root:root`, `set -uo pipefail` (deliberat NU `-e` - trebuie sa
supravietuiasca unui pas esuat si tot sa scrie un fisier de stare).

```
DATA_DIR="${1:-/opt/etva-testare/eTVA-Portal-Testare}"
TRIGGER="$DATA_DIR/restaurare-pg.trigger"
STATUS="$DATA_DIR/restaurare-pg.status"
UPLOAD="$DATA_DIR/restaurare-pg-incarcat.sql.gz"
BACKUP_DIR=/root/backup-pg
DB=etva_testare ; DB_OWNER=etva_app ; SERVICE=etva-testare.service
STAMP=$(date -u +%Y%m%d%H%M%S)
TMP_DB="${DB}_restore_$STAMP"
PREV_DB="${DB}_prev_$STAMP"
```

**Pas 0 - refuza sa ruleze pe serverul gresit.**
`[ -f /etc/systemd/system/etva-testare.service ] || exit 1`. Aparare in
plus fata de verificarile din ruta Flask - acest script NU se instaleaza
niciodata pe go2 (productie).

**Pas 1 - citeste trigger-ul, apoi il sterge.**
```
[ -f "$TRIGGER" ] || exit 0
{ read -r MOMENT; read -r SURSA; } < "$TRIGGER"
rm -f "$TRIGGER"
```
Trigger-ul e sters INAINTE de munca reala (la fel ca toate scripturile
surori) - ca o rulare esuata sa nu blocheze unitatea `.path` intr-o bucla.

**Pas 2 - starea se scrie o singura data, printr-un trap pe EXIT.**
```
STARE=eroare
MESAJ="Restaurarea nu a apucat sa ruleze (eroare neasteptata) - baza live nu a fost atinsa."
SERVICE_OPRIT=0

finalizeaza() {
  if [ "$SERVICE_OPRIT" = 1 ]; then
    if systemctl start "$SERVICE"; then
      MESAJ="$MESAJ Serviciul a fost repornit."
    else
      STARE=partial
      MESAJ="$MESAJ ATENTIE: serviciul $SERVICE NU a putut fi repornit - porneste-l manual."
    fi
  fi
  rm -f "$UPLOAD"
  printf '%s|%s|%s\n' "$STARE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MESAJ" > "$STATUS"
  chmod 644 "$STATUS"
}
trap finalizeaza EXIT
trap 'exit 143' TERM INT
```
Linia `trap 'exit 143' TERM INT` e esentiala: bash NU ruleaza trap-ul de
EXIT la un `SIGTERM` netratat. Fara ea, un `systemctl stop` sau un
timeout ar lasa aplicatia oprita definitiv.

**Pas 3 - valideaza sursa** (`local:<data>` cu glob strict, sau `upload`)
- niciodata prin regex pe partea Python, ci glob shell aici, ca aparare
reala impotriva path traversal (`local:../../root/.ssh` nu poate potrivi
`local:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]`).

**Pas 4 - verificari ieftine, inainte sa opreasca orice:**
`gzip -t` (arhiva valida) + `zcat | head -c 8192 | grep "PostgreSQL database dump"`
(chiar e un dump Postgres, nu doar un gzip oarecare).

**Pas 5 - opreste aplicatia** (`systemctl stop $SERVICE`, `SERVICE_OPRIT=1`
DOAR daca reuseste). Obligatoriu: pool-ul de conexiuni al aplicatiei (min 2
per worker x 3 workeri + scheduler) tine conexiuni deschise, una sta uneori
`idle in transaction` - acelasi motiv documentat pentru orice DDL live.

**Pas 6 - creeaza baza temporara, cu proprietarul corect:**
```
CREATE DATABASE $TMP_DB OWNER $DB_OWNER TEMPLATE template0
  ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
```
**`OWNER $DB_OWNER` nu e optional.** Schema `public` are ACL-ul implicit
PG15+ (`pg_database_owner` primeste USAGE/CREATE) - fara asta, dupa swap
`etva_app` n-ar mai putea scrie in propria baza.

**Pas 7 - incarca dump-ul, ca superuser** (`zcat | runuser -u postgres --
psql -d $TMP_DB -v ON_ERROR_STOP=1 -q`). Ca `postgres`, nu ca `etva_app`
- dump-ul contine `SET row_security = off` si politici RLS, pe care rolul
aplicatiei nu are voie sa le ocoleasca. Esec -> `DROP DATABASE ... WITH
(FORCE)`, `eroare`, "baza live NU a fost atinsa".

**Pas 8 - 4 verificari de sanitate pe `$TMP_DB`, orice esec -> drop + eroare:**

| Verificare | Prag | De ce |
|---|---|---|
| tabele in `public` | >= 20 | floor, nu exact (26 in EXPECTED_SCHEMA) |
| `users`/`firms`/`firm_keys` prezente | = 3 | fara ele, portalul nu porneste |
| exista cont master | >= 1 | fara asta, Andrei ramane blocat afara, inclusiv din acest panou |
| politici `izolare_firma` | >= 7 | fara RLS, izolarea intre firme se rupe silentios |

**Pas 9 - swap-ul, singurul moment distructiv:**
```
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('$DB', '$TMP_DB') AND pid <> pg_backend_pid();
ALTER DATABASE $DB      RENAME TO $PREV_DB;
ALTER DATABASE $TMP_DB  RENAME TO $DB;
```
Ambele redenumiri intr-o singura tranzactie (`psql -1`) - **de confirmat
empiric la prima instalare** (pasul 3 din verificare, mai jos) ca
`ALTER DATABASE ... RENAME` chiar functioneaza intr-un bloc de tranzactie
pe PG16. Daca nu, se renunta la `-1` si mesajul de eroare pentru swap
partial devine load-bearing: *"prima redenumire a reusit dar a doua a
esuat - baza `$DB` nu mai exista. Ruleaza manual: `ALTER DATABASE $TMP_DB
RENAME TO $DB;`"*.

**Pas 10 - dovada ca aplicatia chiar functioneaza, inainte de succes:**
```
set -a; . /etc/etva-testare/db.env; set +a
psql "$DATABASE_URL" -tAc "SELECT count(*) FROM users"
```
Conectare ca `etva_app`, prin TCP, exact cum face aplicatia - singura
verificare care prinde efectiv o greseala de OWNER/ACL de la pasul 6.
**Niciodata nu se scrie `$DATABASE_URL` (contine parola) in fisierul de
stare.**

**Pas 11 - succes + avertisment de curatenie**, numara bazele
`${DB}_prev_*` ramase si avertizeaza daca sunt >= 3 (nu se sterg automat
niciodata - decizie explicita, Andrei le sterge manual dupa ce verifica).

## 3. Unitati systemd

`/etc/systemd/system/etva-testare-restore-pg.path`
```ini
[Unit]
Description=Watch for e-TVA testare Postgres restore trigger written by the app

[Path]
PathExists=/opt/etva-testare/eTVA-Portal-Testare/restaurare-pg.trigger
Unit=etva-testare-restore-pg.service

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/etva-testare-restore-pg.service`
```ini
[Unit]
Description=Restaurare Postgres etva_testare, declansata din panoul master
After=postgresql.service
Wants=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/etva-restore-pg.sh /opt/etva-testare/eTVA-Portal-Testare
TimeoutStartSec=1800
Nice=10
IOSchedulingClass=idle
```

`TimeoutStartSec=1800` e esential - implicitul (90s) ar omori un restore
la mijloc. Combinat cu `trap 'exit 143' TERM INT` din script, un timeout
degradeaza la "restore anulat, aplicatia repornita, stare eroare" in loc
de "aplicatia ramane oprita".

## 4. Adaosul la `/usr/local/sbin/etva-backup-pg.sh`

Dupa blocul de retentie locala (scriptul ruleaza sub `set -euo pipefail`,
deci blocul e infasurat exact ca cele doua blocuri de retentie deja
existente, ca un esec al manifestului sa nu strice un backup altfel
reusit):

```bash
{
  if [ -d "$APP_DATA_DIR" ]; then
    MANIFEST="$APP_DATA_DIR/backup-pg.manifest"
    TMP_MANIFEST="$MANIFEST.nou"
    : > "$TMP_MANIFEST"
    for d in "$DIR"/*/; do
      [ -d "$d" ] || continue
      nume=$(basename "$d")
      [[ "$nume" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
      f="$d/etva_testare.sql.gz"
      [ -f "$f" ] || continue
      printf '%s|%s\n' "$nume" "$(stat -c %s "$f")" >> "$TMP_MANIFEST"
    done
    sort -r -o "$TMP_MANIFEST" "$TMP_MANIFEST"
    chmod 644 "$TMP_MANIFEST"
    mv -f "$TMP_MANIFEST" "$MANIFEST"
  fi
} || echo "manifest: eroare neasteptata, panoul master va afisa lista veche" >&2
```
`APP_DATA_DIR=/opt/etva-testare/eTVA-Portal-Testare` adaugat la
constantele de la inceputul scriptului. Regex-ul pe director exclude
automat fisierele vechi in format plat si orice `etva_productie*`.
Ruleaza atat din timer-ul de noapte, cat si din butonul la cerere
("Trimite backup in OneDrive acum", `etva-backup-trigger.sh`) - un singur
loc de editat, ambele cai beneficiaza.

## 5. Verificare la instalare (pe testare, productia neatinsa)

1. Instaleaza artefactele in ordinea de la §1.
2. `systemctl start etva-backup-pg.service`, confirma
   `backup-pg.manifest` exista (`root:root 0644`, o data valida azi).
3. **Ruleaza scriptul manual, fara UI**, restaurand backup-ul de azi peste
   el insusi (neutru ca date, dar exerseaza tot mecanismul):
   ```
   printf '%s\nlocal:%s\n' "$(date -u +%FT%TZ)" "$(date +%F)" \
     > /opt/etva-testare/eTVA-Portal-Testare/restaurare-pg.trigger
   journalctl -u etva-testare-restore-pg.service -f
   ```
   Confirma `restaurare-pg.status` = `ok`, `etva-testare.service` activ,
   site-ul raspunde, `psql -l` arata `etva_testare_prev_<stamp>`. **Acesta
   e pasul care confirma daca swap-ul in tranzactie (§2, pasul 9)
   functioneaza cu adevarat.**
4. Deploy cod (dev -> testare), confirma cardul nou + lista populata.
5. **Round-trip real**: noteaza `SELECT count(*) FROM firms`, inregistreaza
   o firma de test, restaureaza backup-ul de dinainte, confirma ca firma
   a disparut si numarul a revenit.
6. Cai de esec: fisier ne-gzip (refuzat de aplicatie, serviciu neatins),
   gzip valid fara dump real (refuzat de script, serviciu repornit), kill
   la mijlocul rularii (`systemctl kill etva-testare-restore-pg.service`
   - confirma ca trap-ul tot reporneste aplicatia).
7. Confirma productia neatinsa inainte/dupa:
   `ssh etva-productie-vps 'systemctl is-active etva-productie.service'`.

## 6. Recuperare manuala (daca ceva iese prost)

Daca swap-ul a esuat la jumatate (baza `$DB` nu mai exista dupa prima
redenumire, dar a doua nu s-a mai intamplat):
```
runuser -u postgres -- psql -d postgres -c \
  "ALTER DATABASE etva_testare_restore_<stamp> RENAME TO etva_testare;"
# sau, ca sa revii la starea dinainte de restore:
runuser -u postgres -- psql -d postgres -c \
  "ALTER DATABASE etva_testare_prev_<stamp> RENAME TO etva_testare;"
systemctl start etva-testare.service
```

Curatenie periodica manuala (nu automatizata - decizie explicita):
`DROP DATABASE etva_testare_prev_<stamp>;` dupa ce ai verificat ca
restaurarea a fost cea dorita.

## 7. Intrebari deschise / limitari cunoscute (nedecise inca)

- Backup-urile mai vechi de 14 zile (doar pe OneDrive, criptate GPG) nu
  apar in lista automata - de restaurat prin upload manual, dupa
  descarcare+decriptare separata. Automatizarea completa ar cere ca
  scriptul sa aiba acces la parola GPG (`/root/.etva-backup-passphrase`)
  - neconstruit deliberat, ca aplicatia sa nu ajunga niciodata sa poata
  cere o decriptare.
- Fara limita de marime pentru upload (`MAX_CONTENT_LENGTH` nu exista
  nicaieri in aplicatie azi) - de revizuit daca dump-ul creste mult.
- Restore-ul opreste brutal serviciul - orice cerere in curs (import,
  reconciliere lunga) e intrerupta. Acceptabil pe testare (un singur
  operator), de mentionat explicit daca vreodata se ia in calcul si
  productia (ceea ce ramane, deliberat, in afara scopului acestui feature).
