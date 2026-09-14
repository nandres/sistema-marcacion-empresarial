"""P2-5: la entidad `turnos`, que es lo que faltaba para vender el sistema.

Una empresa con dos turnos no podía usarlo: la hora de entrada era una
constante global del proceso, congelada al importar el módulo, y `JORNADA_INICIO`
del `.env` se ignoraba en silencio. Acá se verifica lo que el turno trajo:

- la hora contra la que se mide la tardanza sale del turno del empleado;
- la prioridad de resolución (rotación > legajo > predeterminado);
- la jornada partida admite dos entradas y dos salidas por día;
- el turno nocturno no queda bloqueado por la jornada de anoche;
- los días fuera del turno son francos y no ausencias;
- corregir una marca aplica la misma regla que marcarla (P2-4).
"""

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import auth
import clock_engine
import reports
import turnos
from clock_engine import ClockEngine
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


def empleado_de_prueba(usuario: str, nombre: str, vinculo: str = "Funcionario"):
    previo = db.get_user_by_username(usuario)
    if previo:
        auth.delete_user(db, admin, previo["id"])
    auth.create_user(db, admin, usuario, "clave123", nombre, "Empleado",
                     2800000, vinculo)
    return db.get_user_by_username(usuario)


def turno_de_prueba(nombre: str, tramos, dias=turnos.MASCARA_LUNES_VIERNES,
                    tolerancia=None):
    previo = db.get_turno_por_nombre(nombre)
    if previo:
        db.eliminar_turno(previo["id"])
    return auth.crear_turno(db, admin, nombre, tramos, dias,
                            "Casa Central", tolerancia)


def lunes_pasado() -> date:
    """Un lunes reciente que no sea feriado, para anclar las pruebas."""
    dia = date.today() - timedelta(days=7)
    while dia.weekday() != 0 or clock_engine.es_dia_de_descanso(dia):
        dia -= timedelta(days=1)
    return dia


LUNES = lunes_pasado()

print("TURNOS · P2-5")
print(f"  Lunes de referencia: {LUNES.isoformat()}")

# ---------------------------------------------------------------- 1. Dominio
print("\n1) Definición del turno")

manana = turno_de_prueba("Prueba Mañana", [{"entrada": "06:00", "salida": "14:00"}])
tarde = turno_de_prueba("Prueba Tarde", [{"entrada": "14:00", "salida": "22:00"}])
noche = turno_de_prueba("Prueba Noche", [{"entrada": "22:00", "salida": "06:00"}],
                        dias="1111110")
partida = turno_de_prueba(
    "Prueba Partida",
    [{"entrada": "07:00", "salida": "11:00"}, {"entrada": "14:00", "salida": "18:00"}],
    dias="1111110",
)

verificar("el turno nocturno se reconoce como tal", noche["nocturno"], noche["horario"])
verificar("la jornada partida declara sus dos tramos",
          partida["partida"] and len(partida["tramos"]) == 2, partida["horario"])
verificar("y suma 8 horas previstas entre los dos tramos",
          partida["horas_previstas"] == 8.0, str(partida["horas_previstas"]))
verificar("los días se describen en castellano",
          partida["dias_texto"] == "Lun a Sáb", partida["dias_texto"])

for invalido, motivo in (
    ([{"entrada": "08:00", "salida": "08:00"}], "duración nula"),
    ([{"entrada": "08:00", "salida": "12:00"},
      {"entrada": "11:00", "salida": "15:00"}], "tramos superpuestos"),
    ([{"entrada": "25:00", "salida": "30:00"}], "hora inexistente"),
):
    try:
        auth.crear_turno(db, admin, f"Inválido {motivo}", invalido)
        verificar(f"se rechaza un turno con {motivo}", False)
    except ValueError as error:
        verificar(f"se rechaza un turno con {motivo}", True, str(error))

# ------------------------------------------------------------ 2. Resolución
print("\n2) Prioridad de resolución")

