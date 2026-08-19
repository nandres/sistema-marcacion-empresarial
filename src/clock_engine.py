from datetime import datetime, timedelta

from database import Database

JORNADA_ESTANDAR = timedelta(hours=8)


class ClockEngine:
    def __init__(self, db, user):
        self.db = db
        self.user = user

    def clock_in(self):
        open_entry = self.db.get_open_entry(self.user["id"])
        if open_entry:
            raise ValueError("Ya hay una entrada abierta sin salida registrada.")
        return self.db.open_clock_in(self.user["id"], datetime.now())

    def clock_out(self):
        open_entry = self.db.get_open_entry(self.user["id"])
        if not open_entry:
            raise ValueError("No hay una entrada abierta para cerrar.")
        ahora = datetime.now()
        horas_extra = self.compute_horas_extra(open_entry, ahora)
        self.db.close_clock_out(open_entry["id"], ahora, horas_extra)
        return open_entry["id"]

    def compute_horas_extra(self, entry, hora_salida):
        trabajado = hora_salida - entry["hora_entrada"]
        return max(trabajado - JORNADA_ESTANDAR, timedelta(0))

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
            extra = entry["horas_extra"] or timedelta(0)
            lines.append(
                f"  #{entry['id']} Entrada: {entry['hora_entrada']} | "
                f"Salida: {entry['hora_salida'] or 'en curso'} | "
                f"Extra: {extra}"
            )
        lines.append(f"Total trabajado: {self.format_duration(self.worked_seconds_today())}")
        return "\n".join(lines)