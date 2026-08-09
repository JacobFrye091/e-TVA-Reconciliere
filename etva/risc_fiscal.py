"""Scoring de risc fiscal, bazat pe metodologia oficiala ANAF publicata in
Anexa 2 ("Fisa indicatorilor de risc fiscal"), parte din Procedura de
stabilire a gradului de risc fiscal in cazul rambursarilor de TVA (Ordin
ANAF din 17.12.2015 - vezi legislatie.just.ro/lege5.ro; textul integral al
fisei confirmat prin static.anaf.ro/.../Anexanr2laproceduraFisaindicriscfiscal.htm).

Fisa oficiala are doua sectiuni:
  - Sectiunea B: 9 conditii de "risc fiscal mare" automat (cazier fiscal,
    entitate noua, fara salariati, fara bunuri, insolventa, evidenta
    speciala, declarat inactiv, raport de inspectie/Garda Financiara) -
    daca oricare e adevarata, dosarul e clasificat direct risc mare,
    INDIFERENT de punctaj. Acest override e independent de cati indicatori
    din Sectiunea C sunt implementati mai jos, deci se poate reproduce
    fidel chiar si partial - vezi calculeaza_scor().
  - Sectiunea C: 8 indicatori cu punctaj. Doar 1-5 sunt implementati aici:
      1. Capitaluri proprii       (sursa: bilant / SAF-T D406)
      2. Grad de indatorare       (sursa: bilant / SAF-T D406)
      3. Profitabilitate          (sursa: bilant / SAF-T D406)
      4. Declaratii fiscale nedepuse (sursa: partial D300, rest manual)
      5. Obligatii fiscale restante   (sursa: manual, sau Fisa Rol daca
         cercetarea SPV o confirma vreodata - vezi planul modulului)
    Indicatorii 6-8 (istoric rambursari TVA solutionate fara verificare)
    sunt date 100% interne ANAF (aplicatia DECIMP + fisa analitica de
    evidenta pe platitori) - IMPOSIBIL de reprodus extern, deci nu apar
    niciodata insumate mai jos, doar marcate explicit "neaplicabil".

ATENTIE la prag - NU folosim pragul/etichetele oficiale ANAF ("risc
mic/mediu/mare", prag >=60 puncte): acel prag e definit pe suma TUTUROR
celor 8 indicatori (max 550 puncte). Cu doar 1-5 implementati (max 370 la
nivelul 'complet', 220 la 'simplu'), aplicarea directa a pragului "60" ar
fi inselatoare - ar putea eticheta "risc mare" o firma care, cu toate cele
8 puncte calculate, ar iesi sub prag. In loc, `clasificare` foloseste
etichete PROPRII ("scazut"/"moderat"/"ridicat") pe un scor normalizat
0-100 (`scor_afisat`), cu exceptia override-ului de Sectiunea B de mai sus
(acela ramane fidel metodologiei oficiale, fiind independent de punctaj).
"""
from dataclasses import dataclass, field

SIMPLU = "simplu"
COMPLET = "complet"
NIVELURI = (SIMPLU, COMPLET)

# Punctajul maxim posibil pe indicatorii implementati, per nivel - folosit
# la normalizarea scor_afisat (0-100). 'simplu' = doar 1-3 (100+50+70);
# 'complet' = 1-5 (+100 indicator 4, +50 indicator 5).
_MAX_PUNCTAJ = {SIMPLU: 220, COMPLET: 370}

NEAPLICABIL_NIVEL = "necesita nivelul complet"
NEAPLICABIL_ANAF = "date interne ANAF, imposibil de reprodus extern"

# Cele 9 conditii din Sectiunea B - cheie interna -> eticheta afisata.
# 'declarat_inactiv' si 'entitate_noua' pot fi populate automat de apelant
# (din etva.anaf_cui.verify_cui), restul sunt introduse manual de contabil
# la nivelul 'complet' (nu exista sursa publica de date pentru ele - vezi
# planul modulului pentru detalii per flag).
FLAGURI_SECTIUNE_B = {
    "cazier_fiscal": "Inscrieri in cazierul fiscal",
    "entitate_noua": "Entitate nou infiintata",
    "fara_salariati": "Absenta salariatilor",
    "fara_bunuri": "Lipsa bunurilor imobile sau mobile",
    "insolventa": "Procedura de insolventa deschisa",
    "evidenta_speciala": "Inregistrare in evidenta speciala",
    "declarat_inactiv": "Declarat inactiv fiscal",
    "raport_inspectie_risc_mare": "Raport de risc fiscal mare de la inspectia fiscala",
    "comunicare_garda_financiara": "Comunicare de risc fiscal mare de la Garda Financiara",
}


@dataclass
class ScorRiscFiscal:
    nivel: str
    scor_total_indicatori: int
    scor_max_posibil: int
    scor_afisat: int
    clasificare: str
    override_sectiune_b: bool
    flaguri_risc_mare_active: "list[str]" = field(default_factory=list)
    detaliu: "list[dict]" = field(default_factory=list)


def _indicator_capitaluri_proprii(capitaluri_proprii) -> "int | None":
    """1: capitaluri proprii <=0 -> 100p, >0 -> 0p."""
    if capitaluri_proprii is None:
        return None
    return 100 if capitaluri_proprii <= 0 else 0


def _indicator_grad_indatorare(datorii_totale, capitaluri_proprii) -> "int | None":
    """2: grad de indatorare = datorii/capital propriu. 0<=valoare<=1 -> 0p;
    >1 -> 50p. Capital propriu <=0 face raportul nedefinit/infinit -
    tratat conservator ca 50p (oricum indicatorul 1 e deja la maxim in
    acel caz)."""
    if datorii_totale is None or capitaluri_proprii is None:
        return None
    if capitaluri_proprii <= 0:
        return 50
    return 0 if (datorii_totale / capitaluri_proprii) <= 1 else 50


