"""Cola local SQLite para marcaciones cuando PostgreSQL no responde.

El kiosco escribe aquí cada marcación que no pudo persistir en el servidor
central (reloj biométrico o base caída). Un hilo de fondo reenvía la cola al
PostgreSQL en orden cronológico y con los timestamps originales intactos.

Cada entrada lleva un ``sync_id`` (UUID) que se persiste también en la tabla
``marcajes`` para garantizar idempotencia: si el hilo se corta a mitad de
una sincronización, re-ejecutar el lote no duplica registros.

Y lleva una **firma HMAC**. El archivo es un SQLite corriente en el disco del
kiosco: sin firma, cualquiera que pueda escribirlo fabrica marcaciones a
nombre de quien quiera y entran al servidor central como legítimas. La clave
vive en el entorno del proceso y no en el archivo, así que copiar la base no
alcanza para falsificar una fila.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from typing import Any, Dict, List, Optional


def _carpeta_de_datos() -> str:
    """Dónde vive la cola: junto al programa, no junto al código.

    Empaquetado, ``__file__`` apunta a la carpeta donde el ejecutable
    descomprime sus módulos. Derivar la ruta de ahí funciona por casualidad
    mientras el empaquetado sea de carpeta, y deja de funcionar en silencio si
    alguna vez pasa a archivo único: esa carpeta se borra al cerrar, y con ella
    las marcas que todavía no se repusieron. Que es exactamente lo que esta
    cola existe para que no pase.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


RUTA_POR_DEFECTO: str = os.path.join(_carpeta_de_datos(), "marcaciones_offline.db")

VARIABLE_CLAVE: str = "COMPROBANTE_CLAVE"
"""Secreto con el que se firma la cola. Es el mismo con el que se firman los
comprobantes de marcación: los dos responden a la misma pregunta —si un
registro de asistencia lo emitió el sistema o lo escribió alguien— y separarlos
sería un secreto más que administrar sin ganar nada."""

MAX_INTENTOS: int = 20
"""Reintentos antes de apartar una marca. A un ciclo cada quince segundos son
cinco minutos de insistencia, suficiente para una caída de red y poco para una
marca que nunca va a entrar."""


class FirmaInvalida(ValueError):
    """La fila de la cola no fue escrita por este kiosco."""


def _clave() -> bytes:
    """Clave de firma de la cola, leída del entorno en cada uso."""
    import auth

    return auth.secreto_requerido(VARIABLE_CLAVE).encode("utf-8")


def firmar(sync_id: str, username: str, momento_iso: str, verificacion: str) -> str:
    """Firma el contenido de una marcación encolada.

    Entran en la firma todos los campos que el servidor central va a creer:
    quién marcó, cuándo y qué dijo el control biométrico. Dejar cualquiera
    afuera permitiría cambiarlo sin romper la firma.
    """
    mensaje = "|".join((sync_id, username, momento_iso, verificacion))
    return hmac.new(_clave(), mensaje.encode("utf-8"), hashlib.sha256).hexdigest()


def verificar(fila: Dict[str, Any]) -> bool:
    """Indica si la fila conserva la firma que le puso el kiosco."""
    esperada = firmar(
        fila["sync_id"],
        fila["username"],
        fila["momento_iso"],
        fila.get("verificacion_facial") or "",
    )
    return hmac.compare_digest(esperada, (fila.get("firma") or "").lower())


