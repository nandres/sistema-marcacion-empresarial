"""Cifrado de las plantillas faciales en reposo (Ley N.º 6534/2020).

La ley paraguaya de protección de datos personales trata el dato biométrico
como **categoría especial** y exige medidas reforzadas. Guardarlo como JPEG
plano en una columna ``BYTEA`` significa que un volcado de la base —o una
copia de seguridad extraviada— entrega la plantilla facial de toda la
plantilla laboral, un dato que, a diferencia de una contraseña, la persona
no puede cambiar.

El cifrado es AES-256-GCM: autenticado, de modo que una foto alterada en la
base no se descifra en silencio sino que falla. La clave vive en
``BIOMETRIA_CLAVE``, fuera de la base, para que quien obtenga un backup no
obtenga también con qué abrirlo.

El formato en disco es ``b"BIO1" + nonce(12) + ciphertext``. El prefijo
permite distinguir una foto cifrada de una anterior en claro y migrarlas sin
perder el registro existente.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIJO: bytes = b"BIO1"
TAMANO_NONCE: int = 12
VARIABLE_CLAVE: str = "BIOMETRIA_CLAVE"


class BiometriaSinClave(RuntimeError):
    """Se intentó operar con biometría sin configurar la clave de cifrado."""


def generar_clave() -> str:
    """Genera una clave nueva lista para pegar en el ``.env``."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _clave() -> bytes:
    """Deriva los 32 bytes de AES-256 desde ``BIOMETRIA_CLAVE``.

    Acepta tanto una clave en base64 de 32 bytes —lo que genera
    ``generar_clave``— como una frase larga, que se deriva con SHA-256 para
    no obligar a nadie a manipular binarios a mano.
    """
    from database import load_dotenv

    load_dotenv()
    valor = (os.getenv(VARIABLE_CLAVE) or "").strip()
    if not valor:
        raise BiometriaSinClave(
            f"Falta {VARIABLE_CLAVE}. Generá una con: "
            f'python -c "import sys; sys.path.insert(0, \'src\'); '
            f'import biometria; print(biometria.generar_clave())" '
            f"y agregala al .env antes de registrar fotos."
        )
    try:
        material = base64.urlsafe_b64decode(valor)
        if len(material) == 32:
            return material
    except (ValueError, TypeError):
        pass
    if len(valor) < 32:
        raise BiometriaSinClave(
            f"{VARIABLE_CLAVE} es demasiado corta: use al menos 32 caracteres "
            f"o una clave generada con biometria.generar_clave()."
        )
    return hashlib.sha256(valor.encode("utf-8")).digest()


def configurada() -> bool:
    """Indica si hay clave de cifrado disponible, sin lanzar excepción."""
    try:
        _clave()
        return True
    except BiometriaSinClave:
        return False


def cifrar(imagen: bytes, user_id: int) -> bytes:
    """Cifra una plantilla facial para guardarla en la base.

    El identificador del empleado viaja como dato autenticado asociado: una
    foto movida de una fila a otra deja de descifrar, así que no se puede
    reasignar el rostro de una persona a otra editando la base.
    """
    nonce = os.urandom(TAMANO_NONCE)
    sellado = AESGCM(_clave()).encrypt(nonce, imagen, str(user_id).encode("ascii"))
    return PREFIJO + nonce + sellado


def descifrar(guardado: bytes, user_id: int) -> Optional[bytes]:
    """Recupera la plantilla facial; ``None`` si no se puede autenticar.

    Las fotos anteriores al cifrado se devuelven tal cual para no romper una
    base existente: ``migrar_fotos`` las convierte y a partir de ahí dejan de
    aparecer en claro.
    """
    if not guardado:
        return None
    if not guardado.startswith(PREFIJO):
        return bytes(guardado)
    cuerpo = bytes(guardado)[len(PREFIJO):]
    nonce, sellado = cuerpo[:TAMANO_NONCE], cuerpo[TAMANO_NONCE:]
    try:
        return AESGCM(_clave()).decrypt(nonce, sellado, str(user_id).encode("ascii"))
    except (InvalidTag, ValueError):
        return None
    except BiometriaSinClave:
        # Sin clave la foto es ilegible, no inexistente: el control biométrico
        # queda en "no verificable" y avisa, en lugar de tumbar el kiosco.
        return None


def esta_cifrada(guardado: bytes) -> bool:
    """Indica si el contenido almacenado ya está bajo cifrado."""
    return bool(guardado) and bytes(guardado).startswith(PREFIJO)


def migrar_fotos(db) -> int:
    """Cifra las fotos que quedaron en claro de una instalación anterior.

    Returns:
        Cantidad de plantillas convertidas.
    """
    convertidas = 0
    for foto in db.list_fotos(descifrar=False):
        if esta_cifrada(foto["imagen"]):
            continue
        db.guardar_foto(foto["user_id"], bytes(foto["imagen"]))
        convertidas += 1
    return convertidas