ana = empleado_de_prueba("turno_ana", "Ana Resolución")
resuelto = clock_engine.turno_vigente(db, ana["id"], LUNES)
verificar("sin turno propio rige el predeterminado de la empresa",
          resuelto.origen == "predeterminado", resuelto.resumen())

auth.asignar_turno_base(db, admin, ana["id"], manana["id"])
resuelto = clock_engine.turno_vigente(db, ana["id"], LUNES)
verificar("el turno del legajo gana al predeterminado",
          resuelto.origen == "legajo" and resuelto.nombre == "Prueba Mañana",
          resuelto.resumen())

auth.rotar_turno(db, admin, ana["id"], tarde["id"], LUNES,
                 LUNES + timedelta(days=6), "Relevo de vacaciones")
resuelto = clock_engine.turno_vigente(db, ana["id"], LUNES)
verificar("una rotación vigente gana al legajo",
          resuelto.origen == "asignacion" and resuelto.nombre == "Prueba Tarde",
          resuelto.resumen())

posterior = clock_engine.turno_vigente(db, ana["id"], LUNES + timedelta(days=20))
verificar("y al vencer, el empleado vuelve solo a su turno de contrato",
          posterior.nombre == "Prueba Mañana", posterior.resumen())

previa = clock_engine.turno_vigente(db, ana["id"], LUNES - timedelta(days=3))
verificar("antes de la vigencia tampoco se aplica",
          previa.nombre == "Prueba Mañana", previa.resumen())

# ---------------------------------------------------------- 3. La tardanza
print("\n3) La tardanza se mide contra el turno, no contra una constante")

beto = empleado_de_prueba("turno_beto", "Beto Mañanero")
auth.asignar_turno_base(db, admin, beto["id"], manana["id"])

puntual = datetime.combine(LUNES, time(6, 5)).astimezone()
evaluacion = clock_engine.evaluar_asistencia(db, beto["id"], puntual)
verificar("06:05 en el turno de mañana es normal",
          evaluacion["estado"] == "Normal", evaluacion["detalle"])
verificar("y se informa contra qué hora se lo midió",
          evaluacion["entrada_prevista"] == "06:00", evaluacion["entrada_prevista"])

tarde_marca = datetime.combine(LUNES, time(6, 40)).astimezone()
evaluacion = clock_engine.evaluar_asistencia(db, beto["id"], tarde_marca)
verificar("06:40 sí es tardanza en ese turno",
          evaluacion["estado"] == "Llegada Tardía", evaluacion["detalle"])

# La misma hora, en el turno tarde, ni siquiera es una llegada.
auth.asignar_turno_base(db, admin, beto["id"], tarde["id"])
evaluacion = clock_engine.evaluar_asistencia(db, beto["id"], tarde_marca)
verificar("la misma marca en el turno tarde no es tardanza",
          evaluacion["estado"] == "Normal", evaluacion["detalle"])

# Tolerancia propia del turno: desplaza a la del vínculo (15 min).
estricto = turno_de_prueba("Prueba Estricta", [{"entrada": "08:00", "salida": "16:00"}],
                           tolerancia=0)
auth.asignar_turno_base(db, admin, beto["id"], estricto["id"])
evaluacion = clock_engine.evaluar_asistencia(
    db, beto["id"], datetime.combine(LUNES, time(8, 5)).astimezone()
)
verificar("un turno con tolerancia cero no perdona 5 minutos",
          evaluacion["estado"] == "Llegada Tardía",
          f"tolerancia efectiva {evaluacion['tolerancia_efectiva_min']} min")

# ------------------------------------------------------- 4. Días de franco
print("\n4) Días fuera del turno")

auth.asignar_turno_base(db, admin, beto["id"], manana["id"])
sabado = LUNES + timedelta(days=5)
evaluacion = clock_engine.evaluar_asistencia(
    db, beto["id"], datetime.combine(sabado, time(9, 30)).astimezone()
)
verificar("trabajar un sábado fuera de turno no genera tardanza",
          evaluacion["estado"] == "Normal" and evaluacion["fuera_de_turno"],
          evaluacion["detalle"])