def _indicator_profitabilitate(rezultat_net) -> "int | None":
    """3: profit/cifra afaceri, redus la semnul rezultatului net (rezultat
    <=0, adica zero sau pierdere -> 70p; >0 -> 0p). Fisa oficiala descrie
    doar "=0 -> 70p, >0 -> 0p" - o pierdere e cel putin la fel de riscanta
    ca un rezultat exact zero, deci tratata identic."""
    if rezultat_net is None:
        return None
    return 70 if rezultat_net <= 0 else 0


def _indicator_declaratii_nedepuse(numar_nedepuse) -> "int | None":
    """4: 0 -> 0p, 1 -> 50p, >1 -> 100p."""
    if numar_nedepuse is None:
        return None
    if numar_nedepuse <= 0:
        return 0
    return 50 if numar_nedepuse == 1 else 100


def _indicator_obligatii_restante(obligatii_restante, obligatii_crescute) -> "int | None":
    """5: fara obligatii restante -> 0p; cu obligatii in crestere fata de
    inceputul perioadei -> 50p; cu obligatii dar in scadere/stabile -> 30p."""
    if obligatii_restante is None:
        return None
    if not obligatii_restante:
        return 0
    return 50 if obligatii_crescute else 30


def _clasifica(scor_afisat: int) -> str:
    if scor_afisat < 34:
        return "scazut"
    if scor_afisat <= 66:
        return "moderat"
    return "ridicat"


def calculeaza_scor(nivel: str, date_financiare: dict, *,
                    declaratii_nedepuse: "int | None" = None,
                    obligatii_restante: "bool | None" = None,
                    obligatii_crescute: "bool | None" = None,
                    flaguri_sectiune_b: "dict[str, bool] | None" = None
                    ) -> ScorRiscFiscal:
    """Calculeaza scorul de risc fiscal pentru o perioada.

    nivel: 'simplu' (doar indicatorii 1-3, din date_financiare) sau
    'complet' (1-3 + 4-5, din declaratii_nedepuse/obligatii_restante, plus
    override-ul de Sectiunea B din flaguri_sectiune_b). La 'simplu',
    parametrii specifici lui 'complet' sunt ignorati chiar daca sunt dati.

    date_financiare: dict cu capitaluri_proprii/datorii_totale/
    cifra_afaceri/rezultat_net (numeric sau None daca lipseste - un
    indicator cu valoarea de intrare lipsa nu se puncteaza, apare in
    detaliu ca 'neaplicabil').
    """
    if nivel not in NIVELURI:
        raise ValueError(f"Nivel necunoscut: {nivel!r}")

    detaliu = []
    scor_total = 0

    def _adauga(indicator, nume, valoare, punctaj, sursa):
        nonlocal scor_total
        if punctaj is None:
            detaliu.append({"indicator": indicator, "nume": nume,
                            "neaplicabil": "valoare de intrare lipsa"})
            return
        scor_total += punctaj
        detaliu.append({"indicator": indicator, "nume": nume,
                        "valoare": valoare, "punctaj": punctaj, "sursa": sursa})

    _adauga(1, "Capitaluri proprii", date_financiare.get("capitaluri_proprii"),
           _indicator_capitaluri_proprii(date_financiare.get("capitaluri_proprii")),
           "saft_d406")
    _adauga(2, "Grad de indatorare",
           date_financiare.get("datorii_totale"),
           _indicator_grad_indatorare(date_financiare.get("datorii_totale"),
                                      date_financiare.get("capitaluri_proprii")),
           "saft_d406")
    _adauga(3, "Profitabilitate", date_financiare.get("rezultat_net"),
           _indicator_profitabilitate(date_financiare.get("rezultat_net")),
           "saft_d406")

    if nivel == COMPLET:
        _adauga(4, "Declaratii fiscale nedepuse", declaratii_nedepuse,
               _indicator_declaratii_nedepuse(declaratii_nedepuse), "manual")
        _adauga(5, "Obligatii fiscale restante", obligatii_restante,
               _indicator_obligatii_restante(obligatii_restante, obligatii_crescute),
               "manual")
    else:
        detaliu.append({"indicator": 4, "nume": "Declaratii fiscale nedepuse",
                        "neaplicabil": NEAPLICABIL_NIVEL})
        detaliu.append({"indicator": 5, "nume": "Obligatii fiscale restante",
                        "neaplicabil": NEAPLICABIL_NIVEL})

    for indicator, nume in (
            (6, "Restituiri solutionate fara verificare (12 luni)"),
            (7, "Pondere sume neaprobate la restituire"),
            (8, "Total sume restituite fara verificare (12 luni)")):
        detaliu.append({"indicator": indicator, "nume": nume,
                        "neaplicabil": NEAPLICABIL_ANAF})

    flaguri_active = []
    if nivel == COMPLET and flaguri_sectiune_b:
        for cheie, activ in flaguri_sectiune_b.items():
            if activ and cheie in FLAGURI_SECTIUNE_B:
                flaguri_active.append(FLAGURI_SECTIUNE_B[cheie])

    scor_max = _MAX_PUNCTAJ[nivel]
    scor_afisat = round(scor_total / scor_max * 100) if scor_max else 0
    override = bool(flaguri_active)
    clasificare = "ridicat" if override else _clasifica(scor_afisat)

    return ScorRiscFiscal(
        nivel=nivel, scor_total_indicatori=scor_total, scor_max_posibil=scor_max,
        scor_afisat=scor_afisat, clasificare=clasificare,
        override_sectiune_b=override, flaguri_risc_mare_active=flaguri_active,
        detaliu=detaliu)
