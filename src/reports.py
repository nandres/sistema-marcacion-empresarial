"""Panel de reportes mensuales para el departamento de contabilidad.

Consulta PostgreSQL, agrupa los marcajes del mes por empleado y exporta
el desglose de horas ordinarias, horas extra al 50% y horas extra al 100%
en Excel (``.xlsx``) o CSV, listo para la liquidación de haberes conforme
a la Ley N.º 213.
"""

from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import auth
from database import Database

ENCABEZADO_RESUMEN: List[str] = [
    "Usuario",
    "Nombre completo",
    "Días trabajados",
    "Tardanzas",
    "Horas ordinarias",
    "Horas extra 50%",
    "Horas extra 100%",
    "Total horas",
]

ENCABEZADO_DETALLE: List[str] = [
    "Usuario",
    "Nombre completo",
    "Fecha",
    "Entrada",
    "Salida",
    "Feriado",
    "Tardanza",
    "Horas ordinarias",
    "Horas extra 50%",
    "Horas extra 100%",
]


def _sumar(marcajes: List[Dict], campo: str) -> timedelta:
    """Suma una columna de tipo INTERVAL de una lista de marcajes."""
    return sum((m[campo] or timedelta(0) for m in marcajes), timedelta(0))


def _fmt(duracion: timedelta) -> str:
    """Formatea un ``timedelta`` como ``HH:MM`` para planillas contables."""
    total = int(duracion.total_seconds())
    signo = "-" if total < 0 else ""
    total = abs(total)
    horas, resto = divmod(total, 3600)
    minutos = resto // 60
    return f"{signo}{horas:02d}:{minutos:02d}"


def _agrupar(marcajes: List[Dict]) -> Dict[int, Dict[str, Any]]:
    """Agrupa marcajes por empleado conservando el orden de la consulta."""
    grupos: Dict[int, Dict[str, Any]] = {}
    for m in marcajes:
        grupo = grupos.setdefault(
            m["user_id"],
            {"usuario": m["username"], "nombre": m["full_name"], "marcajes": []},
        )
        grupo["marcajes"].append(m)
    return grupos


def _fila_resumen(grupo: Dict[str, Any]) -> List[Any]:
    """Construye la fila de resumen mensual de un empleado."""
    registros = grupo["marcajes"]
    ordinarias = _sumar(registros, "horas_ordinarias")
    extra_50 = _sumar(registros, "horas_extra_50")
    extra_100 = _sumar(registros, "horas_extra_100")
    tardanzas = sum(1 for m in registros if m["es_tardanza"])
    return [
        grupo["usuario"],
        grupo["nombre"],
        len(registros),
        tardanzas,
        _fmt(ordinarias),
        _fmt(extra_50),
        _fmt(extra_100),
        _fmt(ordinarias + extra_50 + extra_100),
    ]


def _exportar_xlsx(grupos: Dict[int, Dict[str, Any]], ruta: Path) -> None:
    """Genera el libro Excel con las hojas Resumen y Detalle."""
    from openpyxl import Workbook

    libro = Workbook()
    resumen = libro.active
    resumen.title = "Resumen"
    resumen.append(ENCABEZADO_RESUMEN)
    for grupo in grupos.values():
        resumen.append(_fila_resumen(grupo))
    detalle = libro.create_sheet("Detalle")
    detalle.append(ENCABEZADO_DETALLE)
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
                    "Sí" if m["es_tardanza"] else "No",
                    _fmt(m["horas_ordinarias"]),
                    _fmt(m["horas_extra_50"]),
                    _fmt(m["horas_extra_100"]),
                ]
            )
    libro.save(ruta)


def _exportar_csv(grupos: Dict[int, Dict[str, Any]], ruta: Path) -> None:
    """Genera el CSV (UTF-8 BOM) con el resumen mensual por empleado."""
    with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(ENCABEZADO_RESUMEN)
        for grupo in grupos.values():
            escritor.writerow(_fila_resumen(grupo))


def exportar_asistencia_mensual(
    db: Database,
    actor: Dict,
    anio: int,
    mes: int,
    formato: str = "xlsx",
    ruta: Optional[str] = None,
) -> str:
    """Exporta la asistencia mensual de todos los empleados.

    Solo Administrador y Recursos Humanos pueden ejecutar la exportación
    (validado con RBAC dentro de este módulo).

    Args:
        db: Capa de persistencia conectada.
        actor: Usuario autenticado que solicita el reporte.
        anio: Año del periodo a exportar.
        mes: Mes (1-12) del periodo a exportar.
        formato: ``xlsx`` o ``csv``.
        ruta: Ruta de salida; por defecto ``reportes/asistencia_AAAA-MM.ext``.

    Returns:
        Ruta absoluta del archivo generado.
    """
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