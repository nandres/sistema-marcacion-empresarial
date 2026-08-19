"""Motor de reglas laborales del Código del Trabajo de Paraguay (Ley N.º 213).

Aplica a cada marcaje el desglose legal de jornada diurna (06:00 a 20:00,
máximo 8 horas ordinarias) y nocturna (20:00 a 06:00, máximo 7 horas
ordinarias), liquidando el exceso con recargo del 50% o 100%. Los domingos
y feriados oficiales se liquidan íntegros con recargo del 100%. Incluye la
gracia de tolerancia de 10 minutos en la entrada antes de considerarla
llegada tardía y soporta turnos nocturnos que cruzan la medianoche.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from database import Database

JORNADA_DIURNA: timedelta = timedelta(hours=8)
JORNADA_NOCTURNA: timedelta = timedelta(hours=7)
JORNADA_JUSTIFICADA: timedelta = JORNADA_DIURNA
INICIO_DIURNO: time = time(6, 0)
FIN_DIURNO: time = time(20, 0)
TOLERANCIA_ENTRADA: timedelta = timedelta(minutes=10)

FERIADOS_PARAGUAY_2026: frozenset = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 2, 9),
        date(2026, 4, 2),
        date(2026, 4, 3),
        date(2026, 5, 1),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 6, 8),
        date(2026, 8, 10),
        date(2026, 9, 28),
        date(2026, 12, 8),
        date(2026, 12, 25),
    }
)


def _cargar_inicio_jornada() -> time:
    """Lee la hora de inicio de jornada desde ``JORNADA_INICIO`` (HH:MM)."""
    valor = os.getenv("JORNADA_INICIO", "08:00")
    horas, minutos = valor.split(":")
    return time(int(horas), int(minutos))


INICIO_JORNADA: time = _cargar_inicio_jornada()
LIMITE_TARDANZA: time = (
    datetime.combine(date.min, INICIO_JORNADA) + TOLERANCIA_ENTRADA
).time()


def ahora_local() -> datetime:
    """Retorna la fecha/hora local con zona horaria (aware)."""
    return datetime.now().astimezone()


def es_feriado_o_domingo(momento: datetime) -> bool:
    """Indica si la fecha del momento es domingo o feriado oficial de Paraguay."""
    return momento.weekday() == 6 or momento.date() in FERIADOS_PARAGUAY_2026


def es_tardanza(hora_entrada: datetime) -> bool:
    """Aplica la gracia de 10 minutos: tardanza solo si supera el límite."""
    return hora_entrada.time() > LIMITE_TARDANZA


def _es_nocturno(momento: datetime) -> bool:
    """Clasifica un instante como nocturno (20:00 a 06:00)."""
    return momento.hour >= 20 or momento.hour < 6


def _proxima_frontera(momento: datetime) -> datetime:
    """Retorna la siguiente frontera de cambio de jornada (06:00 o 20:00)."""
    if _es_nocturno(momento):
        frontera = momento.replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        frontera = momento.replace(hour=20, minute=0, second=0, microsecond=0)
    if frontera <= momento:
        frontera += timedelta(days=1)
    return frontera


def _desglose_por_rangos(
    hora_entrada: datetime, hora_salida: datetime
) -> Tuple[timedelta, timedelta]:
    """Segmenta el turno en tramos diurnos y nocturnos (cruza medianoche)."""
    diurno = timedelta(0)
    nocturno = timedelta(0)
    actual = hora_entrada
    while actual < hora_salida:
        fin = min(_proxima_frontera(actual), hora_salida)
        segmento = fin - actual
        if _es_nocturno(actual):
            nocturno += segmento
        else:
            diurno += segmento
        actual = fin
    return diurno, nocturno


def calcular_horas_paraguay(
    hora_entrada: datetime, hora_salida: datetime, es_feriado: bool
) -> Dict[str, timedelta]:
    """Calcula el desglose legal de un turno según la Ley N.º 213.

    Args:
        hora_entrada: Instante de ingreso (aware o naive, consistente).
        hora_salida: Instante de egreso; si es anterior a la entrada se
            interpreta como turno nocturno que cruza la medianoche.
        es_feriado: Indica si el día es domingo o feriado oficial.

    Returns:
        Diccionario con ``horas_ordinarias``, ``horas_extra_50`` y
        ``horas_extra_100`` como ``timedelta``.
    """
    if hora_salida < hora_entrada:
        hora_salida += timedelta(days=1)
    if hora_salida - hora_entrada > timedelta(hours=24):
        raise ValueError("El turno no puede superar las 24 horas.")
    diurno, nocturno = _desglose_por_rangos(hora_entrada, hora_salida)
    if es_feriado:
        return {
            "horas_ordinarias": timedelta(0),
            "horas_extra_50": timedelta(0),
            "horas_extra_100": diurno + nocturno,
        }
    ordinarias_diurnas = min(diurno, JORNADA_DIURNA)
    ordinarias_nocturnas = min(nocturno, JORNADA_NOCTURNA)
    return {
        "horas_ordinarias": ordinarias_diurnas + ordinarias_nocturnas,
        "horas_extra_50": diurno - ordinarias_diurnas,
        "horas_extra_100": nocturno - ordinarias_nocturnas,
    }


class ClockEngine:
    """Orquesta el flujo de marcación aplicando las reglas laborales."""

    def __init__(self, db: Database, user: Dict) -> None:
        self.db = db
        self.user = user

    def clock_in(self) -> Tuple[int, datetime]:
        """Registra la entrada, marcando la tardanza según la tolerancia.

        La llegada que supera la gracia de 10 minutos queda clasificada
        como incidencia ``Llegada Tardía`` en el propio marcaje.

        Returns:
            Tupla con el identificador del marcaje y el instante exacto
            registrado (para el comprobante digital).
        """
        open_entry = self.db.get_open_entry(self.user["id"])
        if open_entry:
            raise ValueError("Ya hay una entrada abierta sin salida registrada.")
        ahora = ahora_local()
        tardanza = es_tardanza(ahora)
        incidencia = "Llegada Tardía" if tardanza else ""
        entry_id = self.db.open_clock_in(self.user["id"], ahora, tardanza, incidencia)
        return entry_id, ahora

    def clock_out(self) -> Tuple[int, datetime]:
        """Cierra la salida calculando y persistiendo el desglose legal.

        Si la jornada se interrumpe antes de completar las 8 horas legales
        en un día laborable, el marcaje queda clasificado como ``Salida
        Anticipada``; la incidencia convive con la de la entrada (por
        ejemplo, ``Llegada Tardía y Salida Anticipada``).

        Returns:
            Tupla con el identificador del marcaje y el instante exacto
            de la salida (para el comprobante digital).
        """
        open_entry = self.db.get_open_entry(self.user["id"])
        if not open_entry:
            raise ValueError("No hay una entrada abierta para cerrar.")
        ahora = ahora_local()
        feriado = es_feriado_o_domingo(open_entry["hora_entrada"])
        desglose = calcular_horas_paraguay(open_entry["hora_entrada"], ahora, feriado)
        incidencia = self._clasificar_incidencia_salida(
            desglose, feriado, open_entry.get("tipo_incidencia") or ""
        )
        self.db.close_clock_out(
            open_entry["id"],
            ahora,
            feriado,
            desglose["horas_ordinarias"],
            desglose["horas_extra_50"],
            desglose["horas_extra_100"],
            incidencia,
        )
        return open_entry["id"], ahora

    @staticmethod
    def _clasificar_incidencia_salida(
        desglose: Dict[str, timedelta], feriado: bool, incidencia_entrada: str
    ) -> str:
        """Combina la incidencia de entrada con la de salida anticipada."""
        trabajado = (
            desglose["horas_ordinarias"]
            + desglose["horas_extra_50"]
            + desglose["horas_extra_100"]
        )
        if not feriado and trabajado < JORNADA_DIURNA:
            return " y ".join(p for p in (incidencia_entrada, "Salida Anticipada") if p)
        return incidencia_entrada

    def detectar_accion_hoy(self) -> str:
        """Detecta la acción automática del botón maestro consultando el día.

        Reglas de auto-detección basadas en PostgreSQL:
        - Sin marcajes hoy: corresponde registrar la ``ENTRADA``.
        - Con entrada abierta (hora_salida vacía): corresponde la ``SALIDA``,
          que liquida los recargos de la Ley N.º 213.
        - Jornada completa: se rechaza con error para evitar duplicados.

        Returns:
            ``ENTRADA`` o ``SALIDA`` según el estado actual del empleado.
        """
        hoy = ahora_local().date()
        registros = self.db.get_entries_by_date(self.user["id"], hoy)
        if not registros:
            return "ENTRADA"
        abierto = next((r for r in registros if r["hora_salida"] is None), None)
        if abierto is not None:
            return "SALIDA"
        raise ValueError("Ya registró su entrada y su salida de hoy.")

    def registrar_asistencia(self) -> Tuple[int, datetime, str]:
        """Botón maestro: registra entrada o salida según el estado del día.

        Una sola acción para el kiosco de recepción: consulta internamente
        PostgreSQL y decide si corresponde abrir la jornada o cerrarla con
        el desglose legal de horas extraordinarias.

        Returns:
            Tupla con el identificador del marcaje, el instante registrado
            y el tipo ejecutado (``ENTRADA`` o ``SALIDA``).
        """
        accion = self.detectar_accion_hoy()
        if accion == "SALIDA":
            entry_id, momento = self.clock_out()
            return entry_id, momento, "SALIDA"
        entry_id, momento = self.clock_in()
        return entry_id, momento, "ENTRADA"

    def justificacion_para(self, fecha: date) -> Optional[Dict]:
        """Retorna la justificación aprobada que cubre la fecha, si existe."""
        return self.db.get_justificacion_por_fecha(self.user["id"], fecha)

    def es_dia_laboral(self, fecha: date) -> bool:
        """Indica si la fecha es un día laboral (ni domingo ni feriado)."""
        return not es_feriado_o_domingo(datetime.combine(fecha, time(12, 0)))

    def horas_justificadas(self, fecha: date) -> timedelta:
        """Horas ordinarias legales que reconoce una justificación aprobada.

        Se reconoce la jornada diurna estándar (8 horas) solo en días
        laborales; domingos y feriados ya son días de descanso y no
        generan horas.
        """
        if not self.justificacion_para(fecha):
            return timedelta(0)
        if not self.es_dia_laboral(fecha):
            return timedelta(0)
        return JORNADA_JUSTIFICADA

    def es_falta_no_justificada(self, fecha: date) -> bool:
        """Indica si una fecha laboral quedó sin marcar y sin justificación."""
        if not self.es_dia_laboral(fecha):
            return False
        if self.justificacion_para(fecha):
            return False
        return not self.db.get_entries_by_date(self.user["id"], fecha)

    def worked_seconds(self, entry: Dict) -> int:
        """Segundos efectivos trabajados en un marcaje cerrado."""
        if entry["hora_salida"] is None:
            return 0
        return max(0, int((entry["hora_salida"] - entry["hora_entrada"]).total_seconds()))

    def worked_seconds_today(self) -> int:
        """Segundos trabajados por el usuario durante el día actual."""
        today = datetime.now().date()
        entries = self.db.get_entries_by_date(self.user["id"], today)
        return sum(self.worked_seconds(entry) for entry in entries)

    def total_worked_seconds(self) -> int:
        """Segundos acumulados por el usuario en toda su historia."""
        entries = self.db.get_all_entries(self.user["id"])
        return sum(self.worked_seconds(entry) for entry in entries)

    @staticmethod
    def format_duration(seconds: int) -> str:
        """Formatea una duración en segundos como ``H:MM:SS``."""
        return str(timedelta(seconds=seconds))

    def report_today(self) -> str:
        """Genera el reporte textual de marcajes del día con su desglose."""
        today = datetime.now().date()
        entries = self.db.get_entries_by_date(self.user["id"], today)
        justificacion = self.justificacion_para(today)
        lines = [f"Registros de hoy ({today.isoformat()}):"]
        if not entries and justificacion:
            lines.append(
                f"  Justificación aprobada: {justificacion['tipo_permiso']} "
                f"({justificacion['fecha_inicio']} a {justificacion['fecha_fin']}) | "
                f"Horas legales reconocidas: {self.horas_justificadas(today)}"
            )
        elif not entries and self.es_falta_no_justificada(today):
            lines.append("  Sin marcajes y sin justificación: falta no justificada.")
        for entry in entries:
            lines.append(
                f"  #{entry['id']} Entrada: {entry['hora_entrada']} | "
                f"Salida: {entry['hora_salida'] or 'en curso'} | "
                f"Feriado: {'Sí' if entry['es_feriado'] else 'No'} | "
                f"Tardanza: {'Sí' if entry['es_tardanza'] else 'No'} | "
                f"Ordinarias: {entry['horas_ordinarias']} | "
                f"Extra 50%: {entry['horas_extra_50']} | "
                f"Extra 100%: {entry['horas_extra_100']}"
            )
        lines.append(f"Total trabajado: {self.format_duration(self.worked_seconds_today())}")
        return "\n".join(lines)