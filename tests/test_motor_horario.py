"""Pruebas de tabla del motor de liquidación (Ley N.º 213).

El motor es lógica pura y sin dependencias: admite cubrir los turnos
frontera de forma exhaustiva sin levantar una base. Cada caso declara el
turno y el desglose esperado, con el artículo que lo respalda.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import clock_engine as ce


def h(horas: float) -> timedelta:
    return timedelta(hours=horas)


# (descripción, entrada, salida, ordinarias, nocturnas, extra_50, extra_100, tipo)
CASOS = [
    (
        "Diurno estándar 08:00-17:00 (martes)",
        datetime(2026, 9, 15, 8, 0), datetime(2026, 9, 15, 17, 0),
        h(8), h(0), h(1), h(0), ce.JORNADA_DIURNA_TIPO,
    ),
    (
        "Diurno exacto 08:00-16:00 sin extras",
        datetime(2026, 9, 15, 8, 0), datetime(2026, 9, 15, 16, 0),
        h(8), h(0), h(0), h(0), ce.JORNADA_DIURNA_TIPO,
    ),
    (
        "Mixto largo 06:00-21:00 · el tope es 7h30, no 8+1",
        datetime(2026, 9, 15, 6, 0), datetime(2026, 9, 15, 21, 0),
        h(7.5), h(0), h(6.5), h(1), ce.JORNADA_MIXTA_TIPO,
    ),
    (
        "Mixto 07:00-22:00 · el ejemplo que la documentación daba por válido",
        datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 15, 22, 0),
        h(7.5), h(0), h(5.5), h(2), ce.JORNADA_MIXTA_TIPO,
    ),
    (
        "Nocturno 20:00-06:00 · 7h ordinarias, todas con recargo del 30%",
        datetime(2026, 9, 15, 20, 0), datetime(2026, 9, 16, 6, 0),
        h(7), h(7), h(0), h(3), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        "Nocturno exacto 22:00-05:00 sin extras",
        datetime(2026, 9, 15, 22, 0), datetime(2026, 9, 16, 5, 0),
        h(7), h(7), h(0), h(0), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        "Mixto con 6h nocturnas 17:00-02:00 · se reputa jornada nocturna",
        datetime(2026, 9, 15, 17, 0), datetime(2026, 9, 16, 2, 0),
        h(7), h(4), h(0), h(2), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        "Sábado 22:00 → domingo 06:00 · solo el domingo va al 100%",
        datetime(2026, 9, 12, 22, 0), datetime(2026, 9, 13, 6, 0),
        h(2), h(2), h(0), h(6), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        "Domingo 22:00 → lunes 06:00 · solo el domingo va al 100%",
        datetime(2026, 9, 13, 22, 0), datetime(2026, 9, 14, 6, 0),
        h(6), h(6), h(0), h(2), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        "Domingo completo 08:00-17:00",
        datetime(2026, 9, 13, 8, 0), datetime(2026, 9, 13, 17, 0),
        h(0), h(0), h(0), h(9), ce.JORNADA_DESCANSO_TIPO,
    ),
    (
        "Feriado 1 de enero 08:00-17:00",
        datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 17, 0),
        h(0), h(0), h(0), h(9), ce.JORNADA_DESCANSO_TIPO,
    ),
    (
        "Feriado de 2027 · el calendario ya no vence en diciembre",
        datetime(2027, 1, 1, 8, 0), datetime(2027, 1, 1, 17, 0),
        h(0), h(0), h(0), h(9), ce.JORNADA_DESCANSO_TIPO,
    ),
    (
        "Turno que arranca justo en la frontera nocturna 20:00-23:00",
        datetime(2026, 9, 15, 20, 0), datetime(2026, 9, 15, 23, 0),
        h(3), h(3), h(0), h(0), ce.JORNADA_NOCTURNA_TIPO,
    ),
    (
        # 10 h nocturnas (≥5) reputan nocturna toda la jornada, con tope de 7 h.
        # Las ordinarias se consumen en el tramo diurno inicial, así que no
        # hay horas con recargo nocturno pese a ser una jornada nocturna.
        "Turno de 24 horas exactas · 10h nocturnas reputan la jornada nocturna",
        datetime(2026, 9, 15, 6, 0), datetime(2026, 9, 16, 6, 0),
        h(7), h(0), h(7), h(10), ce.JORNADA_NOCTURNA_TIPO,
    ),
]

fallos = 0
for desc, entrada, salida, ord_, noct, e50, e100, tipo in CASOS:
    d = ce.calcular_horas_paraguay(entrada, salida)
    esperado = (ord_, noct, e50, e100, tipo)
    obtenido = (
        d.horas_ordinarias, d.horas_nocturnas,
        d.horas_extra_50, d.horas_extra_100, d.tipo_jornada,
    )
    if obtenido == esperado:
        print(f"  OK   {desc}")
    else:
        fallos += 1
        print(f"  FALLA {desc}")
        print(f"        esperado: ord={ord_} noct={noct} e50={e50} e100={e100} {tipo}")
        print(f"        obtenido: ord={d.horas_ordinarias} noct={d.horas_nocturnas} "
              f"e50={d.horas_extra_50} e100={d.horas_extra_100} {d.tipo_jornada}")

print()

# Toda hora trabajada debe quedar clasificada en exactamente una categoría.
for desc, entrada, salida, *_ in CASOS:
    d = ce.calcular_horas_paraguay(entrada, salida)
    real = salida - entrada
    if real < timedelta(0):
        real += timedelta(days=1)
    if d.total_trabajado != real:
        fallos += 1
        print(f"  FALLA conservación de horas en: {desc} "
              f"({d.total_trabajado} != {real})")
    if d.horas_nocturnas > d.horas_ordinarias:
        fallos += 1
        print(f"  FALLA nocturnas exceden ordinarias en: {desc}")
print("  OK   conservación de horas y coherencia de nocturnas")

# Pascua: 2026 cae el 5 de abril, de donde salen el Jueves y Viernes Santo
# que la lista fija del código traía cargados a mano.
assert ce._domingo_de_pascua(2026) == date(2026, 4, 5), "Pascua 2026 incorrecta"
assert ce._domingo_de_pascua(2027) == date(2027, 3, 28), "Pascua 2027 incorrecta"
feriados_2026 = ce.feriados_de(2026)
for esperado in (date(2026, 4, 2), date(2026, 4, 3), date(2026, 2, 9), date(2026, 12, 25)):
    assert esperado in feriados_2026, f"falta el feriado {esperado}"
assert date(2026, 3, 1) not in feriados_2026, "el traslado del Día de los Héroes no se aplicó"
print("  OK   calendario de feriados (Pascua, traslados y fechas fijas)")

# Mezclar naive y aware debe fallar con un mensaje claro, no con un TypeError.
try:
    ce.calcular_horas_paraguay(
        datetime(2026, 9, 15, 8, 0).astimezone(), datetime(2026, 9, 15, 17, 0)
    )
    fallos += 1
    print("  FALLA mezclar naive y aware no fue rechazado")
except ValueError:
    print("  OK   mezclar naive y aware se rechaza con ValueError")

try:
    ce.calcular_horas_paraguay(
        datetime(2026, 9, 15, 6, 0), datetime(2026, 9, 16, 7, 0)
    )
    fallos += 1
    print("  FALLA turno de más de 24 horas no fue rechazado")
except ValueError:
    print("  OK   turno de más de 24 horas se rechaza")

print()
if fallos:
    print(f"MOTOR HORARIO: {fallos} fallo(s)")
    raise SystemExit(1)
print(f"MOTOR HORARIO OK · {len(CASOS)} turnos verificados")
