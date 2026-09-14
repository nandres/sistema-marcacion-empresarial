"""Contrato visual compartido entre el portal web y la GUI de escritorio.

Dos cosas se rompen en silencio en una interfaz: el contraste —nada falla,
simplemente queda texto ilegible— y la deriva del lenguaje visual, cuando
alguien reintroduce la tarjeta redondeada con sombra que el sistema decidió
no usar. Esta prueba vigila las dos.
"""

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gui  # noqa: E402

TEXTO_NORMAL = 4.5
INTERFAZ = 3.0


def _canal(valor: float) -> float:
    valor /= 255
    return valor / 12.92 if valor <= 0.03928 else ((valor + 0.055) / 1.055) ** 2.4


def luminancia(hexa: str) -> float:
    hexa = hexa.lstrip("#")
    r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _canal(r) + 0.7152 * _canal(g) + 0.0722 * _canal(b)


def contraste(frente: str, fondo: str) -> float:
    a, b = luminancia(frente), luminancia(fondo)
    claro, oscuro = max(a, b), min(a, b)
    return (claro + 0.05) / (oscuro + 0.05)


PARES = [
    ("TEXT", "BG", TEXTO_NORMAL, "texto principal sobre el fondo"),
    ("TEXT", "CARD", TEXTO_NORMAL, "texto principal sobre una sección"),
    ("TEXT", "INPUT_BG", TEXTO_NORMAL, "texto escrito en un campo"),
    ("MUTED", "BG", TEXTO_NORMAL, "texto secundario sobre el fondo"),
    ("MUTED", "CARD", TEXTO_NORMAL, "texto secundario sobre una sección"),
    ("MUTED", "INPUT_BG", TEXTO_NORMAL, "marcador de posición en un campo"),
    ("ON_PRIMARY", "PRIMARY", TEXTO_NORMAL, "texto del botón primario"),
    ("PRIMARY", "CARD", INTERFAZ, "acento sobre una sección"),
    ("DANGER", "CARD", INTERFAZ, "ausencia y acciones destructivas"),
    ("ACCENTO", "CARD", INTERFAZ, "tardanza y avisos"),
    ("SUCCESS", "CARD", INTERFAZ, "jornada en curso y confirmaciones"),
    ("CARD_BORDER", "CARD", 1.15, "filete visible contra la sección"),
    ("INPUT_BORDER", "CARD", INTERFAZ, "borde de campo contra la sección"),
]

fallos = 0
for nombre, tema in (("claro", gui.TEMA_CLARO), ("oscuro", gui.TEMA_OSCURO)):
    print(f"\nTema {nombre}")
    for frente, fondo, minimo, descripcion in PARES:
        ratio = contraste(tema[frente], tema[fondo])
        ok = ratio >= minimo
        if not ok:
            fallos += 1
        print(f"  {'OK  ' if ok else 'BAJO'} {ratio:5.2f}:1  (mín {minimo})  {descripcion}")

# Las tres superficies tienen que poder distinguirse entre sí: si el campo se
# funde con la sección, el usuario no ve dónde escribir.
for nombre, tema in (("claro", gui.TEMA_CLARO), ("oscuro", gui.TEMA_OSCURO)):
    for a, b in (("BG", "CARD"), ("CARD", "INPUT_BG")):
        if tema[a] == tema[b]:
            print(f"  BAJO tema {nombre}: {a} y {b} son el mismo color")
            fallos += 1

css = (RAIZ / "src" / "static" / "estilos.css").read_text(encoding="utf-8")


def variable(nombre: str, bloque: str) -> str:
    fragmento = css.split(bloque, 1)[1].split("}", 1)[0]
    valor = re.search(r"--" + nombre + r":\s*([^;]+);", fragmento)
    return valor.group(1).strip().lower() if valor else ""


# La GUI y el portal web comparten paleta: si una cambia, la otra también.
print("\nParidad de paleta entre la GUI y el portal web")
EQUIVALENCIAS = [
    ("acento", "PRIMARY"), ("tinta", "TEXT"), ("papel", "BG"),
    ("sobre-acento", "ON_PRIMARY"), ("ausente", "DANGER"),
    ("tardanza", "ACCENTO"), ("curso", "SUCCESS"),
    ("regla-fuerte", "INPUT_BORDER"),
]
for bloque, tema, etiqueta in ((":root {", gui.TEMA_CLARO, "claro"),
                               ('[data-tema="oscuro"] {', gui.TEMA_OSCURO, "oscuro")):
    for css_var, token in EQUIVALENCIAS:
        web = variable(css_var, bloque)
        escritorio = tema[token].lower()
        if web != escritorio:
            print(f"  DIFIERE tema {etiqueta}: --{css_var}={web} vs {token}={escritorio}")
            fallos += 1
    print(f"  OK   tema {etiqueta}: {len(EQUIVALENCIAS)} tokens coinciden")

# ---------------------------------------------------------------------------
# Deriva del lenguaje visual.
#
# El sistema construye la jerarquía con reglas tipográficas y espacio. Los
# patrones de abajo son los de la tarjeta flotante genérica: se prohíben
# explícitamente para que un retoque apurado no los reintroduzca.
# ---------------------------------------------------------------------------

print("\nLenguaje visual")

radios = [v for v in re.findall(r"border-radius:\s*([^;]+);", css)
          if not re.fullmatch(r"0|0px", v.strip())]
sombras = [v for v in re.findall(r"box-shadow:\s*([^;]+);", css)
           if not v.strip().startswith("inset")]
desenfoques = re.findall(r"backdrop-filter:\s*[^;]+;", css)
tipografia = re.search(r"--grotesca:\s*([^;]+);", css)

PROHIBIDOS = [
    ("esquinas redondeadas", radios),
    ("sombras proyectadas", sombras),
    ("desenfoque de fondo", desenfoques),
]
for descripcion, hallazgos in PROHIBIDOS:
    if hallazgos:
        print(f"  VUELVE {descripcion}: {hallazgos[:3]}")
        fallos += 1
    else:
        print(f"  OK   sin {descripcion}")

if not tipografia or "Inter" in tipografia.group(1):
    print("  VUELVE la tipografía por defecto de plantilla")
    fallos += 1
else:
    print("  OK   tipografía propia del sistema")

# Las cifras van en monoespaciada tabular: es lo que mantiene alineadas las
# columnas de horas cuando cambian de valor.
if "font-variant-numeric: tabular-nums" not in css:
    print("  FALTA cifras tabulares")
    fallos += 1
else:
    print("  OK   cifras tabulares")

print()
if fallos:
    print(f"PALETA: {fallos} problema(s)")
    raise SystemExit(1)
print("PALETA OK · contraste, paridad y lenguaje visual verificados")
