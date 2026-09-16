"""El vocabulario visual del escritorio: paleta, tipografía y piezas.

Es el sistema de diseño «Planilla» escrito en código —reglas finas, cifras
tabulares, sin sombras ni esquinas redondeadas— y vive aparte porque no sabe
nada del sistema: no conoce marcajes, ni turnos, ni empresas. Le dan un padre
y un texto, devuelve un widget.

El tema activo también vive acá, y se cambia asignándolo sobre el módulo
(``interfaz.TEMA_ACTIVO = "claro"``). Es estado de la interfaz, no de la
ventana: hay dos ventanas —el kiosco y el panel— y el color es el mismo.
"""

from __future__ import annotations

import calendar
import datetime
from contextlib import suppress
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Protocol

import customtkinter as ctk

SERIF = "Georgia"
FONT = "Segoe UI"
MONO = "Consolas"

# Esquinas casi rectas: la identidad la dan las reglas y la tipografía, no
# los bordes redondeados.
RADIO = 2

# Paleta compartida con el portal web (``src/static/estilos.css``): planilla
# de tinta sobre papel. El acento *es* la tinta —el botón principal es un
# bloque macizo, como el de un formulario impreso— y el único color de
# reserva es el del sello. ``ON_PRIMARY`` existe porque ese bloque se
# invierte entre temas.
TEMA_OSCURO: Dict[str, str] = {
    "BG": "#15140F",
    "CARD": "#1D1B15",
    "CARD_BORDER": "#333026",
    "INPUT_BG": "#100F0B",
    "INPUT_BORDER": "#6F6A5A",
    "PRIMARY": "#EDE9DC",
    "PRIMARY_HOVER": "#FFFFFF",
    "ON_PRIMARY": "#15140F",
    "TEXT": "#EDE9DC",
    "MUTED": "#8E8875",
    "SUCCESS": "#6FAE8B",
    "DANGER": "#D4735C",
    "ACCENTO": "#D9A441",
}

TEMA_CLARO: Dict[str, str] = {
    "BG": "#F4F1E9",
    "CARD": "#FBF9F4",
    "CARD_BORDER": "#DAD4C6",
    "INPUT_BG": "#EDE8DC",
    "INPUT_BORDER": "#8F8874",
    "PRIMARY": "#17150F",
    "PRIMARY_HOVER": "#37322A",
    "ON_PRIMARY": "#F4F1E9",
    "TEXT": "#17150F",
    "MUTED": "#6F6857",
    "SUCCESS": "#2F5D45",
    "DANGER": "#8C3A2B",
    "ACCENTO": "#8A5A16",
}

TEMAS: Dict[str, Dict[str, str]] = {"oscuro": TEMA_OSCURO, "claro": TEMA_CLARO}
TEMA_ACTIVO: str = "oscuro"


def t(clave: str) -> str:
    """Resuelve un token de color del tema vigente en tiempo de ejecución."""
    return TEMAS[TEMA_ACTIVO][clave]

DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def tarjeta(master: ctk.CTkFrame, **kwargs) -> ctk.CTkFrame:
    """Región delimitada por una regla fina, no una tarjeta flotante.

    El fondo apenas se despega del de la ventana: lo que separa una sección
    de otra es el filete del borde, como en una planilla impresa.
    """
    tarjeta_widget = ctk.CTkFrame(
        master,
        fg_color=t("CARD"),
        corner_radius=RADIO,
        border_width=1,
        border_color=t("CARD_BORDER"),
        **kwargs,
    )
    tarjeta_widget._rol = "tarjeta"
    return tarjeta_widget


