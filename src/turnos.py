"""Turnos de trabajo: definición, vigencia y resolución por empleado.

Hasta acá la hora de entrada era una constante del proceso: una sola para
toda la empresa, congelada al importar el módulo. Con eso no se podía
modelar ni el caso más común del rubro —dos turnos que se relevan— ni la
jornada partida del comercio, ni horarios distintos por sucursal.

Un turno es una lista ordenada de **tramos** (la jornada partida tiene dos),
una máscara de días de la semana y, opcionalmente, una tolerancia propia que
desplaza la del vínculo. Quién trabaja qué turno se resuelve por prioridad:

1. una **asignación vigente** para esa fecha (así se rota sin tocar el legajo),
2. el **turno base** del legajo,
3. el turno marcado como predeterminado de la empresa.

La tercera regla es la que mantiene en pie una instalación que todavía no
definió turnos: el esquema siembra uno con la hora de ``JORNADA_INICIO``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

DIAS_ABREVIADOS: Tuple[str, ...] = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
LARGO_MASCARA: int = 7
MASCARA_LUNES_VIERNES: str = "1111100"
MASCARA_LUNES_SABADO: str = "1111110"
MASCARA_TODOS: str = "1111111"

SUCURSAL_PREDETERMINADA: str = "Casa Central"

MAX_TRAMOS: int = 3
"""Tope defensivo: más de tres entradas y salidas por día no es un turno."""

SEPARADOR_HORARIO: str = "–"

NOMBRE_TURNO_SEMBRADO: str = "Jornada administrativa"
JORNADA_SEMBRADA: timedelta = timedelta(hours=8)
HORA_INICIO_PREDETERMINADA: time = time(8, 0)


def hora_inicio_configurada() -> time:
    """Hora de entrada declarada en ``JORNADA_INICIO``, o las 08:00.

    Se lee en cada llamada y no al importar el módulo: cuando era una
    constante de importación quedaba fijada antes de que ``load_dotenv()``
    llegara a correr, y el valor del ``.env`` se ignoraba en silencio.
    """
    crudo = (os.getenv("JORNADA_INICIO") or "").strip()
    if not crudo:
        return HORA_INICIO_PREDETERMINADA
    try:
        horas, minutos = crudo.split(":")[:2]
        return time(int(horas), int(minutos))
    except (ValueError, IndexError):
        return HORA_INICIO_PREDETERMINADA


class TurnoInvalido(ValueError):
    """La definición del turno no describe una jornada realizable."""


def normalizar_mascara(dias: Any) -> str:
    """Devuelve una máscara de 7 caracteres validada, indexada desde el lunes.

    Acepta la máscara textual (``"1111100"``) o una secuencia de índices de
    ``date.weekday()``, que es lo que llega desde un formulario con casillas.
    """
    if isinstance(dias, str):
        limpio = dias.strip()
        if len(limpio) != LARGO_MASCARA or any(c not in "01" for c in limpio):
            raise TurnoInvalido(
                "Los días del turno se expresan con 7 caracteres 0/1, "
                "empezando por el lunes."
            )
        mascara = limpio
    else:
        indices = {int(d) for d in dias}
        if any(i < 0 or i > 6 for i in indices):
            raise TurnoInvalido("Los días de la semana van de 0 (lunes) a 6 (domingo).")
        mascara = "".join("1" if i in indices else "0" for i in range(LARGO_MASCARA))
    if "1" not in mascara:
        raise TurnoInvalido("Un turno sin ningún día laborable no se puede asignar.")
    return mascara


def describir_dias(mascara: str) -> str:
    """Resume la máscara en la forma en que se lee en una planilla."""
    activos = [i for i, c in enumerate(mascara) if c == "1"]
    if not activos:
        return "—"
    if mascara == MASCARA_LUNES_VIERNES:
        return "Lun a Vie"
    if mascara == MASCARA_LUNES_SABADO:
        return "Lun a Sáb"
    if mascara == MASCARA_TODOS:
        return "Todos los días"
    corrido = activos == list(range(activos[0], activos[-1] + 1))
    if corrido and len(activos) > 2:
        return f"{DIAS_ABREVIADOS[activos[0]]} a {DIAS_ABREVIADOS[activos[-1]]}"
    return ", ".join(DIAS_ABREVIADOS[i] for i in activos)


def _a_hora(valor: Any) -> time:
    """Convierte a ``time`` lo que llega de la base, de un formulario o del código."""
    if isinstance(valor, time):
        return valor.replace(second=0, microsecond=0)
    if isinstance(valor, datetime):
        return valor.time().replace(second=0, microsecond=0)
    texto = str(valor).strip()
    partes = texto.split(":")
    if len(partes) < 2:
        raise TurnoInvalido(f"Hora inválida: '{texto}'. Se espera HH:MM.")
    try:
        return time(int(partes[0]), int(partes[1]))
    except ValueError as exc:
        raise TurnoInvalido(f"Hora inválida: '{texto}'. Se espera HH:MM.") from exc


@dataclass(frozen=True)
class Tramo:
    """Una franja continua del turno, con su hora de entrada y de salida."""

    orden: int
    entrada: time
    salida: time

    @property
    def cruza_medianoche(self) -> bool:
        return self.salida < self.entrada

    @property
    def duracion(self) -> timedelta:
        base = datetime.combine(date.min, self.entrada)
        fin = datetime.combine(date.min, self.salida)
        if self.cruza_medianoche:
            fin += timedelta(days=1)
        return fin - base

    def entrada_del(self, dia: date) -> datetime:
        """Instante naive en que este tramo debería empezar ese día."""
        return datetime.combine(dia, self.entrada)

    def etiqueta(self) -> str:
        return (
            f"{self.entrada.strftime('%H:%M')}"
            f"{SEPARADOR_HORARIO}"
            f"{self.salida.strftime('%H:%M')}"
        )


@dataclass(frozen=True)
class Turno:
    """Horario de trabajo aplicable a un empleado en una fecha dada."""

    id: Optional[int]
    nombre: str
    tramos: Tuple[Tramo, ...]
    dias: str = MASCARA_LUNES_VIERNES
    sucursal: str = SUCURSAL_PREDETERMINADA
    tolerancia_min: Optional[int] = None
    activo: bool = True
    predeterminado: bool = False
    origen: str = "predeterminado"
    """Cómo llegó el turno al empleado: asignación, legajo o predeterminado."""

    def trabaja(self, dia: date) -> bool:
        """Indica si el turno cubre ese día de la semana."""
        return self.dias[dia.weekday()] == "1"

    @property
    def duracion_prevista(self) -> timedelta:
        """Horas que el turno espera del empleado en un día completo."""
        return sum((t.duracion for t in self.tramos), timedelta(0))

    @property
    def entrada_nominal(self) -> time:
        """Hora en que abre el turno; la que se muestra en las planillas."""
        return self.tramos[0].entrada

    @property
    def nocturno(self) -> bool:
        return any(t.cruza_medianoche for t in self.tramos)

    def tramo_para(self, momento: datetime, consumidos: int = 0) -> Tramo:
        """Elige contra qué tramo se mide una marca.

        ``consumidos`` es la cantidad de tramos que el empleado ya cerró en
        la jornada, así que el que corresponde es el primero que le queda
        pendiente. Elegir el más cercano en cambio premiaría faltar: quien
        se presenta a las 11:00 en un turno 07:00-11:00 / 14:00-18:00 sin
        haber marcado a la mañana llega **cuatro horas tarde** al primer
        tramo, no tres horas temprano al segundo.
        """
        indice = min(max(consumidos, 0), len(self.tramos) - 1)
        return self.tramos[indice]

    def entrada_prevista(self, momento: datetime, consumidos: int = 0) -> datetime:
        """Instante en que el empleado debía presentarse para esta marca.

        El tramo fija la hora; el día lo fija la cercanía, que es lo que
        resuelve el turno nocturno: marcar a las 00:05 contra un tramo que
        abre a las 22:00 es llegar tarde al de ayer, no temprano al de hoy.
        """
        tramo = self.tramo_para(momento, consumidos)
        referencia = momento.replace(tzinfo=None) if momento.tzinfo else momento
        opciones = [
            tramo.entrada_del(referencia.date()) + timedelta(days=d)
            for d in (-1, 0, 1)
        ]
        return min(opciones, key=lambda previsto: abs(referencia - previsto))

    def etiqueta(self) -> str:
        """Horario legible: un rango, o los dos tramos de la jornada partida."""
        return " · ".join(t.etiqueta() for t in self.tramos)

    def resumen(self) -> str:
        return f"{self.nombre} · {self.etiqueta()} · {describir_dias(self.dias)}"

    def como_dict(self) -> Dict[str, Any]:
        """Proyección para las APIs y para el portal."""
        return {
            "id": self.id,
            "nombre": self.nombre,
            "sucursal": self.sucursal,
            "dias": self.dias,
            "dias_texto": describir_dias(self.dias),
            "tolerancia_min": self.tolerancia_min,
            "activo": self.activo,
            "predeterminado": self.predeterminado,
            "origen": self.origen,
            "partida": len(self.tramos) > 1,
            "nocturno": self.nocturno,
            "horario": self.etiqueta(),
            "horas_previstas": round(self.duracion_prevista.total_seconds() / 3600, 2),
            "tramos": [
                {
                    "orden": t.orden,
                    "entrada": t.entrada.strftime("%H:%M"),
                    "salida": t.salida.strftime("%H:%M"),
                }
                for t in self.tramos
            ],
        }


def construir_tramos(definiciones: Sequence[Any]) -> Tuple[Tramo, ...]:
    """Valida y ordena los tramos de un turno.

    Acepta filas de la base, diccionarios de un formulario o pares
    ``(entrada, salida)``. Rechaza lo que no describe una jornada: tramos de
    duración nula, superpuestos o desordenados dentro del día.
    """
    if not definiciones:
        raise TurnoInvalido("Un turno necesita al menos un tramo horario.")
    if len(definiciones) > MAX_TRAMOS:
        raise TurnoInvalido(
            f"Un turno admite hasta {MAX_TRAMOS} tramos; se recibieron "
            f"{len(definiciones)}."
        )
    crudos: List[Tuple[time, time]] = []
    for definicion in definiciones:
        if isinstance(definicion, Tramo):
            crudos.append((definicion.entrada, definicion.salida))
        elif isinstance(definicion, dict):
            crudos.append(
                (_a_hora(definicion["entrada"]), _a_hora(definicion["salida"]))
            )
        else:
            entrada, salida = definicion
            crudos.append((_a_hora(entrada), _a_hora(salida)))

    tramos: List[Tramo] = []
    for orden, (entrada, salida) in enumerate(crudos, start=1):
        tramo = Tramo(orden=orden, entrada=entrada, salida=salida)
        if not tramo.duracion:
            raise TurnoInvalido(
                f"El tramo {tramo.etiqueta()} empieza y termina a la misma hora."
            )
        if tramo.duracion > timedelta(hours=16):
            raise TurnoInvalido(
                f"El tramo {tramo.etiqueta()} dura más de 16 horas: revisar las horas."
            )
        tramos.append(tramo)

    # Solo el último tramo puede cruzar la medianoche: si cruzara uno del
    # medio, el siguiente empezaría antes de que termine el anterior.
    for tramo in tramos[:-1]:
        if tramo.cruza_medianoche:
            raise TurnoInvalido(
                f"El tramo {tramo.etiqueta()} cruza la medianoche y no es el "
                f"último del turno."
            )
    for previo, siguiente in zip(tramos, tramos[1:]):
        if siguiente.entrada < previo.salida:
            raise TurnoInvalido(
                f"El tramo {siguiente.etiqueta()} empieza antes de que termine "
                f"{previo.etiqueta()}."
            )
    return tuple(tramos)


def desde_fila(fila: Dict[str, Any], origen: Optional[str] = None) -> Turno:
    """Arma un ``Turno`` con la fila y los tramos que devuelve la base."""
    return Turno(
        id=fila["id"],
        nombre=fila["nombre"],
        tramos=construir_tramos(
            [(t["hora_entrada"], t["hora_salida"]) for t in fila["tramos"]]
        ),
        dias=(fila["dias"] or MASCARA_LUNES_VIERNES).strip(),
        sucursal=fila.get("sucursal") or SUCURSAL_PREDETERMINADA,
        tolerancia_min=fila.get("tolerancia_min"),
        activo=bool(fila.get("activo", True)),
        predeterminado=bool(fila.get("predeterminado", False)),
        origen=origen or fila.get("origen") or "predeterminado",
    )


def de_respaldo() -> Turno:
    """Turno que se usa cuando la base todavía no define ninguno.

    Existe para que una instalación a medio migrar no deje a nadie sin poder
    marcar: reproduce la jornada administrativa de 8 horas que el sistema
    aplicaba cuando la hora de entrada era una constante global.
    """
    inicio = hora_inicio_configurada()
    fin = (datetime.combine(date.min, inicio) + JORNADA_SEMBRADA).time()
    return Turno(
        id=None,
        nombre=NOMBRE_TURNO_SEMBRADO,
        tramos=construir_tramos([(inicio, fin)]),
        dias=MASCARA_LUNES_VIERNES,
        predeterminado=True,
        origen="respaldo",
    )


class FuenteDeTurnos(Protocol):
    """Lo único que la resolución le pide a la capa de persistencia."""

    def resolver_turno(self, usuario_id: int, dia: date) -> Optional[Dict[str, Any]]:
        ...


def resolver(fuente: FuenteDeTurnos, usuario_id: int, dia: date) -> Turno:
    """Turno vigente para un empleado en una fecha.

    Nunca devuelve ``None``: si la cadena de prioridades se agota —base sin
    turnos, o un legajo que apunta a uno borrado— cae al turno de respaldo.
    Un empleado sin horario resoluble no podría marcar, y quedarse sin marcar
    por un hueco de configuración es peor que medirlo contra la jornada
    administrativa.
    """
    fila = fuente.resolver_turno(usuario_id, dia)
    if not fila or not fila.get("tramos"):
        return de_respaldo()
    return desde_fila(fila)
