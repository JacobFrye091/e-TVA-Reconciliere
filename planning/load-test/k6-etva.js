// Test de incarcare e-TVA (go2, productie) - k6
//
// Ruleaza DINTOTDEAUNA de pe o masina externa, NICIODATA de pe go2 -
// altfel k6 concureaza pentru CPU cu gunicorn/Postgres pe care le testezi
// si rezultatele nu mai inseamna nimic (vezi planning/brief-optimizari-performanta.md #11
// si conversatia care a produs acest script).
//
// Foloseste EXCLUSIV contul sintetic dedicat testului (nu una din firmele
// reale) - vezi planning/load-test/README.md pentru cum se creeaza.
//
// Rulare:
//   BASE_URL=https://ereconciliere.ro \
//   TEST_CUI=RO00000000 \
//   TEST_PASSWORD='...' \
//   TEST_RECONCILIERE_ID=1 \
//   k6 run planning/load-test/k6-etva.js
//
// Praguri suplimentare la rulare (optional, suprascriu ramp-ul din fisier):
//   k6 run --stage 30s:5,1m:0 k6-etva.js   # smoke test rapid inainte de ramp-ul real

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "https://ereconciliere.ro";
const TEST_CUI = __ENV.TEST_CUI;
const TEST_PASSWORD = __ENV.TEST_PASSWORD;
const TEST_RECONCILIERE_ID = __ENV.TEST_RECONCILIERE_ID || "1";

if (!TEST_CUI || !TEST_PASSWORD) {
  throw new Error(
    "TEST_CUI si TEST_PASSWORD sunt obligatorii - vezi README.md. " +
    "Nu rula acest script fara contul sintetic dedicat testului."
  );
}

const loginFailRate = new Rate("etva_login_fail_rate");
const dashboardTrend = new Trend("etva_panou_duration");
const exportTrend = new Trend("etva_export_duration");

export const options = {
  // Rampa graduala (Faza B/D din plan) - NU sari direct la concurenta mare.
  // Ajusteaza treptele daca fereastra off-hours aleasa e mai scurta/lunga.
  stages: [
    { duration: "1m", target: 5 },
    { duration: "2m", target: 5 },
    { duration: "1m", target: 10 },
    { duration: "2m", target: 10 },
    { duration: "1m", target: 20 },
    { duration: "2m", target: 20 },
    { duration: "1m", target: 40 },
    { duration: "2m", target: 40 },
    { duration: "1m", target: 80 },
    { duration: "2m", target: 80 },
    { duration: "1m", target: 0 }, // ramp-down
  ],
  thresholds: {
    // Praguri de oprire - k6 iese cu cod nenul daca sunt incalcate,
    // dar TU esti cel care opreste manual (Ctrl+C) la primul semn real
    // de impact asupra celor 2 firme reale, fara sa astepti pragul.
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1000"],
    etva_login_fail_rate: ["rate<0.01"],
  },
};

function extractCsrfFromHtml(html) {
  const m = html.match(/name="csrf_token"[^>]*value="([^"]+)"/);
  return m ? m[1] : null;
}

export default function () {
  const jar = http.cookieJar();

  group("login", function () {
    const getRes = http.get(`${BASE_URL}/autentificare`, { tags: { name: "GET /autentificare" } });
    const csrf = extractCsrfFromHtml(getRes.body);
    check(getRes, { "pagina login raspunde 200": (r) => r.status === 200 });

    const postRes = http.post(
      `${BASE_URL}/autentificare`,
      { cui: TEST_CUI, password: TEST_PASSWORD, csrf_token: csrf },
      { tags: { name: "POST /autentificare" }, redirects: 0 }
    );
    const loginOk = postRes.status === 302 && !/eroare=/.test(postRes.headers.Location || "");
    loginFailRate.add(!loginOk);
    check(postRes, { "login reuseste (redirect 302)": () => loginOk });
    if (!loginOk) {
      return; // nu continua iteratia asta fara sesiune valida
    }
  });

  sleep(1);

  group("navigare (read-heavy, fluxul cel mai folosit)", function () {
    // /app serveste SPA-ul (produsul propriu-zis) - necesita firma cu
    // email_verificat=True (EMAIL_VERIFICARE_OBLIGATORIE=1 pe productie,
    // vezi etva-productie.service), altfel redirect la
    // /asteapta-verificare-email. Contul sintetic TREBUIE sa aiba emailul
    // verificat inainte de test - vezi README.md.
    const dash = http.get(`${BASE_URL}/app`, { tags: { name: "GET /app" } });
    dashboardTrend.add(dash.timings.duration);
    check(dash, { "/app 200": (r) => r.status === 200 });

    sleep(0.5);

    const clients = http.get(`${BASE_URL}/api/clients`, { tags: { name: "GET /api/clients" } });
    check(clients, { "/api/clients 200": (r) => r.status === 200 });

    sleep(0.5);

    // Export-ul unei reconcilieri: fluxul semnalat explicit in
    // planning/brief-optimizari-performanta.md #1 ca cel mai folosit si
    // recent indexat pentru RLS - cel mai relevant de stresat.
    const exp = http.get(
      `${BASE_URL}/api/reconciliations/${TEST_RECONCILIERE_ID}/export`,
      { tags: { name: "GET /api/reconciliations/:id/export" } }
    );
    exportTrend.add(exp.timings.duration);
    check(exp, { "export 200": (r) => r.status === 200 });

    sleep(0.5);

    const audit = http.get(`${BASE_URL}/api/audit`, { tags: { name: "GET /api/audit" } });
    check(audit, { "/api/audit 200": (r) => r.status === 200 });
  });

  sleep(1);

  http.get(`${BASE_URL}/iesire`, { tags: { name: "GET /iesire" } });

  sleep(1);
}
