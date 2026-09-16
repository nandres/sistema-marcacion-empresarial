"""P2-2 y P2-6: los dos casos que dejaban al empleado sin poder marcar.

P2-2 · El botón maestro decidía por calendario. Un turno que entra el lunes
22:00 y sale el martes 06:00 no tiene marcajes con fecha de martes, así que
la función respondía ENTRADA, `clock_in` encontraba la entrada abierta y
abortaba: el empleado no podía marcar ni la salida ni una entrada nueva.

P2-6 · Una entrada sin cierre bloqueaba **todas** las marcaciones futuras de
esa persona, para siempre, sin auto-cierre ni escalamiento.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
from clock_engine import ClockEngine
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

USUARIO = "turno_nocturno"
previo = db.get_user_by_username(USUARIO)
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, USUARIO, "clave123", "Noche Prueba", "Empleado",
                 2500000, "Funcionario")
empleado = db.get_user_by_username(USUARIO)
motor = ClockEngine(db, empleado)

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


def limpiar() -> None:
    hoy = datetime.now().date()
    db.limpiar_marcajes_prueba(empleado["id"], hoy - timedelta(days=40),
                               hoy + timedelta(days=1))


# --- P2-2 · turno que cruza la medianoche ---------------------------------
limpiar()
ayer = clock_engine.ahora_local() - timedelta(hours=8)
entrada_id = db.open_clock_in(empleado["id"], ayer, False)
verificar("entrada de anoche registrada", entrada_id is not None)

verificar("el turno que cruza la medianoche resuelve SALIDA",
          motor.detectar_accion_hoy() == "SALIDA", motor.detectar_accion_hoy())

entry_id, momento, tipo = motor.registrar_asistencia()
verificar("y la salida se registra de verdad", tipo == "SALIDA",
          f"marcaje #{entry_id} a las {momento.strftime('%H:%M')}")

cerrado = next(m for m in db.get_all_entries(empleado["id"]) if m["id"] == entrada_id)
verificar("el marcaje quedó cerrado con su desglose",
          cerrado["hora_salida"] is not None
          and cerrado["horas_ordinarias"].total_seconds() > 0,
          f"{cerrado['horas_ordinarias']} ordinarias")

# --- P2-6 · entrada que nadie cerró ---------------------------------------
limpiar()
vieja = clock_engine.ahora_local() - timedelta(hours=30)
olvidada = db.open_clock_in(empleado["id"], vieja, False)
verificar("entrada de hace 30 horas registrada", olvidada is not None)
verificar("supera el máximo de jornada abierta",
          timedelta(hours=30) > clock_engine.MAX_JORNADA_ABIERTA,
          f"máximo {clock_engine.MAX_JORNADA_ABIERTA}")

verificar("el empleado no queda atrapado: puede volver a entrar",
          motor.detectar_accion_hoy() == "ENTRADA")

abandonada = next(m for m in db.get_all_entries(empleado["id"]) if m["id"] == olvidada)
verificar("la entrada olvidada queda marcada como abandonada",
          abandonada["abandonado"] is True)
verificar("con su incidencia, para que RRHH la corrija",
          abandonada["tipo_incidencia"] == clock_engine.INCIDENCIA_SIN_CIERRE,
          abandonada["tipo_incidencia"])
verificar("y sin hora de salida inventada", abandonada["hora_salida"] is None)

bandeja = [m for m in db.listar_marcajes_abandonados() if m["id"] == olvidada]
verificar("aparece en la bandeja de jornadas sin cierre", len(bandeja) == 1)

alertas = [a for a in db.listar_alertas(limite=20) if a["tipo"] == "jornada_sin_cierre"]
verificar("se avisó a Recursos Humanos", bool(alertas),
          alertas[0]["mensaje"] if alertas else "")

entry_id, _, tipo = motor.registrar_asistencia()
verificar("la marcación nueva entra sin problemas", tipo == "ENTRADA", f"#{entry_id}")

# --- Varias entradas vencidas se liberan todas de una vez ------------------
# Liberar solo la última dejaba al empleado igual de trabado en la marcación
# siguiente: es lo que hizo fallar al kiosco con una cola acumulada.
limpiar()
viejas = [
    db.open_clock_in(empleado["id"], clock_engine.ahora_local() - timedelta(days=d), False)
    for d in (5, 3, 2)
]
verificar("tres entradas vencidas sembradas", all(viejas))
verificar("una sola marcación las libera a todas",
          motor.detectar_accion_hoy() == "ENTRADA")
pendientes = [
    m for m in db.get_all_entries(empleado["id"])
    if m["id"] in viejas and not m["abandonado"]
]
verificar("no queda ninguna sin descartar", not pendientes,
          f"{len(pendientes)} sin descartar")

# --- Una jornada abierta pero reciente NO se toca --------------------------
limpiar()
reciente = clock_engine.ahora_local() - timedelta(hours=3)
en_curso = db.open_clock_in(empleado["id"], reciente, False)
verificar("una jornada de 3 horas sigue en curso",
          motor.detectar_accion_hoy() == "SALIDA")
vigente = next(m for m in db.get_all_entries(empleado["id"]) if m["id"] == en_curso)
verificar("y no se marca como abandonada", vigente["abandonado"] is False)

limpiar()
auth.delete_user(db, admin, empleado["id"])
db.cerrar()

print()
if fallos:
    print(f"TURNO NOCTURNO: {fallos} problema(s)")
    raise SystemExit(1)
print("TURNO NOCTURNO OK · P2-2 y P2-6 cerrados")