motor = ClockEngine(db, db.get_user_by_id(beto["id"]))
verificar("el sábado no es día laboral para ese turno",
          not motor.es_dia_laboral(sabado))
verificar("pero el lunes sí", motor.es_dia_laboral(LUNES))
verificar("y una falta el sábado no es injustificada",
          not motor.es_falta_no_justificada(sabado))

estado = reports._estado_del_dia(sabado, [], False, date.today(), en_turno=False)
verificar("el sábado libre figura como franco y no como ausente",
          estado == "franco", estado)

# --------------------------------------------- 5. Horas que rinde el turno
print("\n5) Horas previstas por el Art. 194")

verificar("el turno diurno de 8 horas rinde 8 ordinarias",
          clock_engine.horas_previstas_legales(
              turnos.desde_fila(db.get_turno(manana["id"])), LUNES
          ) == timedelta(hours=8))
verificar("el nocturno de 8 horas rinde solo 7 (tope del Art. 194)",
          clock_engine.horas_previstas_legales(
              turnos.desde_fila(db.get_turno(noche["id"])), LUNES
          ) == timedelta(hours=7))
verificar("la partida de 4+4 rinde 8",
          clock_engine.horas_previstas_legales(
              turnos.desde_fila(db.get_turno(partida["id"])), LUNES
          ) == timedelta(hours=8))

# ------------------------------------------------------ 6. Jornada partida
print("\n6) Jornada partida: dos entradas y dos salidas")

carla = empleado_de_prueba("turno_carla", "Carla Comercio")
auth.asignar_turno_base(db, admin, carla["id"], partida["id"])
motor_carla = ClockEngine(db, db.get_user_by_id(carla["id"]))
db.limpiar_marcajes_prueba(carla["id"], date.today() - timedelta(days=30),
                           date.today() + timedelta(days=1))

hoy = date.today()
turno_carla = clock_engine.turno_vigente(db, carla["id"], hoy)
verificar("el turno resuelto tiene dos tramos", len(turno_carla.tramos) == 2)

# La jornada se arma hacia atrás desde ahora: 4 h de mañana, 3 h de corte y
# 4 h de tarde que acaban de cerrarse. Ninguna marca cae en el futuro.
base = datetime.now().astimezone().replace(microsecond=0)
cierre_tarde = base - timedelta(minutes=10)
inicio_tarde = cierre_tarde - timedelta(hours=4)
cierre_manana = inicio_tarde - timedelta(hours=3)
inicio_manana = cierre_manana - timedelta(hours=4)

primera = db.open_clock_in(carla["id"], inicio_manana, False, "")
db.close_clock_out(primera, cierre_manana, False, timedelta(hours=4),
                   timedelta(0), timedelta(0), "", timedelta(0), "Diurna")
verificar("cerrado el primer tramo, la acción sigue siendo ENTRADA",
          motor_carla.detectar_accion_hoy() == "ENTRADA")

segunda = db.open_clock_in(carla["id"], inicio_tarde, False, "")
verificar("con el segundo tramo abierto corresponde SALIDA",
          motor_carla.detectar_accion_hoy() == "SALIDA")
db.close_clock_out(segunda, cierre_tarde, False, timedelta(hours=4),
                   timedelta(0), timedelta(0), "", timedelta(0), "Diurna")
try:
    motor_carla.detectar_accion_hoy()
    verificar("completados los dos tramos se rechaza una tercera entrada", False)
except ValueError as error:
    verificar("completados los dos tramos se rechaza una tercera entrada",
              "tramos" in str(error), str(error))

# Cerrar un tramo de 4 horas es cumplir, no irse antes.
incidencia = ClockEngine._clasificar_incidencia_salida(
    clock_engine.calcular_horas_paraguay(
        datetime.combine(LUNES, time(7, 0)), datetime.combine(LUNES, time(11, 0))
    ),
    "",
    timedelta(hours=4),
)
verificar("cumplir el tramo de 4 horas no es salida anticipada",
          incidencia == "", incidencia or "(sin incidencia)")

