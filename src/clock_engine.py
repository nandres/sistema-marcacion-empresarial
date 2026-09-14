"""Motor de reglas laborales del Código del Trabajo de Paraguay (Ley N.º 213).

Liquida cada turno partiéndolo en tramos homogéneos y aplicando el recargo
que corresponde a cada uno:

- **Naturaleza del tramo**: diurno (06:00 a 20:00) o nocturno (20:00 a 06:00).
- **Día calendario del tramo**: un turno que cruza la medianoche hacia un
  domingo o feriado liquida esa porción al 100 %, y solo esa porción.
- **Tope de jornada ordinaria**: se decide **una vez** para todo el turno
  según su naturaleza (Art. 194) — 8 h diurna, 7 h nocturna, 7 h 30 mixta —
  y no sumando los topes de cada tramo. Una jornada cuyo tramo nocturno
  alcanza las 5 horas se reputa nocturna completa.
- **Recargos**: trabajo nocturno ordinario +30 % (Art. 232); hora
  extraordinaria diurna +50 % y nocturna +100 % (Art. 234); domingo o
  feriado +100 % (Art. 233).

Las horas ordinarias se asignan en orden cronológico: son las primeras
efectivamente trabajadas, y todo lo que excede el tope es extraordinario.

``evaluar_asistencia`` aplica además la Resolución de Directorio N.º
3028/2024: los pasantes gozan de tolerancia ordinaria limitada a tres veces
al mes y de la tolerancia climática de 30 minutos en días de lluvia intensa
(con corte a ``Ausencia Injustificada`` a los 30 minutos de retraso),
mientras los funcionarios conservan la gracia general de 15 minutos.

Contra qué hora se mide ese retraso lo decide el **turno** del empleado
(``turnos.py``), no una constante de la empresa: cada uno tiene su horario,
sus días y, si hace falta, su propia tolerancia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from database import Database
import notifications
import turnos

JORNADA_DIURNA: timedelta = timedelta(hours=8)
JORNADA_NOCTURNA: timedelta = timedelta(hours=7)
JORNADA_MIXTA: timedelta = timedelta(hours=7, minutes=30)
JORNADA_JUSTIFICADA: timedelta = JORNADA_DIURNA
INICIO_DIURNO: time = time(6, 0)
FIN_DIURNO: time = time(20, 0)

DESCANSO_ENTRE_JORNADAS: timedelta = timedelta(hours=8)
"""Hueco a partir del cual dos marcas pertenecen a jornadas distintas."""

VENTANA_CONSULTA: timedelta = timedelta(hours=36)
"""Cuánto hacia atrás se leen marcas para reconstruir la jornada en curso."""

# Art. 194: la jornada mixta cuyo tramo nocturno alcanza las 5 horas se
# reputa nocturna a todos los efectos, incluido su tope de 7 horas.
NOCTURNO_QUE_VUELVE_NOCTURNA: timedelta = timedelta(hours=5)

RECARGO_NOCTURNO: float = 0.30
"""Art. 232: recargo sobre la hora ordinaria trabajada en horario nocturno."""

JORNADA_DIURNA_TIPO: str = "Diurna"
JORNADA_NOCTURNA_TIPO: str = "Nocturna"
JORNADA_MIXTA_TIPO: str = "Mixta"
JORNADA_DESCANSO_TIPO: str = "Descanso"

TOPES_POR_TIPO: Dict[str, timedelta] = {
    JORNADA_DIURNA_TIPO: JORNADA_DIURNA,
    JORNADA_NOCTURNA_TIPO: JORNADA_NOCTURNA,
    JORNADA_MIXTA_TIPO: JORNADA_MIXTA,
    JORNADA_DESCANSO_TIPO: timedelta(0),
}

TOLERANCIA_PASANTE: timedelta = timedelta(minutes=10)
TOLERANCIA_FUNCIONARIO: timedelta = timedelta(minutes=15)
TOLERANCIA_CLIMATICA: timedelta = timedelta(minutes=30)
MAX_TARDANZAS_PASANTE: int = 3

# Más allá de este lapso una entrada sin salida deja de ser una jornada en
# curso y pasa a ser un olvido. El techo cubre con holgura el turno legal más
# largo (nocturno de 7 h) y cualquier extra razonable encima.
MAX_JORNADA_ABIERTA: timedelta = timedelta(hours=18)
INCIDENCIA_SIN_CIERRE: str = "Salida no registrada"

FERIADOS_FIJOS: Tuple[Tuple[int, int, str], ...] = (
    (1, 1, "Año Nuevo"),
    (3, 1, "Día de los Héroes"),
    (5, 1, "Día del Trabajador"),
    (5, 14, "Independencia Nacional"),
    (5, 15, "Independencia Nacional"),
    (6, 12, "Paz del Chaco"),
    (8, 15, "Fundación de Asunción"),
    (9, 29, "Victoria de Boquerón"),
    (12, 8, "Virgen de Caacupé"),
    (12, 25, "Navidad"),
)
"""Feriados de fecha fija del calendario paraguayo (sin traslados)."""

# Traslados decretados año a año. Sin el decreto cargado, el calendario base
# ubica estos feriados en su fecha estatutaria, que es lo legalmente correcto
# a falta de norma en contrario.
FERIADOS_TRASLADADOS: Dict[int, Dict[date, date]] = {
    2026: {
        date(2026, 3, 1): date(2026, 2, 9),
        date(2026, 6, 12): date(2026, 6, 8),
        date(2026, 8, 15): date(2026, 8, 10),
        date(2026, 9, 29): date(2026, 9, 28),
    },
}


def _domingo_de_pascua(anio: int) -> date:
    """Domingo de Pascua por el algoritmo gregoriano anónimo."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def feriados_de(anio: int) -> Dict[date, str]:
    """Calendario oficial de feriados de un año, con sus traslados vigentes.

    Combina los feriados de fecha fija, los derivados de la Pascua (Jueves y
    Viernes Santo) y los traslados decretados que estén registrados. A
    diferencia de una lista fija, sigue siendo correcto en cualquier año.
    """
    pascua = _domingo_de_pascua(anio)
    calendario: Dict[date, str] = {
        pascua - timedelta(days=3): "Jueves Santo",
        pascua - timedelta(days=2): "Viernes Santo",
    }
    traslados = FERIADOS_TRASLADADOS.get(anio, {})
    for mes, dia, nombre in FERIADOS_FIJOS:
        original = date(anio, mes, dia)
        calendario[traslados.get(original, original)] = nombre
    return calendario


