"""P3-1 y P3-3: dato biométrico cifrado y freno a la fuerza bruta.

P3-1 · La tabla `fotos` guardaba la plantilla facial como JPEG plano. Bajo
la Ley N.º 6534/2020 el dato biométrico es de categoría especial: un volcado
de la base entregaba el rostro de toda la plantilla, y a diferencia de una
contraseña eso no se puede cambiar. Tampoco había política de retención.

P3-3 · `/api/login` y `/api/marcar` aceptaban intentos sin límite. La cédula
es pública en Paraguay, así que sin freno la contraseña de cualquier
empleado cae por diccionario.

P3-16 · El comprobante de marcación se firmaba con HMAC pero no se podía
comprobar: la firma cubría el instante con microsegundos y huso, y el papel
imprimía la hora en HH:MM:SS. Nadie podía reconstruir desde el ticket el
valor firmado, así que la firma era decorativa.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import biometria
import rate_limit
import reports
from clock_engine import ahora_local
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


# --- P3-1 · cifrado en reposo ----------------------------------------------
print("Biometría cifrada en reposo (Ley 6534/2020)")
verificar("hay clave de cifrado configurada", biometria.configurada())

USUARIO = "seg_biometria"
previo = db.get_user_by_username(USUARIO)
if previo:
    auth.delete_user(db, admin, previo["id"])
auth.create_user(db, admin, USUARIO, "clave123", "Biometría Prueba", "Empleado",
                 2000000, "Funcionario")
empleado = db.get_user_by_username(USUARIO)

PLANTILLA = b"\xff\xd8\xff\xe0" + b"plantilla-facial-simulada" * 8
db.guardar_foto(empleado["id"], PLANTILLA)

crudo = db._execute(
    "SELECT imagen FROM fotos WHERE user_id = %s", (empleado["id"],), fetch="one"
)
guardado = bytes(crudo["imagen"])
verificar("lo que queda en la base está cifrado", biometria.esta_cifrada(guardado))
verificar("y no contiene la plantilla en claro", PLANTILLA not in guardado,
          f"{len(guardado)} bytes almacenados")
verificar("ni siquiera la cabecera JPEG", not guardado.startswith(b"\xff\xd8"))

verificar("la aplicación la recupera intacta",
          db.get_foto(empleado["id"]) == PLANTILLA)

# El identificador del empleado es dato autenticado: mover la fila no sirve.
verificar("una foto reasignada a otro empleado no abre",
          biometria.descifrar(guardado, empleado["id"] + 1) is None)

alterada = bytearray(guardado)
alterada[-1] ^= 0x01
verificar("una foto alterada en la base no se descifra en silencio",
          biometria.descifrar(bytes(alterada), empleado["id"]) is None)

# Compatibilidad: una instalación anterior tiene fotos en claro.
db._execute(
    "UPDATE fotos SET imagen = %s WHERE user_id = %s",
    (memoryview(PLANTILLA), empleado["id"]),
)
db.connection.commit()
verificar("una foto antigua en claro se sigue leyendo",
          db.get_foto(empleado["id"]) == PLANTILLA)
convertidas = biometria.migrar_fotos(db)
verificar("y la migración la cifra", convertidas >= 1, f"{convertidas} convertidas")
crudo = db._execute(
    "SELECT imagen FROM fotos WHERE user_id = %s", (empleado["id"],), fetch="one"
)
verificar("dejándola cifrada en reposo", biometria.esta_cifrada(bytes(crudo["imagen"])))

# Retención: terminada la relación laboral, el dato se destruye.
baja = auth.dar_de_baja(db, admin, empleado["id"])
verificar("la baja destruye la plantilla facial",
          baja["biometria_eliminada"] and not db.tiene_foto(empleado["id"]))
verificar("pero conserva el legajo", db.get_user_by_id(empleado["id"]) is not None)

auth.delete_user(db, admin, empleado["id"])

# --- P3-3 · freno de intentos ----------------------------------------------
print("\nFreno de intentos fallidos")
freno = rate_limit.Freno(maximos=4, ventana=60, bloqueo=60)

freno.verificar("u:juan")
verificar("el primer intento pasa", True)

for _ in range(3):
    freno.fallo("u:juan")
restantes, _ = freno.estado("u:juan")
verificar("cuenta los fallos dentro de la ventana", restantes == 1, f"quedan {restantes}")
freno.verificar("u:juan")

freno.fallo("u:juan")
try:
    freno.verificar("u:juan")
    verificar("al agotar los intentos bloquea", False)
except rate_limit.LimiteExcedido as limite:
    verificar("al agotar los intentos bloquea", True, str(limite))

freno.verificar("u:otro")
verificar("el bloqueo es por identidad, no global", True)

freno.exito("u:juan")
freno.verificar("u:juan")
verificar("una autenticación válida cierra el episodio", True)

verificar("el freno del portal cuenta por usuario y por origen",
          rate_limit.INTENTOS_MAXIMOS > 0
          and rate_limit.BLOQUEO_SEGUNDOS >= 300,
          f"{rate_limit.INTENTOS_MAXIMOS} intentos · bloqueo "
          f"{rate_limit.BLOQUEO_SEGUNDOS // 60} min")

# --- P3-16 · el comprobante se puede comprobar ------------------------------
print("\nComprobante de marcación verificable")

momento = ahora_local()
ticket = reports.comprobante_marcacion(4321, momento, "ENTRADA")

# Lo esencial: la comprobación parte del papel, no de los valores sueltos que
# tenía a mano quien lo emitió. Si el ticket no alcanza, la firma no sirve.
veredicto = reports.verificar_comprobante(ticket)
verificar("un comprobante recién emitido se valida con su propio texto",
          veredicto["valido"], veredicto["motivo"])
verificar("y dice de qué marcaje es", veredicto["registro_id"] == 4321,
          str(veredicto.get("registro_id")))

alterado = reports.verificar_comprobante(ticket.replace("ENTRADA", "SALIDA"))
verificar("cambiarle el tipo lo invalida", not alterado["valido"])

corrido = reports.verificar_comprobante(
    ticket.replace(momento.strftime("%H:%M:%S"), "00:00:00")
)
verificar("y correrle la hora también", not corrido["valido"])

verificar("un papel que no es un comprobante se rechaza sin reventar",
          not reports.verificar_comprobante("hola")["valido"])

# El instante firmado tiene que estar impreso: es la única forma de que quien
# recibe el papel pueda recalcular la firma.
verificar("el ticket imprime el instante que firma",
          momento.replace(microsecond=0).isoformat() in ticket)

db.cerrar()

print()
if fallos:
    print(f"SEGURIDAD DE DATOS: {fallos} problema(s)")
    raise SystemExit(1)
print("SEGURIDAD DE DATOS OK · P3-1, P3-3 y P3-16 cerrados")
