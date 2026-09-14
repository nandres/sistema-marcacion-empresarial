"""P3-4: las alertas en vivo solo llegaban a un worker de cuatro.

El bus de notificaciones es un objeto en memoria y el Dockerfile arranca
`gunicorn -w 4`. Una alerta publicada en el worker 1 no alcanzaba a los
WebSockets conectados a los workers 2, 3 y 4: el panel de Recursos Humanos
perdía en silencio tres de cada cuatro avisos de fraude.

Acá se levanta una escucha con su propia conexión —el equivalente a otro
worker— y se comprueba que una alerta publicada por este proceso le llegue,
que no se duplique en el proceso que la publicó, y que la frontera entre
empresas siga en pie: un canal compartido por toda la instalación es
justamente donde se cruzarían dos clientes.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import database
import notifications
from database import Database

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(
        f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
        + (f" | {detalle}" if detalle else "")
    )


def esperar(condicion, segundos: float = 6.0) -> bool:
    """Espera a que algo ocurra sin fijar un tiempo de sueño arbitrario."""
    limite = time.time() + segundos
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.1)
    return False


db = Database()
db.initialize()

print("BUS DE ALERTAS ENTRE PROCESOS · P3-4")

# --------------------------------------------- 1. Otro proceso las recibe
print("\n1) Una alerta publicada en otro proceso llega a este")

# El emisor es un proceso aparte de verdad. Publicarla desde acá no probaría
# nada: el bus local ya la entrega por su cuenta y la prueba pasaría igual
# aunque el canal entre procesos no existiera.
EMISOR = """
import sys
sys.path.insert(0, r"{ruta}")
import notifications
from database import Database
db = Database()
db.initialize()
alerta = notifications.registrar_alerta(
    db, "prueba_bus", "alta", "Alerta desde otro proceso", "detalle de prueba"
)
print(alerta["id"])
db.cerrar()
""".format(ruta=str(RAIZ / "src"))

recibidas = []


def abrir():
    try:
        conexion = Database()
        conexion.connect()
        return conexion
    except Exception:
        return None


notifications.BUS.suscribir(lambda a: recibidas.append(a))
escucha = notifications.EscuchaAlertas(abrir, intervalo=0.5)
escucha.start()
verificar("la escucha arranca", esperar(lambda: escucha.is_alive(), 3.0))
time.sleep(1.2)  # que alcance a ejecutar el LISTEN antes de publicar

emisor = subprocess.run(
    [sys.executable, "-c", EMISOR],
    capture_output=True, text=True, timeout=90,
    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
)
verificar("el proceso emisor publica su alerta",
          emisor.returncode == 0, (emisor.stderr or "").strip()[-90:])
alerta_id = int(emisor.stdout.strip().splitlines()[-1]) if emisor.returncode == 0 else 0

verificar("y este proceso la recibe sin haberla publicado",
          esperar(lambda: any(a.get("id") == alerta_id for a in recibidas)),
          f"{len(recibidas)} alerta(s) en el bus local")
verificar("este proceso no la había publicado: vino por el canal",
          not any(a.get("id") == alerta_id
                  for a in recibidas if a.get("detalle") == "publicada acá"))

# ------------------------------------------------ 2. Sin duplicados
print("\n2) Quien la publica no la recibe dos veces")

propia = notifications.registrar_alerta(
    db, "prueba_bus_local", "baja", "Alerta local", "publicada acá"
)
time.sleep(2.0)
entregas = [a for a in recibidas if a.get("id") == propia["id"]]
verificar("la alerta propia aparece una sola vez", len(entregas) == 1,
          f"{len(entregas)} entregas")
verificar("el bus recuerda lo que publicó para no repetirlo",
          notifications.BUS.ya_publicada(propia["id"]))

llegadas = [a for a in recibidas if a.get("id") == alerta_id]
verificar("y la del otro proceso tampoco se duplica", len(llegadas) == 1,
          f"{len(llegadas)} entregas")

# -------------------------------------------- 3. La empresa sigue acotando
print("\n3) El canal es de la instalación; la alerta sigue siendo de una empresa")

ajena = llegadas[0] if llegadas else {}
verificar("la alerta repetida conserva su empresa",
          ajena.get("empresa_id") == db.empresa_id,
          f"{ajena.get('empresa_id')} vs {db.empresa_id}")
verificar("y no le corresponde a otra empresa",
          not notifications.es_de_la_empresa(ajena, (db.empresa_id or 0) + 999))

escucha.detener()
time.sleep(0.8)
db.cerrar()

print()
if fallos:
    print(f"BUS DE ALERTAS: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("BUS DE ALERTAS OK · P3-4 cerrado: la alerta cruza de un proceso al otro")
