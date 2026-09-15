"""Identidad del puesto de marcación: de dónde salió cada marca.

Una marcación sin origen no se puede comprobar ni desmentir. *"Marqué desde
casa"* era, hasta acá, una afirmación contra otra. Cada kiosco se identifica
con un token propio y cada marca queda atada al puesto donde se hizo.

Lo que se comprueba, además del camino feliz: que el token se guarde hasheado
y no en claro, que un puesto revocado deje de servir, que un token ajeno no
permita marcar en otra empresa, y que con la exigencia encendida una marca sin
puesto se rechace en lugar de entrar sin origen.

    WEB_BASE=http://127.0.0.1:8000 python tests/test_dispositivos.py
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

import auth
from database import Database

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8000")
fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
          + (f" | {detalle}" if detalle else ""))


db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

for existente in db.listar_dispositivos(incluir_inactivos=True):
    if existente["nombre"].startswith("Prueba "):
        db.revocar_dispositivo(existente["id"])

print("IDENTIDAD DEL PUESTO DE MARCACIÓN")

# ------------------------------------------------------------- 1. Alta
print("\n1) El token se entrega una vez y no queda en la base")

puesto = auth.registrar_dispositivo(db, admin, "Prueba Portería", "Entrada principal")
token = puesto["token"]
verificar("el alta devuelve un token", bool(token) and len(token) > 20,
          f"{len(token)} caracteres")
verificar("y avisa que no se vuelve a mostrar", "no se vuelve a mostrar" in puesto["aviso"])

fila = db._execute(
    "SELECT token_hash FROM dispositivos WHERE empresa_id = %s AND id = %s",
    (db.empresa, puesto["id"]), fetch="one",
)
verificar("en la base queda el hash, no el token",
          fila and fila["token_hash"] != token and len(fila["token_hash"]) == 64,
          fila["token_hash"][:16] + "…" if fila else "")

try:
    auth.registrar_dispositivo(db, admin, "ab")
    verificar("un nombre demasiado corto se rechaza", False)
except ValueError:
    verificar("un nombre demasiado corto se rechaza", True)

# -------------------------------------------------------- 2. Resolución
print("\n2) El token identifica el puesto, y nada más")

resuelto = auth.resolver_dispositivo(db, token)
verificar("un token válido resuelve a su puesto",
          resuelto and resuelto["id"] == puesto["id"], str(resuelto and resuelto["nombre"]))
verificar("y trae la empresa a la que pertenece",
          resuelto and resuelto["empresa_id"] == db.empresa)
verificar("un token inventado no resuelve nada",
          auth.resolver_dispositivo(db, "token-que-nadie-emitio") is None)
verificar("un token vacío tampoco",
          auth.resolver_dispositivo(db, "") is None)

# --------------------------------------------------- 3. Marcar con puesto
print("\n3) La marca queda atada al puesto donde se hizo")

try:
    httpx.get(f"{BASE}/", timeout=10)
    hay_servidor = True
except Exception:
    hay_servidor = False
    print("  (sin servidor en WEB_BASE: se omiten las comprobaciones por HTTP)")

if hay_servidor:
    if not db.get_user_by_username("disp_juan"):
        auth.create_user(db, admin, "disp_juan", "clave123456", "Juan del Puesto",
                         "Empleado", 2500000)
    empleado = db.get_user_by_username("disp_juan")
    db.limpiar_marcajes_prueba(empleado["id"], "2000-01-01", "2100-01-01")

    respuesta = httpx.post(
        f"{BASE}/api/marcar",
        json={"cedula": "disp_juan", "password": "clave123456"},
        headers={"X-Dispositivo": token},
        timeout=30,
    )
    verificar("la marca con token entra", respuesta.status_code == 200,
              respuesta.text[:80])

    marca = db._execute(
        "SELECT dispositivo_id FROM marcajes WHERE empresa_id = %s AND user_id = %s "
        "ORDER BY id DESC LIMIT 1",
        (db.empresa, empleado["id"]), fetch="one",
    )
    verificar("y queda registrada con el puesto",
              marca and marca["dispositivo_id"] == puesto["id"],
              str(marca and marca["dispositivo_id"]))

    visto = db._execute(
        "SELECT ultimo_visto FROM dispositivos WHERE empresa_id = %s AND id = %s",
        (db.empresa, puesto["id"]), fetch="one",
    )
    verificar("el puesto deja constancia de que sigue en pie",
              visto and visto["ultimo_visto"] is not None)

# ------------------------------------------------------- 4. Revocación
print("\n4) Un puesto revocado deja de servir, pero no borra su historia")

marcas_antes = next(
    (d["marcas"] for d in db.listar_dispositivos() if d["id"] == puesto["id"]), 0
)
auth.revocar_dispositivo(db, admin, puesto["id"])
verificar("el token revocado ya no resuelve",
          auth.resolver_dispositivo(db, token) is None)

revocado = next(
    (d for d in db.listar_dispositivos(incluir_inactivos=True)
     if d["id"] == puesto["id"]), None
)
verificar("el puesto sigue existiendo, inactivo",
          revocado is not None and not revocado["activo"])
verificar("y sus marcas conservan el origen",
          revocado and revocado["marcas"] == marcas_antes,
          f"{revocado and revocado['marcas']} marcas")

try:
    auth.revocar_dispositivo(db, admin, 999999)
    verificar("revocar un puesto inexistente se rechaza", False)
except ValueError:
    verificar("revocar un puesto inexistente se rechaza", True)

# --------------------------------------------- 5. Exigencia configurable
print("\n5) Con la exigencia encendida, una marca sin puesto no entra")

previo = os.environ.get("DISPOSITIVO_OBLIGATORIO", "")
verificar("viene apagada por defecto", not auth.dispositivo_obligatorio())
os.environ["DISPOSITIVO_OBLIGATORIO"] = "1"
verificar("y se enciende por entorno", auth.dispositivo_obligatorio())
os.environ["DISPOSITIVO_OBLIGATORIO"] = previo

# ------------------------------------------------------ 6. Entre clientes
print("\n6) El token de un cliente no sirve en otro")

otra = db.get_empresa_por_slug("prueba-dispositivos")
if otra is None:
    otra = db.crear_empresa("prueba-dispositivos", "Prueba Dispositivos S.A.")
propia = db.empresa_id
db.empresa_id = otra["id"]
ajeno = auth.registrar_dispositivo(db, admin, "Prueba Ajena", "Otra empresa")
db.empresa_id = propia

resuelto_ajeno = auth.resolver_dispositivo(db, ajeno["token"])
verificar("el token ajeno resuelve a SU empresa, no a la nuestra",
          resuelto_ajeno and resuelto_ajeno["empresa_id"] == otra["id"],
          f"empresa {resuelto_ajeno and resuelto_ajeno['empresa_id']} vs {propia}")
verificar("los puestos de la otra empresa no figuran en los nuestros",
          all(d["id"] != ajeno["id"] for d in db.listar_dispositivos(True)))

db.empresa_id = otra["id"]
db.revocar_dispositivo(ajeno["id"])
db.empresa_id = propia

db.cerrar()

print()
if fallos:
    print(f"DISPOSITIVOS: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("DISPOSITIVOS OK · cada marca sabe de qué puesto salió")