def es_feriado_o_domingo(momento: datetime) -> bool:
    """Indica si la fecha del momento es domingo o feriado oficial de Paraguay."""
    return es_dia_de_descanso(momento.date())


def es_dia_de_descanso(dia: date) -> bool:
    """Indica si una fecha es domingo o feriado (liquidación al 100 %)."""
    return dia.weekday() == 6 or dia in feriados_de(dia.year)


def ahora_local() -> datetime:
    """Retorna la fecha/hora local con zona horaria (aware)."""
    return datetime.now().astimezone()


def turno_vigente(
    db: Database, usuario_id: int, dia: Optional[date] = None
) -> turnos.Turno:
    """Turno que rige para un empleado en una fecha (hoy por defecto)."""
    return turnos.resolver(db, usuario_id, dia or ahora_local().date())


def tramos_consumidos(db: Database, usuario_id: int, momento: datetime) -> int:
    """Tramos que el empleado ya cerró en la jornada en curso.

    Dónde empieza la jornada lo decide el **descanso**, no el calendario ni
    el horario teórico. El día no sirve porque el turno nocturno reparte una
    jornada entre dos fechas; una ventana fija hacia atrás tampoco, porque
    con 18 horas la entrada de anoche a las 22:00 seguía contando al fichar
    hoy a la misma hora; y anclarla a la hora prevista del turno falla con
    quien trabaja lejos de su horario.

    Se camina hacia atrás desde la marca actual y se corta en el primer
    hueco que constituye un descanso. El umbral vive entre los dos valores
    que lo rodean: la pausa más larga dentro de una jornada partida ronda
    las cuatro horas y el descanso legal entre jornadas son doce.
    """
    referencia = momento if momento.tzinfo else momento.astimezone()
    cerradas = [
        m
        for m in db.listar_marcajes_desde(usuario_id, referencia - VENTANA_CONSULTA)
        if m["hora_salida"] is not None and m["hora_salida"] <= referencia
    ]
    consumidos = 0
    corte = referencia
    for marca in reversed(cerradas):
        if corte - marca["hora_salida"] >= DESCANSO_ENTRE_JORNADAS:
            break
        consumidos += 1
        corte = marca["hora_entrada"]
    return consumidos


