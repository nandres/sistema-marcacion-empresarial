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

import os
import smtplib
import threading
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Callable, Dict, List, Optional


class BusAlertas:
    """Bus de publicación/suscripción seguro para hilos."""

    def __init__(self) -> None:
        self._suscriptores: List[Callable[[Dict[str, Any]], None]] = []
        self._bloqueo = threading.Lock()

    def publicar(self, alerta: Dict[str, Any]) -> None:
        """Entrega la alerta a todos los suscriptores (best-effort)."""
        with self._bloqueo:
            suscriptores = list(self._suscriptores)
        for suscriptor in suscriptores:
            try:
                suscriptor(alerta)
            except Exception:
                continue

    def suscribir(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._bloqueo:
            if callback not in self._suscriptores:
                self._suscriptores.append(callback)

    def desuscribir(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._bloqueo:
            if callback in self._suscriptores:
                self._suscriptores.remove(callback)


BUS = BusAlertas()


def registrar_alerta(
    db: Optional[Any],
    tipo: str,
    severidad: str,
    mensaje: str,
    detalle: str = "",
    usuario_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Persiste (si hay base) y publica una alerta activa."""
    alerta: Dict[str, Any] = {
        "tipo": tipo,
        "severidad": severidad,
        "mensaje": mensaje,
        "detalle": detalle,
        "usuario_id": usuario_id,
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
    return alerta


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