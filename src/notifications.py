"""Notificaciones en tiempo real para Recursos Humanos.

Un bus de publicación/suscripción en memoria distribuye cada alerta
crítica (cuota bloqueada de un artículo reglamentario, llegada tardía
injustificada, intento de suplantación facial) hacia:

- El Panel de Gestión de escritorio (parpadeo de la campana).
- Los clientes conectados por WebSocket al servidor web (push sin recargar).
- Opcionalmente un correo SMTP con el ticket de la marcación (best-effort).

La persistencia vive en la tabla ``alertas``; ``registrar_alerta`` persiste
y publica en un solo paso, tolerando que la base esté caída (la alerta se
sigue publicando en vivo aunque no quede guardada).
"""

from __future__ import annotations

import json
import os
import select
import smtplib
import threading
import time
from collections import OrderedDict
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Callable, Dict, List, Optional


CANAL: str = "alertas_marcacion"
"""Canal de PostgreSQL por el que los procesos se avisan entre sí.

El bus vive en la memoria de un proceso y el servidor corre con varios
workers, así que una alerta publicada en uno no alcanzaba a los WebSockets
conectados a los otros: el panel de Recursos Humanos perdía en silencio tres
de cada cuatro avisos de fraude.

Se usa `LISTEN`/`NOTIFY` y no un Redis porque PostgreSQL ya es una dependencia
del sistema: una pieza de infraestructura más es una pieza más que instalar,
monitorear y explicar en la puesta en marcha de cada cliente.

El aviso lleva solo el identificador y la empresa. El contenido se lee de la
tabla: el payload de `NOTIFY` tiene un tope de 8 kB y el detalle de una alerta
no tiene ninguno, así que mandarlo entero funcionaría hasta el día en que
alguien escriba una nota larga.
"""

RECIENTES_MAX: int = 512
"""Cuántos identificadores propios se recuerdan para no repetir una alerta."""


