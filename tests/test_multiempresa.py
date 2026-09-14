"""Aislamiento entre empresas alojadas en la misma instalación.

Una sola consulta sin acotar basta para que un cliente vea la planilla de
otro, así que acá no se comprueba que el aislamiento "esté implementado"
sino que **no se pueda cruzar**. Se alojan dos empresas con los datos
deliberadamente superpuestos —la misma cédula, el mismo nombre de turno, la
misma fecha con condición declarada— y se intenta llegar de una a la otra
por todos los caminos que existen:

- listados y contadores;
- búsquedas por identificador, que es como llegan los ids desde una URL;
- ediciones y borrados;
- el login, que es la única consulta que cruza empresas a propósito;
- el token de sesión, forjado a mano para apuntar a la empresa equivocada;
- el bus de alertas en vivo, que vive en el proceso y no en la base.

Incluye además el verificador estático: ninguna consulta nueva puede quedar
sin acotar sin que esta prueba falle.
"""

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
import database
import notifications
import reports
import guardia_arrendamiento
from database import Database, SinEmpresa

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(
        f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
        + (f" | {detalle}" if detalle else "")
    )


SLUG_A = "prueba-norte"
SLUG_B = "prueba-sur"
CEDULA = "4185720"          # la misma persona en las dos empresas
NOMBRE_TURNO = "Mañana"     # el mismo nombre de turno en las dos

db = Database()
db.initialize()

print("MULTIEMPRESA · aislamiento entre clientes alojados")

# ------------------------------------------------------ 1. Guardia estática
print("\n1) El código no deja consultas sin acotar")

sin_acotar = guardia_arrendamiento.revisar()
verificar("ninguna consulta de datos de cliente sin empresa",
          not sin_acotar, "; ".join(sin_acotar) if sin_acotar else "")
sin_columna = guardia_arrendamiento.tablas_sin_columna(db)
verificar("todas las tablas de datos tienen empresa_id",
          not sin_columna, ", ".join(sin_columna) if sin_columna else "")

# ------------------------------------------------------- 2. Falla cerrado
print("\n2) Una conexión sin empresa no devuelve nada: falla")

suelta = Database()
suelta.connect()
for nombre, operacion in (
    ("listar personal", lambda: suelta.list_users()),
    ("listar alertas", lambda: suelta.listar_alertas()),
    ("contar marcajes de hoy", lambda: suelta.count_marcajes_hoy()),
    ("buscar un legajo por id", lambda: suelta.get_user_by_id(1)),
):
    try:
        operacion()
        verificar(f"sin empresa, {nombre} falla", False, "devolvió datos")
    except SinEmpresa:
        verificar(f"sin empresa, {nombre} falla", True)
suelta.cerrar()

# --------------------------------------------------- 3. Alojar dos empresas
print("\n3) Dos empresas con los datos superpuestos a propósito")


def alojar(slug: str, razon: str) -> dict:
    """Aloja la empresa de prueba, descartando la que hubiera quedado antes.

    Se borra la empresa entera y no sus filas una por una: el ``ON DELETE
    CASCADE`` de ``empresa_id`` es justamente lo que tiene que arrastrar
    todo, incluida la auditoría, que referencia a los legajos.
    """
    existente = db.get_empresa_por_slug(slug)
    if existente:
        db._execute("DELETE FROM empresas WHERE id = %s", (existente["id"],))
        db.connection.commit()
    return db.crear_empresa(slug, razon)


empresa_a = alojar(SLUG_A, "Norte S.A.")
empresa_b = alojar(SLUG_B, "Sur S.R.L.")
verificar("las dos empresas quedan alojadas",
          empresa_a["id"] != empresa_b["id"],
          f"{empresa_a['slug']}#{empresa_a['id']} · {empresa_b['slug']}#{empresa_b['id']}")


def sembrar(empresa: dict, clave: str, nombre: str) -> dict:
    """Crea en una empresa el mismo legajo, turno y condición que en la otra."""
    db.empresa_id = empresa["id"]
    db.initialize()
    db.empresa_id = empresa["id"]
    if not db.list_users(incluir_bajas=True):
        auth.crear_primer_admin(db, f"admin-{empresa['slug']}", "clave-admin", "Admin")
    admin = db.get_user_by_username(f"admin-{empresa['slug']}")
    if not db.get_user_by_username(CEDULA):
        auth.create_user(db, admin, CEDULA, clave, nombre, "Empleado", 3000000)
    empleado = db.get_user_by_username(CEDULA)
    if not db.get_turno_por_nombre(NOMBRE_TURNO):
        auth.crear_turno(db, admin, NOMBRE_TURNO,
                         [{"entrada": "06:00", "salida": "14:00"}])
    return {"admin": admin, "empleado": empleado,
            "turno": db.get_turno_por_nombre(NOMBRE_TURNO)}