def boton_primario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Acción principal: un bloque macizo de tinta sobre el papel."""
    boton = ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color=t("PRIMARY"),
        hover_color=t("PRIMARY_HOVER"),
        text_color=t("ON_PRIMARY"),
        font=(FONT, 14, "bold"),
        corner_radius=RADIO,
        height=44,
    )
    boton._rol = "primario"
    return boton


def boton_secundario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Botón de contorno para acciones secundarias.

    El realce al pasar el mouse es la superficie hundida y no el acento: con
    la tinta como color principal, rellenarlo dejaría texto oscuro sobre
    fondo oscuro.
    """
    boton = ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color="transparent",
        hover_color=t("INPUT_BG"),
        border_width=1,
        border_color=t("PRIMARY"),
        text_color=t("PRIMARY"),
        font=(FONT, 14),
        corner_radius=RADIO,
        height=44,
    )
    boton._rol = "secundario"
    return boton


def entrada(master, placeholder: str, ancho: int = 320) -> ctk.CTkEntry:
    """Campo de texto estilizado de la interfaz."""
    campo = ctk.CTkEntry(
        master,
        placeholder_text=placeholder,
        font=(FONT, 16),
        fg_color=t("INPUT_BG"),
        border_color=t("INPUT_BORDER"),
        text_color=t("TEXT"),
        corner_radius=RADIO,
        height=46,
        width=ancho,
    )
    campo._rol = "entrada"
    return campo


class CalendarioPopup(ctk.CTkToplevel):
    """Calendario emergente para elegir una fecha con navegación de mes.

    Al hacer clic en un día se invoca ``on_seleccionar(fecha)`` y la
    ventana se cierra; el botón inferior selecciona directamente el día
    de hoy.
    """

    DIAS_SEMANA = ("Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do")

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_seleccionar: Callable[[datetime.date], None],
        valor_inicial: Optional[datetime.date] = None,
    ) -> None:
        super().__init__(master)
        self.on_seleccionar = on_seleccionar
        self.mes_visible = (valor_inicial or datetime.date.today()).replace(day=1)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.title("Elegir fecha")
        # Posicionarlo junto al campo es una cortesía: si el gestor de
        # ventanas no la acepta, el calendario se abre donde quiera y sirve
        # igual.
        with suppress(Exception):
            self.geometry(
                f"+{master.winfo_rootx() + 80}+{master.winfo_rooty() + 100}"
            )
        cuerpo = tarjeta(self)
        cuerpo.pack(fill="both", expand=True, padx=10, pady=10)
        fila_nav = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_nav.pack(pady=(8, 4))
        boton_secundario(fila_nav, "◀", partial(self._cambiar_mes, -1)).pack(
            side="left", padx=4
        )
        self.lbl_mes = etiqueta(fila_nav, "", 15, t("TEXT"), "bold")
        self.lbl_mes.pack(side="left", padx=14)
        boton_secundario(fila_nav, "▶", partial(self._cambiar_mes, 1)).pack(
            side="left", padx=4
        )
        self.grilla = ctk.CTkFrame(cuerpo, fg_color="transparent")
        self.grilla.pack(padx=8)
        for indice, nombre in enumerate(self.DIAS_SEMANA):
            etiqueta(self.grilla, nombre, 11, t("MUTED"), "bold").grid(
                row=0, column=indice, padx=4, pady=(4, 2)
            )
        self.botones_dias: List[ctk.CTkButton] = []
        self._dibujar_mes()
        hoy = datetime.date.today()
        boton_hoy = ctk.CTkButton(
            cuerpo,
            text=f"Hoy · {hoy.strftime('%d/%m/%Y')}",
            command=lambda: self._elegir(hoy),
            fg_color=t("PRIMARY"),
            hover_color=t("PRIMARY_HOVER"),
            text_color=t("ON_PRIMARY"),
            font=(FONT, 13, "bold"),
            corner_radius=RADIO,
            height=38,
        )
        boton_hoy.pack(pady=(8, 12))

    def _cambiar_mes(self, delta: int) -> None:
        """Navega un mes hacia adelante o atrás conservando el día 1."""
        mes = self.mes_visible.month + delta
        anio = self.mes_visible.year + (mes - 1) // 12
        mes = (mes - 1) % 12 + 1
        self.mes_visible = self.mes_visible.replace(year=anio, month=mes)
        self._dibujar_mes()

    def _dibujar_mes(self) -> None:
        """Reconstruye la grilla de días del mes visible."""
        mes = MESES[self.mes_visible.month - 1].capitalize()
        self.lbl_mes.configure(text=f"{mes} {self.mes_visible.year}")
        for boton in self.botones_dias:
            boton.destroy()
        self.botones_dias.clear()
        primer_dia = self.mes_visible.weekday()
        total_dias = calendar.monthrange(
            self.mes_visible.year, self.mes_visible.month
        )[1]
        hoy = datetime.date.today()
        for dia in range(1, total_dias + 1):
            fecha = self.mes_visible.replace(day=dia)
            es_hoy = fecha == hoy
            boton = ctk.CTkButton(
                self.grilla,
                text=str(dia),
                width=40,
                height=30,
                font=(MONO, 12, "bold" if es_hoy else "normal"),
                fg_color=t("PRIMARY") if es_hoy else t("INPUT_BG"),
                hover_color=t("PRIMARY_HOVER") if es_hoy else t("INPUT_BORDER"),
                text_color=t("ON_PRIMARY") if es_hoy else t("TEXT"),
                corner_radius=RADIO,
                command=partial(self._elegir, fecha),
            )
            boton.grid(
                row=(primer_dia + dia - 1) // 7 + 1,
                column=(primer_dia + dia - 1) % 7,
                padx=2,
                pady=2,
            )
            self.botones_dias.append(boton)

    def _elegir(self, fecha: datetime.date) -> None:
        """Entrega la fecha elegida al formulario y cierra el calendario."""
        self.on_seleccionar(fecha)
        self.destroy()