class BusAlertas:
    """Bus de publicación/suscripción seguro para hilos."""

    def __init__(self) -> None:
        self._suscriptores: List[Callable[[Dict[str, Any]], None]] = []
        self._bloqueo = threading.Lock()
        self._propias: "OrderedDict[int, bool]" = OrderedDict()

    def publicar(self, alerta: Dict[str, Any]) -> None:
        """Entrega la alerta a todos los suscriptores (best-effort)."""
        identificador = alerta.get("id")
        if identificador is not None:
            with self._bloqueo:
                self._propias[int(identificador)] = True
                while len(self._propias) > RECIENTES_MAX:
                    self._propias.popitem(last=False)
        with self._bloqueo:
            suscriptores = list(self._suscriptores)
        for suscriptor in suscriptores:
            try:
                suscriptor(alerta)
            except Exception:
                continue

    def ya_publicada(self, identificador: Any) -> bool:
        """Indica si esta alerta ya salió por este proceso.

        El que publica también recibe su propio `NOTIFY`; sin esto, cada
        alerta llegaría dos veces a quien esté conectado a ese worker.
        """
        if identificador is None:
            return False
        with self._bloqueo:
            return int(identificador) in self._propias

    def suscribir(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._bloqueo:
            if callback not in self._suscriptores:
                self._suscriptores.append(callback)

    def desuscribir(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._bloqueo:
            if callback in self._suscriptores:
                self._suscriptores.remove(callback)


BUS = BusAlertas()


def anunciar(db: Any, alerta: Dict[str, Any]) -> None:
    """Avisa a los demás procesos que hay una alerta nueva.

    Es best-effort: si el canal falla, la alerta ya quedó guardada y el panel
    la va a ver al refrescar. Perder el aviso en vivo es molesto; perder la
    alerta sería otra cosa.
    """
    if db is None or alerta.get("id") is None:
        return
    try:
        db._execute(
            "SELECT pg_notify(%s, %s)",
            (CANAL, json.dumps({"id": int(alerta["id"]),
                                "empresa": alerta.get("empresa_id")})),
        )
        db.connection.commit()
    except Exception:
        pass


class EscuchaAlertas(threading.Thread):
    """Repite en este proceso las alertas que publicaron los demás.

    Abre su propia conexión en autocommit —`LISTEN` no funciona dentro de una
    transacción abierta— y espera sin consumir CPU. Cuando llega un aviso lee
    la alerta ya guardada y la entrega al bus local, que es el que alimenta a
    los WebSockets conectados a este worker.
    """

    def __init__(self, abrir_conexion: Callable[[], Any], intervalo: float = 5.0):
        super().__init__(daemon=True, name="escucha-alertas")
        self._abrir = abrir_conexion
        self._intervalo = intervalo
        self._seguir = threading.Event()
        self._seguir.set()

    def detener(self) -> None:
        self._seguir.clear()

    def run(self) -> None:  # pragma: no cover - hilo de fondo
        while self._seguir.is_set():
            try:
                self._escuchar()
            except Exception:
                # Una caída de la base no puede llevarse el hilo: se reintenta
                # hasta que vuelva, que es lo que hace el resto del sistema.
                time.sleep(self._intervalo)

    def _escuchar(self) -> None:  # pragma: no cover - hilo de fondo
        db = self._abrir()
        if db is None:
            time.sleep(self._intervalo)
            return
        try:
            # `connect` deja una transacción abierta al publicar la empresa en
            # la sesión, y no se puede pasar a autocommit con una en curso.
            db.connection.rollback()
            db.connection.autocommit = True
            with db.connection.cursor() as cursor:
                cursor.execute(f"LISTEN {CANAL}")
            while self._seguir.is_set():
                self._esperar_aviso(db.connection)
                db.connection.poll()
                while db.connection.notifies:
                    aviso = db.connection.notifies.pop(0)
                    _repetir(db, aviso.payload)
        finally:
            db.cerrar()

    def _esperar_aviso(self, conexion: Any) -> None:  # pragma: no cover
        """Espera a que llegue algo sin quemar CPU.

        `select` sobre una conexión es lo eficiente, pero depende de que la
        plataforma acepte descriptores que no son sockets. Donde no lo acepte
        se cae a preguntar cada tanto: con alertas, un cuarto de segundo de
        demora no se nota y quedarse sin avisos sí.
        """
        try:
            select.select([conexion], [], [], self._intervalo)
        except (OSError, ValueError, TypeError):
            time.sleep(0.25)


def _repetir(db: Any, payload: str) -> None:  # pragma: no cover - hilo de fondo
    """Lee la alerta anunciada y la entrega al bus de este proceso."""
    try:
        datos = json.loads(payload)
    except (TypeError, ValueError):
        return
    if BUS.ya_publicada(datos.get("id")):
        return
    empresa = datos.get("empresa")
    if empresa is None:
        return
    db.empresa_id = int(empresa)
    fila = db.get_alerta(int(datos["id"]))
    if fila:
        BUS.publicar(dict(fila, empresa_id=int(empresa)))


def registrar_alerta(
    db: Optional[Any],
    tipo: str,
    severidad: str,
    mensaje: str,
    detalle: str = "",
    usuario_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Persiste (si hay base) y publica una alerta activa.

    La alerta viaja con su ``empresa_id``. El bus es un objeto en memoria
    compartido por todo el proceso, así que sin ese dato la tardanza de un
    cliente aparecería en la campana del panel de otro.
    """
    alerta: Dict[str, Any] = {
        "tipo": tipo,
        "severidad": severidad,
        "mensaje": mensaje,
        "detalle": detalle,
        "usuario_id": usuario_id,
        "empresa_id": getattr(db, "empresa_id", None) if db is not None else None,
        "creado_en": datetime.now().astimezone().isoformat(),
        "leida": False,
    }
    if db is not None:
        try:
            fila = db.crear_alerta(tipo, severidad, mensaje, detalle, usuario_id)
            alerta["id"] = fila["id"]
            alerta["creado_en"] = fila["creado_en"]
        except Exception:
            alerta["id"] = None
    BUS.publicar(alerta)
    anunciar(db, alerta)
    return alerta


def es_de_la_empresa(alerta: Dict[str, Any], empresa_id: Optional[int]) -> bool:
    """Indica si una alerta del bus le corresponde a esa empresa.

    Una alerta sin empresa (la base estaba caída al registrarla) no se
    entrega a nadie: mostrarla a todos sería peor que perderla.
    """
    return (
        empresa_id is not None
        and alerta.get("empresa_id") is not None
        and int(alerta["empresa_id"]) == int(empresa_id)
    )


def _config_smtp() -> Optional[Dict[str, str]]:
    """Lee la configuración SMTP del entorno; None si no está definida."""
    host = os.getenv("SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": os.getenv("SMTP_PORT", "587"),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from": os.getenv("SMTP_FROM", "no-reply@sistema-marcacion.com"),
    }


def enviar_correo(
    destinatario: str,
    asunto: str,
    cuerpo: str,
) -> bool:
    """Envía un correo SMTP best-effort; False si no hay configuración."""
    config = _config_smtp()
    if not config:
        return False
    try:
        mensaje = MIMEText(cuerpo, "plain", "utf-8")
        mensaje["Subject"] = asunto
        mensaje["From"] = config["from"]
        mensaje["To"] = destinatario
        with smtplib.SMTP(config["host"], int(config["port"]), timeout=10) as cliente:
            cliente.ehlo()
            if config["user"]:
                cliente.starttls()
                cliente.login(config["user"], config["password"])
            cliente.sendmail(config["from"], [destinatario], mensaje.as_string())
        return True
    except Exception:
        return False


def enviar_correo_ticket(destinatario: str, ticket: str) -> bool:
    """Envía el comprobante de una marcación por correo (best-effort)."""
    return enviar_correo(
        destinatario,
        "Sistema de Marcación · Comprobante de asistencia",
        f"Estimado colaborador:\n\nSu marcación fue registrada correctamente.\n\n"
        f"Comprobante:\n{ticket}\n\nSistema de Marcación",
    )