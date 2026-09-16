"""Validación integral del catálogo reglamentario, cuotas y fechas."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth
import reglamento
import reports
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")
assert admin, "admin no existe"

def nuevo_usuario(nombre, vinculo):
    previo = db.get_user_by_username(nombre)
    if previo:
        auth.delete_user(db, admin, previo["id"])
    user_id = auth.create_user(
        db, admin, nombre, "clave123", nombre, "Empleado", 2500000, vinculo
    )
    return db.get_user_by_id(user_id)

def disponibilidad(user):
    return {d["tipo"]: d for d in reglamento.disponibilidad_permisos(db, user)}

# --- Migración: columna horas_usadas ---
columnas = db.list_justificaciones()
hoy = date.today()

p1 = nuevo_usuario("p1_2026_func", "Funcionario")
p2 = nuevo_usuario("p2_2026_pas", "Pasante")
print("creados:", p1["username"], p2["username"])

# --- Disponibilidad inicial ---
d1 = disponibilidad(p1)
assert d1["Salidas Personales"]["cuota"] == 6, d1["Salidas Personales"]
assert d1["Vacaciones"]["cuota"] == 12, d1["Vacaciones"]
assert d1["Vacaciones"]["disponible"]
d2 = disponibilidad(p2)
assert d2["Licencia de Pasante"]["cuota"] == 10
assert d2["Salidas Salud"]["cuota"] == 6
assert d2["Horas No Remuneradas"]["cuota"] == 2
assert d2["Omision de Registro"]["cuota"] == 3
print("OK disponibilidad inicial")

# --- Horas: 2.5 + 3.5 = 6.0 y luego bloqueo ---
auth.crear_justificacion(db, admin, p2["id"], "Salidas Salud", hoy, hoy, 2.5)
d2b = disponibilidad(p2)
assert d2b["Salidas Salud"]["usados"] == 2.5
assert d2b["Salidas Salud"]["restantes"] == 3.5
auth.crear_justificacion(db, admin, p2["id"], "Salidas Salud", hoy, hoy, 3.5)
d2c = disponibilidad(p2)
assert d2c["Salidas Salud"]["usados"] == 6.0
assert not d2c["Salidas Salud"]["disponible"]
try:
    auth.crear_justificacion(db, admin, p2["id"], "Salidas Salud", hoy, hoy, 0.5)
    raise SystemExit("FALLO: debió bloquear cuota agotada")
except ValueError as e:
    assert "Cuota agotada" in str(e)
print("OK cuotas de horas mensuales")

# --- Fechas: fin > hoy bloqueado ---
try:
    auth.crear_justificacion(db, admin, p2["id"], "Matrimonio", hoy, hoy + timedelta(days=2))
    raise SystemExit("FALLO: debió bloquear fecha futura")
except ValueError as e:
    assert "no puede superar" in str(e)
print("OK fecha futura bloqueada")

# --- Artículo no aplica al vínculo (Vacaciones es solo de funcionarios) ---
try:
    auth.crear_justificacion(db, admin, p2["id"], "Vacaciones", hoy, hoy)
    raise SystemExit("FALLO: debió rechazar artículo de funcionario")
except ValueError as e:
    assert "no aplica" in str(e)
print("OK artículo por vínculo")

# --- Días: licencia de pasante y conteo anual ---
anio_pasado = hoy.replace(year=hoy.year - 1)
auth.crear_justificacion(db, admin, p2["id"], "Licencia de Pasante",
                         anio_pasado, anio_pasado + timedelta(days=1))
d2d = disponibilidad(p2)
assert d2d["Licencia de Pasante"]["usados"] == 0, "no debe contar del año pasado"

# El Art. 23 se cuenta en días HÁBILES, así que el rango se ancla a un lunes:
# tomado de otra forma, el resultado dependería del día en que corra la prueba.
lunes = hoy - timedelta(days=7)
while lunes.weekday() != 0:
    lunes -= timedelta(days=1)
auth.crear_justificacion(db, admin, p2["id"], "Licencia de Pasante",
                         lunes, lunes + timedelta(days=1))
d2e = disponibilidad(p2)
assert d2e["Licencia de Pasante"]["usados"] == 2, d2e["Licencia de Pasante"]["usados"]
assert d2e["Licencia de Pasante"]["restantes"] == 8
print("OK conteo de días por período")

# Un fin de semana no consume cuota de un artículo definido en días hábiles.
sabado = lunes + timedelta(days=5)
auth.crear_justificacion(db, admin, p2["id"], "Licencia de Pasante",
                         sabado, sabado + timedelta(days=1))
d2f = disponibilidad(p2)
assert d2f["Licencia de Pasante"]["usados"] == 2, d2f["Licencia de Pasante"]["usados"]
print("OK sábado y domingo no consumen días hábiles (P3-7)")

# Y un permiso a caballo entre dos años le imputa a cada uno los suyos.
fin_de_anio = date(hoy.year - 1, 12, 28)
auth.crear_justificacion(db, admin, p1["id"], "Motivos Particulares",
                         fin_de_anio, date(hoy.year, 1, 3))
d1a = disponibilidad(p1)
assert d1a["Motivos Particulares"]["usados"] == 3, d1a["Motivos Particulares"]["usados"]
print("OK el permiso entre dos años imputa a cada uno lo suyo (P3-8)")

# P1-6: el rango pedido se valida contra lo que queda, no solo las horas.
try:
    auth.crear_justificacion(db, admin, p1["id"], "Motivos Particulares",
                             hoy - timedelta(days=60), hoy - timedelta(days=1))
    raise SystemExit("FALLO: emitió 60 días contra una cuota de 5")
except ValueError as e:
    assert "quedan" in str(e), str(e)
print("OK el rango en días se valida contra la cuota (P1-6)")

# --- Funcionario: vacaciones dinámicas + permisos del mes ---
auth.crear_justificacion(db, admin, p1["id"], "Salidas Personales", hoy, hoy, 2)
d1b = disponibilidad(p1)
assert d1b["Salidas Personales"]["usados"] == 2
assert d1b["Vacaciones"]["cuota"] == 12
resumen = reports.resumen_empleado(db, p1, hoy)
assert resumen["vacaciones"]["devengadas"] == 12
assert "disponibilidad" in resumen and len(resumen["disponibilidad"]) == 15
resumen2 = reports.resumen_empleado(db, p2, hoy)
assert resumen2["vacaciones"]["devengadas"] == 10
assert resumen2["vacaciones"]["usadas"] == 2
print("OK resumen_empleado por vínculo")

# --- resumen_historico: hasta <= hoy y rango enero-cualquier-año ---
try:
    reports.resumen_historico(db, p2, date(2020, 1, 1), hoy + timedelta(days=3))
    raise SystemExit("FALLO: debió bloquear hasta futura")
except ValueError as e:
    assert "no puede superar" in str(e)
hist = reports.resumen_historico(db, p2, date(2020, 1, 1), hoy)
assert hist["desde"] == "2020-01-01" and hist["hasta"] == hoy.isoformat()
print("OK resumen_historico enero-2020 -> hoy")

# --- Limpieza ---
auth.delete_user(db, admin, p2["id"])
auth.delete_user(db, admin, p1["id"])
print("LIMPIEZA OK")
print("TODAS LAS VALIDACIONES PASARON")
