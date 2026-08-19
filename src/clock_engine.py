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

    def clock_in(self) -> int:
        """Registra la entrada, marcando la tardanza según la tolerancia."""
        open_entry = self.db.get_open_entry(self.user["id"])
        if open_entry:
            raise ValueError("Ya hay una entrada abierta sin salida registrada.")
        ahora = ahora_local()
        return self.db.open_clock_in(self.user["id"], ahora, es_tardanza(ahora))

    def clock_out(self) -> int:
        """Cierra la salida calculando y persistiendo el desglose legal."""
        open_entry = self.db.get_open_entry(self.user["id"])
        if not open_entry:
            raise ValueError("No hay una entrada abierta para cerrar.")
        ahora = ahora_local()
        feriado = es_feriado_o_domingo(open_entry["hora_entrada"])
        desglose = calcular_horas_paraguay(open_entry["hora_entrada"], ahora, feriado)
        self.db.close_clock_out(
            open_entry["id"],
            ahora,
            feriado,
            desglose["horas_ordinarias"],
            desglose["horas_extra_50"],
            desglose["horas_extra_100"],
        )
        return open_entry["id"]

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
        lines = [f"Registros de hoy ({today.isoformat()}):"]
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