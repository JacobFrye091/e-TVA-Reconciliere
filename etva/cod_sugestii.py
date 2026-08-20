"""Stratul 2 de clasificare: opinia platformei despre codurile TVA pe care
`etva/d300.py::suggest_line` le lasa neclasificate.

Ruleaza EXCLUSIV peste codurile ramase in lista `unmapped` dupa clasificarea
automata, iar iesirea lui nu intra niciodata singura intr-o reconciliere:
`portal/app.py` o serveste ca preselectie in picker-ul ghidat din
`web/index.html`, iar persistenta se intampla tot prin
`etva/cod_mappings.py::save_mapping`, dupa ce utilizatorul apasa "Confirma".

De ce un modul separat si nu reguli in plus in suggest_line(): acela e
deliberat conservator (intoarce None la orice ambiguitate legala) tocmai
pentru ca rezultatul lui se aplica automat, fara ca cineva sa il vada.
Regulile de aici sunt mai indraznete - propun o linie si acolo unde exista
doua citiri legale plauzibile - si asta e acceptabil doar cat timp raman de
partea asta a granitei: sugestie motivata, vazuta de om, confirmata manual.

Fiecare regula isi poarta propriul `motiv` in romana, afisat langa sugestie:
contabilul trebuie sa poata contesta rationamentul, nu doar rezultatul. Din
acelasi motiv modulul REFUZA explicit sa sugereze - cu motivul scris - acolo
unde nu are temei, in loc sa dea un raspuns slab pe care cineva l-ar putea
confirma din inertie.
"""
import re
from dataclasses import dataclass
from typing import Callable

from etva.d300 import D300_LINES, valid_lines_for_direction
# Aceeasi normalizare de diacritice ca suggest_line() - o copie separata ar
# diverge tacut de regulile stratului 1, care citesc aceleasi etichete.
from etva.d300 import _norm as _normalizeaza

RIDICATA = "ridicata"
MEDIE = "medie"
SCAZUTA = "scazuta"

# Doar sugestiile de la aceste niveluri preselecteaza linia in picker (vezi
# web/index.html); la SCAZUTA dropdown-ul ramane gol si se afiseaza doar
# candidatii, ca alegerea sa ramana vizibil a utilizatorului.
INCREDERE_PRESELECTATA = (RIDICATA, MEDIE)

# Cotele de TVA folosite in Romania de-a lungul timpului, la care se
# "lipeste" cota calculata aritmetic. Doar 21/11/9 au linie proprie in
# D300_LINES; restul apar aici ca sa putem spune EXACT ce cota am gasit
# ("cota 19% nu corespunde niciunei linii D300 curente") in loc sa taca.
_COTE_CUNOSCUTE = (24.0, 21.0, 20.0, 19.0, 11.0, 9.0, 5.0, 0.0)
# Rotunjirile per factura, acumulate in totalul din legenda, misca raportul
# cu zecimi de punct - 0.3 pp acopera cazurile reale fara sa confunde intre
# ele doua cote vecine (cea mai mica distanta e 11 -> 9, adica 2 pp).
_TOLERANTA_COTA = 0.3


def cota_dedusa(baza: float, tva: float) -> "float | None":
    """Cota de TVA dedusa aritmetic din sumele codului, lipita de cea mai
    apropiata cota cunoscuta. None cand baza e nula sau negativa - acolo nu
    exista semnal aritmetic, nu "cota 0"."""
    if not baza or baza <= 0:
        return None
    procent = tva / baza * 100.0
    for cota in _COTE_CUNOSCUTE:
        if abs(procent - cota) <= _TOLERANTA_COTA:
            return cota
    return round(procent, 2)


@dataclass(frozen=True)
class Semnale:
    """Ce a citit motorul din (directie, eticheta, sume) - separat de reguli
    ca sa poata fi inspectat si testat de unul singur."""
    directie: str
    eticheta: str
    baza: float
    tva: float
    cota: "float | None"
    scutit: bool
    intracomunitar: bool
    taxare_inversa: bool
    simplificare: bool
    bunuri: bool
    servicii: bool
    export: bool
    distanta: bool
    regularizare: bool
    la_incasare: bool


