"""El horario del pasado se mide con el horario que regía en el pasado.

Hasta acá la definición de un turno se editaba en el lugar: cambiar el horario
pisaba el anterior y no quedaba rastro de cuál había estado vigente. Los
marcajes ya liquidados conservaban sus números, así que de lejos parecía
inofensivo — pero todo lo que se **vuelve a calcular** cambiaba de resultado:

* corregir una marca de marzo la medía contra el horario de septiembre;
* el informe del mes pasado cambiaba solo cuando RRHH tocaba un turno;
* una justificación vieja reconocía las horas del horario nuevo.

Ahora ``turnos`` guarda la identidad —nombre, sucursal— y ``turno_versiones``
guarda la definición fechada. Resolver un turno pide una fecha, y esa fecha
elige la versión.

    python tests/test_turnos_historicos.py
"""

import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

import auth
import clock_engine
import turnos as turnos_dominio
from database import Database

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8000")
fallos = 0

NOMBRE = "Prueba Histórica"
NOMBRE_EMPLEADO = "hist_turno"


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
          + (f" | {detalle}" if detalle else ""))


def miercoles_cerca_de(referencia: date) -> date:
    """Un miércoles, para que ni domingo ni turno de fin de semana estorben."""
    return referencia - timedelta(days=(referencia.weekday() - 2) % 7)


def horario(turno) -> str:
    return " · ".join(
        f"{t['hora_entrada']:%H:%M}–{t['hora_salida']:%H:%M}"
        for t in turno["tramos"]
    )


db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

for viejo in db.listar_turnos(incluir_inactivos=True):
    if viejo["nombre"].startswith(NOMBRE):
        db._execute("DELETE FROM turnos WHERE empresa_id = %s AND id = %s",
                    (db.empresa, viejo["id"]))
        db.connection.commit()

HOY = date.today()
MARZO = miercoles_cerca_de(HOY - timedelta(days=60))
ABRIL = miercoles_cerca_de(HOY - timedelta(days=30))
PREHISTORIA = HOY - timedelta(days=400)

print("TURNOS CON HISTORIA")
print(f"  definición vieja desde {MARZO} · marca a corregir del {ABRIL}")

# ------------------------------------------------- 1. Editar abre una versión
print("\n1) Editar el horario abre una versión, no pisa la anterior")

turno_id = db.crear_turno(
    NOMBRE, [(time(8, 0), time(16, 0))], turnos_dominio.MASCARA_TODOS,
    "Casa Central", None, vigente_desde=MARZO, creado_por=admin["id"],
)
verificar("el turno nace con una versión",
          len(db.historial_turno(turno_id)) == 1)

auth.actualizar_turno(
    db, admin, turno_id,
    tramos=[{"entrada": "06:00", "salida": "13:00"}],
)
historial = db.historial_turno(turno_id)
verificar("editarlo hoy deja dos versiones", len(historial) == 2,
          str([v["vigente_desde"].isoformat() for v in historial]))
verificar("la más nueva rige desde hoy", historial[0]["vigente_desde"] == HOY,
          str(historial[0]["vigente_desde"]))
verificar("y la vieja conserva su fecha", historial[1]["vigente_desde"] == MARZO,
          str(historial[1]["vigente_desde"]))

# --------------------------------------------- 2. Cada fecha, su definición
print("\n2) El mismo turno resuelto en dos fechas da dos horarios")

en_abril = db.get_turno(turno_id, ABRIL)
en_hoy = db.get_turno(turno_id, HOY)
verificar("en abril rige el horario de marzo", horario(en_abril) == "08:00–16:00",
          horario(en_abril))
verificar("hoy rige el nuevo", horario(en_hoy) == "06:00–13:00", horario(en_hoy))
verificar("y cada uno dice desde cuándo",
          en_abril["vigente_desde"] == MARZO and en_hoy["vigente_desde"] == HOY)

antiguo = db.get_turno(turno_id, PREHISTORIA)
verificar("una fecha anterior a todo usa la definición más vieja conocida",
          horario(antiguo) == "08:00–16:00", horario(antiguo))

