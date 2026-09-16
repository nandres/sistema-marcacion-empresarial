import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth
import database

db = database.Database()
db.initialize()

if not db.usuario_por_cedula("admin"):
    auth.crear_primer_admin(db, "admin", "admin123", "Administrador del Sistema")

admin = db.usuario_por_cedula("admin")
if not db.usuario_por_cedula("juan"):
    auth.crear_usuario(
        db, admin, "juan", "clave123", "Juan Pérez", "Empleado",
        2500000, "Funcionario",
    )

# El turno decide cuántas veces se puede marcar en el día, así que dejarlo
# indefinido hace que el resultado de una prueba dependa de lo que haya hecho
# la anterior. Los usuarios de la suite arrancan en el turno predeterminado y
# sin rotaciones pendientes.
for usuario in ("admin", "juan"):
    registro = db.usuario_por_cedula(usuario)
    db.asignar_turno_base(registro["id"], None)
    for asignacion in db.listar_asignaciones_turno(registro["id"]):
        db.eliminar_asignacion_turno(asignacion["id"])

print("SETUP CI OK: admin/admin123 y juan/clave123 listos")
db.cerrar()