def campo_fecha(
    master: ctk.CTkFrame, placeholder: str, ancho: int = 170
) -> ctk.CTkFrame:
    """Entrada de fecha con calendario emergente y botón para el día de hoy.

    Devuelve un contenedor con la entrada accesible como ``.entrada``;
    también expone ``.boton_hoy`` por si el consumidor quiere reestilizarlo.
    """
    fila = ctk.CTkFrame(master, fg_color="transparent")

    def aplicar(fecha: datetime.date) -> None:
        entrada_fecha.delete(0, "end")
        entrada_fecha.insert(0, fecha.isoformat())

    def abrir_calendario() -> None:
        try:
            actual = datetime.date.fromisoformat(entrada_fecha.get().strip())
        except ValueError:
            actual = datetime.date.today()
        CalendarioPopup(entrada_fecha.winfo_toplevel(), aplicar, actual)

    entrada_fecha = ctk.CTkEntry(
        fila,
        placeholder_text=placeholder,
        font=(FONT, 14),
        fg_color=t("INPUT_BG"),
        border_color=t("INPUT_BORDER"),
        text_color=t("TEXT"),
        corner_radius=RADIO,
        height=40,
        width=ancho,
    )
    entrada_fecha._rol = "entrada"
    entrada_fecha.pack(side="left", padx=(0, 6))
    boton_cal = ctk.CTkButton(
        fila,
        text="Elegir",
        width=62,
        height=40,
        font=(FONT, 13),
        fg_color=t("PRIMARY"),
        hover_color=t("PRIMARY_HOVER"),
        text_color=t("ON_PRIMARY"),
        corner_radius=RADIO,
        command=abrir_calendario,
    )
    boton_cal.pack(side="left", padx=(0, 6))
    boton_hoy = ctk.CTkButton(
        fila,
        text="Hoy",
        width=52,
        height=40,
        font=(FONT, 12),
        fg_color="transparent",
        hover_color=t("INPUT_BG"),
        border_width=1,
        border_color=t("PRIMARY"),
        text_color=t("PRIMARY"),
        corner_radius=RADIO,
        command=lambda: aplicar(datetime.date.today()),
    )
    boton_hoy.pack(side="left")
    fila.entrada = entrada_fecha
    fila.boton_hoy = boton_hoy
    return fila


def etiqueta(master, texto: str, tamano: int = 14, color: str = t("TEXT"),
             peso: str = "normal") -> ctk.CTkLabel:
    """Texto corriente de la interfaz, en la grotesca del sistema."""
    return ctk.CTkLabel(
        master, text=texto, font=(FONT, tamano, peso), text_color=color
    )


