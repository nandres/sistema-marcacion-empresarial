"""Catálogo reglamentario de permisos y licencias de la CONATEL.

Codifica los artículos aplicables de los dos reglamentos vigentes:

- **Reglamento Interno de la CONATEL** (Res. Directorio N.º 1307/2010,
  homologado por la SFP) para funcionarios y personal contratado:
  Art. 18 (salidas por motivos personales), Art. 29 (vacaciones) y
  Art. 34 (permisos con y sin goce de sueldo).
- **Programa de Pasantía y Régimen Disciplinario** (Res. Directorio
  N.º 3028/2024) para pasantes: Art. 10, 14, 23 y 25.

Cada artículo define su cuota (días, horas o veces), el período de
cómputo (anual, mensual o por evento), los vínculos a los que aplica
y las condiciones legales que se muestran al momento de justificar.
El módulo es autocontenido (sin dependencias del proyecto) para poder
ser consumido por ``database``, ``auth``, ``reports``, la GUI y la web.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

UNIDAD_DIAS = "dias"
UNIDAD_HORAS = "horas"
UNIDAD_VECES = "veces"

PERIODO_ANUAL = "anual"
PERIODO_MENSUAL = "mensual"
PERIODO_EVENTO = "evento"

REGLAMENTO_INTERNO = "Reglamento Interno · Res. 1307/2010"
REGLAMENTO_PASANTIA = "Programa de Pasantía · Res. 3028/2024"

# ---------------------------------------------------------------------------
# Catálogo de artículos (un tipo por artículo; "Vacaciones" usa cuota
# dinámica devengada según antigüedad para funcionarios)
# ---------------------------------------------------------------------------

PERMISOS_FUNCIONARIOS: List[Dict[str, Any]] = [
    {
        "tipo": "Vacaciones",
        "articulo": "Art. 29",
        "nombre": "Vacaciones anuales con goce de sueldo",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "cuota_dinamica": True,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Después de cada año de trabajo continuo, conforme al "
        "Código del Trabajo. No acumulables (máx. dos años con autorización).",
    },
    {
        "tipo": "Maternidad",
        "articulo": "Art. 34, inc. a.1",
        "nombre": "Licencia por maternidad",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Conforme al Código del Trabajo. Acompañar certificado "
        "médico con fechas de inicio probable y parto.",
    },
    {
        "tipo": "Paternidad",
        "articulo": "Art. 34, inc. a.2",
        "nombre": "Permiso por paternidad",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Diez (10) días corridos. Acompañar certificado de nacimiento.",
    },
    {
        "tipo": "Adopcion",
        "articulo": "Art. 34, inc. a.3",
        "nombre": "Permiso por adopción",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 42,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Seis (6) semanas corridas para la madre, cuando se trate "
        "de un menor de dos (2) años. Acompañar sentencia definitiva de adopción.",
    },
    {
        "tipo": "Lactancia",
        "articulo": "Art. 34, inc. a.4",
        "nombre": "Permiso por lactancia",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Dos (2) permisos extraordinarios por día de 30 minutos "
        "cada uno o uno de 1 hora por día, conforme al Código del Trabajo.",
    },
    {
        "tipo": "Reposo",
        "articulo": "Art. 34, inc. a.5 y Art. 39",
        "nombre": "Licencia por razones de salud",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 90,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Conforme al certificado médico. El permiso por salud no "
        "podrá exceder de 90 días. Certificado original con diagnóstico, fecha, "
        "firma y sello del médico.",
    },
    {
        "tipo": "Salud Familiar",
        "articulo": "Art. 34, inc. a.6",
        "nombre": "Salud de cónyuge, hijos o padres",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Según la urgencia y gravedad de cada caso, con "
        "autorización de la Presidencia.",
    },
    {
        "tipo": "Matrimonio",
        "articulo": "Art. 34, inc. a.7",
        "nombre": "Permiso por matrimonio",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Diez (10) días corridos a contar desde el día hábil "
        "siguiente a la celebración del matrimonio civil. Acompañar certificado.",
    },
    {
        "tipo": "Duelo",
        "articulo": "Art. 34, inc. a.8",
        "nombre": "Fallecimiento de cónyuge, hijos, padres o hermanos",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Diez (10) días corridos. Acompañar certificado de "
        "defunción y documento que avale el parentesco.",
    },
    {
        "tipo": "Permiso por Examen",
        "articulo": "Art. 34, inc. a.9",
        "nombre": "Exámenes universitarios o técnicos",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Derecho al permiso el día del examen correspondiente "
        "(carreras universitarias de grado, postgrado o técnicas). Acompañar "
        "documento que acredite la fecha del examen.",
    },
    {
        "tipo": "Motivos Particulares",
        "articulo": "Art. 34, inc. a.10",
        "nombre": "Motivos particulares con goce de sueldo",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 5,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Hasta cinco (5) días en el año. Solicitud por intermedio "
        "del superior de la dependencia.",
    },
    {
        "tipo": "Descuento de Vacaciones",
        "articulo": "Art. 34, inc. a.11",
        "nombre": "Motivos particulares descontando vacaciones causadas",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Hasta diez (10) días en el año. No se otorgan permisos a "
        "descontar de vacaciones a las que aún no se tenga derecho.",
    },
    {
        "tipo": "Particulares Sin Goces",
        "articulo": "Art. 34, inc. b.2",
        "nombre": "Motivos particulares sin goce de sueldo",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Hasta diez (10) días en el año, sin goce de sueldo.",
    },
    {
        "tipo": "Salidas Personales",
        "articulo": "Art. 18",
        "nombre": "Salidas por motivos personales",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": 6,
        "unidad": UNIDAD_HORAS,
        "periodo": PERIODO_MENSUAL,
        "condiciones": "Hasta seis (6) horas al mes, no acumulables. No se admite "
        "la utilización íntegra en un mismo día. Autorización del Gerente de área.",
    },
    {
        "tipo": "Omision de Registro",
        "articulo": "Art. 13",
        "nombre": "Justificación de omisión de registro",
        "reglamento": REGLAMENTO_INTERNO,
        "vinculos": ("Funcionario",),
        "cuota": None,
        "unidad": UNIDAD_VECES,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Solo se admite dentro del siguiente día hábil, con aval "
        "del Gerente del área de que cumplió la jornada.",
    },
]

PERMISOS_PASANTES: List[Dict[str, Any]] = [
    {
        "tipo": "Licencia de Pasante",
        "articulo": "Art. 23",
        "nombre": "Licencia anual de pasante",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Diez (10) días hábiles, sin sanciones disciplinarias en "
        "los últimos 12 meses, no acumulable, al cumplirse el año desde el "
        "inicio de la pasantía. No se puede adelantar su usufructo. Autorización "
        "expresa del tutor.",
    },
    {
        "tipo": "Maternidad",
        "articulo": "Art. 25, inc. a.1",
        "nombre": "Licencia por maternidad",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Conforme al Código del Trabajo y la Ley N.º 5508/2015. "
        "Acompañar certificado médico y constancia de estudiante activo.",
    },
    {
        "tipo": "Paternidad",
        "articulo": "Art. 25, inc. a.2",
        "nombre": "Permiso por paternidad",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 14,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Catorce (14) días corridos. Acompañar certificado de nacimiento.",
    },
    {
        "tipo": "Adopcion",
        "articulo": "Art. 25, inc. a.3",
        "nombre": "Permiso por adopción",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 42,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Seis (6) semanas corridas para la madre, cuando se trate "
        "de un menor de dos (2) años. Acompañar sentencia definitiva de adopción.",
    },
    {
        "tipo": "Lactancia",
        "articulo": "Art. 25, inc. b",
        "nombre": "Permiso por lactancia",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Un permiso al día de 90 minutos durante los primeros 6 "
        "meses; 60 minutos al día de 7 a 24 meses (renovable cada 3 meses). "
        "Ley N.º 5508/2015.",
    },
    {
        "tipo": "Reposo",
        "articulo": "Art. 25, inc. c.1 y Art. 31",
        "nombre": "Licencia por razones de salud",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 90,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Conforme a reposo médico original (diagnóstico, fecha, "
        "firma y sello del médico y visación del MSPyBS). Máximo 90 días por "
        "ejercicio fiscal. Remitir el reposo dentro de las 24 horas de la ausencia.",
    },
    {
        "tipo": "Salud Familiar",
        "articulo": "Art. 25, inc. c.2",
        "nombre": "Salud de cónyuge, hijos, tutor o padres",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Según la urgencia y gravedad de cada caso, con "
        "autorización de la Gerencia de Capital Humano.",
    },
    {
        "tipo": "Prevencion Femenina",
        "articulo": "Art. 25, inc. c.3 (Ley N.º 6211/2018)",
        "nombre": "Exámenes de Papanicolaou y mamografía",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 2,
        "unidad": UNIDAD_VECES,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Hasta dos (2) veces al año, para someterse a exámenes de "
        "detección precoz (Ley N.º 6211/2018).",
    },
    {
        "tipo": "Prevencion Masculina",
        "articulo": "Art. 25, inc. c.4 (Ley N.º 6280/2019)",
        "nombre": "Detección precoz de cáncer de próstata y colón",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 2,
        "unidad": UNIDAD_VECES,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Hasta dos (2) veces al año, para someterse a exámenes de "
        "detección precoz (Ley N.º 6280/2019).",
    },
    {
        "tipo": "Matrimonio",
        "articulo": "Art. 25, inc. d",
        "nombre": "Permiso por matrimonio",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Diez (10) días corridos a contar desde la fecha del "
        "evento. Acompañar certificado de matrimonio.",
    },
    {
        "tipo": "Duelo",
        "articulo": "Art. 25, inc. e.1",
        "nombre": "Fallecimiento de cónyuge, hijos, tutor, padres o hermanos",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 10,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Diez (10) días corridos a partir de la fecha del evento. "
        "Acompañar certificado de defunción y documento que avale el parentesco.",
    },
    {
        "tipo": "Duelo Abuelos",
        "articulo": "Art. 25, inc. e.2 (Ley N.º 3384/2007)",
        "nombre": "Fallecimiento de abuelos",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 3,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Tres (3) días corridos a contar desde la fecha del "
        "evento (Ley N.º 3384/2007).",
    },
    {
        "tipo": "Permiso por Examen",
        "articulo": "Art. 25, inc. f",
        "nombre": "Exámenes finales",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": None,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_EVENTO,
        "condiciones": "Derecho al permiso el día del examen final correspondiente. "
        "Acompañar documento original que acredite la fecha del examen.",
    },
    {
        "tipo": "Fuerza Mayor",
        "articulo": "Art. 25, inc. g",
        "nombre": "Motivos de fuerza mayor",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 5,
        "unidad": UNIDAD_DIAS,
        "periodo": PERIODO_ANUAL,
        "condiciones": "Máximo 5 días hábiles al año, con visto bueno del tutor "
        "y refrendado por la Gerencia de Capital Humano. Para extensiones "
        "universitarias, defensa de tesis o causas imprevistas debidamente justificadas.",
    },
    {
        "tipo": "Salidas Salud",
        "articulo": "Art. 25, inc. h",
        "nombre": "Salidas por consultas médicas",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 6,
        "unidad": UNIDAD_HORAS,
        "periodo": PERIODO_MENSUAL,
        "condiciones": "Hasta seis (6) horas al mes, no acumulable, para acudir a "
        "consultas médicas, verificado con la constancia de consulta.",
    },
    {
        "tipo": "Horas No Remuneradas",
        "articulo": "Art. 25, inc. i",
        "nombre": "Día libre por horas trabajadas no remuneradas",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 2,
        "unidad": UNIDAD_VECES,
        "periodo": PERIODO_MENSUAL,
        "condiciones": "Hasta 2 veces al mes: por cada seis horas realizadas, un "
        "día libre, cuando la institución requiera el apoyo del pasante, previa "
        "autorización de la Gerencia de Capital Humano.",
    },
    {
        "tipo": "Omision de Registro",
        "articulo": "Art. 10",
        "nombre": "Justificación de omisión de registro",
        "reglamento": REGLAMENTO_PASANTIA,
        "vinculos": ("Pasante",),
        "cuota": 3,
        "unidad": UNIDAD_VECES,
        "periodo": PERIODO_MENSUAL,
        "condiciones": "Hasta 3 veces al mes, presentada dentro del siguiente "
        "día hábil, con aval del tutor.",
    },
]

CATALOGO_PERMISOS: List[Dict[str, Any]] = PERMISOS_FUNCIONARIOS + PERMISOS_PASANTES

# Tipos válidos para el CHECK de la base de datos (incluye el tipo histórico
# "Permiso" para no romper filas ya registradas).
TIPOS_PERMISO: tuple = tuple(dict.fromkeys(p["tipo"] for p in CATALOGO_PERMISOS))
TIPOS_PERMISO_CHECK: tuple = TIPOS_PERMISO + ("Permiso",)


def _vacaciones_funcionario(antiguedad_anios: float) -> int:
    """Días de vacaciones devengadas del funcionario según su antigüedad.

    Escala estándar de la función pública paraguaya (Ley N.º 1626/00):
    12 días bajo 5 años, 20 días entre 5 y 10 años y 30 días desde los
    10 años de servicio.
    """
    if antiguedad_anios < 5:
        return 12
    if antiguedad_anios < 10:
        return 20
    return 30


def articulos_aplicables(vinculo: str) -> List[Dict[str, Any]]:
    """Devuelve los artículos del reglamento que aplican a un vínculo."""
    vinculo = vinculo or "Funcionario"
    return [p for p in CATALOGO_PERMISOS if vinculo in p["vinculos"]]


def _en_periodo(justificacion: Dict[str, Any], periodo: str, hoy: date) -> bool:
    """Indica si una justificación cae dentro del período de cómputo."""
    inicio = justificacion["fecha_inicio"]
    if periodo == PERIODO_MENSUAL:
        return inicio.year == hoy.year and inicio.month == hoy.month
    if periodo == PERIODO_ANUAL:
        return inicio.year == hoy.year
    return True


def _usados(
    articulo: Dict[str, Any], justificaciones: List[Dict[str, Any]]
) -> float:
    """Suma los días, horas o veces consumidos del artículo en el período."""
    filas = [j for j in justificaciones if j["tipo_permiso"] == articulo["tipo"]]
    if not filas:
        return 0.0
    if articulo["unidad"] == UNIDAD_HORAS:
        return float(sum(j.get("horas_usadas") or 0 for j in filas))
    if articulo["unidad"] in (UNIDAD_VECES,):
        return float(len(filas))
    return float(
        sum(
            (j["fecha_fin"] - j["fecha_inicio"]).days + 1
            for j in filas
        )
    )


def disponibilidad_permisos(
    db, user: Dict[str, Any], fecha: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Calcula la disponibilidad de cada artículo para un empleado.

    Args:
        db: Capa de persistencia conectada.
        user: Empleado (dict de ``users``) con su ``tipo_vinculo``.
        fecha: Día de referencia; por defecto la fecha actual.

    Returns:
        Lista de artículos con ``cuota``, ``usados``, ``restantes`` (``None``
        si no tiene límite) y el booleano ``disponible``.
    """
    hoy = fecha or date.today()
    vinculo = user.get("tipo_vinculo") or "Funcionario"
    antiguedad = (hoy - user["created_at"].date()).days / 365.25
    todas = [
        j for j in db.list_justificaciones() if j["usuario_id"] == user["id"]
    ]
    resultado: List[Dict[str, Any]] = []
    for articulo in articulos_aplicables(vinculo):
        en_periodo = [
            j for j in todas if _en_periodo(j, articulo["periodo"], hoy)
        ]
        if articulo.get("cuota_dinamica"):
            cuota = _vacaciones_funcionario(antiguedad)
        else:
            cuota = articulo["cuota"]
        usados = _usados(articulo, en_periodo)
        restantes = None if cuota is None else max(0.0, float(cuota) - usados)
        resultado.append(
            {
                "tipo": articulo["tipo"],
                "articulo": articulo["articulo"],
                "nombre": articulo["nombre"],
                "reglamento": articulo["reglamento"],
                "unidad": articulo["unidad"],
                "periodo": articulo["periodo"],
                "cuota": cuota,
                "usados": usados,
                "restantes": restantes,
                "disponible": cuota is None or usados < cuota,
                "condiciones": articulo["condiciones"],
            }
        )
    return resultado


def encontrar_articulo(tipo: str, vinculo: str) -> Optional[Dict[str, Any]]:
    """Localiza el artículo del catálogo para un tipo y vínculo dados."""
    for articulo in articulos_aplicables(vinculo):
        if articulo["tipo"] == tipo:
            return articulo
    return None