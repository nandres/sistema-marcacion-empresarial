"""Rotación automática: semana A / semana B sin cargarla semana por semana.

Una empresa con turnos que rotan tenía que crear una asignación por persona y
por semana, para siempre. El ciclo describe la regla —qué turnos, en qué orden
y cada cuántos días— y el turno de cada día se calcula.

Se calcula y no se materializa a propósito: no hay filas que regenerar, el
calendario sigue siendo correcto en cualquier fecha futura y cambiar el ciclo
no obliga a rehacer nada. La contracara es que hace falta una proyección para
poder verlo, y eso también se comprueba acá.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
from database import Database

db = Database()
db.initialize()
admin = db.get_user_by_username("admin")

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(
        f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
        + (f" | {detalle}" if detalle else "")
    )


def limpiar() -> None:
    for nombre in ("Rot Mañana", "Rot Tarde", "Rot Noche"):
        turno = db.get_turno_por_nombre(nombre)
        if turno:
            db.eliminar_turno(turno["id"])
    for ciclo in db.listar_ciclos(incluir_inactivos=True):
        if ciclo["nombre"].startswith("Prueba "):
            db.eliminar_ciclo(ciclo["id"])
    for usuario in ("rot_ana", "rot_beto", "rot_caro"):
        registro = db.get_user_by_username(usuario)
        if registro:
            auth.delete_user(db, admin, registro["id"])


limpiar()

# Un lunes fijo: el resultado no puede depender del día en que corra la prueba.
ANCLA = date(2026, 9, 7)
TODOS_LOS_DIAS = "1111111"

manana = auth.crear_turno(db, admin, "Rot Mañana",
                          [{"entrada": "06:00", "salida": "14:00"}], TODOS_LOS_DIAS)
tarde = auth.crear_turno(db, admin, "Rot Tarde",
                         [{"entrada": "14:00", "salida": "22:00"}], TODOS_LOS_DIAS)
noche = auth.crear_turno(db, admin, "Rot Noche",
                         [{"entrada": "22:00", "salida": "06:00"}], TODOS_LOS_DIAS)


def empleado(usuario: str):
    auth.create_user(db, admin, usuario, "clave123", usuario, "Empleado", 2500000)
    return db.get_user_by_username(usuario)


print("ROTACIÓN AUTOMÁTICA")

# ------------------------------------------------------- 1. La definición
print("\n1) El ciclo describe la regla, no las semanas")

ciclo = auth.crear_ciclo(db, admin, "Prueba A/B", [manana["id"], noche["id"]],
                         7, ANCLA)
verificar("queda la secuencia en orden",
          ciclo["secuencia"] == "Rot Mañana → Rot Noche", ciclo["secuencia"])
verificar("con su paso y su fecha de arranque",
          ciclo["dias_por_tramo"] == 7 and ciclo["ancla"] == ANCLA.isoformat())

for invalido, motivo in (
    ([manana["id"]], "un solo turno"),
    ([manana["id"], manana["id"]], "el mismo turno dos veces"),
):
    try:
        auth.crear_ciclo(db, admin, f"Prueba {motivo}", invalido, 7, ANCLA)
        verificar(f"se rechaza un ciclo con {motivo}", False)
    except ValueError as error:
        verificar(f"se rechaza un ciclo con {motivo}", True, str(error))

# ---------------------------------------------- 2. Dos personas, dos turnos
print("\n2) La posición mantiene a cada uno en un turno distinto")

ana = empleado("rot_ana")
beto = empleado("rot_beto")
auth.asignar_ciclo(db, admin, ana["id"], ciclo["id"], 0)
auth.asignar_ciclo(db, admin, beto["id"], ciclo["id"], 1)

esperado = ["Rot Mañana", "Rot Noche", "Rot Mañana", "Rot Noche"]
obtenido_ana, obtenido_beto = [], []
for semana in range(4):
    dia = ANCLA + timedelta(days=7 * semana + 2)
    obtenido_ana.append(clock_engine.turno_vigente(db, ana["id"], dia).nombre)
    obtenido_beto.append(clock_engine.turno_vigente(db, beto["id"], dia).nombre)

verificar("Ana alterna semana a semana", obtenido_ana == esperado,
          " · ".join(obtenido_ana))
verificar("Beto alterna en contrafase", obtenido_beto == esperado[1:] + [esperado[0]],
          " · ".join(obtenido_beto))
verificar("nunca coinciden de turno",
          all(a != b for a, b in zip(obtenido_ana, obtenido_beto)))
verificar("y el turno viene del ciclo, no del legajo",
          clock_engine.turno_vigente(db, ana["id"], ANCLA).origen == "ciclo")

# El paso no tiene que ser semanal.
caro = empleado("rot_caro")
quincenal = auth.crear_ciclo(db, admin, "Prueba quincenal",
                             [manana["id"], tarde["id"], noche["id"]], 15, ANCLA)
auth.asignar_ciclo(db, admin, caro["id"], quincenal["id"], 0)
tramos = [
    clock_engine.turno_vigente(db, caro["id"], ANCLA + timedelta(days=15 * i)).nombre
    for i in range(4)
]
verificar("un ciclo de tres turnos cada quince días también rota",
          tramos == ["Rot Mañana", "Rot Tarde", "Rot Noche", "Rot Mañana"],
          " · ".join(tramos))

# ------------------------------------------------ 3. Lo puntual gana
print("\n3) Un cambio puntual le gana al ciclo")

auth.rotar_turno(db, admin, ana["id"], tarde["id"],
                 ANCLA + timedelta(days=7), ANCLA + timedelta(days=13),
                 "Cambio puntual")
en_la_semana = clock_engine.turno_vigente(db, ana["id"], ANCLA + timedelta(days=9))
verificar("esa semana manda la asignación manual",
          en_la_semana.nombre == "Rot Tarde" and en_la_semana.origen == "asignacion",
          f"{en_la_semana.nombre} ({en_la_semana.origen})")
despues = clock_engine.turno_vigente(db, ana["id"], ANCLA + timedelta(days=16))
verificar("y al vencer vuelve al ciclo, no al legajo",
          despues.origen == "ciclo", f"{despues.nombre} ({despues.origen})")

# ------------------------------------------- 4. Antes del arranque, nada
print("\n4) Un ciclo no describe lo que pasó antes de empezar")

previo_ana = clock_engine.turno_vigente(db, ana["id"], ANCLA - timedelta(days=5))
previo_beto = clock_engine.turno_vigente(db, beto["id"], ANCLA - timedelta(days=5))
verificar("antes del ancla no se aplica a nadie",
          previo_ana.origen != "ciclo" and previo_beto.origen != "ciclo",
          f"{previo_ana.origen} / {previo_beto.origen}")
verificar("y los dos caen al mismo lugar: el registro no queda incoherente",
          previo_ana.nombre == previo_beto.nombre,
          f"{previo_ana.nombre} vs {previo_beto.nombre}")

# --------------------------------------------- 5. Se puede consultar
print("\n5) La rotación se puede ver, no solo calcular")

calendario = auth.calendario_de_rotacion(db, admin, beto["id"], semanas=4)
verificar("el calendario proyecta los próximos tramos", len(calendario) == 4,
          f"{len(calendario)} tramos")
verificar("el primero es el que está en curso",
          calendario and calendario[0]["en_curso"])
verificar("cada tramo dice desde cuándo y hasta cuándo rige",
          all({"desde", "hasta", "turno", "horario"} <= set(t) for t in calendario))
verificar("y los tramos son contiguos",
          all(
            date.fromisoformat(b["desde"]) - date.fromisoformat(a["hasta"])
            == timedelta(days=1)
            for a, b in zip(calendario, calendario[1:])
          ))

propio = auth.calendario_de_rotacion(db, db.get_user_by_id(beto["id"]), beto["id"])
verificar("el empleado puede ver el suyo", len(propio) > 0)
try:
    auth.calendario_de_rotacion(db, db.get_user_by_id(beto["id"]), ana["id"])
    verificar("pero no el de un compañero", False)
except PermissionError as error:
    verificar("pero no el de un compañero", True, str(error))

sin_ciclo = auth.calendario_de_rotacion(db, admin, admin["id"])
verificar("quien no rota no tiene calendario", sin_ciclo == [])

# --------------------------------------------- 6. Reglas de eliminación
print("\n6) Un ciclo con gente adentro no se elimina")

try:
    auth.eliminar_ciclo(db, admin, ciclo["id"])
    verificar("no se elimina un ciclo con dotación", False)
except ValueError as error:
    verificar("no se elimina un ciclo con dotación", True, str(error))

auth.asignar_ciclo(db, admin, ana["id"], None)
auth.asignar_ciclo(db, admin, beto["id"], None)
verificar("sin gente adentro sí se elimina",
          auth.eliminar_ciclo(db, admin, ciclo["id"]))
verificar("y quien salió del ciclo vuelve a su turno de contrato",
          clock_engine.turno_vigente(db, beto["id"], ANCLA + timedelta(days=30)).origen
          != "ciclo")

limpiar()
db.cerrar()

print()
if fallos:
    print(f"ROTACIÓN: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("ROTACIÓN OK · el ciclo se calcula, se consulta y lo puntual le gana")