# --------------------------------------------- 3. Qué versiona y qué no
print("\n3) Se versiona el horario, no el nombre")

auth.actualizar_turno(db, admin, turno_id, nombre=f"{NOMBRE} renombrado")
verificar("renombrar no abre una versión", len(db.historial_turno(turno_id)) == 2,
          f"{len(db.historial_turno(turno_id))} versiones")
verificar("y el nombre nuevo vale también para el pasado",
          db.get_turno(turno_id, ABRIL)["nombre"] == f"{NOMBRE} renombrado")

auth.actualizar_turno(
    db, admin, turno_id, tramos=[{"entrada": "06:00", "salida": "13:00"}],
)
verificar("guardar el mismo horario tampoco abre una versión",
          len(db.historial_turno(turno_id)) == 2)

auth.actualizar_turno(
    db, admin, turno_id, tramos=[{"entrada": "07:00", "salida": "15:00"}],
)
verificar("dos cambios el mismo día son una sola versión",
          len(db.historial_turno(turno_id)) == 2,
          str([v["vigente_desde"].isoformat() for v in db.historial_turno(turno_id)]))
verificar("y queda el último", horario(db.get_turno(turno_id)) == "07:00–15:00",
          horario(db.get_turno(turno_id)))

# Se restaura el horario con el que siguen las comprobaciones de abajo.
auth.actualizar_turno(
    db, admin, turno_id, tramos=[{"entrada": "06:00", "salida": "13:00"}],
)

# ------------------------------------ 4. Lo que de verdad se recalculaba
print("\n4) Corregir una marca vieja la mide con el horario de entonces")

if not db.get_user_by_username(NOMBRE_EMPLEADO):
    auth.create_user(db, admin, NOMBRE_EMPLEADO, "clave123456",
                     "Empleado Histórico", "Empleado", 2500000)
empleado = db.get_user_by_username(NOMBRE_EMPLEADO)
db.asignar_turno_base(empleado["id"], turno_id)

resuelto_abril = turnos_dominio.resolver(db, empleado["id"], ABRIL)
resuelto_hoy = turnos_dominio.resolver(db, empleado["id"], HOY)
verificar("la resolución de abril entra a las 08:00",
          resuelto_abril.entrada_nominal == time(8, 0),
          str(resuelto_abril.entrada_nominal))
verificar("la de hoy entra a las 06:00",
          resuelto_hoy.entrada_nominal == time(6, 0),
          str(resuelto_hoy.entrada_nominal))

# 07:30 es media hora temprano contra el horario de marzo y hora y media tarde
# contra el de hoy: el mismo reloj, dos veredictos, y el correcto depende de
# la fecha de la marca.
viejo = clock_engine.evaluar_asistencia(
    db, empleado["id"], datetime.combine(ABRIL, time(7, 30))
)
nuevo = clock_engine.evaluar_asistencia(
    db, empleado["id"], datetime.combine(HOY, time(7, 30))
)
verificar("marcar 07:30 en abril es puntual", viejo["estado"] == "Normal",
          viejo["estado"])
verificar("marcar 07:30 hoy es tardanza", nuevo["estado"] != "Normal",
          nuevo["estado"])

# ------------------------------------ 5. Y lo que reconoce una justificación
print("\n5) Una justificación vieja reconoce las horas de entonces")

horas_abril = clock_engine.horas_previstas_legales(resuelto_abril, ABRIL)
horas_hoy = clock_engine.horas_previstas_legales(resuelto_hoy, HOY)
verificar("el turno de marzo rendía 8 horas", horas_abril == timedelta(hours=8),
          str(horas_abril))
verificar("el de hoy rinde 7", horas_hoy == timedelta(hours=7), str(horas_hoy))

# --------------------------------------------------- 6. Historial legible
print("\n6) El historial se puede mostrar y dice quién lo definió")

reporte = auth.historial_turno(db, admin, turno_id)
verificar("el historial trae las dos versiones",
          len(reporte["versiones"]) == 2, str(len(reporte["versiones"])))
verificar("la primera es la vigente",
          reporte["versiones"][0]["vigente_desde"] == HOY.isoformat(),
          reporte["versiones"][0]["vigente_desde"])
