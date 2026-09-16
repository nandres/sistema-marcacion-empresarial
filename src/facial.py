"""Validación facial del kiosco contra el modelo de Buddy Punching.

Cada colaborador registra una foto (tomada con la cámara del kiosco y
almacenada en la tabla ``fotos`` como JPEG). Antes de registrar la
asistencia, el kiosco captura ~1 segundo de cámara, detecta el rostro
(Haar Cascade) y lo compara con todas las fotos de referencia mediante
un reconocedor LBPH entrenado al vuelo (población pequeña, kiosco local).

El resultado tiene **tres** estados y no dos. Un control que no puede
comprobar la identidad —porque el empleado todavía no registró su foto,
porque no hay cámara o porque falta OpenCV— no puede devolver "verificado":
devuelve ``NO_VERIFICABLE``, y la política decide si eso bloquea la marca
o la deja pasar marcada y con aviso a Recursos Humanos. La versión
anterior devolvía ``True`` en ese caso, que es el estado por defecto de
todo empleado nuevo: bastaba con no registrar la foto para quedar exento.

Requiere ``opencv-python`` + ``opencv-contrib-python`` (cv2.face).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, List, Optional, Tuple

import numpy as np

UMBRAL_CONFIANZA: float = 80.0
"""Confianza LBPH máxima aceptada (menor es mejor)."""

VERIFICADA = "Verificada"
RECHAZADA = "Rechazada"
NO_VERIFICABLE = "No verificable"


@dataclass(frozen=True)
class Resultado:
    """Veredicto del control biométrico sobre una captura."""

    estado: str
    detalle: str

    @property
    def verificada(self) -> bool:
        return self.estado == VERIFICADA

    @property
    def rechazada(self) -> bool:
        """Hubo identidad comprobable y no coincidió: es fraude, no falta de datos.

        Una suplantación por foto también lo es: la cara podía ser la correcta,
        pero quien la puso delante de la cámara no estaba ahí.
        """
        return self.estado in (RECHAZADA, SUPLANTADA)

    def marca(self) -> str:
        """Etiqueta que viaja con el marcaje en ``marcajes.verificacion_facial``."""
        return VERIFICADA if self.verificada else "No verificada"


@dataclass(frozen=True)
class Decision:
    """Qué hacer con una marca según el veredicto biométrico."""

    permitir: bool
    marca: str
    severidad: str
    motivo: str


def obligatoria() -> bool:
    """Indica si una marca sin verificación biométrica debe bloquearse.

    Se controla con ``BIOMETRIA_OBLIGATORIA`` y viene desactivada: una
    plantilla recién migrada no tiene fotos cargadas y activarla de entrada
    dejaría a todos sin poder marcar. Mientras esté apagada, la marca pasa
    pero queda registrada como no verificada y Recursos Humanos recibe el
    aviso — que es lo contrario de darla por buena en silencio.
    """
    return _encendida("BIOMETRIA_OBLIGATORIA")


def con_prueba_de_vida() -> bool:
    """Indica si el kiosco pide un gesto antes de aceptar la marca.

    Se controla con ``BIOMETRIA_PRUEBA_VIDA`` y viene desactivada, porque
    cambia lo que la persona tiene que hacer para marcar: encenderla sin
    avisar deja a la plantilla frente a un kiosco que le pide algo que nadie
    le explicó.
    """
    return _encendida("BIOMETRIA_PRUEBA_VIDA")


def _encendida(variable: str) -> bool:
    """Lee un interruptor del entorno tolerando cómo lo escriba cada quien."""
    return os.getenv(variable, "").strip().lower() in (
        "1", "true", "si", "sí", "on",
    )


def decidir(resultado: Resultado) -> Decision:
    """Aplica la política sobre un veredicto biométrico.

    Un rostro que no coincide bloquea siempre: ahí hay identidad
    comprobable y no es la que dice ser. La falta de datos para comparar
    sigue la política configurada.
    """
    if resultado.verificada:
        return Decision(True, VERIFICADA, "baja", resultado.detalle)
    if resultado.rechazada:
        return Decision(False, "No verificada", "alta", resultado.detalle)
    if obligatoria():
        return Decision(
            False,
            "No verificada",
            "alta",
            f"{resultado.detalle} La verificación biométrica es obligatoria.",
        )
    return Decision(True, "No verificada", "media", resultado.detalle)

TAMANO_CARA: int = 200
"""Tamaño al que se normaliza cada rostro para entrenar y comparar."""

_RUTA_CASCADA: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data",
    "haarcascade_frontalface_default.xml",
)


def disponible() -> bool:
    """Indica si el motor de visión está operativo (OpenCV + cv2.face)."""
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def _cascada():
    import cv2

    ruta = _RUTA_CASCADA
    if not os.path.exists(ruta):
        ruta = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    return cv2.CascadeClassifier(ruta)


def _detectar_rostros(frame) -> List[Tuple[int, int, int, int]]:
    """Devuelve los rectángulos (x, y, w, h) de los rostros del frame."""
    import cv2

    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gris = cv2.equalizeHist(gris)
    rostros = _cascada().detectMultiScale(
        gris, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )
    return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in rostros]


def _recortar_rostro(frame, rect: Tuple[int, int, int, int]):
    """Extrae y normaliza el rostro a escala de grises."""
    import cv2

    x, y, w, h = rect
    cara = frame[y : y + h, x : x + w]
    cara = cv2.cvtColor(cara, cv2.COLOR_BGR2GRAY)
    return cv2.resize(cara, (TAMANO_CARA, TAMANO_CARA))


def capturar(segundos: float = 1.0, camara: int = 0) -> Optional[Any]:
    """Toma ``segundos`` de cámara y devuelve el último frame con rostro.

    Returns:
        Frame BGR con al menos un rostro, o ``None`` si la cámara falla
        o no aparece ninguna cara durante la ventana.
    """
    if not disponible():
        return None
    import cv2

    captura = cv2.VideoCapture(camara)
    if not captura.isOpened():
        return None
    intentos = max(3, int(segundos * 8))
    mejor = None
    try:
        for _ in range(intentos):
            ok, frame = captura.read()
            if not ok:
                continue
            if _detectar_rostros(frame):
                mejor = frame
    finally:
        captura.release()
    return mejor


def registrar_foto(db: Any, user_id: int, frame_bgr) -> Tuple[bool, str]:
    """Detecta el rostro del frame y lo guarda como foto de referencia.

    Returns:
        Tupla ``(ok, detalle)``; si no hay rostro no se guarda nada.
    """
    import cv2

    rostros = _detectar_rostros(frame_bgr)
    if not rostros:
        return False, "No se detectó ningún rostro en la captura."
    if len(rostros) > 1:
        return False, "Se detectó más de un rostro; use una foto individual."
    cara = _recortar_rostro(frame_bgr, rostros[0])
    ok, bytes_jpg = cv2.imencode(".jpg", cara, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return False, "No se pudo codificar la fotografía."
    db.guardar_foto(user_id, bytes_jpg.tobytes())
    return True, "Foto biométrica registrada correctamente."


def _aumentar(cara) -> List[Any]:
    """Genera variaciones del rostro para que LBPH pueda entrenar con pocas
    fotos de referencia (pequeños desplazamientos del recorte)."""
    import cv2

    variantes = [cara]
    for dx, dy in ((-6, 0), (6, 0), (0, -6), (0, 6), (-6, -6)):
        matriz = np.float32([[1, 0, dx], [0, 1, dy]])
        variantes.append(cv2.warpAffine(cara, matriz, (TAMANO_CARA, TAMANO_CARA)))
    return variantes


def validar(db: Any, user_id: int, frame_bgr) -> Resultado:
    """Compara el rostro capturado contra las fotos de referencia.

    Returns:
        ``Resultado`` con estado ``Verificada`` (coincide), ``Rechazada``
        (hay con qué comparar y no coincide, o la captura es inservible) o
        ``No verificable`` (falta la foto de referencia del empleado).
        Quién bloquea la marca lo decide la política, no este módulo.
    """
    import cv2

    if frame_bgr is None:
        return Resultado(NO_VERIFICABLE, "No se obtuvo imagen de la cámara.")
    rostros = _detectar_rostros(frame_bgr)
    if not rostros:
        return Resultado(RECHAZADA, "No se detectó ningún rostro en la captura.")
    if len(rostros) > 1:
        return Resultado(RECHAZADA, "Se detectó más de un rostro en la captura.")
    captura = _recortar_rostro(frame_bgr, rostros[0])

    muestras: List[Any] = []
    etiquetas: List[int] = []
    fotos = db.listar_fotos()
    for foto in fotos:
        imagen = cv2.imdecode(np.frombuffer(foto["imagen"], np.uint8), cv2.IMREAD_COLOR)
        if imagen is None:
            continue
        rostros_foto = _detectar_rostros(imagen)
        if not rostros_foto:
            continue
        cara = _recortar_rostro(imagen, rostros_foto[0])
        muestras.extend(_aumentar(cara))
        etiquetas.extend([int(foto["user_id"])] * 6)

    if not any(int(foto["user_id"]) == user_id for foto in fotos):
        return Resultado(
            NO_VERIFICABLE,
            "El empleado no tiene foto de referencia registrada.",
        )
    if not any(etiqueta == user_id for etiqueta in etiquetas):
        return Resultado(
            NO_VERIFICABLE,
            "La foto de referencia guardada no es legible; hay que volver a tomarla.",
        )

    modelo = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    modelo.train(np.array(muestras), np.array(etiquetas))
    etiqueta, confianza = modelo.predict(captura)
    if etiqueta == user_id and confianza < UMBRAL_CONFIANZA:
        return Resultado(VERIFICADA, f"Rostro verificado (confianza {confianza:.0f}).")
    return Resultado(
        RECHAZADA,
        f"El rostro no coincide con la foto de la cédula "
        f"(confianza {confianza:.0f}).",
    )


# --- Prueba de vida ---------------------------------------------------------

SUPLANTADA = "Suplantada"
"""La cara coincide pero no dio señales de estar viva: es una foto."""

MOVIMIENTO_MINIMO: float = 0.06
"""Cuánto tiene que desplazarse el rostro, en anchos de cara, para aceptar el
desafío. Un 6 % son unos diez píxeles en una cara de 180: por debajo de eso
está el temblor de una mano sosteniendo un teléfono."""

CAMBIO_MINIMO: float = 3.0
"""Diferencia media de píxeles entre cuadros dentro del rostro. Una imagen
plana —impresa o en pantalla— apenas varía; una cara viva cambia siempre."""

CUADROS_MINIMOS: int = 6
"""Cuadros con rostro que hacen falta para tener algo que comparar."""

ACERCARSE = "acercarse"
ALEJARSE = "alejarse"
GIRAR_IZQUIERDA = "girar a la izquierda"
GIRAR_DERECHA = "girar a la derecha"

DESAFIOS: Tuple[str, ...] = (ACERCARSE, GIRAR_IZQUIERDA, GIRAR_DERECHA)


def desafio_al_azar() -> str:
    """Elige el gesto que el kiosco va a pedir.

    Se sortea en cada marcación: un desafío fijo se graba una vez en video y
    se reproduce siempre, que es exactamente lo que hay que evitar.
    """
    return secrets.choice(DESAFIOS)


def _trayectoria(cuadros: List[Any]) -> List[Tuple[float, float, float]]:
    """Centro y tamaño del rostro en cada cuadro donde se lo detecta."""
    camino: List[Tuple[float, float, float]] = []
    for cuadro in cuadros:
        rostros = _detectar_rostros(cuadro)
        if not rostros:
            continue
        x, y, w, h = max(rostros, key=lambda r: r[2] * r[3])
        camino.append((x + w / 2.0, y + h / 2.0, float(w)))
    return camino


def _cambio_medio(cuadros: List[Any]) -> float:
    """Diferencia media entre cuadros consecutivos dentro del rostro."""
    import cv2

    grises = []
    for cuadro in cuadros:
        rostros = _detectar_rostros(cuadro)
        if not rostros:
            continue
        grises.append(_recortar_rostro(cuadro, max(rostros, key=lambda r: r[2] * r[3])))
    if len(grises) < 2:
        return 0.0
    diferencias = [
        float(np.mean(cv2.absdiff(anterior, siguiente)))
        for anterior, siguiente in pairwise(grises)
    ]
    return float(np.mean(diferencias)) if diferencias else 0.0


def prueba_de_vida(cuadros: List[Any], desafio: str) -> Resultado:
    """Comprueba que frente a la cámara haya una persona y no una imagen.

    El kiosco pide un gesto sorteado y mira si la secuencia lo muestra: el
    rostro tiene que desplazarse —o cambiar de tamaño, si el gesto fue
    acercarse— y además variar entre cuadros. Una foto impresa o un teléfono
    con una foto en pantalla no hacen ninguna de las dos cosas.

    **Es un disuasivo, no una prueba.** Quien mueva el teléfono siguiendo la
    consigna pasa igual. Cerrar el hueco de verdad pide una cámara con
    infrarrojo o profundidad, o un modelo de anti-suplantación entrenado, que
    es hardware y datos que este sistema no tiene. Lo que sí hace es dejar
    afuera el ataque habitual: una foto sostenida quieta frente a la cámara.

    Returns:
        ``VERIFICADA`` si la secuencia muestra el gesto; ``SUPLANTADA`` si hay
        un rostro que no se mueve; ``NO_VERIFICABLE`` si no hubo cuadros
        suficientes para decidir.
    """
    if not disponible():
        return Resultado(NO_VERIFICABLE, "El motor de visión no está instalado.")
    camino = _trayectoria(cuadros or [])
    if len(camino) < CUADROS_MINIMOS:
        return Resultado(
            NO_VERIFICABLE,
            f"Solo {len(camino)} cuadro(s) con rostro: se necesitan "
            f"{CUADROS_MINIMOS} para evaluar el movimiento.",
        )

    ancho = max(c[2] for c in camino) or 1.0
    xs = [c[0] for c in camino]
    anchos = [c[2] for c in camino]
    desplazamiento = (max(xs) - min(xs)) / ancho
    acercamiento = (max(anchos) - min(anchos)) / ancho
    cambio = _cambio_medio(cuadros)

    if desafio == ACERCARSE:
        cumplio = acercamiento >= MOVIMIENTO_MINIMO
        medida = f"acercamiento {acercamiento:.0%}"
    else:
        cumplio = desplazamiento >= MOVIMIENTO_MINIMO
        medida = f"desplazamiento {desplazamiento:.0%}"

    if cumplio and cambio >= CAMBIO_MINIMO:
        return Resultado(
            VERIFICADA,
            f"Prueba de vida superada: {medida}, variación {cambio:.1f}.",
        )
    return Resultado(
        SUPLANTADA,
        f"El rostro no reaccionó al pedido de {desafio}: {medida}, "
        f"variación {cambio:.1f}. Puede ser una foto frente a la cámara.",
    )


def capturar_secuencia(
    segundos: float = 2.5, camara: int = 0
) -> List[Any]:
    """Toma una ráfaga de cuadros para evaluar el gesto pedido.

    Devuelve todos los cuadros y no el mejor: la prueba de vida necesita la
    secuencia entera, que es precisamente el dato que una foto no tiene.
    """
    if not disponible():
        return []
    import cv2

    captura = cv2.VideoCapture(camara)
    if not captura.isOpened():
        return []
    cuadros: List[Any] = []
    try:
        for _ in range(max(CUADROS_MINIMOS * 2, int(segundos * 10))):
            ok, cuadro = captura.read()
            if ok:
                cuadros.append(cuadro)
    finally:
        captura.release()
    return cuadros