def _contine(text: str, *fragmente: str) -> bool:
    return any(f in text for f in fragmente)


def citeste_semnale(directie: str, eticheta: str, baza: float,
                    tva: float) -> Semnale:
    t = _normalizeaza(eticheta)
    return Semnale(
        directie=directie, eticheta=t, baza=baza, tva=tva,
        cota=cota_dedusa(baza, tva),
        scutit=_contine(t, "scutit", "neimpozabil"),
        intracomunitar=(_contine(t, "intracomunitar")
                       or re.search(r"\baic\b", t) is not None),
        # "obligat la plata TVA" e formularea din chiar catalogul D300 pentru
        # taxare inversa - suggest_line() nu o vede, fiindca testul lui
        # pentru TVA la incasare ("la plata") o inghite prima si intoarce
        # None. De aceea codurile cu eticheta asta ajung aici.
        taxare_inversa=(_contine(t, "taxare invers", "tax. invers",
                                "taxa invers")
                       or ("obligat la plata" in t
                           and not _contine(t, "tva la plata"))),
        simplificare=_contine(t, "simplific", "art. 331", "art.331"),
        bunuri=_contine(t, "bunuri", "marfa", "marfur", "produse"),
        servicii=_contine(t, "servici", "prestar"),
        export=_contine(t, "export", "extracomunitar", "tari terte",
                       "in afara comunitatii"),
        distanta=(_contine(t, "la distanta", "one stop shop")
                 or re.search(r"\b(oss|tbe)\b", t) is not None),
        regularizare=_contine(t, "regularizar", "stornar", "corectie"),
        # Deliberat mai strict decat suggest_line(), care marcheaza orice
        # "la plata" ca TVA neexigibila: "beneficiarul obligat la plata TVA"
        # e taxare inversa, nu TVA la incasare (vezi taxare_inversa mai sus).
        la_incasare=_contine(t, "tva la plata", "t.v.a. la plata",
                            "neexigibil", "la incasare"),
    )


@dataclass(frozen=True)
class Regula:
    nume: str
    directii: tuple
    cand: Callable
    linie: "str | None"          # None = refuz motivat, nu sugestie slaba
    incredere: "str | None"
    motiv: str                   # sablon .format(**_context(semnale))
    alternative: tuple = ()      # ((line_no, motiv), ...)


_AMBELE = ("vanzari", "cumparari")
_V = ("vanzari",)
_C = ("cumparari",)