def titulo(master, texto: str, tamano: int = 17) -> ctk.CTkLabel:
    """Encabezado de sección, en serif: separa el rótulo del contenido."""
    return ctk.CTkLabel(
        master, text=texto, font=(SERIF, tamano, "bold"), text_color=t("TEXT")
    )


def cifra(master, texto: str, tamano: int = 26, color: str = None) -> ctk.CTkLabel:
    """Número destacado, siempre monoespaciado para que las columnas alineen."""
    return ctk.CTkLabel(
        master, text=texto, font=(MONO, tamano), text_color=color or t("TEXT")
    )


def _normalizar_color(valor: Any) -> str:
    """CustomTkinter devuelve a veces tuplas de color; usa siempre el primero."""
    return valor[0] if isinstance(valor, tuple) else valor


def _recolorear(widget, anterior: Dict[str, str], nuevo: Dict[str, str]) -> None:
    """Reaplica los tokens del nuevo tema sobre la jerarquía de widgets.

    Los widgets creados por los helpers llevan una etiqueta ``_rol`` con su
    función visual; el resto se clasifica comparando sus colores actuales
    contra el tema anterior para conservar su intención de diseño.
    """
    rol = getattr(widget, "_rol", None)
    if rol == "tarjeta":
        widget.configure(
            fg_color=nuevo["CARD"], border_color=nuevo["CARD_BORDER"]
        )
    elif rol == "entrada":
        widget.configure(
            fg_color=nuevo["INPUT_BG"],
            border_color=nuevo["INPUT_BORDER"],
            text_color=nuevo["TEXT"],
            placeholder_text_color=nuevo["MUTED"],
        )
    elif rol == "primario":
        widget.configure(
            fg_color=nuevo["PRIMARY"],
            hover_color=nuevo["PRIMARY_HOVER"],
            text_color=nuevo["ON_PRIMARY"],
        )
    elif rol == "secundario":
        widget.configure(
            fg_color="transparent",
            hover_color=nuevo["INPUT_BG"],
            border_color=nuevo["PRIMARY"],
            text_color=nuevo["PRIMARY"],
        )
    elif rol == "plano":
        color = _normalizar_color(widget.cget("fg_color"))
        if color == anterior.get("PRIMARY"):
            widget.configure(
                fg_color=nuevo["PRIMARY"],
                hover_color=nuevo["PRIMARY_HOVER"],
                text_color=nuevo["ON_PRIMARY"],
            )
        else:
            widget.configure(
                fg_color="transparent",
                hover_color=nuevo["INPUT_BG"],
                text_color=nuevo["MUTED"],
            )
    elif isinstance(widget, ctk.CTkLabel):
        color = _normalizar_color(widget.cget("text_color"))
        for clave in ("MUTED", "TEXT", "SUCCESS", "DANGER", "ACCENTO", "PRIMARY"):
            if color == anterior.get(clave):
                widget.configure(text_color=nuevo[clave])
                break
    elif isinstance(widget, ctk.CTkFrame):
        color = _normalizar_color(widget.cget("fg_color"))
        if color == "transparent":
            pass
        elif color == anterior.get("INPUT_BG"):
            widget.configure(fg_color=nuevo["INPUT_BG"])
        elif color == anterior.get("BG"):
            widget.configure(fg_color=nuevo["BG"])
        else:
            widget.configure(fg_color=nuevo["CARD"])
    elif isinstance(widget, ctk.CTkEntry):
        widget.configure(
            fg_color=nuevo["INPUT_BG"],
            border_color=nuevo["INPUT_BORDER"],
            text_color=nuevo["TEXT"],
            placeholder_text_color=nuevo["MUTED"],
        )
    elif isinstance(widget, ctk.CTkButton):
        color = _normalizar_color(widget.cget("fg_color"))
        borde = _normalizar_color(widget.cget("border_color"))
        if color == anterior.get("PRIMARY"):
            widget.configure(
                fg_color=nuevo["PRIMARY"],
                hover_color=nuevo["PRIMARY_HOVER"],
                text_color=nuevo["ON_PRIMARY"],
            )
        elif color == anterior.get("SUCCESS"):
            widget.configure(
                fg_color=nuevo["SUCCESS"], hover_color=nuevo["SUCCESS"]
            )
        elif color == anterior.get("INPUT_BG"):
            widget.configure(
                fg_color=nuevo["INPUT_BG"],
                hover_color=nuevo["INPUT_BORDER"],
                text_color=nuevo["TEXT"],
            )
        elif color == "transparent":
            if (
                borde == anterior.get("DANGER")
                or _normalizar_color(widget.cget("text_color")) == anterior.get("DANGER")
            ):
                widget.configure(
                    hover_color=nuevo["DANGER"],
                    border_color=nuevo["DANGER"],
                    text_color=nuevo["DANGER"],
                )
            elif borde == anterior.get("PRIMARY"):
                widget.configure(
                    hover_color=nuevo["INPUT_BG"],
                    border_color=nuevo["PRIMARY"],
                    text_color=nuevo["PRIMARY"],
                )
            else:
                widget.configure(text_color=nuevo["MUTED"])
    elif isinstance(widget, ctk.CTkOptionMenu):
        widget.configure(
            fg_color=nuevo["INPUT_BG"],
            button_color=nuevo["PRIMARY"],
            button_hover_color=nuevo["PRIMARY_HOVER"],
            text_color=nuevo["TEXT"],
            dropdown_fg_color=nuevo["INPUT_BG"],
            dropdown_hover_color=nuevo["PRIMARY"],
        )
    elif isinstance(widget, ctk.CTkSwitch):
        widget.configure(
            fg_color=nuevo["INPUT_BG"],
            progress_color=nuevo["PRIMARY"],
            text_color=nuevo["MUTED"],
        )
    elif isinstance(widget, ctk.CTkTextbox):
        color = _normalizar_color(widget.cget("text_color"))
        widget.configure(fg_color=nuevo["INPUT_BG"])
        if color == anterior.get("MUTED"):
            widget.configure(text_color=nuevo["MUTED"])
        else:
            widget.configure(text_color=nuevo["TEXT"])
    elif isinstance(widget, ctk.CTkScrollableFrame):
        color = _normalizar_color(widget.cget("fg_color"))
        if color != "transparent":
            widget.configure(fg_color=nuevo["CARD"])
    for hijo in widget.winfo_children():
        _recolorear(hijo, anterior, nuevo)


