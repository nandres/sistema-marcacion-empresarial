"""Permisos pedidos por el empleado desde el portal, sin formulario en papel.

Recorre el ciclo completo: el empleado ve los artículos que le aplican con su
saldo real, presenta el pedido, el sistema lo valida contra el reglamento
antes de guardarlo, Recursos Humanos lo aprueba y la justificación oficial
queda emitida con su PDF. Nadie transcribe nada.

Lo que más importa fijar acá es la cuota reservada: sin ella un empleado
podía presentar diez pedidos del mismo artículo y todos pasaban, porque
ninguno había llegado a consumir la cuota todavía.
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
admin = db.get_user_by_username("admin")

USUARIO = "permiso_autoservicio"
previo = db.get_user_by_username(USUARIO)
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, USUARIO, "clave123", "Ana Solicitante", "Empleado",
                 3000000, "Funcionario")
empleado = db.get_user_by_username(USUARIO)

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


def saldo(tipo: str):
    return next(
        d for d in reglamento.disponibilidad_permisos(db, empleado)
        if d["tipo"] == tipo
    )


hoy = date.today()
proxima_semana = hoy + timedelta(days=7)

# 1) El catálogo que ve el empleado es el de su vínculo, con su saldo.
articulos = reglamento.disponibilidad_permisos(db, empleado)
tipos = {a["tipo"] for a in articulos}
verificar("ve los artículos del Reglamento Interno",
          "Motivos Particulares" in tipos and "Salidas Personales" in tipos)
verificar("no ve los artículos de pasante",
          "Licencia de Pasante" not in tipos and "Fuerza Mayor" not in tipos)

art = saldo("Motivos Particulares")
verificar("Motivos Particulares es el Art. 34, inc. a.10 con 5 días al año",
          art["articulo"] == "Art. 34, inc. a.10" and art["cuota"] == 5,
          f"{art['articulo']} · cuota {art['cuota']}")

# 2) Un permiso a futuro es el caso normal: pedir vacaciones de la semana que viene.
pedido = auth.solicitar_permiso(
    db, empleado, "Motivos Particulares", proxima_semana, proxima_semana + timedelta(days=1),
    0, "Trámite en el Registro Civil que solo atiende de mañana.",
)
verificar("el pedido a futuro se acepta", pedido["estado"] == "Pendiente", str(pedido["id"]))
verificar("responde con el artículo invocado",
          pedido["articulo"] == "Art. 34, inc. a.10")

# 3) La cuota queda reservada aunque todavía no esté aprobada.
art = saldo("Motivos Particulares")
verificar("el pedido pendiente reserva 2 de los 5 días",
          art["pendientes"] == 2 and art["restantes_efectivos"] == 3,
          f"pendientes {art['pendientes']} · efectivos {art['restantes_efectivos']}")
verificar("pero todavía no cuenta como usado", art["usados"] == 0)

# 4) Un segundo pedido que excede lo que queda se rechaza en el momento.
try:
    auth.solicitar_permiso(
        db, empleado, "Motivos Particulares", proxima_semana + timedelta(days=10),
        proxima_semana + timedelta(days=14), 0,
        "Otro trámite que necesita cinco días completos.",
    )
    verificar("un pedido que excede la cuota reservada se rechaza", False)
except ValueError as error:
    verificar("un pedido que excede la cuota reservada se rechaza", True, str(error))

# 5) Validaciones de forma, en lenguaje del reglamento.
casos = [
    ("fin antes que inicio", "Motivos Particulares", hoy, hoy - timedelta(days=1), 0,
     "Motivo suficientemente largo."),
    ("motivo demasiado corto", "Motivos Particulares", hoy, hoy, 0, "corto"),
    ("artículo de otro vínculo", "Licencia de Pasante", hoy, hoy, 0,
     "Motivo suficientemente largo."),
    ("permiso por días con horas", "Motivos Particulares", hoy, hoy, 3,
     "Motivo suficientemente largo."),
    ("permiso por horas sin horas", "Salidas Personales", hoy, hoy, 0,
     "Motivo suficientemente largo."),
    ("permiso por horas en varios días", "Salidas Personales", hoy,
     hoy + timedelta(days=1), 2, "Motivo suficientemente largo."),
]
for descripcion, tipo, desde, hasta, cantidad, motivo in casos:
    try:
        auth.solicitar_permiso(db, empleado, tipo, desde, hasta, cantidad, motivo)
        verificar(f"se rechaza: {descripcion}", False)
    except ValueError as error:
        verificar(f"se rechaza: {descripcion}", True, str(error))

# 6) Recursos Humanos aprueba y la justificación oficial nace sola.
resultado = auth.resolver_solicitud_permiso(db, admin, pedido["id"], True)
verificar("la aprobación emite la justificación",
          resultado["estado"] == "Aprobado" and resultado["justificacion_id"],
          f"justificación #{resultado['justificacion_id']}")

justificacion = next(
    (j for j in db.list_justificaciones() if j["id"] == resultado["justificacion_id"]),
    None,
)
verificar("la justificación queda a nombre del empleado",
          justificacion and justificacion["usuario_id"] == empleado["id"])
verificar("con las fechas futuras que se pidieron",
          justificacion["fecha_inicio"] == proxima_semana)

art = saldo("Motivos Particulares")
verificar("la cuota pasa de reservada a usada",
          art["usados"] == 2 and art["pendientes"] == 0,
          f"usados {art['usados']} · pendientes {art['pendientes']}")

# 7) El PDF del permiso se genera sin intervención.
ruta = Path(reports.generar_pdf_permiso(resultado["justificacion_id"]))
verificar("el PDF del permiso se emite solo", ruta.exists() and ruta.stat().st_size > 1000,
          f"{ruta.name} · {ruta.stat().st_size} bytes")

# 8) Un pedido resuelto no se puede volver a resolver.
try:
    auth.resolver_solicitud_permiso(db, admin, pedido["id"], False, "Me arrepentí.")
    verificar("no se puede resolver dos veces", False)
except ValueError as error:
    verificar("no se puede resolver dos veces", True, str(error))

# 9) Rechazo: no consume cuota y deja el motivo a la vista del empleado.
otro = auth.solicitar_permiso(
    db, empleado, "Salidas Personales", hoy, hoy, 2,
    "Necesito salir dos horas para una gestión bancaria.",
)
auth.resolver_solicitud_permiso(db, admin, otro["id"], False, "Ese día hay cierre de mes.")
art = saldo("Salidas Personales")
verificar("un pedido rechazado no consume cuota",
          art["usados"] == 0 and art["pendientes"] == 0,
          f"usados {art['usados']} · pendientes {art['pendientes']}")
rechazado = db.get_solicitud_permiso(otro["id"])
verificar("el motivo del rechazo queda con el pedido",
          rechazado["observacion"] == "Ese día hay cierre de mes.")

# 10) Un empleado no puede resolver pedidos, ni el propio.
try:
    tercero = auth.solicitar_permiso(
        db, empleado, "Salidas Personales", hoy, hoy, 1,
        "Consulta odontológica al mediodía.",
    )
    auth.resolver_solicitud_permiso(db, empleado, tercero["id"], True)
    verificar("un Empleado no puede aprobar su propio pedido", False)
except PermissionError as error:
    verificar("un Empleado no puede aprobar su propio pedido", True, str(error))

auth.delete_user(db, admin, empleado["id"])
db.cerrar()

print()
if fallos:
    print(f"PERMISOS AUTOSERVICIO: {fallos} problema(s)")
    raise SystemExit(1)
print("PERMISOS AUTOSERVICIO OK · pedido, cuota reservada, aprobación y PDF")
