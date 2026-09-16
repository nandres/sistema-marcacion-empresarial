"""Respaldo y restauración: el archivo sirve, y avisa cuando no va a servir.

Una herramienta de respaldo sin prueba es la que falla el día que hace falta,
que es el peor día para descubrirlo. Se comprueba el viaje redondo completo
—volcar, verificar, restaurar y contar— y sobre todo las dos formas de fallar
en silencio: restaurar con otra clave biométrica, que deja las fotos ilegibles
sin que nada proteste, y restaurar sobre una base que no existe, que antes
terminaba describiendo un esquema recién sembrado como si fuera el respaldo.

Va al final de la suite a propósito: la restauración reemplaza el contenido de
la base, así que ningún otro conjunto puede depender de lo que quede después.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import respaldo
from database import Database, load_config

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
          + (f" | {detalle}" if detalle else ""))


CARPETA = Path(os.getenv("TEMP", "/tmp")) / "respaldos_prueba"

print("RESPALDO Y RESTAURACIÓN")

# ------------------------------------------------------------- 1. Volcado
print("\n1) El respaldo sale y se describe a sí mismo")

archivo = respaldo.crear(CARPETA)
verificar("el archivo existe y no está vacío",
          archivo.exists() and archivo.stat().st_size > 1024,
          f"{archivo.stat().st_size} bytes")

manifiesto = respaldo.leer_manifiesto(archivo)
verificar("queda un manifiesto al lado", manifiesto is not None)
verificar("con la fecha, la base y los recuentos",
          manifiesto and {"creado", "base", "recuentos"} <= set(manifiesto))

antes = dict(manifiesto["recuentos"])
verificar("los recuentos incluyen las tablas que importan",
          {"users", "marcajes", "empresas"} <= set(antes), str(list(antes)[:4]))

# --------------------------------------------------- 2. Lectura del archivo
print("\n2) Se puede comprobar sin restaurar")

estado = respaldo.verificar(archivo)
verificar("el volcado se lee y tiene objetos", estado["objetos"] > 0,
          f"{estado['objetos']} objetos")

try:
    respaldo.verificar(CARPETA / "no-existe.dump")
    verificar("un archivo ausente se rechaza", False)
except respaldo.RespaldoInvalido:
    verificar("un archivo ausente se rechaza", True)

roto = CARPETA / "roto.dump"
roto.write_bytes(b"esto no es un volcado de PostgreSQL")
try:
    respaldo.verificar(roto)
    verificar("un archivo corrupto se rechaza", False)
except respaldo.RespaldoInvalido:
    verificar("un archivo corrupto se rechaza", True)

# ------------------------------------------- 3. La clave que no está en la base
print("\n3) Restaurar con otra clave biométrica no pasa en silencio")

huella_real = respaldo.huella_de_clave()
verificar("la huella identifica la clave sin revelarla",
          bool(huella_real) and len(huella_real) == 16, huella_real)

original = os.environ.get("BIOMETRIA_CLAVE", "")
os.environ["BIOMETRIA_CLAVE"] = "una-clave-distinta-de-treinta-y-dos-o-mas-caracteres"
import biometria

biometria._clave.cache_clear() if hasattr(biometria._clave, "cache_clear") else None

verificar("con otra clave la huella cambia",
          respaldo.huella_de_clave() != huella_real)

try:
    respaldo.restaurar(archivo, forzar=True)
    verificar("restaurar con la clave equivocada se rechaza", False)
except respaldo.RespaldoInvalido as error:
    verificar("restaurar con la clave equivocada se rechaza", True,
              str(error).splitlines()[0][:60])

os.environ["BIOMETRIA_CLAVE"] = original
verificar("y con la clave correcta vuelve a coincidir",
          respaldo.huella_de_clave() == huella_real)

# ------------------------------------------ 4. Una base que no existe es un error
print("\n4) Restaurar sobre una base inexistente falla, no inventa")

config_real = load_config()["dbname"]
os.environ["DB_NAME"] = "base_que_no_existe_" + huella_real[:6]
try:
    respaldo.restaurar(archivo, forzar=True)
    verificar("se rechaza antes de tocar nada", False)
except respaldo.RespaldoInvalido as error:
    verificar("se rechaza antes de tocar nada", True,
              str(error).splitlines()[0][:60])
finally:
    os.environ["DB_NAME"] = config_real

# --------------------------------------------------------- 5. Viaje redondo
print("\n5) Restaurar deja la base como estaba")

respaldo.restaurar(archivo, forzar=True)

db = Database()
db.connect()
despues = respaldo._recuentos(db)
db.cerrar()

for tabla, esperado in antes.items():
    verificar(f"{tabla} vuelve con sus filas", despues.get(tabla) == esperado,
              f"esperado {esperado}, hay {despues.get(tabla)}")

for resto in CARPETA.glob("*"):
    resto.unlink()
CARPETA.rmdir()

print()
if fallos:
    print(f"RESPALDO: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("RESPALDO OK · el archivo sirve, y avisa cuando no va a servir")
