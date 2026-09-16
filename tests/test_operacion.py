"""Lo que hace falta para operar el sistema, no para usarlo.

Tres cosas que no cambian nada de lo que ve un empleado y deciden si el
sistema se puede sostener en producción:

* **Registro operativo.** Un 500 a las siete de la mañana tiene que dejar
  rastro, y las líneas de una misma petición tienen que poder juntarse.
* **Ruta de salud.** El chequeo anterior pedía la portada, que no toca
  PostgreSQL: con la base caída el contenedor se reportaba sano.
* **Versión del esquema.** Sin un sello en la propia base, *¿en qué versión
  está este cliente?* solo se podía contestar mirando el código que uno cree
  haberle instalado.

    WEB_BASE=http://127.0.0.1:8000 python tests/test_operacion.py
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from fastapi import Response

import database
import registro
import web_server
from database import Database

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8000")
fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
          + (f" | {detalle}" if detalle else ""))


def en_proceso_aparte(guion: str) -> str:
    """Corre un fragmento con el módulo recién importado.

    ``configurar()`` se ejecuta una sola vez por proceso a propósito, así que
    lo que pasa la primera vez no se puede observar desde un proceso que ya la
    llamó al importar el servidor.
    """
    resultado = subprocess.run(
        [sys.executable, "-c", guion],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(RAIZ), env={**os.environ, "PYTHONPATH": str(RAIZ / "src")},
    )
    return (resultado.stdout + resultado.stderr).strip()


print("OPERACIÓN · registro, salud y versión del esquema")

# ------------------------------------------------------ 1. Registro operativo
print("\n1) Cada línea dice de qué petición salió")

salida = en_proceso_aparte(
    "import registro\n"
    "registro.configurar()\n"
    "identificador = registro.abrir_peticion()\n"
    "registro.obtener('prueba').warning('algo pasó')\n"
    "print('ID=' + identificador)\n"
)
identificador = next(
    (linea[3:] for linea in salida.splitlines() if linea.startswith("ID=")), ""
)
verificar("la línea registrada lleva el identificador de la petición",
          bool(identificador) and f"[{identificador}]" in salida,
          identificador)
verificar("y lleva el nivel y el módulo que la emitió",
          "WARNING" in salida and "prueba" in salida)

fuera = en_proceso_aparte(
    "import registro\n"
    "registro.configurar()\n"
    "registro.obtener('prueba').info('sin petición en curso')\n"
)
verificar("fuera de una petición no inventa un identificador", "[-]" in fuera,
          fuera.splitlines()[-1][-40:] if fuera else "")

mal = en_proceso_aparte(
    "import logging, registro\n"
    "registro.configurar('nivel-que-no-existe')\n"
    "print('NIVEL=' + logging.getLevelName(logging.getLogger().level))\n"
)
verificar("un nivel mal escrito no impide arrancar: cae a INFO",
          "NIVEL=INFO" in mal, mal.splitlines()[-1] if mal else "")

antes = len(logging.getLogger().handlers)
registro.configurar()
registro.configurar()
verificar("configurar() de nuevo no duplica manejadores",
          len(logging.getLogger().handlers) == antes, f"{antes} manejador(es)")

# ------------------------------------------------------------ 2. Ruta de salud
print("\n2) La salud se mide contra la base, no contra la portada")

respuesta_local = Response()
cuerpo = web_server.api_salud(respuesta_local)
verificar("con la base arriba responde ok", cuerpo == {"estado": "ok"}, str(cuerpo))
verificar("y no filtra versiones ni nombres de host", list(cuerpo) == ["estado"],
          str(list(cuerpo)))


class BaseCaida(Database):
    """Una base que acepta la conexión y después no contesta.

    Es el caso que importaba: una conexión abierta contra un PostgreSQL que
    dejó de responder pasaba por sana, porque nadie le preguntaba nada.
    """

    def connect(self):
        return None

    def latido(self) -> None:
        raise RuntimeError("la base no contesta")

    def cerrar(self) -> None:
        return None


original = web_server.database.Database
web_server.database.Database = BaseCaida
try:
    respuesta_caida = Response()
    cuerpo_caido = web_server.api_salud(respuesta_caida)
finally:
    web_server.database.Database = original

verificar("con la base caída responde 503", respuesta_caida.status_code == 503,
          str(respuesta_caida.status_code))
verificar("y lo dice como degradado", cuerpo_caido == {"estado": "degradado"},
          str(cuerpo_caido))

try:
    httpx.get(f"{BASE}/", timeout=10)
    hay_servidor = True
except Exception:
    hay_servidor = False
    print("  (sin servidor en WEB_BASE: se omiten las comprobaciones por HTTP)")

if hay_servidor:
    salud = httpx.get(f"{BASE}/salud", timeout=15)
    verificar("por HTTP responde 200 sin credenciales", salud.status_code == 200,
              salud.text[:60])

    primera = httpx.get(f"{BASE}/salud", timeout=15)
    segunda = httpx.get(f"{BASE}/salud", timeout=15)
    verificar("cada respuesta trae su identificador de petición",
              bool(primera.headers.get("X-Peticion")),
              primera.headers.get("X-Peticion", ""))
    verificar("y dos peticiones no comparten el mismo",
              primera.headers.get("X-Peticion") != segunda.headers.get("X-Peticion"))

    perdida = httpx.get(f"{BASE}/api/ruta-que-no-existe", timeout=15)
    verificar("un 404 también queda identificado",
              bool(perdida.headers.get("X-Peticion")) and perdida.status_code == 404,
              str(perdida.status_code))

# --------------------------------------------------- 3. Versión del esquema
print("\n3) La base dice en qué versión está")

db = Database()
db.connect()

verificar("la base migrada declara la versión que este código espera",
          db.version_esquema() == database.ESQUEMA_VERSION,
          f"{db.version_esquema()} vs {database.ESQUEMA_VERSION}")
verificar("y no le falta nada por aplicar", db.migraciones_pendientes() == [],
          str(db.migraciones_pendientes()))
verificar("el esquema queda declarado listo", db.esquema_listo())

historial = db.historial_esquema()
verificar("el historial dice qué se aplicó y cuándo",
          any(p["nombre"] == f"base:{database.ESQUEMA_VERSION}" for p in historial),
          f"{len(historial)} anotación(es)")

# Subir la versión es exactamente lo que pasa cuando se despliega código nuevo
# sobre una base vieja. El servidor tiene que negarse a arrancar ahí, y no
# descubrirlo en la primera marcación.
version_real = database.ESQUEMA_VERSION
database.ESQUEMA_VERSION = version_real + 1
try:
    verificar("con código más nuevo que la base, la migración queda pendiente",
              db.migraciones_pendientes() == [f"base:{version_real + 1}"],
              str(db.migraciones_pendientes()))
    verificar("y el esquema NO se declara listo", not db.esquema_listo())
finally:
    database.ESQUEMA_VERSION = version_real

verificar("al restaurar la versión, la base vuelve a estar al día",
          db.esquema_listo())

# ------------------------------------------- 4. Un paso único corre una vez
print("\n4) Un paso de una sola vez corre una sola vez")

PASO = "prueba-paso-unico"
pasos_reales = database.PASOS_UNICOS
database.PASOS_UNICOS = (
    (PASO,
     "CREATE TABLE IF NOT EXISTS prueba_pasos (marca SERIAL PRIMARY KEY); "
     "INSERT INTO prueba_pasos DEFAULT VALUES"),
)
cursor = db.connection.cursor()
try:
    primera_vez = db._anotar_migraciones(cursor)
    segunda_vez = db._anotar_migraciones(cursor)
    db.connection.commit()

    cursor.execute("SELECT COUNT(*) FROM prueba_pasos")
    veces = cursor.fetchone()[0]
    verificar("la primera migración lo aplica", primera_vez == [PASO], str(primera_vez))
    verificar("la segunda lo saltea", segunda_vez == [], str(segunda_vez))
    verificar("y su efecto quedó una sola vez en la base", veces == 1, f"{veces} fila(s)")
finally:
    database.PASOS_UNICOS = pasos_reales
    cursor.execute("DROP TABLE IF EXISTS prueba_pasos")
    cursor.execute(
        f"DELETE FROM {database.TABLA_MIGRACIONES} WHERE nombre = %s", (PASO,)
    )
    db.connection.commit()
    cursor.close()

db.cerrar()

print()
if fallos:
    print(f"OPERACIÓN: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("OPERACIÓN OK · el sistema deja rastro, se puede monitorear y dice su versión")
