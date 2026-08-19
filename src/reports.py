import csv
from datetime import timedelta
from pathlib import Path

import auth


def _sumar(marcajes, campo):
    return sum((m[campo] or timedelta(0) for m in marcajes), timedelta(0))


def _fmt(duracion):
    total = int(duracion.total_seconds())
    signo = "-" if total < 0 else ""
    total = abs(total)
    horas, resto = divmod(total, 3600)
    minutos = resto // 60
    return f"{signo}{horas:02d}:{minutos:02d}"


def _agrupar(marcajes):
    grupos = {}
    for m in marcajes:
        grupo = grupos.setdefault(
            m["user_id"],
            {"usuario": m["username"], "nombre": m["full_name"], "marcajes": []},
        )
        grupo["marcajes"].append(m)
    return grupos


def _fila_resumen(grupo):
    registros = grupo["marcajes"]
    ordinarias = _sumar(registros, "horas_ordinarias")
    extra_50 = _sumar(registros, "horas_extra_50")
    extra_100 = _sumar(registros, "horas_extra_100")
    return [
        grupo["usuario"],
        grupo["nombre"],
        len(registros),
        _fmt(ordinarias),
        _fmt(extra_50),
        _fmt(extra_100),
        _fmt(ordinarias + extra_50 + extra_100),
    ]


def _exportar_xlsx(grupos, ruta):
    from openpyxl import Workbook

    libro = Workbook()
    resumen = libro.active
    resumen.title = "Resumen"
    resumen.append(
        [
            "Usuario",
            "Nombre completo",
            "Días trabajados",
            "Horas ordinarias",
            "Horas extra 50%",
            "Horas extra 100%",
            "Total horas",
        ]
    )
    for grupo in grupos.values():
        resumen.append(_fila_resumen(grupo))
    detalle = libro.create_sheet("Detalle")
    detalle.append(
        [
            "Usuario",
            "Nombre completo",
            "Fecha",
            "Entrada",
            "Salida",
            "Feriado",
            "Horas ordinarias",
            "Horas extra 50%",
            "Horas extra 100%",
        ]
    )
    for grupo in grupos.values():
        for m in grupo["marcajes"]:
            detalle.append(
                [
                    grupo["usuario"],
                    grupo["nombre"],
                    m["hora_entrada"].date().isoformat(),
                    m["hora_entrada"].strftime("%H:%M"),
                    m["hora_salida"].strftime("%H:%M") if m["hora_salida"] else "en curso",
                    "Sí" if m["es_feriado"] else "No",
                    _fmt(m["horas_ordinarias"]),
                    _fmt(m["horas_extra_50"]),
                    _fmt(m["horas_extra_100"]),
                ]
            )
    libro.save(ruta)


def _exportar_csv(grupos, ruta):
    with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(
            [
                "Usuario",
                "Nombre completo",
                "Días trabajados",
                "Horas ordinarias",
                "Horas extra 50%",
                "Horas extra 100%",
                "Total horas",
            ]
        )
        for grupo in grupos.values():
            escritor.writerow(_fila_resumen(grupo))


def exportar_asistencia_mensual(db, actor, anio, mes, formato="xlsx", ruta=None):
    auth.require_role(db, actor, auth.ROLES_REPORTES)
    if formato not in ("xlsx", "csv"):
        raise ValueError("Formato no soportado. Use 'xlsx' o 'csv'.")
    marcajes = db.get_marcajes_month(anio, mes)
    grupos = _agrupar(marcajes)
    if ruta is None:
        ruta = Path("reportes") / f"asistencia_{anio:04d}-{mes:02d}.{formato}"
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if formato == "xlsx":
        _exportar_xlsx(grupos, ruta)
    else:
        _exportar_csv(grupos, ruta)
    return str(ruta)