"""Valida la interfaz estática del portal antes de servirla.

El portal vivía dentro de un f-string de Python y un escape mal resuelto
rompía el parseo del script **completo**, dejando el sitio inutilizable sin
que fallara ninguna prueba de API. Ahora vive en ``src/static``; esta prueba
vigila que siga siendo JavaScript válido y que no reaparezcan los patrones
que causaron aquel incidente.

Requiere Node para el chequeo de sintaxis; sin Node se limita a las
validaciones estructurales.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ESTATICOS = RAIZ / "src" / "static"
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for nombre in ("index.html", "estilos.css", "portal.js"):
    ruta = ESTATICOS / nombre
    assert ruta.exists(), f"falta {ruta}"
    print(f"  {nombre}: {ruta.stat().st_size:,} bytes")

html = (ESTATICOS / "index.html").read_text(encoding="utf-8")
script = (ESTATICOS / "portal.js").read_text(encoding="utf-8")

# La política de contenido exige script-src 'self': ningún script embebido.
assert "<script>" not in html, "el HTML volvió a traer un script embebido"
assert 'src="/static/portal.js"' in html, "el HTML no carga portal.js"
print("  sin script embebido en el HTML: OK")

# Los manejadores en línea rompen script-src 'self' y son el patrón que
# obligaba a escapar comillas dentro de la plantilla.
enlineados = re.findall(r'\son[a-z]+\s*=\s*"', html)
assert not enlineados, f"manejadores en línea en el HTML: {enlineados}"
print("  sin manejadores onclick en línea: OK")

# El texto que llega de la base nunca se inyecta como HTML en los avisos.
assert "innerHTML" not in script.split("function notificar")[1].split("function conectarAlertas")[0], \
    "el aviso flotante volvió a usar innerHTML (riesgo de XSS)"
assert "textContent" in script, "el aviso flotante no usa textContent"
print("  avisos construidos con textContent: OK")

for requerida in ("iniciarSesion", "esc", "conectarAlertas", "abrirPestana", "cargarPortal"):
    assert re.search(r"function\s+" + requerida + r"\s*\(", script), f"falta {requerida}"
declaradas = re.findall(r"function\s+([A-Za-z_]\w*)\s*\(", script)
print("  funciones declaradas:", len(declaradas))

# El servidor debe poder resolver la carpeta que sirve.
import web_server  # noqa: E402

assert web_server.ESTATICOS.is_dir(), "el servidor no encuentra src/static"
csp = web_server.CABECERAS_SEGURIDAD["Content-Security-Policy"]
assert "script-src 'self';" in csp, "la CSP dejó de exigir script-src 'self'"
print("  CSP con script-src 'self': OK")

node = shutil.which("node")
if not node:
    print("Node no disponible: se omite el chequeo de sintaxis")
    raise SystemExit(0)

resultado = subprocess.run(
    [node, "--check", str(ESTATICOS / "portal.js")], capture_output=True, text=True
)
if resultado.returncode != 0:
    print("portal.js NO parsea:", file=sys.stderr)
    print(resultado.stderr, file=sys.stderr)
    raise SystemExit(1)

print("  node --check: portal.js parsea sin errores")
print("PORTAL OK")
