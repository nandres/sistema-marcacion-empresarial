"""P1-1: la tolerancia climática la declara la empresa, no quien llega tarde.

El hallazgo era que ``es_dia_lluvioso`` viajaba como una casilla del kiosco
marcada por el propio empleado: el sujeto de la regla controlaba el dato que
la activaba, así que la regla no existía. Ahora la condición vive en
``condiciones_dia``, la firma Recursos Humanos y alcanza a toda la plantilla.

Esta prueba fija las dos mitades: que la tolerancia declarada funciona, y
que ya no hay ninguna vía para que el empleado se la conceda solo.
"""

import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
import web_server
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

USUARIO = "p1_1_clima"
previo = db.get_user_by_username(USUARIO)
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, USUARIO, "clave123", "Clima Prueba", "Empleado", 2500000, "Funcionario")
empleado = db.get_user_by_username(USUARIO)

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


# El día de prueba es pasado para no chocar con marcas reales del kiosco.
dia = datetime.now().date() - timedelta(days=3)
inicio = datetime.combine(dia, clock_engine.INICIO_JORNADA)
llegada = inicio + timedelta(minutes=25)

db.borrar_condicion_dia(dia)

# 1) Sin declaración rige la tolerancia ordinaria del funcionario (15 min).
evaluacion = clock_engine.evaluar_asistencia(db, empleado["id"], llegada)
verificar("sin declaración, 25 min de retraso es tardanza",
          evaluacion["estado"] == "Llegada Tardía", evaluacion["detalle"])
verificar("y el día figura como normal", evaluacion["condicion_dia"] == "")

# 2) Recursos Humanos declara la condición para toda la plantilla.
auth.declarar_condicion_dia(db, admin, dia, "Lluvia intensa", 30, "Tormenta matinal")
evaluacion = clock_engine.evaluar_asistencia(db, empleado["id"], llegada)
verificar("con lluvia declarada, la misma llegada es normal",
          evaluacion["estado"] == "Normal", evaluacion["detalle"])
verificar("la marca hereda el nombre de la condición",
          evaluacion["condicion_dia"] == "Lluvia intensa")
verificar("la tolerancia efectiva es 15 + 30",
          evaluacion["tolerancia_efectiva_min"] == 45,
          str(evaluacion["tolerancia_efectiva_min"]))

# 3) La declaración queda firmada y es auditable.
declarada = db.get_condicion_dia(dia)
verificar("queda registrado quién la firmó",
          declarada["declarado_por"] == admin["id"])

# 4) Revocarla restituye el veredicto original.
verificar("se puede revocar", db.borrar_condicion_dia(dia))
evaluacion = clock_engine.evaluar_asistencia(db, empleado["id"], llegada)
verificar("revocada, vuelve a ser tardanza",
          evaluacion["estado"] == "Llegada Tardía")

# 5) Un empleado no puede declararla.
try:
    auth.declarar_condicion_dia(db, empleado, dia, "Lluvia intensa", 30)
    verificar("un Empleado no puede declarar la condición", False)
except PermissionError as error:
    verificar("un Empleado no puede declarar la condición", True, str(error))

# 6) Tope: nadie puede declarar una tolerancia que anule el control.
try:
    auth.declarar_condicion_dia(db, admin, dia, "Lluvia intensa", 600)
    verificar("la tolerancia tiene techo", False)
except ValueError as error:
    verificar("la tolerancia tiene techo", True, str(error))

# 7) No queda ninguna vía para que el dato lo aporte quien marca.
firma_evaluar = inspect.signature(clock_engine.evaluar_asistencia).parameters
verificar("evaluar_asistencia ya no acepta es_dia_lluvioso",
          "es_dia_lluvioso" not in firma_evaluar, ", ".join(firma_evaluar))

firma_marcar = inspect.signature(clock_engine.ClockEngine.registrar_asistencia).parameters
verificar("registrar_asistencia tampoco lo acepta",
          "es_dia_lluvioso" not in firma_marcar, ", ".join(firma_marcar))

verificar("el cuerpo de /api/marcar no expone el campo",
          "es_dia_lluvioso" not in web_server.MarcarRequest.model_fields,
          ", ".join(web_server.MarcarRequest.model_fields))

interfaz = (RAIZ / "src" / "static").glob("*.*")
rastro = [
    f.name for f in interfaz
    if "es_dia_lluvioso" in f.read_text(encoding="utf-8")
    or "k-lluvia" in f.read_text(encoding="utf-8")
]
verificar("la interfaz web no volvió a traer la casilla", not rastro, ", ".join(rastro))

gui = (RAIZ / "src" / "gui.py").read_text(encoding="utf-8")
verificar("el kiosco de escritorio tampoco la tiene",
          "dia_lluvioso" not in gui)

db.borrar_condicion_dia(dia)
auth.delete_user(db, admin, empleado["id"])
db.cerrar()

print()
if fallos:
    print(f"CONDICIÓN DEL DÍA: {fallos} problema(s)")
    raise SystemExit(1)
print("CONDICIÓN DEL DÍA OK · P1-1 cerrado en motor, API e interfaces")