# --------------------------------------------------- 7. Turno nocturno
print("\n7) El turno nocturno no se bloquea a sí mismo")

dario = empleado_de_prueba("turno_dario", "Darío Nocturno")
auth.asignar_turno_base(db, admin, dario["id"], noche["id"])
motor_dario = ClockEngine(db, db.get_user_by_id(dario["id"]))
db.limpiar_marcajes_prueba(dario["id"], date.today() - timedelta(days=30),
                           date.today() + timedelta(days=1))

ahora = datetime.now().astimezone().replace(microsecond=0)
anoche = ahora - timedelta(hours=24)
jornada_anterior = db.open_clock_in(dario["id"], anoche, False, "")
db.close_clock_out(jornada_anterior, anoche + timedelta(hours=8), False,
                   timedelta(hours=7), timedelta(0), timedelta(hours=1), "",
                   timedelta(hours=7), "Nocturna")
verificar("la jornada de anoche no bloquea la de hoy",
          motor_dario.detectar_accion_hoy() == "ENTRADA")


consumidos = clock_engine.tramos_consumidos(db, dario["id"], ahora)
verificar("y no cuenta como tramo consumido de la jornada en curso",
          consumidos == 0, f"{consumidos} consumidos")

# El otro lado de la misma moneda: recién cerrada la jornada, volver a tocar
# el botón no puede abrir una entrada nueva. Es el caso que la ventana anclada
# al turno tiene que seguir atajando.
db.limpiar_marcajes_prueba(dario["id"], date.today() - timedelta(days=30),
                           date.today() + timedelta(days=1))
recien = ahora - timedelta(minutes=5)
cerrada = db.open_clock_in(dario["id"], recien - timedelta(hours=8), False, "")
db.close_clock_out(cerrada, recien, False, timedelta(hours=7), timedelta(0),
                   timedelta(hours=1), "", timedelta(hours=7), "Nocturna")
try:
    motor_dario.detectar_accion_hoy()
    verificar("pero volver a tocar el botón al salir no abre otra jornada", False)
except ValueError as error:
    verificar("pero volver a tocar el botón al salir no abre otra jornada",
              True, str(error))

# ------------------------------------------- 8. Una sola regla de tardanza
print("\n8) P2-4: corregir una marca no la castiga")

elsa = empleado_de_prueba("turno_elsa", "Elsa Corrección")
auth.asignar_turno_base(db, admin, elsa["id"], manana["id"])
marca = datetime.combine(LUNES, time(6, 12)).astimezone()
en_vivo = clock_engine.evaluar_asistencia(db, elsa["id"], marca)["estado"] != "Normal"
corregida = clock_engine.es_tardanza(db, elsa["id"], marca)
verificar("marcar y corregir a la misma hora dan el mismo veredicto",
          en_vivo == corregida, f"en vivo {en_vivo} · corrección {corregida}")
verificar("y a las 06:12 con gracia de 15 no es tardanza", not corregida)

# ------------------------------------------------------ 9. Reglas de baja
print("\n9) Un turno con gente adentro no se retira")

try:
    auth.retirar_turno(db, admin, manana["id"])
    verificar("no se puede retirar un turno con dotación", False)
except ValueError as error:
    verificar("no se puede retirar un turno con dotación", True, str(error))

predeterminado = next(t for t in db.listar_turnos() if t["predeterminado"])
try:
    auth.retirar_turno(db, admin, predeterminado["id"])
    verificar("ni el predeterminado", False)
except ValueError as error:
    verificar("ni el predeterminado", True, str(error))

vacante = turno_de_prueba("Prueba Vacante", [{"entrada": "09:00", "salida": "17:00"}])
resultado = auth.retirar_turno(db, admin, vacante["id"])
verificar("uno sin usar se elimina directamente", resultado == "eliminado", resultado)

