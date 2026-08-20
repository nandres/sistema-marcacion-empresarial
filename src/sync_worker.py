"""Sincronizador en segundo plano de la cola offline hacia PostgreSQL.

Procesa las marcaciones pendientes en el mismo orden en que ocurrieron,
resuelve la acción (ENTRADA/SALIDA) contra el estado real del servidor y
reinserta cada marca con su timestamp original y su ``sync_id``, de modo
que un reintento nunca duplique registros.

Cuando la conexión central vuelve a estar disponible, el lote se sube con
un solo commit por marca (bulk en el orden cronológico) y la cola local se
vacía. Las incidencias de tardanza/ausencia que se detectan al sincronizar
generan alertas para Recursos Humanos igual que si la marca hubiera
ocurrido en línea.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import database
import notifications
from clock_engine import (
    ClockEngine,
    calcular_horas_paraguay,
    es_feriado_o_domingo,
    evaluar_asistencia_conatel,
)
from offline_queue import ColaOffline


def _desde_iso(momento_iso: str) -> datetime:
    """Reconstruye el instante original (aware) desde su ISO."""
    return datetime.fromisoformat(momento_iso)


def _nueva_db() -> Optional[database.Database]:
    """Abre una conexión ya inicializada, o None si PostgreSQL no responde."""
    try:
        db = database.Database()
        db.initialize()
        return db
    except Exception:
        return None


def sincronizar(
    cola: ColaOffline,
    al_aviso: Optional[Callable[[Dict[str, Any]], None]] = None,
    db: Optional[database.Database] = None,
) -> Dict[str, int]:
    """Sube la cola pendiente a PostgreSQL; retorna el resumen del lote.

    Si se provee ``db`` (conexión ya inicializada del proceso principal) se
    reutiliza sin ejecutar migraciones; en caso contrario se abre y cierra
    una conexión fresca por lote.

    Returns:
        Diccionario con ``subidas``, ``descartadas`` y ``fallidas``.
    """
    resumen = {"subidas": 0, "descartadas": 0, "fallidas": 0}
    pendientes = cola.pendientes()
    if not pendientes:
        return resumen
    usar_db_externo = db is not None
    if db is None:
        db = _nueva_db()
        if db is None:
            return resumen

    def avisar(
        tipo: str,
        severidad: str,
        mensaje: str,
        detalle: str = "",
        usuario_id: Optional[int] = None,
    ) -> None:
        alerta = notifications.registrar_alerta(
            db, tipo, severidad, mensaje, detalle, usuario_id
        )
        if al_aviso is not None:
            al_aviso(alerta)

    try:
        for pendiente in pendientes:
            try:
                user = db.get_user_by_username(pendiente["username"])
                if not user:
                    raise ValueError("Usuario inexistente en el servidor.")
                momento = _desde_iso(pendiente["momento_iso"])
                lluvia = bool(pendiente["es_dia_lluvioso"])
                registros = db.get_entries_by_date(user["id"], momento.date())
                abierto = next((r for r in registros if r["hora_salida"] is None), None)

                if abierto is None and not registros:
                    _sincronizar_entrada(db, user, momento, lluvia, pendiente, avisar)
                    resumen["subidas"] += 1
                elif abierto is not None:
                    if _ya_sincronizada_salida(registros, momento):
                        resumen["descartadas"] += 1
                    else:
                        _sincronizar_salida(db, user, abierto, momento, pendiente)
                        resumen["subidas"] += 1
                else:
                    resumen["descartadas"] += 1
                cola.eliminar(pendiente["id"])
            except Exception:
                resumen["fallidas"] += 1
    finally:
        if not usar_db_externo:
            db.cerrar()
    return resumen


def _sincronizar_entrada(db, user, momento, lluvia, pendiente, avisar) -> None:
    """Reinserta una ENTRADA offline con su timestamp y evaluación original."""
    evaluacion = evaluar_asistencia_conatel(db, user["id"], momento, lluvia)
    estado = evaluacion["estado"]
    incidencia = (
        "Ausencia Injustificada"
        if estado == "Ausencia Injustificada"
        else ("Llegada Tardía" if estado == "Llegada Tardía" else "")
    )
    tolerancia = evaluacion["tolerancia_climatica"] or evaluacion["retraso_min"] > 0
    condicion = "Lluvia intensa" if lluvia else ""
    entry_id = db.open_clock_in(
        user["id"],
        momento,
        estado != "Normal",
        incidencia,
        tolerancia,
        condicion,
        sync_id=pendiente["sync_id"],
    )
    if entry_id is None:
        return
    if estado != "Normal":
        avisar(
            "marcacion_incidente",
            "media",
            f"Llegada tardía sin justificar de {user['full_name']} (sincronizada offline).",
            f"Estado: {estado} · {momento.isoformat()} · sync_id {pendiente['sync_id'][:8]}",
            usuario_id=user["id"],
        )


def _sincronizar_salida(db, user, abierto, momento, pendiente) -> None:
    """Cierra una SALIDA offline con el desglose legal calculado sobre
    el timestamp original."""
    feriado = es_feriado_o_domingo(abierto["hora_entrada"])
    desglose = calcular_horas_paraguay(abierto["hora_entrada"], momento, feriado)
    incidencia = ClockEngine._clasificar_incidencia_salida(
        desglose, feriado, abierto.get("tipo_incidencia") or ""
    )
    db.close_clock_out(
        abierto["id"],
        momento,
        feriado,
        desglose["horas_ordinarias"],
        desglose["horas_extra_50"],
        desglose["horas_extra_100"],
        incidencia,
    )


def _ya_sincronizada_salida(registros: List[Dict[str, Any]], momento: datetime) -> bool:
    """Detecta si la salida pendiente ya se aplicó (worker cortado a mitad)."""
    for registro in registros:
        salida = registro.get("hora_salida")
        if salida is None:
            continue
        diferencia = abs((salida - momento).total_seconds())
        if diferencia < 2.0:
            return True
    return False


def iniciar_hilo(
    cola: ColaOffline,
    intervalo: float = 15.0,
    al_aviso: Optional[Callable[[Dict[str, Any]], None]] = None,
    db: Optional[database.Database] = None,
) -> threading.Thread:
    """Arranca el hilo demonio que intenta sincronizar cada ``intervalo`` s.

    Si se provee ``db`` (la conexión ya inicializada de la aplicación) se
    reutiliza en todos los ciclos y se evitan bloqueos por migraciones DDL
    frente a la conexión principal; en caso contrario el hilo abre una
    conexión propia al arrancar.
    """
    def bucle() -> None:
        conexion = db
        while True:
            try:
                sincronizar(cola, al_aviso, db=conexion)
            except Exception:
                conexion = None  # conexión rota: se reintentará al siguiente ciclo
            threading.Event().wait(intervalo)

    hilo = threading.Thread(target=bucle, name="sync-offline", daemon=True)
    hilo.start()
    return hilo