verificar("cada versión dice su horario",
          reporte["versiones"][1]["horario"].startswith("08:00"),
          reporte["versiones"][1]["horario"])
verificar("y quién la definió",
          reporte["versiones"][0]["definido_por"] == admin["full_name"],
          reporte["versiones"][0]["definido_por"])

# ------------------------------------------- 7. Corregir el pasado, a mano
print("\n7) Un horario cargado mal se puede corregir, con fecha explícita")

auth.actualizar_turno(
    db, admin, turno_id,
    tramos=[{"entrada": "09:00", "salida": "17:00"}],
    vigente_desde=MARZO.isoformat(),
)
verificar("no agrega una versión: reescribe la de esa fecha",
          len(db.historial_turno(turno_id)) == 2,
          str(len(db.historial_turno(turno_id))))
verificar("abril pasa a medirse con el horario corregido",
          horario(db.get_turno(turno_id, ABRIL)) == "09:00–17:00",
          horario(db.get_turno(turno_id, ABRIL)))
verificar("y hoy sigue con el suyo",
          horario(db.get_turno(turno_id, HOY)) == "06:00–13:00",
          horario(db.get_turno(turno_id, HOY)))

# ------------------------------------------------ 8. Aislamiento por cliente
print("\n8) La historia de un turno no cruza de empresa")

otra = db.get_empresa_por_slug("prueba-historial")
if otra is None:
    otra = db.crear_empresa("prueba-historial", "Prueba Historial S.A.")
propia = db.empresa_id
db.empresa_id = otra["id"]
verificar("otra empresa no ve la versión ajena",
          db.version_de_turno(turno_id, HOY) is None)
verificar("ni su historial", db.historial_turno(turno_id) == [])
db.empresa_id = propia

verificar("y la nuestra la sigue viendo",
          db.version_de_turno(turno_id, HOY) is not None)

# ------------------------------------------------- 9. El panel lo puede pedir
print("\n9) RRHH puede consultarlo desde el panel")

try:
    httpx.get(f"{BASE}/", timeout=10)
    hay_servidor = True
except Exception:
    hay_servidor = False
    print("  (sin servidor en WEB_BASE: se omite la comprobación por HTTP)")

if hay_servidor:
    with httpx.Client(base_url=BASE, timeout=30) as cliente:
        acceso = cliente.post("/api/login",
                              json={"cedula": "admin", "password": "admin123"})
        cabeceras = {"Authorization": "Bearer " + acceso.json()["token"]}
        respuesta = cliente.get(f"/api/panel/turnos/{turno_id}/historial",
                                headers=cabeceras)
        verificar("el historial se sirve por HTTP", respuesta.status_code == 200,
                  respuesta.text[:70])
        cuerpo = respuesta.json() if respuesta.status_code == 200 else {}
        verificar("y trae las dos versiones en orden",
                  len(cuerpo.get("versiones", [])) == 2,
                  str(len(cuerpo.get("versiones", []))))

        inexistente = cliente.get("/api/panel/turnos/999999/historial",
                                  headers=cabeceras)
        verificar("un turno que no existe da 404", inexistente.status_code == 404,
                  str(inexistente.status_code))

    # Cliente nuevo: el anterior guardó la cookie de sesión al iniciar, así
    # que preguntarle a él "¿y sin credenciales?" es preguntarle con ellas.
    with httpx.Client(base_url=BASE, timeout=30) as anonimo:
        sin_credenciales = anonimo.get(f"/api/panel/turnos/{turno_id}/historial")
        verificar("y sin credenciales no se sirve",
                  sin_credenciales.status_code in (401, 403),
                  str(sin_credenciales.status_code))

db.asignar_turno_base(empleado["id"], None)
db._execute("DELETE FROM turnos WHERE empresa_id = %s AND id = %s",
            (db.empresa, turno_id))
db.connection.commit()
db.cerrar()

print()
if fallos:
    print(f"TURNOS HISTÓRICOS: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("TURNOS HISTÓRICOS OK · el pasado se mide con el horario del pasado")