# Ordinea CONTEAZA: prima regula care se potriveste castiga, deci cele mai
# specifice stau primele (regularizarile inaintea operatiunilor obisnuite,
# markerii de scutire inaintea celor de cota).
_REGULI = (
    Regula(
        nume="tva_neexigibila", directii=_AMBELE,
        cand=lambda s: s.la_incasare,
        linie=None, incredere=None,
        motiv="TVA neexigibila (la incasare): taxa nu e datorata in "
              "perioada curenta, deci codul nu are corespondent in decontul "
              "acestei luni. Ramane neclasificat deliberat - mapeaza-l doar "
              "daca stii ca exigibilitatea cade chiar in aceasta perioada.",
    ),

    # --- Vanzari (taxa colectata) ---
    Regula(
        nume="regularizare_ic_bunuri", directii=_V,
        cand=lambda s: s.regularizare and s.intracomunitar and s.bunuri,
        linie="2", incredere=RIDICATA,
        motiv="Eticheta arata o regularizare a unor livrari intracomunitare "
              "de bunuri - rd.2 e linia de regularizare a rd.1.",
    ),
    Regula(
        nume="regularizare_ic_servicii", directii=_V,
        cand=lambda s: s.regularizare and s.intracomunitar and s.servicii,
        linie="4", incredere=MEDIE,
        motiv="Regularizare de prestari de servicii intracomunitare - rd.4 "
              "regularizeaza rd.3.1. Verifica totusi daca nu e o "
              "regularizare de livrari de bunuri (rd.2).",
    ),
    Regula(
        nume="regularizare_distanta", directii=_V,
        cand=lambda s: s.regularizare and s.distanta,
        linie="18", incredere=MEDIE,
        motiv="Regularizare de vanzari la distanta / servicii TBE - rd.18 "
              "regularizeaza rd.17.",
    ),
    Regula(
        nume="regularizare_colectata", directii=_V,
        cand=lambda s: s.regularizare,
        linie="16", incredere=MEDIE,
        motiv="Regularizare de taxa colectata, fara indicii de operatiune "
              "intracomunitara sau de vanzare la distanta - rd.16 e linia "
              "generala de regularizare a taxei colectate.",
    ),
    Regula(
        nume="vanzari_la_distanta", directii=_V,
        cand=lambda s: s.distanta,
        linie="17", incredere=RIDICATA,
        motiv="Vanzari intracomunitare la distanta / servicii TBE catre "
              "persoane neimpozabile (regimul OSS) - rd.17.",
    ),
    Regula(
        nume="export", directii=_V,
        cand=lambda s: s.export,
        linie="3", incredere=RIDICATA,
        motiv="Operatiune cu locul livrarii/prestarii in afara Romaniei "
              "(export / client extracomunitar) - rd.3.",
    ),
    Regula(
        nume="livrare_ic_bunuri", directii=_V,
        cand=lambda s: s.intracomunitar and s.bunuri,
        linie="1", incredere=RIDICATA,
        motiv="Livrare intracomunitara de bunuri, scutita conform art. 294 "
              "alin.(2) lit.a) si d) - rd.1.",
    ),
    Regula(
        nume="prestare_ic_servicii", directii=_V,
        cand=lambda s: s.intracomunitar and s.servicii,
        linie="3", incredere=SCAZUTA,
        motiv="Prestare de servicii intracomunitare. Aici platforma nu poate "
              "decide singura: serviciul se declara pe rd.3, iar pe rd.3.1 "
              "doar daca NU beneficiaza de scutire in statul membru unde e "
              "datorata taxa. Alege in functie de regimul clientului.",
        alternative=(("3.1", "Daca serviciul nu e scutit in statul membru "
                            "al clientului."),),
    ),
    Regula(
        nume="simplificare_vanzari", directii=_V,
        cand=lambda s: s.simplificare,
        linie="13", incredere=RIDICATA,
        motiv="Livrare supusa masurilor de simplificare (art. 331 CF, "
              "taxare inversa la beneficiar) - rd.13, indiferent de cota.",
    ),
    Regula(
        nume="taxare_inversa_interna", directii=_V,
        cand=lambda s: s.taxare_inversa and not s.intracomunitar,
        linie="13", incredere=MEDIE,
        motiv="Livrare interna cu taxare inversa - rd.13. Daca de fapt e o "
              "prestare cu locul in afara Romaniei, linia corecta e rd.3.",
        alternative=(("3", "Daca locul prestarii e in afara Romaniei."),),
    ),
    Regula(
        nume="intracomunitar_neclar", directii=_V,
        cand=lambda s: s.intracomunitar,
        linie="1", incredere=SCAZUTA,
        motiv="Eticheta indica o operatiune intracomunitara, dar nu spune "
              "daca e vorba de bunuri (rd.1) sau de servicii (rd.3 / "
              "rd.3.1). Alege dupa natura operatiunii.",
        alternative=(("3", "Daca sunt prestari de servicii."),),
    ),
    Regula(
        nume="scutit_intern", directii=_V,
        cand=lambda s: s.scutit,
        linie="14+15", incredere=RIDICATA,
        motiv="Livrare scutita fara legatura cu operatiuni intracomunitare "
              "sau cu exportul - si scutirile cu drept de deducere, si cele "
              "fara, se declara pe aceeasi linie, rd.14+15.",
    ),
    Regula(
        nume="cota_21_vanzari", directii=_V,
        cand=lambda s: s.cota == 21.0,
        linie="9", incredere=RIDICATA,
        motiv="Cota de {cota} rezulta aritmetic din sumele codului "
              "(TVA {tva} / baza {baza}), desi eticheta nu o scrie - "
              "livrare taxabila cu cota 21%, rd.9.",
    ),
    Regula(
        nume="cota_11_vanzari", directii=_V,
        cand=lambda s: s.cota == 11.0,
        linie="10", incredere=RIDICATA,
        motiv="Cota de {cota} rezulta aritmetic din sumele codului "
              "(TVA {tva} / baza {baza}) - livrare taxabila cu cota 11%, "
              "rd.10.",
    ),
    Regula(
        nume="cota_9_vanzari", directii=_V,
        cand=lambda s: s.cota == 9.0,
        linie="11", incredere=RIDICATA,
        motiv="Cota de {cota} rezulta aritmetic din sumele codului "
              "(TVA {tva} / baza {baza}) - livrare taxabila cu cota 9%, "
              "rd.11.",
    ),

    # --- Cumparari (taxa deductibila) ---
    Regula(
        nume="regularizare_ic_bunuri_ded", directii=_C,
        cand=lambda s: s.regularizare and s.intracomunitar and s.bunuri,
        linie="21", incredere=MEDIE,
        motiv="Regularizare a unor achizitii intracomunitare de bunuri - "
              "rd.21 regularizeaza rd.20.",
    ),
    Regula(
        nume="regularizare_ic_servicii_ded", directii=_C,
        cand=lambda s: s.regularizare and s.intracomunitar and s.servicii,
        linie="23", incredere=MEDIE,
        motiv="Regularizare a unor achizitii intracomunitare de servicii - "
              "rd.23 regularizeaza rd.22.1.",
    ),
    Regula(
        nume="regularizare_deductibila", directii=_C,
        cand=lambda s: s.regularizare,
        linie=None, incredere=None,
        motiv="Regularizare de taxa deductibila fara indicii de operatiune "
              "intracomunitara. Linia generala pentru asa ceva (rd.33) e "
              "tratata in aplicatie ca total calculat, nu ca destinatie a "
              "unui cod din jurnal, deci nu se poate mapa aici - "
              "corecteaza operatiunea in jurnal sau declar-o manual.",
    ),
    Regula(
        nume="ic_scutit_servicii", directii=_C,
        cand=lambda s: s.intracomunitar and s.scutit and s.servicii,
        linie="29.1", incredere=SCAZUTA,
        motiv="Achizitie intracomunitara de servicii marcata ca scutita / "
              "neimpozabila. Daca e chiar scutita, linia e rd.29.1; daca e "
              "o achizitie taxabila cu taxare inversa, e rd.22.1. Eticheta "
              "nu distinge intre cele doua.",
        alternative=(("22.1", "Daca e o achizitie taxabila cu taxare "
                             "inversa."),),
    ),
    Regula(
        nume="ic_scutit_altele", directii=_C,
        cand=lambda s: s.intracomunitar and s.scutit,
        linie="29", incredere=SCAZUTA,
        motiv="Achizitie intracomunitara marcata ca scutita / neimpozabila. "
              "Daca e chiar scutita, linia e rd.29; daca e o achizitie "
              "intracomunitara de bunuri cu taxare inversa, e rd.20.1.",
        alternative=(("20.1", "Daca e o achizitie intracomunitara de bunuri "
                             "cu taxare inversa."),),
    ),
    Regula(
        nume="achizitie_ic_servicii_inversa", directii=_C,
        cand=lambda s: s.intracomunitar and s.servicii and s.taxare_inversa,
        linie="22.1", incredere=RIDICATA,
        motiv="Achizitie intracomunitara de servicii cu taxare inversa - "
              "rd.22.1 (partea deductibila). Aplicatia oglindeste automat "
              "aceeasi suma pe rd.7.1 din taxa colectata, asa cum face si "
              "formularul ANAF.",
    ),
    Regula(
        nume="achizitie_ic_bunuri", directii=_C,
        cand=lambda s: s.intracomunitar and s.bunuri,
        linie="20.1", incredere=RIDICATA,
        motiv="Achizitie intracomunitara de bunuri, cu furnizor inregistrat "
              "in scopuri de TVA in statul membru de livrare - rd.20.1 "
              "(oglindita automat pe rd.5.1 din taxa colectata).",
    ),
    Regula(
        nume="achizitie_ic_servicii", directii=_C,
        cand=lambda s: s.intracomunitar and s.servicii,
        linie="22.1", incredere=MEDIE,
        motiv="Achizitie intracomunitara de servicii - regimul obisnuit e "
              "taxarea inversa la beneficiar, rd.22.1.",
    ),
    Regula(
        nume="achizitie_ic_neclara", directii=_C,
        cand=lambda s: s.intracomunitar,
        linie="20.1", incredere=SCAZUTA,
        motiv="Eticheta indica o achizitie intracomunitara, dar nu spune "
              "daca e vorba de bunuri (rd.20.1) sau de servicii (rd.22.1). "
              "Alege dupa natura operatiunii.",
        alternative=(("22.1", "Daca sunt achizitii de servicii."),),
    ),
    Regula(
        nume="taxare_inversa_interna_ded", directii=_C,
        cand=lambda s: s.taxare_inversa,
        linie="22", incredere=SCAZUTA,
        motiv="Achizitie interna la care beneficiarul e obligat la plata "
              "taxei. Nu se poate decide automat intre rd.22 (servicii, "
              "art. 307) si rd.26 (bunuri/servicii supuse masurilor de "
              "simplificare, art. 331) - eticheta nu numeste articolul.",
        alternative=(("26", "Daca operatiunea intra sub masurile de "
                           "simplificare, art. 331."),),
    ),
    Regula(
        nume="cota_21_cumparari", directii=_C,
        cand=lambda s: s.cota == 21.0,
        linie="24", incredere=RIDICATA,
        motiv="Cota de {cota} rezulta aritmetic din sumele codului "
              "(TVA {tva} / baza {baza}), desi eticheta nu o scrie - "
              "achizitie taxabila cu cota 21%, rd.24.",
    ),
    Regula(
        nume="cota_11_cumparari", directii=_C,
        cand=lambda s: s.cota == 11.0,
        linie="25", incredere=RIDICATA,
        motiv="Cota de {cota} rezulta aritmetic din sumele codului "
              "(TVA {tva} / baza {baza}) - achizitie taxabila cu cota 11%, "
              "rd.25.",
    ),
    Regula(
        # Asimetrie reala a catalogului D300_LINES: exista rd.11 pentru
        # livrari cu cota 9%, dar nicio linie de 9% in sectiunea
        # deductibila. Se raporteaza ca refuz explicit, nu se ghiceste o
        # linie vecina.
        nume="cota_9_cumparari_fara_linie", directii=_C,
        cand=lambda s: s.cota == 9.0,
        linie=None, incredere=None,
        motiv="Din sumele codului rezulta o cota de {cota}, dar sectiunea de "
              "taxa deductibila din catalogul aplicatiei nu are o linie de "
              "9% (are doar rd.24 pentru 21% si rd.25 pentru 11%). "
              "Verifica cota din jurnal inainte de a alege manual o linie.",
    ),

    # --- Refuzuri comune, la finalul listei ---
    Regula(
        nume="cota_fara_linie", directii=_AMBELE,
        cand=lambda s: s.cota is not None and s.cota != 0.0,
        linie=None, incredere=None,
        motiv="Din sumele codului rezulta o cota de {cota}, care nu "
              "corespunde niciunei linii din decontul D300 in vigoare "
              "(21%, 11%, 9%). Verifica perioada si cotele din jurnal.",
    ),
    Regula(
        nume="tva_zero_fara_indicii", directii=_AMBELE,
        cand=lambda s: s.cota == 0.0,
        linie=None, incredere=None,
        motiv="Codul are TVA 0 pe o baza de {baza}, dar eticheta nu spune "
              "daca e o operatiune scutita, una cu taxare inversa sau una "
              "cu locul in afara Romaniei - cele trei duc pe linii diferite "
              "din decont, asa ca platforma nu ghiceste.",
    ),
    Regula(
        nume="fara_semnal", directii=_AMBELE,
        cand=lambda s: True,
        linie=None, incredere=None,
        motiv="Eticheta codului nu contine niciun indiciu pe care platforma "
              "sa il poata folosi (articol din Codul fiscal, cota, marcaj "
              "de scutire sau de operatiune intracomunitara), iar sumele nu "
              "dau nici ele o cota utilizabila.",
    ),
)


