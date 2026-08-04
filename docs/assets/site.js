(function () {
  // Meniu mobil — inlocuieste vechiul comportament in care linkurile
  // disparea pur si simplu sub breakpoint, fara nicio alternativa.
  var toggle = document.querySelector('.menu-toggle');
  var topbar = document.querySelector('.topbar');
  if (toggle && topbar) {
    toggle.addEventListener('click', function () {
      var deschis = topbar.classList.toggle('nav-deschis');
      toggle.setAttribute('aria-expanded', deschis ? 'true' : 'false');
    });
    topbar.querySelectorAll('.nav a').forEach(function (a) {
      a.addEventListener('click', function () {
        topbar.classList.remove('nav-deschis');
        toggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // Reveal la scroll pentru elementele marcate .reveal
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('vazut'); obs.unobserve(e.target); }
      });
    }, { threshold: .12 });
    document.querySelectorAll('.reveal').forEach(function (el) { obs.observe(el); });
  } else {
    document.querySelectorAll('.reveal').forEach(function (el) { el.classList.add('vazut'); });
  }

  // Pe GitHub Pages nu exista backend: Autentificare/Creeaza cont duc spre o
  // demonstratie statica cu date exemplu in loc de 404. Cand pagina e servita
  // chiar de platforma (Flask), linkurile raman cele reale.
  if (location.hostname.endsWith('.github.io')) {
    document.querySelectorAll('[data-auth]').forEach(function (a) { a.href = 'demo.html'; });
  }
})();
