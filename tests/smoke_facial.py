"""Control biométrico del kiosco y la política que decide sobre su veredicto.

El hallazgo P1-2 era que ``validar`` devolvía "verificado" cuando el empleado
no tenía foto de referencia —el estado por defecto de todo empleado nuevo—,
así que bastaba con no registrarla para quedar exento del control. Esta
prueba fija los tres estados posibles y lo que la política hace con cada uno.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

import database
import facial

print("motor disponible:", facial.disponible())

db = database.Database()
db.initialize()

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}" +
          (f" | {detalle}" if detalle else ""))


# 1) Frame sólido: no hay rostro que registrar.
frame_vacio = np.full((480, 640, 3), 128, dtype=np.uint8)
ok, detalle = facial.registrar_foto(db, 2, frame_vacio)
verificar("frame sin rostro no se guarda como foto", not ok and not db.tiene_foto(2), detalle)

# 2) Captura inservible: se rechaza, no se omite.
resultado = facial.validar(db, 2, frame_vacio)
verificar("captura sin rostro se rechaza", resultado.estado == facial.RECHAZADA, resultado.detalle)
verificar("rechazo bloquea la marca", not facial.decidir(resultado).permitir)

# 3) Sin cámara tampoco hay verificación posible.
resultado = facial.validar(db, 2, None)
verificar("sin imagen el estado es no verificable",
          resultado.estado == facial.NO_VERIFICABLE, resultado.detalle)

# Detección simulada: recorta siempre el mismo rectángulo.
facial._detectar_rostros = lambda frame: [(80, 60, 240, 240)]

semilla = np.random.RandomState(42)
cara_juan = semilla.randint(0, 256, (480, 640, 3), dtype=np.uint8)
ok, detalle = facial.registrar_foto(db, 2, cara_juan)
verificar("foto de referencia registrada", ok and db.tiene_foto(2), detalle)

# 4) Mismo rostro: verificado.
resultado = facial.validar(db, 2, cara_juan)
verificar("mismo rostro se verifica", resultado.verificada, resultado.detalle)
verificar("verificación marca la fila como Verificada",
          facial.decidir(resultado).marca == facial.VERIFICADA)

# 5) Otro rostro: rechazo con identidad comprobable, o sea fraude.
cara_otro = np.zeros((480, 640, 3), dtype=np.uint8)
resultado = facial.validar(db, 2, cara_otro)
verificar("otro rostro se rechaza", resultado.rechazada, resultado.detalle)
verificar("el rechazo bloquea siempre", not facial.decidir(resultado).permitir)

# 6) El corazón de P1-2: sin foto de referencia ya no se aprueba.
db.eliminar_foto(2)
resultado = facial.validar(db, 2, cara_juan)
verificar("sin foto de referencia NO se da por verificada",
          not resultado.verificada, resultado.detalle)
verificar("sin foto el estado es no verificable",
          resultado.estado == facial.NO_VERIFICABLE)

# 7) La política decide qué hacer con lo no verificable, y lo deja marcado.
os.environ.pop("BIOMETRIA_OBLIGATORIA", None)
permisiva = facial.decidir(resultado)
verificar("con política permisiva la marca pasa", permisiva.permitir)
verificar("pero queda registrada como no verificada",
          permisiva.marca == "No verificada", permisiva.marca)
verificar("y avisa a RRHH con severidad media", permisiva.severidad == "media")

os.environ["BIOMETRIA_OBLIGATORIA"] = "1"
estricta = facial.decidir(resultado)
verificar("con biometría obligatoria la marca se bloquea", not estricta.permitir,
          estricta.motivo)
os.environ.pop("BIOMETRIA_OBLIGATORIA", None)

db.cerrar()
print()
if fallos:
    print(f"SMOKE FACIAL: {fallos} problema(s)")
    raise SystemExit(1)
print("SMOKE FACIAL OK · tres estados y política verificados")
