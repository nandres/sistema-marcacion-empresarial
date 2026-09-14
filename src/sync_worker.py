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

import clock_engine
import database
import notifications
from clock_engine import (
    ClockEngine,
    calcular_horas_paraguay,
    evaluar_asistencia,
    persistir_desglose,
)
from offline_queue import ColaOffline


def _desde_iso(momento_iso: str) -> datetime:
    """Reconstruye el instante original (aware) desde su ISO."""
    return datetime.fromisoformat(momento_iso)


def _nueva_db(empresa_id: Optional[int] = None) -> Optional[database.Database]:
    """Abre una conexión al servidor central, o None si no responde.

    No aplica migraciones: el DDL bloquea a las conexiones de lectura largas
    y este hilo corre cada 15 segundos. El esquema se aplica una sola vez en
    ``migrate.py``, antes de levantar la aplicación.

    Un kiosco atiende a una empresa. Sin ``empresa_id`` se adopta la de la
    instalación, que es la única que hay cuando el kiosco no forma parte de
    un alojamiento compartido.
    """
    try:
        db = database.Database(empresa_id=empresa_id)
        db.connect()
        if db.empresa_id is None:
            db._adoptar_empresa_base()
        return db
    except Exception:
        return None


def sincronizar(
    cola: ColaOffline,
    al_aviso: Optional[Callable[[Dict[str, Any]], None]] = None,
    db: Optional[database.Database] = None,
    empresa_id: Optional[int] = None,
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
        db = _nueva_db(empresa_id)
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
                # Ni el día calendario ni "¿ya marcó hoy?" sirven para decidir:
                # el turno nocturno reparte una jornada entre dos fechas y la
                # jornada partida son dos entradas en la misma. La decisión es
                # la misma que toma el kiosco en línea.
                abierto = db.get_open_entry(user["id"], antes_de=momento)
                # Una entrada que quedó abierta de otra jornada no es la que
                # esta marca viene a cerrar: tomarla produciría un turno de
                # veintidós horas. Es el mismo techo que aplica el kiosco.
                if abierto is not None and (
                    momento - abierto["hora_entrada"] > clock_engine.MAX_JORNADA_ABIERTA
                ):
                    abierto = None
                previas = db.listar_marcajes_desde(
                    user["id"], momento - clock_engine.VENTANA_CONSULTA
                )

                if abierto is not None:
                    if _ya_sincronizada_salida(previas, momento):
                        resumen["descartadas"] += 1
                    else:
                        _sincronizar_salida(db, user, abierto, momento, pendiente)
                        resumen["subidas"] += 1
                elif _ya_cubierta(previas, momento):
                    resumen["descartadas"] += 1
                elif _queda_tramo_por_cubrir(db, user, momento):
                    _sincronizar_entrada(db, user, momento, pendiente, avisar)
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


def _ya_cubierta(previas: List[Dict[str, Any]], momento: datetime) -> bool:
    """Indica si el instante ya cae dentro de una jornada registrada.

    Es la defensa contra el reintento de una cola que ya se subió. No alcanza
    con "¿marcó ese día?": la jornada partida tiene dos entradas legítimas en
    la misma fecha, y descartarlas por el día perdería la segunda.
    """
    for registro in previas:
        fin = registro["hora_salida"]
        if registro["hora_entrada"] <= momento and (fin is None or momento <= fin):
            return True
    return False


def _queda_tramo_por_cubrir(db, user, momento: datetime) -> bool:
    """Indica si el turno del empleado admite otra entrada en esa jornada."""
    turno = clock_engine.turno_vigente(db, user["id"], momento.date())
    return clock_engine.tramos_consumidos(db, user["id"], momento) < len(turno.tramos)


def _sincronizar_entrada(db, user, momento, pendiente, avisar) -> None:
    """Reinserta una ENTRADA offline con su timestamp y evaluación original.

    La condición excepcional del día se resuelve acá y no en el kiosco: la
    declaración de Recursos Humanos puede firmarse después de que el kiosco
    perdiera la conexión, y la marca igual tiene que quedar amparada.
    """
    evaluacion = evaluar_asistencia(db, user["id"], momento)
    estado = evaluacion["estado"]
    incidencia = (
        "Ausencia Injustificada"
        if estado == "Ausencia Injustificada"
        else ("Llegada Tardía" if estado == "Llegada Tardía" else "")
    )
    tolerancia = evaluacion["tolerancia_climatica"] or evaluacion["retraso_min"] > 0
    condicion = evaluacion["condicion_dia"]
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
    desglose = calcular_horas_paraguay(abierto["hora_entrada"], momento)
    incidencia = ClockEngine._clasificar_incidencia_salida(
        desglose,
        abierto.get("tipo_incidencia") or "",
        clock_engine.duracion_comprometida(db, user["id"], abierto["hora_entrada"]),
    )
    persistir_desglose(db, abierto["id"], momento, desglose, incidencia)


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