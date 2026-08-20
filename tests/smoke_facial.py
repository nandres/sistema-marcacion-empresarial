import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import cv2

import database
import facial

print("motor disponible:", facial.disponible())

db = database.Database()
db.initialize()

# 1) Frame sin rostro (sólido): registrar_foto debe rechazar sin guardar
frame_vacio = np.full((480, 640, 3), 128, dtype=np.uint8)
ok, detalle = facial.registrar_foto(db, 2, frame_vacio)
print("sin rostro:", ok, "|", detalle, "| foto guardada?", db.tiene_foto(2))

# 2) validar sin rostro → bloqueo
ok2, detalle2 = facial.validar(db, 2, frame_vacio)
print("validar sin rostro:", ok2, "|", detalle2)

# 3) Simular detección: recortar siempre un rectángulo (x,y,w,h)
facial._detectar_rostros = lambda frame: [(80, 60, 240, 240)]

# 4) registrar foto del usuario 2 con una imagen sintética
semilla = np.random.RandomState(42)
cara_juan = semilla.randint(0, 256, (480, 640, 3), dtype=np.uint8)
ok3, detalle3 = facial.registrar_foto(db, 2, cara_juan)
print("registrar foto:", ok3, "|", detalle3, "| foto guardada?", db.tiene_foto(2))

# 5) validar con la MISMA imagen → debe coincidir (confianza 0)
ok4, detalle4 = facial.validar(db, 2, cara_juan)
print("validar mismo rostro:", ok4, "|", detalle4)

# 6) validar con OTRA imagen → debe rechazar (bloquea la marcación)
cara_otro = np.zeros((480, 640, 3), dtype=np.uint8)
ok5, detalle5 = facial.validar(db, 2, cara_otro)
print("validar otro rostro:", ok5, "|", detalle5)

# 7) usuario sin foto → validación omitida
db.eliminar_foto(2)
ok6, detalle6 = facial.validar(db, 2, cara_juan)
print("sin foto registrada:", ok6, "|", detalle6)

db.cerrar()
print("SMOKE FACIAL OK")