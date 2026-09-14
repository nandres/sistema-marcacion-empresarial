from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth
import database

db = database.Database()
db.initialize()

if not db.get_user_by_username("admin"):
    auth.crear_primer_admin(db, "admin", "admin123", "Administrador del Sistema")

admin = db.get_user_by_username("admin")
if not db.get_user_by_username("juan"):
    auth.create_user(
        db, admin, "juan", "clave123", "Juan Pérez", "Empleado",
        2500000, "Funcionario",
    )

# El turno decide cuántas veces se puede marcar en el día, así que dejarlo
# indefinido hace que el resultado de una prueba dependa de lo que haya hecho
# la anterior. Los usuarios de la suite arrancan en el turno predeterminado y
# sin rotaciones pendientes.
for usuario in ("admin", "juan"):
    registro = db.get_user_by_username(usuario)
    db.asignar_turno_base(registro["id"], None)
    for asignacion in db.listar_asignaciones_turno(registro["id"]):
        db.eliminar_asignacion_turno(asignacion["id"])

print("SETUP CI OK: admin/admin123 y juan/clave123 listos")
db.cerrar()