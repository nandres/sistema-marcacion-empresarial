"""Registro operativo: qué hizo el servidor y por qué contestó lo que contestó.

No confundir con ``logs_auditoria``, que guarda decisiones de negocio —quién
aprobó qué permiso, quién corrigió qué marca— para mostrárselas a una
inspección, y por eso vive en la base junto al resto de los datos del cliente.
Esto otro existe para quien tiene que entender a las siete de la mañana por qué
una marcación devolvió 500, y sale por la salida estándar porque es donde lo
busca cualquier orquestador.

Cada línea lleva el identificador de la petición que la produjo. Sin eso, con
cuatro *workers* atendiendo el pico de la mañana, las tres líneas de un mismo
error quedan intercaladas con las de otras veinte peticiones y no hay forma de
saber cuáles van juntas.

Lo que nunca entra acá: contraseñas, tokens de sesión, tokens de kiosco y
plantillas faciales. Un registro operativo se copia a un tercero para pedir
ayuda; el día que eso pasa, tiene que poder copiarse entero.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from contextvars import ContextVar
from typing import Optional

VARIABLE_NIVEL: str = "LOG_NIVEL"
NIVELES: frozenset = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

AJENOS_RUIDOSOS: tuple = (
    "httpx", "httpcore", "urllib3", "asyncio", "multipart",
    "python_multipart", "PIL", "watchfiles", "uvicorn.access",
)
"""Bibliotecas que cuentan en INFO lo que a nadie le sirve en producción.

Una línea por conexión HTTP saliente y otra por parte de un formulario dejan
el registro con el 90 % de renglones escritos por terceros, y un registro así
no lo lee nadie. En DEBUG vuelven a hablar, que es cuando se las quiere oír.
"""

_peticion: ContextVar[str] = ContextVar("peticion", default="-")
_configurado: bool = False


class _ContextoDePeticion(logging.Filter):
    """Agrega el identificador de la petición en curso a cada registro.

    Va en el manejador y no en un *logger* concreto para que también alcance a
    las líneas que emiten las bibliotecas de terceros.
    """

    def filter(self, registro: logging.LogRecord) -> bool:
        registro.peticion = _peticion.get()
        return True


def configurar(nivel: Optional[str] = None) -> None:
    """Deja el registro listo. Llamarlo dos veces no duplica líneas.

    Gunicorn arranca varios *workers* importando el mismo módulo y el
    recargador de uvicorn lo vuelve a importar en cada cambio: sin la guarda,
    cada pasada agrega un manejador más y el mismo mensaje termina saliendo
    tres veces.
    """
    global _configurado
    if _configurado:
        return

    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)-12s [%(peticion)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    manejador.addFilter(_ContextoDePeticion())

    pedido = (nivel or os.getenv(VARIABLE_NIVEL) or "INFO").upper()
    raiz = logging.getLogger()
    # Un nivel mal escrito en el entorno no puede ser motivo para que el
    # servicio no arranque: se cae a INFO y se dice en voz alta.
    raiz.setLevel(pedido if pedido in NIVELES else "INFO")
    raiz.addHandler(manejador)

    if raiz.level > logging.DEBUG:
        for ajeno in AJENOS_RUIDOSOS:
            logging.getLogger(ajeno).setLevel(logging.WARNING)

    _configurado = True

    if pedido not in NIVELES:
        raiz.warning(
            "%s=%r no es un nivel conocido; se registra en INFO",
            VARIABLE_NIVEL, pedido,
        )


def obtener(nombre: str) -> logging.Logger:
    """El registrador de un módulo, con el nombre que se ve en cada línea."""
    return logging.getLogger(nombre)


def abrir_peticion() -> str:
    """Marca el inicio de una petición y devuelve su identificador.

    Cada petición corre en su propia tarea con su copia del contexto, así que
    el valor no se escapa hacia las que estén en curso al mismo tiempo.
    """
    identificador = secrets.token_hex(4)
    _peticion.set(identificador)
    return identificador


def peticion_actual() -> str:
    """El identificador de la petición en curso, o ``-`` fuera de una."""
    return _peticion.get()
