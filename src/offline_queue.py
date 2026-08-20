"""Cola local SQLite para marcaciones cuando PostgreSQL no responde.

El kiosco escribe aquí cada marcación que no pudo persistir en el servidor
central (reloj biométrico o base caída). Un hilo de fondo reenvía la cola al
PostgreSQL en orden cronológico y con los timestamps originales intactos.

Cada entrada lleva un ``sync_id`` (UUID) que se persiste también en la tabla
``marcajes`` para garantizar idempotencia: si el hilo se corta a mitad de
una sincronización, re-ejecutar el lote no duplica registros.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

RUTA_POR_DEFECTO: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "marcaciones_offline.db",
)


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
                conexion.commit()
            finally:
                conexion.close()

    def encolar(
        self, username: str, momento: datetime, es_dia_lluvioso: bool = False
    ) -> Dict[str, Any]:
        """Agrega una marcación pendiente conservando su instante original."""
        sync_id = uuid.uuid4().hex
        momento_iso = momento.isoformat()
        creado_en = datetime.now().astimezone().isoformat()
        with self._bloqueo:
            conexion = self._conexion()
            try:
                conexion.execute(
                    """
                    INSERT INTO pendientes
                        (sync_id, username, momento_iso, es_dia_lluvioso, creado_en_iso)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (sync_id, username, momento_iso, int(es_dia_lluvioso), creado_en),
                )
                conexion.commit()
            finally:
                conexion.close()
        return {
            "sync_id": sync_id,
            "username": username,
            "momento_iso": momento_iso,
            "es_dia_lluvioso": bool(es_dia_lluvioso),
            "creado_en_iso": creado_en,
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

    def __len__(self) -> int:
        with self._bloqueo:
            conexion = self._conexion()
            try:
                fila = conexion.execute("SELECT COUNT(*) AS n FROM pendientes").fetchone()
            finally:
                conexion.close()
        return int(fila["n"])