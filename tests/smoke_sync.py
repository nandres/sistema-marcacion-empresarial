import os
import sys
import tempfile

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timedelta, timezone

import database
from offline_queue import ColaOffline
import sync_worker

ruta = os.path.join(tempfile.gettempdir(), "marcaciones_offline_prueba.db")
if os.path.exists(ruta):
    os.remove(ruta)

cola = ColaOffline(ruta=ruta)

db = database.Database()
db.initialize()

hoy = datetime.now(timezone.utc).date()
hace = hoy - timedelta(days=7)
db.limpiar_marcajes_prueba(2, hace - timedelta(days=1), hace)

# 1) Entrada puntual (07:30) -> estado Normal, sin alerta
momento_entrada = datetime(hace.year, hace.month, hace.day, 7, 30, tzinfo=timezone.utc)
cola.encolar("juan", momento_entrada, False)

# 2) Entrada tardía (09:30) -> Llegada Tardía -> alerta para RRHH
momento_tarde = datetime(hace.year, hace.month, hace.day - 1, 9, 30, tzinfo=timezone.utc)
cola.encolar("juan", momento_tarde, False)

# 3) Salida del día puntual (17:00) -> cierra la entrada del punto 1
momento_salida = datetime(hace.year, hace.month, hace.day, 17, 0, tzinfo=timezone.utc)
cola.encolar("juan", momento_salida, False)

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

registros2 = db.get_entries_by_date(2, hace - timedelta(days=1))
print("registros día tardío:", len(registros2), "incidencia:", registros2[0]["tipo_incidencia"])

# 4) Segundo lote: no debe duplicar nada
resumen2 = sync_worker.sincronizar(cola, db=db)
print("lote 2 (duplicados):", resumen2, "cola restante:", len(cola))

# 5) Reintento de los mismos momentos: sync_id único impide duplicados
cola.encolar("juan", momento_entrada, False)
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