class ColaOffline:
    """Cola SQLite de un solo escritor con bloqueo de hilos."""

    def __init__(self, ruta: Optional[str] = None) -> None:
        self.ruta: str = ruta or RUTA_POR_DEFECTO
        self._bloqueo = threading.Lock()
        self._crear_esquema()

    def _conexion(self) -> sqlite3.Connection:
        conexion = sqlite3.connect(self.ruta, timeout=10)
        conexion.row_factory = sqlite3.Row
        return conexion

    def _crear_esquema(self) -> None:
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pendientes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sync_id TEXT NOT NULL UNIQUE,
                        username TEXT NOT NULL,
                        momento_iso TEXT NOT NULL,
                        es_dia_lluvioso INTEGER NOT NULL DEFAULT 0,
                        creado_en_iso TEXT NOT NULL
                    )
                    """
                )
                # Una cola de una versión anterior no tiene estas columnas.
                for columna, definicion in (
                    ("verificacion_facial", "TEXT NOT NULL DEFAULT ''"),
                    ("firma", "TEXT NOT NULL DEFAULT ''"),
                    ("intentos", "INTEGER NOT NULL DEFAULT 0"),
                    ("ultimo_error", "TEXT NOT NULL DEFAULT ''"),
                ):
                    # SQLite no tiene ADD COLUMN IF NOT EXISTS: la forma de
                    # preguntar si la columna está es intentar agregarla.
                    with suppress(sqlite3.OperationalError):
                        conexion.execute(
                            f"ALTER TABLE pendientes ADD COLUMN {columna} {definicion}"
                        )
                # Las marcas que no entran se apartan acá en lugar de
                # reintentarse para siempre: quedan a la vista de quien opere
                # el kiosco, que es lo único que puede resolverlas.
                conexion.execute(
                    """
                    CREATE TABLE IF NOT EXISTS descartadas (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sync_id TEXT NOT NULL,
                        username TEXT NOT NULL,
                        momento_iso TEXT NOT NULL,
                        motivo TEXT NOT NULL,
                        descartado_en_iso TEXT NOT NULL
                    )
                    """
                )
                conexion.commit()
            finally:
                conexion.close()

    def encolar(
        self,
        username: str,
        momento: datetime,
        verificacion_facial: str = "",
    ) -> Dict[str, Any]:
        """Agrega una marcación pendiente conservando su instante original.

        La condición del día no viaja en la cola: al sincronizar se resuelve
        contra lo que Recursos Humanos haya declarado, que puede haberse
        firmado después de que el kiosco perdiera la conexión.

        El resultado del control biométrico sí viaja, porque no se puede
        reconstruir más tarde: la cámara estaba ahí y ahora no.
        """
        sync_id = uuid.uuid4().hex
        momento_iso = momento.isoformat()
        creado_en = datetime.now().astimezone().isoformat()
        firma = firmar(sync_id, username, momento_iso, verificacion_facial)
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute(
                    """
                    INSERT INTO pendientes
                        (sync_id, username, momento_iso, es_dia_lluvioso,
                         creado_en_iso, verificacion_facial, firma)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sync_id, username, momento_iso, 0, creado_en,
                     verificacion_facial, firma),
                )
                conexion.commit()
            finally:
                conexion.close()
        return {
            "sync_id": sync_id,
            "username": username,
            "momento_iso": momento_iso,
            "creado_en_iso": creado_en,
            "verificacion_facial": verificacion_facial,
            "firma": firma,
        }

    def pendientes(self) -> List[Dict[str, Any]]:
        """Lista las marcaciones pendientes en orden cronológico."""
        with self._bloqueo:
            conexion = self._conexion()
            try:
                filas = conexion.execute(
                    "SELECT * FROM pendientes ORDER BY momento_iso, id"
                ).fetchall()
            finally:
                conexion.close()
        return [dict(fila) for fila in filas]

    def eliminar(self, id_local: int) -> None:
        """Quita de la cola la marcación ya sincronizada."""
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute("DELETE FROM pendientes WHERE id = ?", (id_local,))
                conexion.commit()
            finally:
                conexion.close()

    def registrar_fallo(self, id_local: int, motivo: str) -> int:
        """Anota un intento fallido y devuelve cuántos lleva la marca."""
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute(
                    "UPDATE pendientes SET intentos = intentos + 1, "
                    "ultimo_error = ? WHERE id = ?",
                    (motivo[:200], id_local),
                )
                conexion.commit()
                fila = conexion.execute(
                    "SELECT intentos FROM pendientes WHERE id = ?", (id_local,)
                ).fetchone()
            finally:
                conexion.close()
        return int(fila["intentos"]) if fila else 0

    def descartar(self, fila: Dict[str, Any], motivo: str) -> None:
        """Aparta una marca que no va a entrar, conservando el rastro.

        No se borra en silencio: una marcación que se pierde sin dejar
        constancia es una hora de trabajo que nadie puede reclamar.
        """
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute(
                    """
                    INSERT INTO descartadas
                        (sync_id, username, momento_iso, motivo, descartado_en_iso)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (fila["sync_id"], fila["username"], fila["momento_iso"],
                     motivo, datetime.now().astimezone().isoformat()),
                )
                conexion.execute("DELETE FROM pendientes WHERE id = ?", (fila["id"],))
                conexion.commit()
            finally:
                conexion.close()

    def descartadas(self) -> List[Dict[str, Any]]:
        """Marcas apartadas que esperan una decisión humana."""
        with self._bloqueo:
            conexion = self._conexion()
            try:
                filas = conexion.execute(
                    "SELECT * FROM descartadas ORDER BY descartado_en_iso DESC"
                ).fetchall()
            finally:
                conexion.close()
        return [dict(fila) for fila in filas]

    def __len__(self) -> int:
        with self._bloqueo:
            conexion = self._conexion()
            try:
                fila = conexion.execute("SELECT COUNT(*) AS n FROM pendientes").fetchone()
            finally:
                conexion.close()
        return int(fila["n"])