def duracion_comprometida(
    db: Database, usuario_id: int, entrada: datetime
) -> timedelta:
    """Cuánto duraba el tramo que el empleado vino a cubrir.

    Sin esto, cerrar el tramo de la mañana de una jornada partida a las
    cuatro horas —que es exactamente lo pactado— se reprocha como salida
    anticipada por no haber llegado al tope legal de ocho.
    """
    momento = entrada if entrada.tzinfo else entrada.astimezone()
    turno = turno_vigente(db, usuario_id, momento.date())
    indice = tramos_consumidos(db, usuario_id, momento)
    return turno.tramo_para(momento, indice).duracion


def horas_previstas_legales(turno: turnos.Turno, dia: date) -> timedelta:
    """Horas **ordinarias** que rinde el turno en un día, según el Art. 194.

    No es la duración del horario sino lo que de ella es jornada ordinaria:
    un turno de 22:00 a 06:00 dura ocho horas pero rinde siete, porque el
    tope nocturno son siete y la octava ya es extraordinaria.
    """
    total = timedelta(0)
    for tramo in turno.tramos:
        entrada = tramo.entrada_del(dia)
        desglose = calcular_horas_paraguay(
            entrada, entrada + tramo.duracion, lambda _: False
        )
        total += desglose.horas_ordinarias
    return total


def es_tardanza(db: Database, usuario_id: int, hora_entrada: datetime) -> bool:
    """Indica si una entrada llega fuera de la tolerancia de su turno.

    Delega en ``evaluar_asistencia`` en lugar de aplicar su propia gracia.
    Cuando eran dos reglas distintas —10 minutos acá, 15 en el flujo de
    marcación— un funcionario que fichaba 08:12 era Normal al marcar y
    Llegada Tardía si Recursos Humanos le corregía la marca **a esa misma
    hora**: la corrección castigaba por corregir.
    """
    return evaluar_asistencia(db, usuario_id, hora_entrada)["estado"] != "Normal"


def condicion_declarada(db: Database, dia: date) -> Dict[str, Any]:
    """Condición excepcional vigente para un día, según la declaró RRHH.

    La tolerancia climática la activa una declaración administrativa que
    alcanza a toda la plantilla, no el empleado que llega tarde: si el
    sujeto de la regla controla el dato que la dispara, la regla no existe.

    Returns:
        ``{"condicion": str, "tolerancia": timedelta, "declarante": str}``;
        condición vacía y tolerancia cero si el día es normal.
    """
    fila = db.get_condicion_dia(dia)
    if not fila:
        return {"condicion": "", "tolerancia": timedelta(0), "declarante": ""}
    minutos = int(fila["tolerancia_min"] or 0)
    return {
        "condicion": fila["condicion"],
        "tolerancia": timedelta(minutes=minutos),
        "declarante": fila.get("declarante") or "",
    }