try:
    auth.listar_turnos(db, db.get_user_by_id(ana["id"]))
    verificar("un Empleado puede consultar el catálogo", True)
except PermissionError as error:
    verificar("un Empleado puede consultar el catálogo", False, str(error))

try:
    auth.crear_turno(db, db.get_user_by_id(ana["id"]), "Turno Pirata",
                     [{"entrada": "00:00", "salida": "08:00"}])
    verificar("pero no puede crear turnos", False)
except PermissionError as error:
    verificar("pero no puede crear turnos", True, str(error))

# ------------------------------------------------------------- 10. Portal
print("\n10) El empleado ve su horario en el portal")

resumen = reports.resumen_empleado(db, db.get_user_by_id(beto["id"]))
verificar("el resumen expone el turno vigente",
          resumen["turno"]["nombre"] == "Prueba Mañana", resumen["turno"]["horario"])
verificar("con su horario y sus días",
          resumen["turno"]["dias_texto"] == "Lun a Vie"
          and resumen["turno"]["entrada_prevista"] == "06:00")

propio = auth.turno_de_empleado(db, db.get_user_by_id(ana["id"]), ana["id"])
verificar("y cada uno puede consultar el suyo", propio["nombre"] in
          ("Prueba Mañana", "Prueba Tarde"), propio["nombre"])
try:
    auth.turno_de_empleado(db, db.get_user_by_id(ana["id"]), beto["id"])
    verificar("pero no el de un compañero", False)
except PermissionError as error:
    verificar("pero no el de un compañero", True, str(error))

# --------------------------------------------- 11. Reposición sin conexión
print("\n11) La cola offline repone los dos tramos de la partida")

import os
import tempfile

from offline_queue import ColaOffline
import sync_worker

ruta_cola = os.path.join(tempfile.gettempdir(), "cola_turnos_prueba.db")
if os.path.exists(ruta_cola):
    os.remove(ruta_cola)
cola = ColaOffline(ruta=ruta_cola)

felipe = empleado_de_prueba("turno_felipe", "Felipe Offline")
auth.asignar_turno_base(db, admin, felipe["id"], partida["id"])
db.limpiar_marcajes_prueba(felipe["id"], LUNES - timedelta(days=2),
                           LUNES + timedelta(days=2))

for hora in (time(7, 0), time(11, 0), time(14, 0), time(18, 0)):
    cola.encolar("turno_felipe", datetime.combine(LUNES, hora).astimezone())
resumen = sync_worker.sincronizar(cola, db=db)
repuestos = db.get_entries_by_date(felipe["id"], LUNES)
verificar("las cuatro marcas suben sin descartes",
          resumen["subidas"] == 4 and resumen["descartadas"] == 0, str(resumen))
verificar("y quedan dos jornadas cerradas, no una",
          len(repuestos) == 2
          and all(m["hora_salida"] is not None for m in repuestos),
          f"{len(repuestos)} marcaje(s)")
verificar("ninguna se reprocha como salida anticipada",
          all(not (m["tipo_incidencia"] or "") for m in repuestos),
          " · ".join(m["tipo_incidencia"] or "sin incidencia" for m in repuestos))

# ------------------------------------------------------------- Limpieza
for usuario in ("turno_ana", "turno_beto", "turno_carla", "turno_dario", "turno_elsa",
                "turno_felipe"):
    registro = db.get_user_by_username(usuario)
    if registro:
        auth.delete_user(db, admin, registro["id"])
for nombre in ("Prueba Mañana", "Prueba Tarde", "Prueba Noche", "Prueba Partida",
               "Prueba Estricta"):
    registro = db.get_turno_por_nombre(nombre)
    if registro:
        db.eliminar_turno(registro["id"])

print()
if fallos:
    print(f"TURNOS: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("TURNOS OK · P2-5 cerrado: horarios, rotación, jornada partida y francos")
