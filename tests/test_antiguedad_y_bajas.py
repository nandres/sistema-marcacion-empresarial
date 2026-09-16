"""P3-6: la antigüedad se contaba desde el alta en el sistema, no desde el contrato.

De la antigüedad dependen los días de vacaciones (12 / 20 / 30 según la
escala de la Ley 1626/00) y los meses de aguinaldo. Calcularla desde
``created_at`` significaba que al migrar la plantilla un empleado con diez
años de servicio pasaba a tener cero: 12 días en lugar de 30.

Cubre también la baja lógica, que faltaba por completo: solo existía el
borrado, y borrar a quien se va destruye el respaldo de las liquidaciones
que ya se le pagaron.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import reglamento
import reports
from database import Database

db = Database()
db.initialize()
admin = db.usuario_por_cedula("admin")

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


def alta(usuario: str, ingreso, nombre: str = "Prueba"):
    previo = db.usuario_por_cedula(usuario)
    if previo:
        auth.eliminar_usuario(db, admin, previo["id"])
    auth.crear_usuario(db, admin, usuario, "clave123", nombre, "Empleado",
                     2400000, "Funcionario", fecha_ingreso=ingreso)
    return db.usuario_por_cedula(usuario)


def vacaciones(user) -> float:
    return next(
        d for d in reglamento.disponibilidad_permisos(db, user)
        if d["tipo"] == "Vacaciones"
    )["cuota"]


hoy = date.today()

# --- Escala de vacaciones por antigüedad real ------------------------------
casos = [
    ("ant_nuevo", hoy - timedelta(days=200), 12, "menos de 5 años"),
    ("ant_medio", hoy - timedelta(days=int(365.25 * 7)), 20, "entre 5 y 10 años"),
    ("ant_veterano", hoy - timedelta(days=int(365.25 * 12)), 30, "más de 10 años"),
]
for usuario, ingreso, esperado, tramo in casos:
    empleado = alta(usuario, ingreso, f"Empleado {tramo}")
    obtenido = vacaciones(empleado)
    verificar(f"{tramo} → {esperado} días de vacaciones", obtenido == esperado,
              f"ingreso {ingreso} · cuota {obtenido}")

# El caso que motivó el hallazgo: alta de hoy en el sistema, contrato viejo.
veterano = db.usuario_por_cedula("ant_veterano")
verificar("el alta en el sistema es hoy", veterano["created_at"].date() == hoy)
verificar("pero la antigüedad sale del contrato",
          reglamento.antiguedad_anios(veterano) > 11,
          f"{reglamento.antiguedad_anios(veterano):.1f} años")
verificar("y por eso le corresponden 30 y no 12 días",
          vacaciones(veterano) == 30)

# Sin fecha de ingreso cargada se cae a created_at, sin romperse.
sin_fecha = dict(veterano)
sin_fecha["fecha_ingreso"] = None
verificar("un legajo sin fecha de ingreso usa el alta como respaldo",
          reglamento.fecha_ingreso(sin_fecha) == veterano["created_at"].date())

# --- El aguinaldo cuenta meses desde el ingreso ----------------------------
aguinaldo = reports.aguinaldo_periodo(db, veterano, date(hoy.year, 1, 1), hoy)
verificar("el aguinaldo cuenta los meses del año, no desde el alta",
          aguinaldo["meses_periodo"] == hoy.month,
          f"{aguinaldo['meses_periodo']} meses")

reciente = alta("ant_reciente", date(hoy.year, hoy.month, 1), "Ingreso de este mes")
aguinaldo_corto = reports.aguinaldo_periodo(db, reciente, date(hoy.year, 1, 1), hoy)
verificar("quien ingresó este mes devenga un solo mes",
          aguinaldo_corto["meses_periodo"] == 1,
          f"{aguinaldo_corto['meses_periodo']} meses")

# --- Baja lógica ------------------------------------------------------------
baja = auth.dar_de_baja(db, admin, reciente["id"])
verificar("la baja se registra con su fecha", baja["fecha_baja"] == hoy)

dado_de_baja = db.usuario_por_id(reciente["id"])
verificar("el legajo sigue existiendo", dado_de_baja is not None)
verificar("marcado como inactivo", dado_de_baja["activo"] is False)

activos = [u["id"] for u in db.listar_usuarios()]
todos = [u["id"] for u in db.listar_usuarios(incluir_bajas=True)]
verificar("sale de la nómina activa", reciente["id"] not in activos)
verificar("pero no de la tabla", reciente["id"] in todos)

verificar("y pierde el acceso aunque la contraseña siga siendo válida",
          auth.authenticate(db, "ant_reciente", "clave123") is None)

try:
    auth.dar_de_baja(db, admin, reciente["id"])
    verificar("no se puede dar de baja dos veces", False)
except ValueError as error:
    verificar("no se puede dar de baja dos veces", True, str(error))

try:
    auth.dar_de_baja(db, admin, admin["id"])
    verificar("nadie se da de baja a sí mismo", False)
except ValueError as error:
    verificar("nadie se da de baja a sí mismo", True, str(error))

auth.reincorporar(db, admin, reciente["id"])
verificar("la reincorporación devuelve el acceso",
          auth.authenticate(db, "ant_reciente", "clave123") is not None)

for usuario in ("ant_nuevo", "ant_medio", "ant_veterano", "ant_reciente"):
    fila = db.usuario_por_cedula(usuario)
    if fila:
        auth.eliminar_usuario(db, admin, fila["id"])
db.cerrar()

print()
if fallos:
    print(f"ANTIGÜEDAD Y BAJAS: {fallos} problema(s)")
    raise SystemExit(1)
print("ANTIGÜEDAD Y BAJAS OK · P3-6 cerrado y baja lógica verificada")