def evaluar_asistencia(
    db: Database,
    usuario_id: int,
    hora_marca: datetime,
) -> Dict[str, Any]:
    """Evalúa una entrada según la Res. 3028/2024.

    Para pasantes:
    - Tolerancia ordinaria de 10 minutos, consumible como máximo 3 veces
      al mes; a partir de la 4.ª llegada el retraso cuenta de inmediato.
    - Si Recursos Humanos declaró una condición excepcional para el día
      (lluvia intensa, corte de rutas), su tolerancia se acumula a la
      ordinaria vigente.
    - Si el retraso excede la tolerancia máxima del día, el estado pasa
      directamente a ``Ausencia Injustificada``.

    Para funcionarios:
    - Gracia general de 15 minutos sin límite mensual de uso.
    - Sin corte de ausencia por retraso y con la misma cobertura climática.

    La hora contra la que se mide el retraso sale del turno vigente y del
    tramo que le toca cubrir; si el turno declara su propia tolerancia, esa
    desplaza a la del vínculo. En un día que el turno no cubre no hay hora a
    la cual llegar tarde: la marca se registra y se liquida, pero no genera
    incidencia.

    Returns:
        Diccionario con ``estado`` (Normal, Llegada Tardía o Ausencia
        Injustificada), el turno y la hora previstos, las tolerancias
        consideradas y un resumen legible.
    """
    usuario = db.get_user_by_id(usuario_id)
    if not usuario:
        raise ValueError("Empleado no encontrado.")
    vinculo = (usuario.get("tipo_vinculo") or "Funcionario").strip()
    if vinculo not in ("Pasante", "Funcionario"):
        raise ValueError(f"Tipo de vínculo desconocido: '{vinculo}'.")
    instante = hora_marca if hora_marca.tzinfo else hora_marca.astimezone()
    turno = turno_vigente(db, usuario_id, instante.date())
    consumidos = tramos_consumidos(db, usuario_id, instante)
    hora_marca = instante.replace(tzinfo=None)
    prevista = turno.entrada_prevista(hora_marca, consumidos)
    retraso = max(timedelta(0), hora_marca - prevista)
    dia_del_turno = prevista.date()
    excepcion = condicion_declarada(db, dia_del_turno)
    climatica = excepcion["tolerancia"]
    # Una tolerancia propia del turno desplaza a la del vínculo: el horario
    # de atención al público no admite la misma gracia que una oficina.
    propia = (
        timedelta(minutes=turno.tolerancia_min)
        if turno.tolerancia_min is not None
        else None
    )
    tardanzas_mes: Optional[int] = None
    if not turno.trabaja(dia_del_turno):
        # Fuera de los días del turno no hay hora a la cual llegar tarde. La
        # marca se registra igual —el trabajo existió y se liquida— pero no
        # puede generar una incidencia disciplinaria.
        tolerancia = timedelta(0)
        retraso = timedelta(0)
        estado = "Normal"
    elif vinculo == "Pasante":
        tardanzas_mes = db.contar_tardanzas_mes(usuario_id, dia_del_turno)
        base = propia if propia is not None else TOLERANCIA_PASANTE
        ordinaria = base if tardanzas_mes < MAX_TARDANZAS_PASANTE else timedelta(0)
        tolerancia = ordinaria + climatica
        # El corte de ausencia se corre junto con la tolerancia declarada: si
        # la empresa reconoce 30 minutos por lluvia, llegar a los 25 no puede
        # computarse como ausencia.
        if retraso > max(TOLERANCIA_CLIMATICA, tolerancia):
            estado = "Ausencia Injustificada"
        elif retraso > tolerancia:
            estado = "Llegada Tardía"
        else:
            estado = "Normal"
    else:
        base = propia if propia is not None else TOLERANCIA_FUNCIONARIO
        tolerancia = base + climatica
        estado = "Llegada Tardía" if retraso > tolerancia else "Normal"
    return {
        "tipo_vinculo": vinculo,
        "estado": estado,
        "retraso_min": int(retraso.total_seconds() // 60),
        "tolerancia_efectiva_min": int(tolerancia.total_seconds() // 60),
        "condicion_dia": excepcion["condicion"],
        "tolerancia_climatica": bool(climatica),
        "tardanzas_mes_previas": tardanzas_mes,
        "turno": turno.nombre,
        "turno_id": turno.id,
        "tramo": consumidos + 1,
        "entrada_prevista": prevista.strftime("%H:%M"),
        "fuera_de_turno": not turno.trabaja(dia_del_turno),
        "detalle": (
            f"{vinculo} · {turno.nombre} ({prevista.strftime('%H:%M')}) · "
            f"retraso {int(retraso.total_seconds() // 60)} min vs "
            f"tolerancia {int(tolerancia.total_seconds() // 60)} min "
            f"({excepcion['condicion'] or 'día normal'}) → {estado}"
        ),
    }


@dataclass(frozen=True)
class DesgloseJornada:
    """Liquidación legal de un turno, lista para persistir y para nómina."""

    horas_ordinarias: timedelta
    horas_nocturnas: timedelta
    horas_extra_50: timedelta
    horas_extra_100: timedelta
    tipo_jornada: str
    toca_descanso: bool

    @property
    def total_trabajado(self) -> timedelta:
        return self.horas_ordinarias + self.horas_extra_50 + self.horas_extra_100


@dataclass(frozen=True)
class _Tramo:
    """Porción de turno homogénea en naturaleza y en día calendario."""

    duracion: timedelta
    nocturno: bool
    descanso: bool


def _es_nocturno(momento: datetime) -> bool:
    """Clasifica un instante como nocturno (20:00 a 06:00)."""
    return momento.hour >= 20 or momento.hour < INICIO_DIURNO.hour


def _proxima_frontera(momento: datetime) -> datetime:
    """Siguiente instante donde cambia la clasificación del tramo.

    Son fronteras tanto los cortes de jornada (06:00 y 20:00) como la
    medianoche: cruzar al día siguiente puede cambiar si el tramo cae en
    domingo o feriado, aunque su naturaleza diurna o nocturna no varíe.
    """
    base = momento.replace(minute=0, second=0, microsecond=0)
    candidatas = [
        base.replace(hour=h) + timedelta(days=dias)
        for dias in (0, 1)
        for h in (0, INICIO_DIURNO.hour, FIN_DIURNO.hour)
    ]
    return min(c for c in candidatas if c > momento)


def _segmentar(
    hora_entrada: datetime,
    hora_salida: datetime,
    es_descanso: Callable[[date], bool],
) -> List[_Tramo]:
    """Parte el turno en tramos homogéneos, en orden cronológico."""
    tramos: List[_Tramo] = []
    actual = hora_entrada
    while actual < hora_salida:
        fin = min(_proxima_frontera(actual), hora_salida)
        tramos.append(
            _Tramo(
                duracion=fin - actual,
                nocturno=_es_nocturno(actual),
                descanso=es_descanso(actual.date()),
            )
        )
        actual = fin
    return tramos


def _tope_ordinario(tramos: List[_Tramo]) -> Tuple[timedelta, str]:
    """Determina el tope de jornada ordinaria y el tipo de turno (Art. 194).

    El tope se decide una sola vez para todo el turno. Sumar el tope diurno
    al nocturno permitiría declarar 15 horas ordinarias en un mismo día.
    """
    laborables = [t for t in tramos if not t.descanso]
    if not laborables:
        return timedelta(0), JORNADA_DESCANSO_TIPO
    nocturno = sum((t.duracion for t in laborables if t.nocturno), timedelta(0))
    diurno = sum((t.duracion for t in laborables if not t.nocturno), timedelta(0))
    if not nocturno:
        return JORNADA_DIURNA, JORNADA_DIURNA_TIPO
    if not diurno or nocturno >= NOCTURNO_QUE_VUELVE_NOCTURNA:
        return JORNADA_NOCTURNA, JORNADA_NOCTURNA_TIPO
    return JORNADA_MIXTA, JORNADA_MIXTA_TIPO


def calcular_horas_paraguay(
    hora_entrada: datetime,
    hora_salida: datetime,
    es_descanso: Optional[Callable[[date], bool]] = None,
) -> DesgloseJornada:
    """Liquida un turno según la Ley N.º 213.

    Args:
        hora_entrada: Instante de ingreso.
        hora_salida: Instante de egreso; si es anterior a la entrada se
            interpreta como turno nocturno que cruza la medianoche.
        es_descanso: Predicado que indica si una fecha es domingo o feriado.
            Se consulta **por cada día que toca el turno**, de modo que un
            turno de sábado a domingo liquida al 100 % solo la porción del
            domingo. Por defecto usa el calendario oficial paraguayo.

    Returns:
        ``DesgloseJornada`` con las horas ordinarias, el subconjunto de
        ellas trabajado en horario nocturno (recargo del 30 %) y las
        extraordinarias al 50 % y al 100 %.

    Raises:
        ValueError: si los instantes mezclan zona horaria, o si el turno
            supera las 24 horas.
    """
    if (hora_entrada.tzinfo is None) != (hora_salida.tzinfo is None):
        raise ValueError(
            "Entrada y salida deben compartir zona horaria: una es naive y la otra aware."
        )
    if es_descanso is None:
        es_descanso = es_dia_de_descanso
    if hora_salida < hora_entrada:
        hora_salida += timedelta(days=1)
    if hora_salida - hora_entrada > timedelta(hours=24):
        raise ValueError("El turno no puede superar las 24 horas.")

    tramos = _segmentar(hora_entrada, hora_salida, es_descanso)
    restante, tipo = _tope_ordinario(tramos)

    ordinarias = nocturnas = extra_50 = extra_100 = timedelta(0)
    for tramo in tramos:
        if tramo.descanso:
            extra_100 += tramo.duracion
            continue
        # Las ordinarias son las primeras horas efectivamente trabajadas.
        comun = min(tramo.duracion, restante)
        if comun:
            ordinarias += comun
            if tramo.nocturno:
                nocturnas += comun
            restante -= comun
        exceso = tramo.duracion - comun
        if exceso:
            if tramo.nocturno:
                extra_100 += exceso
            else:
                extra_50 += exceso

    return DesgloseJornada(
        horas_ordinarias=ordinarias,
        horas_nocturnas=nocturnas,
        horas_extra_50=extra_50,
        horas_extra_100=extra_100,
        tipo_jornada=tipo,
        toca_descanso=any(t.descanso for t in tramos),
    )


def persistir_desglose(
    db: Database,
    marcaje_id: int,
    hora_salida: datetime,
    desglose: DesgloseJornada,
    incidencia: str = "",
) -> None:
    """Único punto de escritura de una liquidación en ``marcajes``.

    El cierre en línea, la sincronización offline y la corrección aprobada
    por RRHH convergen acá para que las tres rutas no puedan divergir en
    cómo guardan el mismo cálculo.
    """
    db.close_clock_out(
        marcaje_id,
        hora_salida,
        desglose.toca_descanso,
        desglose.horas_ordinarias,
        desglose.horas_extra_50,
        desglose.horas_extra_100,
        incidencia,
        desglose.horas_nocturnas,
        desglose.tipo_jornada,
    )


class ClockEngine:
    """Orquesta el flujo de marcación aplicando las reglas laborales."""

    def __init__(self, db: Database, user: Dict) -> None:
        self.db = db
        self.user = user

    def clock_in(self, verificacion_facial: str = "No verificada") -> Tuple[int, datetime]:
        """Registra la entrada aplicando la Res. 3028/2024.

        La evaluación distingue pasantes de funcionarios, consume la
        tolerancia ordinaria (10 o 15 minutos), suma la que Recursos Humanos
        haya declarado para el día y clasifica la incidencia como ``Llegada
        Tardía`` o ``Ausencia Injustificada``.

        Args:
            verificacion_facial: Resultado del control biométrico del kiosco,
                que se guarda con la marca para que una marca verificada no
                se confunda con una que el motor no pudo comprobar.

        Returns:
            Tupla con el identificador del marcaje y el instante exacto
            registrado (para el comprobante digital).
        """
        self._descartar_jornadas_abandonadas()
        open_entry = self.db.get_open_entry(self.user["id"])
        if open_entry:
            raise ValueError("Ya hay una entrada abierta sin salida registrada.")
        ahora = ahora_local()
        evaluacion = evaluar_asistencia(self.db, self.user["id"], ahora)
        estado = evaluacion["estado"]
        incidencia = (
            "Ausencia Injustificada"
            if estado == "Ausencia Injustificada"
            else ("Llegada Tardía" if estado == "Llegada Tardía" else "")
        )
        condicion = evaluacion["condicion_dia"]
        tolerancia_aplicada = (
            evaluacion["tolerancia_climatica"] or evaluacion["retraso_min"] > 0
        )
        entry_id = self.db.open_clock_in(
            self.user["id"],
            ahora,
            estado != "Normal",
            incidencia,
            tolerancia_aplicada,
            condicion,
            verificacion_facial=verificacion_facial,
        )
        if estado != "Normal":
            notifications.registrar_alerta(
                self.db,
                "marcacion_incidente",
                "media",
                f"{incidencia} de {self.user['full_name']}.",
                evaluacion["detalle"],
                usuario_id=self.user["id"],
            )
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
        desglose = calcular_horas_paraguay(open_entry["hora_entrada"], ahora)
        incidencia = self._clasificar_incidencia_salida(
            desglose,
            open_entry.get("tipo_incidencia") or "",
            duracion_comprometida(self.db, self.user["id"], open_entry["hora_entrada"]),
        )
        persistir_desglose(self.db, open_entry["id"], ahora, desglose, incidencia)
        return open_entry["id"], ahora

    @staticmethod
    def _clasificar_incidencia_salida(
        desglose: DesgloseJornada,
        incidencia_entrada: str,
        comprometida: Optional[timedelta] = None,
    ) -> str:
        """Combina la incidencia de entrada con la de salida anticipada.

        La referencia es lo que el empleado debía cubrir, acotado por el tope
        legal de su tipo de jornada: un turno nocturno que cierra a las 7
        horas cumplió su jornada completa, y un tramo pactado de 4 horas se
        cumple a las 4 aunque el tope diurno sea de 8.
        """
        tope = TOPES_POR_TIPO[desglose.tipo_jornada]
        if comprometida is not None:
            tope = min(tope, comprometida) if tope else comprometida
        if tope and desglose.total_trabajado < tope:
            return " y ".join(p for p in (incidencia_entrada, "Salida Anticipada") if p)
        return incidencia_entrada

    def _descartar_jornadas_abandonadas(self) -> List[Dict]:
        """Saca del camino las entradas que superaron el máximo de jornada abierta.

        Si nadie marcó la salida, la hora no se puede inventar; pero dejar la
        entrada abierta condenaba al empleado a que **todas** sus marcaciones
        futuras fallaran con "Ya hay una entrada abierta". Se marcan como
        abandonadas, se avisa a Recursos Humanos y el empleado vuelve a marcar
        normalmente: la hora real se repone por el circuito de correcciones.

        Returns:
            Los marcajes descartados; lista vacía si no había ninguno vencido.
        """
        limite = ahora_local() - MAX_JORNADA_ABIERTA
        vencidas = self.db.abandonar_jornadas_vencidas(
            self.user["id"], limite, INCIDENCIA_SIN_CIERRE
        )
        if not vencidas:
            return []
        dias = ", ".join(
            v["hora_entrada"].astimezone().strftime("%d/%m/%Y %H:%M") for v in vencidas
        )
        notifications.registrar_alerta(
            self.db,
            "jornada_sin_cierre",
            "media",
            f"{len(vencidas)} jornada(s) sin salida registrada de "
            f"{self.user['full_name']}.",
            f"Entradas sin cierre: {dias}. Corregir la salida desde el panel "
            f"de gestión.",
            usuario_id=self.user["id"],
        )
        return vencidas

    def detectar_accion_hoy(self) -> str:
        """Decide si corresponde ENTRADA o SALIDA según el estado del empleado.

        La decisión mira el **estado real** y no el calendario: un turno que
        entra el lunes a las 22:00 y sale el martes a las 06:00 no tiene
        marcajes con fecha de martes, así que decidir por fecha respondía
        ``ENTRADA`` y dejaba al empleado sin poder marcar ni la salida ni una
        entrada nueva.

        Cuántas veces se puede marcar en el día lo dice el turno: la jornada
        partida del comercio son dos entradas y dos salidas, no una.

        Returns:
            ``ENTRADA`` o ``SALIDA`` según el estado actual del empleado.

        Raises:
            ValueError: si el empleado ya completó todos los tramos de su turno.
        """
        self._descartar_jornadas_abandonadas()
        if self.db.get_open_entry(self.user["id"]) is not None:
            return "SALIDA"
        ahora = ahora_local()
        turno = turno_vigente(self.db, self.user["id"], ahora.date())
        cerrados = tramos_consumidos(self.db, self.user["id"], ahora)
        if cerrados >= len(turno.tramos):
            if len(turno.tramos) == 1:
                raise ValueError("Ya registró su entrada y su salida de hoy.")
            raise ValueError(
                f"Ya completó los {len(turno.tramos)} tramos de su turno "
                f"{turno.nombre} ({turno.etiqueta()})."
            )
        return "ENTRADA"

    def registrar_asistencia(
        self, verificacion_facial: str = "No verificada"
    ) -> Tuple[int, datetime, str]:
        """Botón maestro: registra entrada o salida según el estado del día.

        Una sola acción para el kiosco de recepción: consulta internamente
        PostgreSQL y decide si corresponde abrir la jornada (con las reglas
        vigentes según el vínculo) o cerrarla con el desglose legal
        de horas extraordinarias.

        Returns:
            Tupla con el identificador del marcaje, el instante registrado
            y el tipo ejecutado (``ENTRADA`` o ``SALIDA``).
        """
        accion = self.detectar_accion_hoy()
        if accion == "SALIDA":
            entry_id, momento = self.clock_out()
            return entry_id, momento, "SALIDA"
        entry_id, momento = self.clock_in(verificacion_facial)
        return entry_id, momento, "ENTRADA"

    def justificacion_para(self, fecha: date) -> Optional[Dict]:
        """Retorna la justificación aprobada que cubre la fecha, si existe."""
        return self.db.get_justificacion_por_fecha(self.user["id"], fecha)

    def turno_del_dia(self, fecha: date) -> turnos.Turno:
        """Turno que le corresponde al empleado en una fecha."""
        return turno_vigente(self.db, self.user["id"], fecha)

    def es_dia_laboral(self, fecha: date) -> bool:
        """Indica si la fecha es laborable para **este** empleado.

        Ni domingo ni feriado, y además un día que su turno cubra: quien
        descansa los lunes porque su turno va de martes a sábado no está
        ausente los lunes.
        """
        if es_feriado_o_domingo(datetime.combine(fecha, time(12, 0))):
            return False
        return self.turno_del_dia(fecha).trabaja(fecha)

    def horas_justificadas(self, fecha: date) -> timedelta:
        """Horas ordinarias legales que reconoce una justificación aprobada.

        Reconoce lo que ese día rendía el turno del empleado, no una jornada
        fija: justificarle ocho horas a quien tiene turno nocturno de siete
        sería pagarle una hora extra por faltar.
        """
        if not self.justificacion_para(fecha):
            return timedelta(0)
        if not self.es_dia_laboral(fecha):
            return timedelta(0)
        return horas_previstas_legales(self.turno_del_dia(fecha), fecha)

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