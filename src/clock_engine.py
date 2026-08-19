from datetime import date, datetime, time, timedelta

from database import Database

JORNADA_DIURNA = timedelta(hours=8)
JORNADA_NOCTURNA = timedelta(hours=7)
INICIO_DIURNO = time(6, 0)
FIN_DIURNO = time(20, 0)

FERIADOS_PARAGUAY_2026 = frozenset(
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


def ahora_local():
    return datetime.now().astimezone()


def es_feriado_o_domingo(momento):
    return momento.weekday() == 6 or momento.date() in FERIADOS_PARAGUAY_2026


def _es_nocturno(momento):
    return momento.hour >= 20 or momento.hour < 6


def _proxima_frontera(momento):
    if _es_nocturno(momento):
        frontera = momento.replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        frontera = momento.replace(hour=20, minute=0, second=0, microsecond=0)
    if frontera <= momento:
        frontera += timedelta(days=1)
    return frontera


def _desglose_por_rangos(hora_entrada, hora_salida):
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


def calcular_horas_paraguay(hora_entrada, hora_salida, es_feriado):
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
    def __init__(self, db, user):
        self.db = db
        self.user = user

    def clock_in(self):
        open_entry = self.db.get_open_entry(self.user["id"])
        if open_entry:
            raise ValueError("Ya hay una entrada abierta sin salida registrada.")
        return self.db.open_clock_in(self.user["id"], ahora_local())

    def clock_out(self):
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

    def worked_seconds(self, entry):
        if entry["hora_salida"] is None:
            return 0
        return max(0, int((entry["hora_salida"] - entry["hora_entrada"]).total_seconds()))

    def worked_seconds_today(self):
        today = datetime.now().date()
        entries = self.db.get_entries_by_date(self.user["id"], today)
        return sum(self.worked_seconds(entry) for entry in entries)

    def total_worked_seconds(self):
        entries = self.db.get_all_entries(self.user["id"])
        return sum(self.worked_seconds(entry) for entry in entries)

    @staticmethod
    def format_duration(seconds):
        return str(timedelta(seconds=seconds))

    def report_today(self):
        today = datetime.now().date()
        entries = self.db.get_entries_by_date(self.user["id"], today)
        lines = [f"Registros de hoy ({today.isoformat()}):"]
        for entry in entries:
            lines.append(
                f"  #{entry['id']} Entrada: {entry['hora_entrada']} | "
                f"Salida: {entry['hora_salida'] or 'en curso'} | "
                f"Feriado: {'Sí' if entry['es_feriado'] else 'No'} | "
                f"Ordinarias: {entry['horas_ordinarias']} | "
                f"Extra 50%: {entry['horas_extra_50']} | "
                f"Extra 100%: {entry['horas_extra_100']}"
            )
        lines.append(f"Total trabajado: {self.format_duration(self.worked_seconds_today())}")
        return "\n".join(lines)