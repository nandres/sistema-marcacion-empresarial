"""Validación de Art. 14: cuota 4 h/mes + máximo 3 usos/mes."""
import sys
from datetime import date, timedelta

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth
import reglamento
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")
previo = db.get_user_by_username("p3_2026_pas")
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, "p3_2026_pas", "clave123", "p3", "Empleado", 2500000, "Pasante")
user = db.get_user_by_id(db.get_user_by_username("p3_2026_pas")["id"])
hoy = date.today()

def estado():
    return {d["tipo"]: d for d in reglamento.disponibilidad_permisos(db, user, hoy)}

d = estado()
art14 = d["Salidas Personales"]
assert art14["cuota"] == 4 and art14["usos_max"] == 3, art14
print("OK Art. 14 en catalogo pasante: 4 h/mes, 3 usos/mes")

auth.crear_justificacion(db, admin, user["id"], "Salidas Personales", hoy, hoy, 1)
auth.crear_justificacion(db, admin, user["id"], "Salidas Personales", hoy, hoy, 1)
d2 = estado()
assert d2["Salidas Personales"]["usados"] == 2
assert d2["Salidas Personales"]["usos"] == 2
assert d2["Salidas Personales"]["restantes"] == 2
print("OK 2 usos / 2 h consumidas, quedan 2 h")

auth.crear_justificacion(db, admin, user["id"], "Salidas Personales", hoy, hoy, 1)
d3 = estado()
assert d3["Salidas Personales"]["usos"] == 3
assert d3["Salidas Personales"]["disponible"] is False, "3 usos deben agotar el articulo"
assert d3["Salidas Personales"]["restantes"] == 1, "aun queda 1 h pero se agoto por usos"
try:
    auth.crear_justificacion(db, admin, user["id"], "Salidas Personales", hoy, hoy, 0.5)
    raise SystemExit("FALLO: debio bloquear por usos_max")
except ValueError as e:
    mensaje = str(e)
    assert "usos" in mensaje, mensaje
print("OK 3.er uso bloqueado con mensaje:", mensaje)

d4 = estado()
assert d4["Salidas Personales"]["usos"] == 3, "no debe crearse la 4.ta justificacion"

auth.delete_user(db, admin, user["id"])
print("LIMPIEZA OK")
print("VALIDACION ART. 14 COMPLETA")