datos_a = sembrar(empresa_a, "clave-norte", "Ramón Norte")
datos_b = sembrar(empresa_b, "clave-sur", "Ramón Sur")

verificar("la misma cédula existe en las dos, con legajos distintos",
          datos_a["empleado"]["id"] != datos_b["empleado"]["id"],
          f"#{datos_a['empleado']['id']} y #{datos_b['empleado']['id']}")
verificar("y el mismo nombre de turno convive en las dos",
          datos_a["turno"]["id"] != datos_b["turno"]["id"])

# Marcas, permisos y condición del día en cada una.
DIA = date.today() - timedelta(days=2)
for empresa, datos in ((empresa_a, datos_a), (empresa_b, datos_b)):
    db.empresa_id = empresa["id"]
    db.limpiar_marcajes_prueba(datos["empleado"]["id"], DIA, DIA)
    entrada = datetime.combine(DIA, time(6, 0)).astimezone()
    marcaje = db.open_clock_in(datos["empleado"]["id"], entrada, False, "")
    db.close_clock_out(marcaje, entrada + timedelta(hours=8), False,
                       timedelta(hours=8), timedelta(0), timedelta(0), "",
                       timedelta(0), "Diurna")
    db.borrar_condicion_dia(DIA)
    auth.declarar_condicion_dia(db, datos["admin"], DIA,
                                f"Lluvia en {empresa['slug']}", 30)

# --------------------------------------------------------- 4. No se cruzan
print("\n4) Nada de una empresa aparece en la otra")

db.empresa_id = empresa_a["id"]
personal_a = db.list_users(incluir_bajas=True)
db.empresa_id = empresa_b["id"]
personal_b = db.list_users(incluir_bajas=True)
ids_a = {u["id"] for u in personal_a}
ids_b = {u["id"] for u in personal_b}
verificar("los listados de personal no comparten un solo legajo",
          not (ids_a & ids_b), f"{len(personal_a)} y {len(personal_b)} legajos")

db.empresa_id = empresa_a["id"]
verificar("un id de la otra empresa no resuelve a nadie",
          db.get_user_by_id(datos_b["empleado"]["id"]) is None)
verificar("ni siquiera su turno",
          db.get_turno(datos_b["turno"]["id"]) is None)
verificar("la condición del día es la propia y no la ajena",
          (db.get_condicion_dia(DIA) or {}).get("condicion") == f"Lluvia en {SLUG_A}",
          (db.get_condicion_dia(DIA) or {}).get("condicion", "—"))

marcas_a = db.get_marcajes_month(DIA.year, DIA.month)
ajenas = [m for m in marcas_a if m["user_id"] in ids_b]
verificar("el mes no trae marcajes de la otra empresa", not ajenas,
          f"{len(marcas_a)} marcajes propios")

db.empresa_id = empresa_a["id"]
turnos_a = {t["id"] for t in db.listar_turnos(incluir_inactivos=True)}
db.empresa_id = empresa_b["id"]
turnos_b = {t["id"] for t in db.listar_turnos(incluir_inactivos=True)}
verificar("los catálogos de turnos no se solapan", not (turnos_a & turnos_b))

# --------------------------------------------- 5. Escribir tampoco se cruza
print("\n5) Escribir sobre la otra empresa no hace nada")

db.empresa_id = empresa_a["id"]
antes = datos_b["empleado"]["full_name"]
db.update_user(datos_b["empleado"]["id"], full_name="INTRUSO")
db.empresa_id = empresa_b["id"]
verificar("una edición dirigida a la otra empresa no toca el legajo",
          db.get_user_by_id(datos_b["empleado"]["id"])["full_name"] == antes,
          db.get_user_by_id(datos_b["empleado"]["id"])["full_name"])

db.empresa_id = empresa_a["id"]
db.delete_user(datos_b["empleado"]["id"])
db.empresa_id = empresa_b["id"]
verificar("un borrado dirigido a la otra empresa no lo elimina",
          db.get_user_by_id(datos_b["empleado"]["id"]) is not None)

db.empresa_id = empresa_a["id"]
db.cambiar_estado_turno(datos_b["turno"]["id"], False)
db.empresa_id = empresa_b["id"]
verificar("ni retira su turno",
          db.get_turno(datos_b["turno"]["id"])["activo"] is True)

# ----------------------------------------------------------- 6. El acceso
print("\n6) El login: la contraseña decide de qué empresa es la sesión")

