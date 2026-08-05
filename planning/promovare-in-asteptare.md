# Promovare în așteptare: 2 commit-uri blocate pe `dev`, local pe go2

Scris 2026-08-05, în timpul lucrului la tunarea Postgres + testul de
încărcare (`planning/brief-optimizari-performanta.md` #10/#11). Documentează
un gol de proces descoperit live, nu doar presupus: **go2 (productie) nu
poate publica nimic pe GitHub**, indiferent de branch.

## Ce există, unde

Două commit-uri pe branch-ul local `dev`, într-un worktree izolat creat pe
go2 (`git worktree`, la o cale temporară din scratchpad — nu în checkout-ul
live de pe `main` care rulează `etva-productie.service`; acela n-a fost
atins):

| Commit | Conținut |
|---|---|
| `da36333` `feat(load-test)` | `planning/load-test/k6-etva.js` + `README.md` — scriptul de test de încărcare (Faza A din planul de tunare Postgres) |
| `7ffb441` `fix(app)` | `portal/app.py` — adaugă `MAX_CONTENT_LENGTH = 50MB`, lipsea complet (limitare cunoscută din `planning/restaurare-postgres.md` §7). 481 teste SQLite trec neschimbate. |

`origin/dev` e la `dd875ab` — cele 2 commit-uri de mai sus sunt "ahead 1"/"ahead 2"
față de el, doar local.

Patch exportat pentru portabilitate (ambele commit-uri, aplicabil cu
`git am`): `/tmp/.../scratchpad/0001-load-test-k6.patch` (cale temporară,
pe go2 — nu supraviețuiește dincolo de sesiunea curentă; de copiat pe altă
mașină înainte să dispară, dacă rămâne varianta aleasă).

## De ce nu pot ajunge singure pe GitHub

Verificat direct în `portal/pipeline.py` + configurația reală de pe go2,
nu presupus din documentație:

1. **Promovarea `dev → testare` e un pipeline local**, `local_pipeline_available()`
   cere trei foldere alăturate (`DEV/`, `TESTARE/`, `PROD/`, worktree-uri
   ale aceluiași repo) — există doar pe mașina locală de dezvoltare, nu pe
   niciun VPS.
2. **Butonul "Actualizeaza testare din GitHub" și promovarea `testare → productie`**
   (push pe `main` + deploy la distanță prin SSH către go2) rulează prin
   unități systemd `.path` deținute de root, instalate **doar pe VPS-ul
   testare (go1)** — acolo trăiește cheia cu drept de scriere pe GitHub și
   cheia SSH care ajunge la go2.
3. **Pe go2, confirmat cu `systemctl list-units`**: există doar
   `etva-productie-restart.path` și `etva-productie-backup-onedrive.path`
   — niciun echivalent `pull-testare`/`promote-productie`. Producția e un
   capăt pasiv, primește deploy de la testare, nu se auto-actualizează
   niciodată singură.
4. **Cheia SSH către GitHub de pe go2** (`/root/.ssh/etva_productie_deploy_ed25519`)
   e read-only — confirmat cu `ssh -T git@github.com`/eroare la `git push`,
   nu presupus. Foarte probabil deliberat (producția n-ar trebui să poată
   scrie pe repo, doar să fie ținta unui deploy).

Concluzie: din go2, cu accesul disponibil în această sesiune, **niciuna din
verigile `github dev → github testare → VPS testare+refresh → github main`
nu poate fi executată** — lipsesc atât mașina locală de dezvoltare, cât și
accesul la go1 (testare). Singura verigă tehnic posibilă de pe go2 e un
`git pull origin main` + restart manual, dar abia după ce `main` chiar are
commit-urile — ceea ce ține de pașii de dinainte.

## Ce rămâne de făcut

1. **Aplică patch-ul** (`0001-load-test-k6.patch`) pe o mașină cu drept de
   scriere pe repo — mașina locală de dezvoltare (unde există `DEV/`) sau
   direct pe go1/testare dacă are checkout separat pe `dev`:
   ```
   git am 0001-load-test-k6.patch
   git push origin dev
   ```
2. **Promovează `dev → testare`** prin pipeline-ul local (`portal/pipeline.py::promote("dev","testare")`,
   sau echivalentul lui în UI/CLI pe mașina locală) — fast-forward +
   push pe `origin/testare`.
3. **"Actualizeaza testare din GitHub"** din panoul master de pe go1 —
   pull + restart pe testare.
4. **Verificare pe testare**: `k6-etva.js` rulează cel puțin un smoke test
   (`--stage 20s:2,20s:0`) împotriva testare, `MAX_CONTENT_LENGTH` confirmat
   activ (`curl` cu un fișier >50MB → `413`).
5. **Promovează `testare → productie`** din același panou (push pe `main`
   + deploy SSH la go2) — singurul pas care chiar atinge codul rulat de
   `etva-productie.service`.
6. **Verificare finală pe go2**: `git -C /opt/etva-productie/app log -1 --format=%H main`
   arată `7ffb441` (sau commit-ul rezultat după promovare), `systemctl status etva-productie`
   activ, `curl` de test pentru `413` pe upload supradimensionat.

## Ce NU s-a schimbat

Checkout-ul live de pe go2 (`/opt/etva-productie/app`, branch `main`) —
neatins pe tot parcursul; `etva-productie.service` a rulat continuu, fără
restart, cu codul de dinainte de aceste 2 commit-uri.
