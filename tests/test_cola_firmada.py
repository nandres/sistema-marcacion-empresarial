"""P3-10: la cola offline dejaba fabricar marcaciones a mano.

El kiosco guarda en un SQLite del disco las marcas que no pudo subir, y el
sincronizador las reinyectaba **sin verificar nada**: quien pudiera escribir
ese archivo fabricaba horas de trabajo a nombre de quien quisiera, y entraban
al servidor central como legítimas.

Además, una marca que fallaba de forma permanente —un usuario borrado, una
fecha imposible— se reintentaba cada quince segundos para siempre: no había
contador ni lugar donde apartarla.

Acá se comprueban las dos cosas atacando la cola, no leyéndola: se escribe una
fila a mano, se altera una legítima y se encola una que nunca va a entrar.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import database
import offline_queue
import sync_worker
from offline_queue import ColaOffline

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(
        f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
        + (f" | {detalle}" if detalle else "")
    )


RUTA = os.path.join(tempfile.gettempdir(), "cola_firmada_prueba.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
cola = ColaOffline(ruta=RUTA)

db = database.Database()
db.initialize()
empleado = db.usuario_por_cedula("juan")
DIA = datetime.now().astimezone() - timedelta(days=9)
db.limpiar_marcajes_prueba(empleado["id"], DIA.date(), DIA.date())

print("COLA OFFLINE FIRMADA · P3-10")

# ---------------------------------------------------------------- 1. Firma
print("\n1) Cada marca se firma al encolarla")

legitima = cola.encolar("juan", DIA, "Verificada")
guardada = cola.pendientes()[0]
verificar("la marca del kiosco queda firmada",
          offline_queue.verificar(guardada), guardada["firma"][:16] + "…")
verificar("y conserva el veredicto biométrico del momento",
          guardada["verificacion_facial"] == "Verificada")


def escribir_a_mano(sync_id: str, username: str, momento: datetime) -> None:
    """Simula a quien puede escribir el archivo pero no conoce la clave."""
    conexion = sqlite3.connect(RUTA)
    conexion.execute(
        "INSERT INTO pendientes (sync_id, username, momento_iso, creado_en_iso) "
        "VALUES (?, ?, ?, ?)",
        (sync_id, username, momento.isoformat(),
         datetime.now().astimezone().isoformat()),
    )
    conexion.commit()
    conexion.close()


escribir_a_mano("falsificada01", "juan", DIA + timedelta(hours=1))
fabricada = [f for f in cola.pendientes() if f["sync_id"] == "falsificada01"][0]
verificar("una fila escrita a mano no verifica",
          not offline_queue.verificar(fabricada))

conexion = sqlite3.connect(RUTA)
conexion.execute("UPDATE pendientes SET username = 'admin' WHERE sync_id = ?",
                 (legitima["sync_id"],))
conexion.commit()
conexion.close()
alterada = [f for f in cola.pendientes() if f["sync_id"] == legitima["sync_id"]][0]
verificar("cambiarle el empleado a una fila legítima la invalida",
          not offline_queue.verificar(alterada))

# ------------------------------------------- 2. El sincronizador la aparta
print("\n2) Lo que no verifica no entra al servidor central")

antes = len(db.marcajes_del_dia(empleado["id"], DIA.date()))
resumen = sync_worker.sincronizar(cola, db=db)
despues = len(db.marcajes_del_dia(empleado["id"], DIA.date()))

verificar("las dos filas sin firma válida se cuentan como falsificadas",
          resumen["falsificadas"] == 2, str(resumen))
verificar("y no se creó ningún marcaje", despues == antes,
          f"{antes} antes · {despues} después")
verificar("la cola queda vacía: no se reintentan", len(cola) == 0)

apartadas = cola.descartadas()
verificar("quedan apartadas con su motivo, no borradas en silencio",
          len(apartadas) == 2
          and all("Firma" in a["motivo"] for a in apartadas),
          "; ".join(a["motivo"] for a in apartadas))

# -------------------------------------- 3. Lo legítimo sigue entrando
print("\n3) Una marca firmada por el kiosco sí entra")

cola.encolar("juan", DIA, "Verificada")
resumen = sync_worker.sincronizar(cola, db=db)
verificar("sube la marca firmada", resumen["subidas"] == 1, str(resumen))
marcas = db.marcajes_del_dia(empleado["id"], DIA.date())
verificar("y llega con el veredicto biométrico que registró el kiosco",
          marcas and marcas[-1]["verificacion_facial"] == "Verificada",
          marcas[-1]["verificacion_facial"] if marcas else "sin marcas")

# ------------------------------------------ 4. Reintentos con un techo
print("\n4) Una marca que nunca va a entrar deja de reintentarse")

cola.encolar("no_existe_este_usuario", DIA + timedelta(hours=3), "")
intentos = 0
while len(cola) and intentos < offline_queue.MAX_INTENTOS + 5:
    sync_worker.sincronizar(cola, db=db)
    intentos += 1

verificar("la cola se vacía en lugar de insistir para siempre",
          len(cola) == 0, f"{intentos} lotes")
verificar("no superó el techo de intentos",
          intentos <= offline_queue.MAX_INTENTOS + 1,
          f"{intentos} vs techo {offline_queue.MAX_INTENTOS}")
descartada = [a for a in cola.descartadas()
              if a["username"] == "no_existe_este_usuario"]
verificar("y queda apartada con el motivo del fallo",
          len(descartada) == 1 and "intentos" in descartada[0]["motivo"],
          descartada[0]["motivo"][:70] if descartada else "no se apartó")

alertas = [a for a in db.listar_alertas(limite=40)
           if a["tipo"] in ("cola_falsificada", "cola_descartada")]
verificar("Recursos Humanos se entera de las dos situaciones",
          {a["tipo"] for a in alertas} == {"cola_falsificada", "cola_descartada"},
          ", ".join(sorted({a["tipo"] for a in alertas})))

db.limpiar_marcajes_prueba(empleado["id"], DIA.date(), DIA.date())
db.cerrar()
os.remove(RUTA)

print()
if fallos:
    print(f"COLA OFFLINE: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("COLA OFFLINE OK · P3-10 cerrado: firma, descarte y techo de reintentos")