def _formateaza_cota(cota: "float | None") -> str:
    if cota is None:
        return "necunoscuta"
    if cota == int(cota):
        return f"{int(cota)}%"
    return f"{cota}%"


def _context(s: Semnale) -> dict:
    return {"cota": _formateaza_cota(s.cota),
            "baza": f"{s.baza:.2f}", "tva": f"{s.tva:.2f}"}


def _potriveste(s: Semnale) -> Regula:
    for regula in _REGULI:
        if s.directie in regula.directii and regula.cand(s):
            return regula
    return _REGULI[-1]


def reguli_invalide() -> list:
    """Regulile care ar propune o linie inexistenta sau din sectiunea
    opusa directiei lor. Trebuie sa fie mereu goala - e verificata in
    tests/test_cod_sugestii.py, nu la import, ca o greseala de catalog sa
    nu poata impiedica pornirea aplicatiei."""
    probleme = []
    for regula in _REGULI:
        for directie in regula.directii:
            valide = valid_lines_for_direction(directie)
            tinte = [regula.linie] if regula.linie else []
            tinte += [ln for ln, _ in regula.alternative]
            for linie in tinte:
                if linie not in valide:
                    probleme.append(f"{regula.nume}/{directie}: {linie}")
    return probleme


def sugereaza(direction: str, cod: str, label: str, base: float = 0.0,
              vat: float = 0.0) -> dict:
    """Opinia platformei pentru un singur cod neclasificat.

    Intoarce mereu un dict cu aceeasi forma - `line_no` None inseamna ca
    platforma refuza sa sugereze, iar `motiv` spune de ce. Liniile propuse
    sunt filtrate prin valid_lines_for_direction(), acelasi guardrail ca
    la salvarea maparii (etva/cod_mappings.py::save_mapping): o sugestie nu
    poate ajunge sa propuna o linie din sectiunea opusa nici daca o regula
    e scrisa gresit.
    """
    semnale = citeste_semnale(direction, label, base, vat)
    regula = _potriveste(semnale)
    context = _context(semnale)
    valide = valid_lines_for_direction(direction)

    linie = regula.linie if regula.linie in valide else None
    incredere = regula.incredere if linie else None
    alternative = [{"line_no": ln, "line_label": valide[ln], "motiv": motiv}
                  for ln, motiv in regula.alternative if ln in valide]
    motiv = regula.motiv.format(**context)
    if regula.linie and linie is None:
        # Nu ar trebui sa se intample (vezi reguli_invalide), dar daca se
        # intampla, utilizatorul primeste un refuz onest, nu o linie gresita.
        motiv = ("Platforma a identificat o regula pentru acest cod, dar "
                "linia rezultata nu e valida pentru un jurnal de "
                f"{direction} - alege manual.")

    return {
        "cod": cod, "direction": direction, "label": label,
        "base": base, "vat": vat,
        "cota": semnale.cota,
        "line_no": linie,
        "line_label": D300_LINES.get(linie, "") if linie else "",
        "incredere": incredere,
        "motiv": motiv,
        "regula": regula.nume,
        "alternative": alternative,
    }


def sugereaza_lot(coduri) -> list:
    """`coduri` are exact forma listei `unmapped` intoarsa de
    etva/d300.py::classify_legend, ca sa poata fi pasata direct."""
    return [sugereaza(c["direction"], c["cod"], c.get("label", ""),
                     c.get("base", 0.0) or 0.0, c.get("vat", 0.0) or 0.0)
            for c in coduri]
