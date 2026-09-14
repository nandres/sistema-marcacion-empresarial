"""La planilla de horas extraordinarias se compone sola desde los marcajes.

Antes había que llenarla a mano a partir de los reportes, que es donde se
cuelan los errores de transcripción y donde el empleador pierde la prueba
de lo que liquidó. El sistema ya tiene cada hora clasificada por el motor:
la planilla es una lectura de esa tabla, no un formulario.

Verifica que solo aparezcan los días con recargo, que los totales cierren
contra el detalle y que la liquidación aplique los multiplicadores de los
artículos 232, 233 y 234.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
import reports
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

USUARIO = "planilla_extras"
SALARIO = 3_200_000
previo = db.get_user_by_username(USUARIO)
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, USUARIO, "clave123", "Extra Prueba", "Empleado",
                 SALARIO, "Funcionario")
empleado = db.get_user_by_username(USUARIO)

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


def marcar(dia, entrada_h, salida_h) -> None:
    """Registra un turno completo liquidado por el motor real."""
    entrada = datetime.combine(dia, datetime.min.time()).replace(
        hour=entrada_h
    ).astimezone()
    salida = entrada + timedelta(hours=salida_h - entrada_h)
    if salida_h <= entrada_h:
        salida = salida + timedelta(days=1)
    entry_id = db.open_clock_in(empleado["id"], entrada, False)
    desglose = clock_engine.calcular_horas_paraguay(entrada, salida)
    clock_engine.persistir_desglose(db, entry_id, salida, desglose, "")


hoy = datetime.now().date()
primero = hoy.replace(day=1)

# Tres turnos de perfil distinto dentro del mes en curso:
#  - uno normal de 8 h, que no debe aparecer en la planilla de extras;
#  - uno diurno de 10 h, que deja 2 h al 50 %;
#  - uno que cruza la medianoche, que deja nocturnas y extras al 100 %.
db.limpiar_marcajes_prueba(empleado["id"], primero, primero + timedelta(days=20))
marcar(primero + timedelta(days=1), 8, 16)
marcar(primero + timedelta(days=2), 8, 18)
marcar(primero + timedelta(days=3), 20, 6)

datos = reports.planilla_horas_extra(db, empleado, hoy.year, hoy.month)

verificar("el turno de 8 h no entra en la planilla de extras",
          len(datos["filas"]) == 2, f"{len(datos['filas'])} filas")

suma_50 = sum(f["extra_50"] for f in datos["filas"])
suma_100 = sum(f["extra_100"] for f in datos["filas"])
suma_noct = sum(f["nocturnas"] for f in datos["filas"])
verificar("el total de extras al 50 % cierra contra el detalle",
          abs(datos["total_50"] - suma_50) < 0.01, f"{datos['total_50']:g} h")
verificar("el total al 100 % cierra contra el detalle",
          abs(datos["total_100"] - suma_100) < 0.01, f"{datos['total_100']:g} h")
verificar("el total de nocturnas cierra contra el detalle",
          abs(datos["total_nocturnas"] - suma_noct) < 0.01,
          f"{datos['total_nocturnas']:g} h")

verificar("el turno de 10 h deja 2 h al 50 % (Art. 234)",
          abs(datos["total_50"] - 2) < 0.01, f"{datos['total_50']:g} h")
verificar("el turno que cruza la medianoche deja nocturnas (Art. 232)",
          datos["total_nocturnas"] > 0, f"{datos['total_nocturnas']:g} h")

valor_hora = SALARIO / reports.HORAS_BASE_MENSUAL
verificar("el valor hora sale del salario del legajo",
          abs(datos["valor_hora"] - valor_hora) < 0.01, f"Gs. {datos['valor_hora']:,.0f}")
verificar("las extras diurnas se liquidan al 150 % (Art. 234)",
          abs(datos["importe_50"] - datos["total_50"] * valor_hora * 1.5) < 1,
          f"Gs. {datos['importe_50']:,.0f}")
verificar("las del 100 % se liquidan al doble (Art. 233)",
          abs(datos["importe_100"] - datos["total_100"] * valor_hora * 2.0) < 1,
          f"Gs. {datos['importe_100']:,.0f}")
verificar("el nocturno liquida solo el recargo del 30 % (Art. 232)",
          abs(datos["importe_nocturno"] - datos["total_nocturnas"] * valor_hora * 0.3) < 1,
          f"Gs. {datos['importe_nocturno']:,.0f}")

ruta = Path(reports.generar_pdf_horas_extra(db, empleado, hoy.year, hoy.month))
verificar("el PDF se emite sin ningún campo a completar",
          ruta.exists() and ruta.stat().st_size > 1500,
          f"{ruta.name} · {ruta.stat().st_size} bytes")

# Un mes sin extras produce igual un documento válido, que es lo que hay que
# archivar para dejar constancia de que no hubo horas extraordinarias.
vacio = Path(reports.generar_pdf_horas_extra(db, empleado, 2020, 1))
verificar("un mes sin extras también emite su constancia",
          vacio.exists() and vacio.stat().st_size > 1000,
          f"{vacio.name} · {vacio.stat().st_size} bytes")

# La constancia de asistencia es el otro papel que se pedía en ventanilla.
constancia = Path(reports.generar_pdf_constancia(
    db, empleado, primero, primero + timedelta(days=20)
))
verificar("la constancia de asistencia se emite sola",
          constancia.exists() and constancia.stat().st_size > 1500,
          f"{constancia.name} · {constancia.stat().st_size} bytes")
try:
    reports.generar_pdf_constancia(db, empleado, primero + timedelta(days=5), primero)
    verificar("la constancia rechaza un rango invertido", False)
except ValueError as error:
    verificar("la constancia rechaza un rango invertido", True, str(error))

db.limpiar_marcajes_prueba(empleado["id"], primero, primero + timedelta(days=20))
auth.delete_user(db, admin, empleado["id"])
db.cerrar()

print()
if fallos:
    print(f"PLANILLA DE EXTRAS: {fallos} problema(s)")
    raise SystemExit(1)
print("PLANILLA DE EXTRAS OK · detalle, totales y liquidación verificados")