class VentanaConTema(Protocol):
    """Lo único que el interruptor le pide a la ventana que lo aloja.

    Se declara la forma y no la clase para que el vocabulario visual no
    dependa de la ventana: un módulo de widgets que necesita importar la
    aplicación para dibujar un switch deja de ser reutilizable.
    """

    variable_tema: Any

    def cambiar_tema(self) -> None:
        ...


def interruptor_tema(master, app: VentanaConTema) -> ctk.CTkSwitch:
    """Switch superior que alterna el modo claro y el modo oscuro al instante."""
    interruptor = ctk.CTkSwitch(
        master,
        text="Modo Oscuro" if TEMA_ACTIVO == "oscuro" else "Modo Claro",
        variable=app.variable_tema,
        command=app.cambiar_tema,
        font=(FONT, 12),
        text_color=t("MUTED"),
        progress_color=t("PRIMARY"),
        fg_color=t("INPUT_BG"),
    )
    interruptor._rol = "switch_tema"
    return interruptor


def _buscar_switches_tema(raiz) -> List[ctk.CTkSwitch]:
    """Recolecta los interruptores de tema para sincronizar su etiqueta."""
    resultado: List[ctk.CTkSwitch] = []
    if getattr(raiz, "_rol", None) == "switch_tema":
        resultado.append(raiz)
    for hijo in raiz.winfo_children():
        resultado.extend(_buscar_switches_tema(hijo))
    return resultado
