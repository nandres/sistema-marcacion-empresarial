import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import UTC, datetime, timedelta

import database
import sync_worker
from offline_queue import ColaOffline

ruta = os.path.join(tempfile.gettempdir(), "marcaciones_offline_prueba.db")
if os.path.exists(ruta):
    os.remove(ruta)

cola = ColaOffline(ruta=ruta)

db = database.Database()
db.initialize()

hoy = datetime.now(UTC).date()
hace = hoy - timedelta(days=7)
db.limpiar_marcajes_prueba(2, hace - timedelta(days=1), hace)

# 1) Entrada puntual (07:30) -> estado Normal, sin alerta
momento_entrada = datetime(hace.year, hace.month, hace.day, 7, 30, tzinfo=UTC)
cola.encolar("juan", momento_entrada)

# 2) Entrada tardía -> Llegada Tardía -> alerta para RRHH.
# La hora se construye en horario local y en un día que el turno cubra: la
# tardanza se mide contra el reloj de pared del turno, no contra UTC, y fuera
# de los días del turno no hay hora a la cual llegar tarde.
dia_tarde = hace - timedelta(days=1)
while dia_tarde.weekday() > 4:
    dia_tarde -= timedelta(days=1)
db.limpiar_marcajes_prueba(2, dia_tarde, dia_tarde)
momento_tarde = datetime(
    dia_tarde.year, dia_tarde.month, dia_tarde.day, 9, 30
).astimezone()
cola.encolar("juan", momento_tarde)

# 3) Salida del día puntual (17:00) -> cierra la entrada del punto 1
momento_salida = datetime(hace.year, hace.month, hace.day, 17, 0, tzinfo=UTC)
cola.encolar("juan", momento_salida)

pendientes = cola.pendientes()
print("encoladas:", len(pendientes))

resumen = sync_worker.sincronizar(cola, db=db)
print("lote 1:", resumen, "cola restante:", len(cola))

registros = db.get_entries_by_date(2, hace)
print("registros día puntual:", len(registros))
r = registros[0]
print("entrada preservada:", r["hora_entrada"].isoformat(), "== 07:30 UTC?",
      r["hora_entrada"] == momento_entrada)
print("salida preservada:", r["hora_salida"].isoformat(), "== 17:00 UTC?",
      r["hora_salida"] == momento_salida)
print("sync_id presente:", bool(r["sync_id"]))

registros2 = db.get_entries_by_date(2, dia_tarde)
print("registros día tardío:", len(registros2),
      "incidencia:", registros2[0]["tipo_incidencia"])

# 4) Segundo lote: no debe duplicar nada
resumen2 = sync_worker.sincronizar(cola, db=db)
print("lote 2 (duplicados):", resumen2, "cola restante:", len(cola))

# 5) Reintento de los mismos momentos: sync_id único impide duplicados
cola.encolar("juan", momento_entrada)
resumen3 = sync_worker.sincronizar(cola, db=db)
print("lote 3 (reinserción):", resumen3)
print("sin duplicados:", len(db.get_entries_by_date(2, hace)) == 1)

alertas = db.listar_alertas(limite=30)
tardanzas = [a for a in alertas if a["tipo"] == "marcacion_incidente"]
print("alertas de incidente generadas:", len(tardanzas))
for a in tardanzas:
    print(" -", a["mensaje"], "| usuario_id:", a["usuario_id"])

db.cerrar()
print("SMOKE OFFLINE SYNC OK")
