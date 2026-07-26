"""Inregistreaza fonturi Unicode (Noto Sans) pentru randare corecta a
diacriticelor romanesti in PDF-urile generate cu reportlab.

Fonturile standard ale reportlab-ului (Helvetica/Times, incorporate ca
fonturi Type1 cu WinAnsiEncoding) NU acopera s/t cu virgula dedesubt
(s/U+0219, t/U+021B) - reportlab le inlocuieste silentios cu alt caracter
in loc sa ridice o eroare, deci trece complet neobservat pana cineva
citeste cu atentie PDF-ul rezultat (descoperit la contractul de prestari
servicii - vezi portal/contract.py). Noto Sans (licenta SIL OFL, sursa
github.com/google/fonts) are acoperire completa Unicode pentru romana;
fisierele din portal/fonts/ sunt instante statice (Regular/Bold) generate
o singura data din fontul variabil oficial, cu fontTools.
"""
import os

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

REGULAR = "NotoSans"
BOLD = "NotoSans-Bold"

_inregistrat = False


def asigura_fonturi() -> None:
    """Inregistreaza fonturile Noto Sans la nivel global in reportlab -
    idempotent, sigur de apelat din mai multe module care genereaza PDF-uri
    (portal/invoicing.py, portal/contract.py)."""
    global _inregistrat
    if _inregistrat:
        return
    pdfmetrics.registerFont(
        TTFont(REGULAR, os.path.join(_FONTS_DIR, "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(
        TTFont(BOLD, os.path.join(_FONTS_DIR, "NotoSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD)
    _inregistrat = True