sesion = Database()
sesion.connect()
norte = auth.authenticate(sesion, CEDULA, "clave-norte")
verificar("la misma cédula con la clave de Norte entra a Norte",
          norte is not None and norte["empresa_id"] == empresa_a["id"],
          norte["empresa_nombre"] if norte else "no entró")
verificar("y la conexión queda atada a esa empresa",
          sesion.empresa_id == empresa_a["id"])

sur = auth.authenticate(sesion, CEDULA, "clave-sur")
verificar("la misma cédula con la clave de Sur entra a Sur",
          sur is not None and sur["empresa_id"] == empresa_b["id"],
          sur["empresa_nombre"] if sur else "no entró")

verificar("la clave de Norte no entra indicando Sur",
          auth.authenticate(sesion, CEDULA, "clave-norte", SLUG_B) is None)
verificar("una clave inexistente no entra a ninguna",
          auth.authenticate(sesion, CEDULA, "clave-que-no-es") is None)

db.cambiar_estado_empresa(empresa_b["id"], False)
verificar("una empresa suspendida no deja entrar a los suyos",
          auth.authenticate(sesion, CEDULA, "clave-sur") is None)
db.cambiar_estado_empresa(empresa_b["id"], True)
sesion.cerrar()

# ---------------------------------------------------- 7. El token firmado
print("\n7) El token no se puede apuntar a otra empresa")

token_legitimo = auth.crear_token_acceso(
    datos_a["empleado"]["id"], "Empleado", empresa_a["id"]
)
claims = auth.verificar_token_acceso(token_legitimo)
verificar("el token transporta la empresa firmada",
          claims["emp"] == empresa_a["id"], str(claims.get("emp")))

# Un token forjado: usuario de Norte, empresa de Sur. Aunque la firma fuera
# válida, la búsqueda acotada no encuentra a ese usuario en esa empresa.
forjado = auth.crear_token_acceso(
    datos_a["empleado"]["id"], "Empleado", empresa_b["id"]
)
reclamado = auth.verificar_token_acceso(forjado)
impostor = Database(empresa_id=reclamado["emp"])
impostor.connect()
verificar("un token con el usuario de una y la empresa de otra no resuelve a nadie",
          impostor.get_user_by_id(int(reclamado["sub"])) is None)
impostor.cerrar()

# ------------------------------------------------- 8. Las alertas en vivo
print("\n8) El bus de alertas en memoria respeta la frontera")

recibidas = []
notifications.BUS.suscribir(lambda a: recibidas.append(a))
db.empresa_id = empresa_b["id"]
notifications.registrar_alerta(db, "prueba_aislamiento", "baja",
                               "Alerta de Sur", "solo para Sur")
propias_de_a = [a for a in recibidas
                if notifications.es_de_la_empresa(a, empresa_a["id"])]
propias_de_b = [a for a in recibidas
                if notifications.es_de_la_empresa(a, empresa_b["id"])]
verificar("la alerta de una empresa no le corresponde a la otra",
          not propias_de_a and len(propias_de_b) == 1,
          f"Norte {len(propias_de_a)} · Sur {len(propias_de_b)}")

db.empresa_id = empresa_a["id"]
verificar("y tampoco aparece en su bandeja persistida",
          not [a for a in db.listar_alertas(limite=10)
               if a["tipo"] == "prueba_aislamiento"])

# ------------------------------------------------------- 9. Los informes
print("\n9) Los informes se arman con una sola empresa")

db.empresa_id = empresa_a["id"]
resumen = reports.resumen_empleado(db, db.get_user_by_id(datos_a["empleado"]["id"]))
verificar("el tablero personal se compone sin cruzar datos",
          resumen["nombre"] == "Ramón Norte", resumen["nombre"])
verificar("y su turno es el de su empresa",
          resumen["turno"]["id"] in turnos_a, str(resumen["turno"]["id"]))

db.empresa_id = empresa_a["id"]
aguinaldos_a = {f["id"] for f in db.get_proyeccion_aguinaldos()}
verificar("la proyección de aguinaldos no incluye a la otra plantilla",
          not (aguinaldos_a & ids_b), f"{len(aguinaldos_a)} empleados")

# ------------------------------------------------------------- Limpieza
for empresa in (empresa_a, empresa_b):
    db._execute("DELETE FROM empresas WHERE id = %s", (empresa["id"],))
db.connection.commit()
db._adoptar_empresa_base()

print()
if fallos:
    print(f"MULTIEMPRESA: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("MULTIEMPRESA OK · dos clientes alojados, ningún dato cruzado")
