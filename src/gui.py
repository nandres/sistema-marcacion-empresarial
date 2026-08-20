"""Interfaz premium en CustomTkinter del Sistema de Marcación (Ley 213/93).

Dos modos de uso:
- Modo Recepción (pantalla pública por defecto): reloj digital en tiempo
  real, marcación por cédula/usuario y ticket criptográfico de reports.py.
- Modo Gestión (RRHH/Administrador): acceso mediante modal de credenciales
  autenticado con auth.py y panel protegido con navegación lateral
  minimalista (personal, justificaciones, reportes, correcciones y
  analítica visual).
"""

from __future__ import annotations

import datetime
import calendar
from functools import partial
from typing import Callable, Dict, List, Optional

import customtkinter as ctk
import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import auth
import reglamento
import reports
from clock_engine import ClockEngine
from database import Database

FONT = "Segoe UI"
MONO = "Consolas"

TEMA_OSCURO: Dict[str, str] = {
    "BG": "#0B0B0C",
    "CARD": "#1E1E24",
    "CARD_BORDER": "#2A2A32",
    "INPUT_BG": "#191920",
    "INPUT_BORDER": "#26262C",
    "PRIMARY": "#1A56DB",
    "PRIMARY_HOVER": "#2E66E8",
    "TEXT": "#F2F2EE",
    "MUTED": "#8E8E96",
    "SUCCESS": "#4ADE80",
    "DANGER": "#F0544F",
    "ACCENTO": "#F5C26B",
}

TEMA_CLARO: Dict[str, str] = {
    "BG": "#F8F9FA",
    "CARD": "#FFFFFF",
    "CARD_BORDER": "#E4E7EB",
    "INPUT_BG": "#FFFFFF",
    "INPUT_BORDER": "#D8DCE1",
    "PRIMARY": "#1A56DB",
    "PRIMARY_HOVER": "#2E66E8",
    "TEXT": "#1A1A1E",
    "MUTED": "#6B7280",
    "SUCCESS": "#16A34A",
    "DANGER": "#DC2626",
    "ACCENTO": "#B45309",
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
    """Crea una tarjeta flotante con la identidad visual del sistema."""
    tarjeta_widget = ctk.CTkFrame(
        master,
        fg_color=t("CARD"),
        corner_radius=16,
        border_width=1,
        border_color=t("CARD_BORDER"),
        **kwargs,
    )
    tarjeta_widget._rol = "tarjeta"
    return tarjeta_widget


def boton_primario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Botón de acción principal en azul eléctrico con transición al hover."""
    boton = ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color=t("PRIMARY"),
        hover_color=t("PRIMARY_HOVER"),
        text_color="white",
        font=(FONT, 15, "bold"),
        corner_radius=8,
        height=44,
    )
    boton._rol = "primario"
    return boton


def boton_secundario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Botón de contorno en azul para acciones secundarias."""
    boton = ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color="transparent",
        hover_color=t("PRIMARY_HOVER"),
        border_width=2,
        border_color=t("PRIMARY"),
        text_color=t("PRIMARY"),
        font=(FONT, 15, "bold"),
        corner_radius=8,
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
        corner_radius=10,
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
        try:
            self.geometry(
                f"+{master.winfo_rootx() + 80}+{master.winfo_rooty() + 100}"
            )
        except Exception:
            pass
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
            text_color="white",
            font=(FONT, 13, "bold"),
            corner_radius=8,
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
        self.lbl_mes.configure(text=f"{MESES[self.mes_visible.month - 1].capitalize()} {self.mes_visible.year}")
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
                font=(FONT, 12, "bold" if es_hoy else "normal"),
                fg_color=t("PRIMARY") if es_hoy else t("INPUT_BG"),
                hover_color=t("PRIMARY_HOVER"),
                text_color="white" if es_hoy else t("TEXT"),
                corner_radius=8,
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
        corner_radius=10,
        height=40,
        width=ancho,
    )
    entrada_fecha._rol = "entrada"
    entrada_fecha.pack(side="left", padx=(0, 6))
    boton_cal = ctk.CTkButton(
        fila,
        text="📅",
        width=42,
        height=40,
        font=(FONT, 13),
        fg_color=t("PRIMARY"),
        hover_color=t("PRIMARY_HOVER"),
        corner_radius=8,
        command=abrir_calendario,
    )
    boton_cal.pack(side="left", padx=(0, 6))
    boton_hoy = ctk.CTkButton(
        fila,
        text="Hoy",
        width=52,
        height=40,
        font=(FONT, 12, "bold"),
        fg_color="transparent",
        hover_color=t("PRIMARY_HOVER"),
        border_width=2,
        border_color=t("PRIMARY"),
        text_color=t("PRIMARY"),
        corner_radius=8,
        command=lambda: aplicar(datetime.date.today()),
    )
    boton_hoy.pack(side="left")
    fila.entrada = entrada_fecha
    fila.boton_hoy = boton_hoy
    return fila


def etiqueta(master, texto: str, tamano: int = 14, color: str = t("TEXT"), peso: str = "normal") -> ctk.CTkLabel:
    """Etiqueta tipográfica limpia del sistema."""
    return ctk.CTkLabel(
        master, text=texto, font=(FONT, tamano, peso), text_color=color
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
            text_color="#FFFFFF",
        )
    elif rol == "secundario":
        widget.configure(
            fg_color="transparent",
            hover_color=nuevo["PRIMARY_HOVER"],
            border_color=nuevo["PRIMARY"],
            text_color=nuevo["PRIMARY"],
        )
    elif rol == "plano":
        color = _normalizar_color(widget.cget("fg_color"))
        if color == anterior.get("PRIMARY"):
            widget.configure(
                fg_color=nuevo["PRIMARY"],
                hover_color=nuevo["PRIMARY_HOVER"],
                text_color="#FFFFFF",
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
                text_color="#FFFFFF",
            )
        elif color == anterior.get("SUCCESS"):
            widget.configure(
                fg_color=nuevo["SUCCESS"], hover_color=nuevo["SUCCESS"]
            )
        elif color == anterior.get("INPUT_BG"):
            widget.configure(
                fg_color=nuevo["INPUT_BG"],
                hover_color=nuevo["PRIMARY_HOVER"],
                text_color=nuevo["MUTED"],
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
                    hover_color=nuevo["PRIMARY_HOVER"],
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


def interruptor_tema(master, app: "MarcacionApp") -> ctk.CTkSwitch:
    """Switch superior que alterna el modo claro y el modo oscuro al instante."""
    interruptor = ctk.CTkSwitch(
        master,
        text="Modo Oscuro" if TEMA_ACTIVO == "oscuro" else "Modo Claro",
        variable=app.variable_tema,
        command=app._cambiar_tema,
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


class MarcacionApp(ctk.CTk):
    """Ventana principal que alterna entre recepción y gestión."""

    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.db.initialize()
        self.actor: Optional[Dict] = None
        self.panel_gestion: Optional[ctk.CTkFrame] = None
        self.variable_tema = ctk.BooleanVar(value=False)
        self._refrescos_tema: List[Callable] = []
        self._configurar_ventana()
        self._construir_vista_publica()
        self._mostrar_dos_puntos = True
        self._actualizar_reloj()
        self.after(500, self._alternar_dos_puntos)

    def _configurar_ventana(self) -> None:
        self.title("Sistema de Marcación · Paraguay")
        self.geometry("1180x780")
        self.minsize(1024, 700)
        self.configure(fg_color=t("BG"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

    def registrar_refresco_tema(self, refresco: Callable) -> None:
        """Suscribe un panel para que redibuje sus gráficos al cambiar de tema."""
        if refresco not in self._refrescos_tema:
            self._refrescos_tema.append(refresco)

    def quitar_refresco_tema(self, refresco: Callable) -> None:
        """Retira la suscripción de un panel destruido."""
        if refresco in self._refrescos_tema:
            self._refrescos_tema.remove(refresco)

    def _cambiar_tema(self) -> None:
        """Aplica el tema elegido a toda la jerarquía visual al instante."""
        global TEMA_ACTIVO
        TEMA_ACTIVO = "claro" if self.variable_tema.get() else "oscuro"
        ctk.set_appearance_mode("light" if TEMA_ACTIVO == "claro" else "dark")
        anterior = TEMAS["claro" if TEMA_ACTIVO == "oscuro" else "oscuro"]
        self.configure(fg_color=t("BG"))
        _recolorear(self, anterior, TEMAS[TEMA_ACTIVO])
        for refresco in list(self._refrescos_tema):
            try:
                refresco()
            except Exception:
                continue
        for interruptor in _buscar_switches_tema(self):
            interruptor.configure(
                text="Modo Oscuro" if TEMA_ACTIVO == "oscuro" else "Modo Claro"
            )

    # ------------------------------------------------------------------
    # Modo Recepción (pantalla pública)
    # ------------------------------------------------------------------
    def _construir_vista_publica(self) -> None:
        self.frame_publico = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_publico.grid(row=0, column=0, sticky="nsew")
        self.frame_publico.grid_columnconfigure(0, weight=1)
        self.frame_publico.grid_rowconfigure(1, weight=1)

        cabecera = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(cabecera, "Sistema de Marcación", 22, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w"
        )
        etiqueta(
            cabecera, "Cumplimiento Ley N.º 213/93 · Res. 3028/2024 CONATEL", 13, t("MUTED")
        ).grid(row=1, column=0, sticky="w")
        interruptor_tema(cabecera, self).grid(row=0, column=1, rowspan=2, sticky="e")

        contenido = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        contenido.grid(row=1, column=0, sticky="nsew", padx=24, pady=24)
        contenido.grid_columnconfigure(0, weight=3)
        contenido.grid_columnconfigure(1, weight=2)
        contenido.grid_rowconfigure(0, weight=1)

        columna_kiosco = ctk.CTkFrame(contenido, fg_color="transparent")
        columna_kiosco.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        columna_kiosco.grid_columnconfigure(0, weight=1)
        self._construir_tarjeta_reloj(columna_kiosco)
        self._construir_tarjeta_marcacion(columna_kiosco)
        self._construir_tarjeta_ticket(columna_kiosco)

        self.zona_empleado = ctk.CTkFrame(contenido, fg_color="transparent")
        self.zona_empleado.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.zona_empleado.grid_columnconfigure(0, weight=1)
        self.zona_empleado.grid_rowconfigure(0, weight=1)
        self._mostrar_portal()

        self.pie = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        self.pie.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))
        self.pie.grid_columnconfigure(0, weight=1)
        etiqueta(
            self.pie,
            "Marque su asistencia en el kiosco · el Portal del Empleado y la Gestión requieren usuario y contraseña",
            12,
            t("MUTED"),
        ).grid(row=0, column=0, sticky="w")

    def _mostrar_portal(self) -> None:
        """Inicio de sesión único para el Portal del Empleado y la Gestión."""
        for hijo in self.zona_empleado.winfo_children():
            hijo.destroy()
        tarjeta_login = tarjeta(self.zona_empleado)
        tarjeta_login.grid(row=0, column=0, sticky="nsew")
        tarjeta_login.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_login, "Iniciar sesión", 20, t("TEXT"), "bold").grid(
            row=0, column=0, pady=(30, 4)
        )
        etiqueta(
            tarjeta_login,
            "Portal del Empleado · Recursos Humanos · Administrador",
            13,
            t("MUTED"),
        ).grid(row=1, column=0, pady=(0, 24))
        self.entrada_usuario = entrada(tarjeta_login, "Usuario", ancho=360)
        self.entrada_usuario.grid(row=2, column=0, pady=6)
        self.entrada_clave = entrada(tarjeta_login, "Contraseña", ancho=360)
        self.entrada_clave.configure(show="•")
        self.entrada_clave.grid(row=3, column=0, pady=6)
        self.entrada_clave.bind("<Return>", lambda _e: self._ingresar())
        boton_primario(tarjeta_login, "Ingresar", self._ingresar).grid(
            row=4, column=0, pady=(18, 8)
        )
        self.lbl_login = etiqueta(tarjeta_login, "", 12, t("DANGER"))
        self.lbl_login.grid(row=5, column=0, pady=(0, 12))
        boton_secundario(
            tarjeta_login, "Cambiar contraseña", self._mostrar_cambio_clave
        ).grid(row=6, column=0, pady=(0, 24))
        self.entrada_usuario.focus_set()

    def _ingresar(self) -> None:
        """Valida credenciales y abre el tablero del empleado o la gestión."""
        usuario = self.entrada_usuario.get().strip()
        clave = self.entrada_clave.get()
        if not usuario:
            self.lbl_login.configure(
                text="Ingrese su usuario.", text_color=t("DANGER")
            )
            return
        user = self.db.get_user_by_username(usuario)
        if not user:
            self.lbl_login.configure(
                text="El usuario no existe. Verifique el nombre.",
                text_color=t("DANGER"),
            )
            return
        user = auth.authenticate(self.db, usuario, clave)
        if not user:
            self.lbl_login.configure(
                text="Contraseña incorrecta.", text_color=t("DANGER")
            )
            return
        rol = auth.get_role_name(self.db, user)
        for hijo in self.zona_empleado.winfo_children():
            hijo.destroy()
        if rol in auth.ROLES_GESTION_USUARIOS:
            self.actor = user
            self.frame_publico.grid_forget()
            self.panel_gestion = PanelGestion(self, self.db, user, self._volver_publico)
            self.panel_gestion.grid(row=0, column=0, sticky="nsew")
            return
        self.dashboard_empleado = EmployeeDashboard(
            self.zona_empleado, self.db, user, self._mostrar_portal
        )
        self.dashboard_empleado.grid(row=0, column=0, sticky="nsew")

    def _mostrar_cambio_clave(self) -> None:
        """Formulario de cambio de contraseña del propio usuario."""
        for hijo in self.zona_empleado.winfo_children():
            hijo.destroy()
        tarjeta_cambio = tarjeta(self.zona_empleado)
        tarjeta_cambio.grid(row=0, column=0, sticky="nsew")
        tarjeta_cambio.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_cambio, "Cambiar contraseña", 20, t("TEXT"), "bold").grid(
            row=0, column=0, pady=(30, 4)
        )
        etiqueta(
            tarjeta_cambio,
            "Verifique su identidad con la contraseña actual",
            13,
            t("MUTED"),
        ).grid(row=1, column=0, pady=(0, 24))
        self.entrada_usuario = entrada(tarjeta_cambio, "Usuario", ancho=360)
        self.entrada_usuario.grid(row=2, column=0, pady=6)
        self.entrada_actual = entrada(tarjeta_cambio, "Contraseña actual", ancho=360)
        self.entrada_actual.configure(show="•")
        self.entrada_actual.grid(row=3, column=0, pady=6)
        self.entrada_nueva = entrada(
            tarjeta_cambio, "Contraseña nueva (mín. 6 caracteres)", ancho=360
        )
        self.entrada_nueva.configure(show="•")
        self.entrada_nueva.grid(row=4, column=0, pady=6)
        self.entrada_repetir = entrada(tarjeta_cambio, "Repetir contraseña nueva", ancho=360)
        self.entrada_repetir.configure(show="•")
        self.entrada_repetir.grid(row=5, column=0, pady=6)
        self.entrada_repetir.bind("<Return>", lambda _e: self._ejecutar_cambio_clave())
        boton_primario(
            tarjeta_cambio, "Cambiar contraseña", self._ejecutar_cambio_clave
        ).grid(row=6, column=0, pady=(18, 8))
        self.lbl_cambio = etiqueta(tarjeta_cambio, "", 12, t("DANGER"))
        self.lbl_cambio.grid(row=7, column=0, pady=(0, 12))
        boton_secundario(
            tarjeta_cambio, "Volver al inicio de sesión", self._mostrar_portal
        ).grid(row=8, column=0, pady=(0, 24))
        self.entrada_usuario.focus_set()

    def _ejecutar_cambio_clave(self) -> None:
        """Ejecuta el cambio de contraseña con las validaciones pertinentes."""
        usuario = self.entrada_usuario.get().strip()
        if not usuario:
            self.lbl_cambio.configure(
                text="Ingrese su usuario.", text_color=t("DANGER")
            )
            return
        user = self.db.get_user_by_username(usuario)
        if not user:
            self.lbl_cambio.configure(
                text="El usuario no existe. Verifique el nombre.",
                text_color=t("DANGER"),
            )
            return
        nueva = self.entrada_nueva.get()
        if nueva != self.entrada_repetir.get():
            self.lbl_cambio.configure(
                text="Las contraseñas nuevas no coinciden.", text_color=t("DANGER")
            )
            return
        try:
            auth.cambiar_clave(self.db, user, self.entrada_actual.get(), nueva)
        except ValueError as error:
            self.lbl_cambio.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_cambio.configure(
            text="Contraseña actualizada. Inicie sesión con la nueva clave.",
            text_color=t("SUCCESS"),
        )
        self.after(1400, self._mostrar_portal)

    def _construir_tarjeta_reloj(self, master: ctk.CTkFrame) -> None:
        tarjeta_reloj = tarjeta(master)
        tarjeta_reloj.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        tarjeta_reloj.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_reloj, "Recepción · Marque su asistencia", 13, t("MUTED")).grid(
            row=0, column=0, pady=(18, 0)
        )
        self.lbl_hora = ctk.CTkLabel(
            tarjeta_reloj,
            text="--:--:--",
            font=(MONO, 76, "bold"),
            text_color=t("TEXT"),
        )
        self.lbl_hora.grid(row=1, column=0, pady=(4, 0))
        self.lbl_fecha = etiqueta(tarjeta_reloj, "", 16, t("MUTED"))
        self.lbl_fecha.grid(row=2, column=0, pady=(0, 18))

    def _construir_tarjeta_marcacion(self, master: ctk.CTkFrame) -> None:
        self.tarjeta_marcacion = tarjeta(master)
        self.tarjeta_marcacion.grid(row=1, column=0, sticky="ew", pady=(0, 24))
        self.tarjeta_marcacion.grid_columnconfigure(0, weight=1)
        etiqueta(
            self.tarjeta_marcacion, "Ingrese su cédula o nombre de usuario", 16, t("TEXT")
        ).grid(row=0, column=0, pady=(22, 12))
        self.entrada_id = entrada(self.tarjeta_marcacion, "Ej. 1234567 o juan")
        self.entrada_id.grid(row=1, column=0, pady=(0, 16))
        self.entrada_id.bind("<Return>", lambda _e: self._marcar())
        boton_primario(self.tarjeta_marcacion, "REGISTRAR ASISTENCIA", self._marcar).grid(
            row=2, column=0, pady=(0, 6)
        )
        fila_clima = ctk.CTkFrame(self.tarjeta_marcacion, fg_color="transparent")
        fila_clima.grid(row=3, column=0, pady=(0, 10))
        self.dia_lluvioso = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            fila_clima,
            text="Día de Lluvia Intensa · tolerancia climática 30 min (Res. 3028/2024)",
            variable=self.dia_lluvioso,
            font=(FONT, 12),
            text_color=t("MUTED"),
            progress_color=t("PRIMARY"),
            fg_color=t("INPUT_BG"),
        ).pack(side="left")
        etiqueta(
            self.tarjeta_marcacion,
            "El sistema detecta automáticamente si corresponde Entrada o Salida",
            12,
            t("MUTED"),
        ).grid(row=4, column=0, pady=(0, 14))
        self.lbl_estado = etiqueta(self.tarjeta_marcacion, "", 14, t("SUCCESS"))
        self.lbl_estado.grid(row=5, column=0, pady=(0, 18))

    def _construir_tarjeta_ticket(self, master: ctk.CTkFrame) -> None:
        self.tarjeta_ticket = tarjeta(master)
        self.tarjeta_ticket.grid(row=2, column=0, sticky="ew")
        self.tarjeta_ticket.grid_columnconfigure(0, weight=1)
        etiqueta(self.tarjeta_ticket, "Último comprobante criptográfico", 13, t("MUTED")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(16, 10)
        )
        self.ticket_box = ctk.CTkTextbox(
            self.tarjeta_ticket,
            font=(MONO, 12),
            fg_color=t("INPUT_BG"),
            text_color=t("TEXT"),
            corner_radius=12,
            height=150,
            wrap="word",
        )
        self.ticket_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

    def _actualizar_reloj(self) -> None:
        ahora = datetime.datetime.now()
        self.hora_actual = ahora.strftime("%H:%M:%S")
        self._dibujar_hora()
        self.lbl_fecha.configure(
            text=f"{DIAS[ahora.weekday()]}, {ahora.day} de {MESES[ahora.month - 1]} de {ahora.year}"
        )
        self.after(1000, self._actualizar_reloj)

    def _dibujar_hora(self) -> None:
        """Renderiza la hora con los separadores visibles u ocultos."""
        if getattr(self, "_mostrar_dos_puntos", True):
            self.lbl_hora.configure(text=self.hora_actual)
        else:
            self.lbl_hora.configure(text=self.hora_actual.replace(":", " ", 2))

    def _alternar_dos_puntos(self) -> None:
        """Parpadeo suavizado de los separadores, sincronizado con la hora."""
        self._mostrar_dos_puntos = not self._mostrar_dos_puntos
        self._dibujar_hora()
        self.after(500, self._alternar_dos_puntos)

    def _marcar(self) -> None:
        username = self.entrada_id.get().strip()
        if not username:
            self._mostrar_estado("Ingrese su cédula o usuario.", t("DANGER"))
            return
        user = self.db.get_user_by_username(username)
        if not user:
            self._mostrar_estado("Empleado no encontrado. Verifique su cédula.", t("DANGER"))
            return
        engine = ClockEngine(self.db, user)
        try:
            entry_id, momento, tipo = engine.registrar_asistencia(
                es_dia_lluvioso=self.dia_lluvioso.get()
            )
        except ValueError as error:
            self._mostrar_estado(str(error), t("DANGER"))
            return
        ticket = reports.comprobante_marcacion(entry_id, momento, tipo)
        self.ticket_box.delete("1.0", "end")
        self.ticket_box.insert("1.0", ticket)
        self.entrada_id.delete(0, "end")
        self._mostrar_estado(
            f"{user['full_name']}: {tipo.lower()} registrada correctamente.", t("SUCCESS")
        )
        self._mostrar_panel_exito(tipo, ticket)

    def _mostrar_panel_exito(self, tipo: str, ticket: str) -> None:
        """Despliega el panel temporal de éxito con check verde y el ticket.

        Reemplaza el área de marcación durante 5 segundos y vuelve al kiosco.
        """
        if hasattr(self, "panel_exito"):
            self.panel_exito.destroy()
        self.panel_exito = tarjeta(self.frame_publico)
        self.panel_exito.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=24, pady=(0, 24))
        self.panel_exito.grid_columnconfigure(0, weight=1)
        self.panel_exito.grid_rowconfigure(1, weight=1)
        etiqueta(self.panel_exito, "✓", 54, t("SUCCESS"), "bold").grid(
            row=0, column=0, pady=(34, 0)
        )
        etiqueta(self.panel_exito, f"¡{tipo} Registrada!", 24, t("TEXT"), "bold").grid(
            row=1, column=0, pady=(6, 0)
        )
        etiqueta(
            self.panel_exito, "Comprobante criptográfico · SHA-256", 12, t("MUTED")
        ).grid(row=2, column=0, pady=(2, 10))
        caja_ticket = ctk.CTkTextbox(
            self.panel_exito,
            font=(MONO, 11),
            fg_color=t("INPUT_BG"),
            text_color=t("MUTED"),
            corner_radius=12,
            height=110,
            wrap="word",
        )
        caja_ticket.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 8))
        caja_ticket.insert("1.0", ticket)
        caja_ticket.configure(state="disabled")
        etiqueta(
            self.panel_exito, "Volviendo a recepción…", 11, t("MUTED")
        ).grid(row=4, column=0, pady=(0, 26))
        self.tarjeta_marcacion.grid_remove()
        self.tarjeta_ticket.grid_remove()
        self.pie.grid_remove()
        self.after(5000, self._ocultar_panel_exito)

    def _ocultar_panel_exito(self) -> None:
        if hasattr(self, "panel_exito"):
            self.panel_exito.destroy()
            del self.panel_exito
        self.tarjeta_marcacion.grid()
        self.tarjeta_ticket.grid()
        self.pie.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))

    def _mostrar_estado(self, mensaje: str, color: str) -> None:
        self.lbl_estado.configure(text=mensaje, text_color=color)

    # ------------------------------------------------------------------
    # Modo Gestión (RRHH/Administrador)
    # ------------------------------------------------------------------
    def _volver_publico(self) -> None:
        if self.panel_gestion is not None:
            self.panel_gestion.destroy()
            self.panel_gestion = None
        self.actor = None
        self.frame_publico.grid(row=0, column=0, sticky="nsew")
        self._mostrar_portal()


class EmployeeDashboard(ctk.CTkFrame):
    """Tablero personal del empleado con tarjetas y gráfico mensual.

    Muestra en un solo vistazo las vacaciones disponibles y usufructuadas
    (Art. 23 Res. 3028/2024), el contador de permisos del mes (Art. 25),
    las horas extra acumuladas y un gráfico de barras con las horas
    ordinarias de cada marca del mes en curso.
    """

    def __init__(
        self,
        master: ctk.CTkFrame,
        db: Database,
        user: Dict,
        on_volver: Callable,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.user = user
        self.on_volver = on_volver
        self.resumen: Dict = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(cabecera, f"Resumen de {user['full_name']}", 17, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 2)
        )
        etiqueta(
            cabecera,
            f"{user['username']} · {user.get('tipo_vinculo') or 'Funcionario'}",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))
        boton_secundario(cabecera, "Volver", self.on_volver).grid(
            row=0, column=1, rowspan=2, padx=16, sticky="e"
        )

        self.area = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.area.grid(row=1, column=0, sticky="nsew")
        self.area.grid_columnconfigure(0, weight=1)
        self.area.grid_columnconfigure(1, weight=1)
        self._refrescar()
        self.app_raiz = master.winfo_toplevel()
        if hasattr(self.app_raiz, "registrar_refresco_tema"):
            self.app_raiz.registrar_refresco_tema(self._refrescar)
            self.bind("<Destroy>", self._al_destruir)

    def _al_destruir(self, evento) -> None:
        """Desuscribe el refresco de tema cuando el tablero se cierra."""
        if evento.widget is self and hasattr(self.app_raiz, "quitar_refresco_tema"):
            self.app_raiz.quitar_refresco_tema(self._refrescar)

    def _refrescar(self) -> None:
        """Recarga el resumen del empleado y reconstruye tarjetas y gráfico."""
        for hijo in self.area.winfo_children():
            hijo.destroy()
        self.resumen = reports.resumen_empleado(self.db, self.user)

        tarjeta_vacaciones = tarjeta(self.area)
        tarjeta_vacaciones.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        vacaciones = self.resumen["vacaciones"]
        es_pasante = self.resumen["vinculo"] == "Pasante"
        etiqueta(
            tarjeta_vacaciones,
            "Licencia anual · Art. 23" if es_pasante else "Vacaciones · Art. 29",
            13,
            t("MUTED"),
            "bold",
        ).grid(row=0, column=0, padx=18, pady=(16, 2))
        etiqueta(
            tarjeta_vacaciones,
            f"{vacaciones['disponibles']:.0f} días disponibles",
            22,
            t("TEXT"),
            "bold",
        ).grid(row=1, column=0, padx=18, sticky="w")
        etiqueta(
            tarjeta_vacaciones,
            f"Usufructuados {vacaciones['usadas']:.0f} de {vacaciones['devengadas']:.0f} devengados",
            12,
            t("MUTED"),
        ).grid(row=2, column=0, padx=18, sticky="w", pady=(0, 16))

        tarjeta_permisos = tarjeta(self.area)
        tarjeta_permisos.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
        detalle = self.resumen["permisos_mes"]["detalle"]
        texto_detalle = " · ".join(f"{tipo}: {cantidad}" for tipo, cantidad in detalle.items())
        etiqueta(
            tarjeta_permisos,
            "Permisos del mes · Art. 25" if es_pasante else "Permisos del mes · Art. 34",
            13,
            t("MUTED"),
            "bold",
        ).grid(row=0, column=0, padx=18, pady=(16, 2))
        etiqueta(
            tarjeta_permisos,
            f"{self.resumen['permisos_mes']['total']} permisos utilizados",
            22,
            t("TEXT"),
            "bold",
        ).grid(row=1, column=0, padx=18, sticky="w")
        etiqueta(
            tarjeta_permisos,
            texto_detalle or "Sin permisos en el mes en curso",
            12,
            t("MUTED"),
        ).grid(row=2, column=0, padx=18, sticky="w", pady=(0, 16))

        tarjeta_extras = tarjeta(self.area)
        tarjeta_extras.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
        extras = self.resumen["extras_mes"]
        etiqueta(
            tarjeta_extras,
            f"Horas extra del mes · Ley 213: 50% {extras['horas_50']} h · "
            f"100% {extras['horas_100']} h · {len(self.resumen['marcas_mes']['dias'])} marcas",
            13,
            t("TEXT"),
            "bold",
        ).grid(row=0, column=0, padx=18, pady=(14, 4))

        self._construir_grafico(tarjeta_extras)

        permisos = self.resumen["permisos"]
        if permisos:
            etiqueta(self.area, "Permisos aprobados · descargue el PDF oficial", 13, t("MUTED")).grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(4, 6)
            )
            for permiso in permisos:
                fila = ctk.CTkFrame(self.area, fg_color=t("INPUT_BG"), corner_radius=10)
                fila.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)
                fila.grid_columnconfigure(0, weight=1)
                etiqueta(
                    fila,
                    f"#{permiso['id']} · {permiso['tipo']} · "
                    f"{permiso['inicio']} al {permiso['fin']} · aprobó {permiso['aprobador']}",
                    12,
                ).grid(row=0, column=0, sticky="w", padx=14, pady=10)
                boton_pdf = ctk.CTkButton(
                    fila,
                    text="Descargar PDF",
                    width=120,
                    height=32,
                    font=(FONT, 12),
                    fg_color=t("PRIMARY"),
                    hover_color=t("PRIMARY_HOVER"),
                    corner_radius=8,
                    command=partial(self._descargar_pdf, permiso["id"]),
                )
                boton_pdf._rol = "primario"
                boton_pdf.grid(row=0, column=1, padx=(0, 10))
        else:
            etiqueta(self.area, "Sin permisos aprobados todavía", 12, t("MUTED")).grid(
                row=2, column=0, columnspan=2, pady=(8, 4)
            )

        self._construir_historial(3 + len(permisos))

    def _construir_historial(self, fila_inicio: int) -> None:
        """Tarjeta de historial de marcas desde enero de cualquier año a hoy."""
        hoy = datetime.date.today()
        tarjeta_historial = tarjeta(self.area)
        tarjeta_historial.grid(
            row=fila_inicio, column=0, columnspan=2, sticky="nsew", pady=(12, 12)
        )
        tarjeta_historial.grid_columnconfigure(1, weight=1)
        etiqueta(tarjeta_historial, "Historial de marcas", 16, t("TEXT"), "bold").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 2)
        )
        etiqueta(
            tarjeta_historial,
            "Desde el 1 de enero de cualquier año hasta el día de hoy",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 10))
        fila_rango = ctk.CTkFrame(tarjeta_historial, fg_color="transparent")
        fila_rango.grid(row=2, column=0, columnspan=2, sticky="w", padx=18)
        self.fila_hist_desde = campo_fecha(fila_rango, "Desde (AAAA-MM-DD)", ancho=160)
        self.fila_hist_desde.pack(side="left", padx=(0, 8))
        self.ent_hist_desde = self.fila_hist_desde.entrada
        self.ent_hist_desde.insert(0, f"{hoy.year}-01-01")
        self.fila_hist_hasta = campo_fecha(fila_rango, "Hasta (AAAA-MM-DD)", ancho=160)
        self.fila_hist_hasta.pack(side="left", padx=(0, 8))
        self.ent_hist_hasta = self.fila_hist_hasta.entrada
        self.ent_hist_hasta.insert(0, hoy.isoformat())
        boton_primario(fila_rango, "Consultar", self._consultar_historial).pack(
            side="left", padx=8
        )
        self.lbl_hist_resultado = etiqueta(tarjeta_historial, "", 12, t("SUCCESS"))
        self.lbl_hist_resultado.grid(
            row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(10, 0)
        )
        self.scroll_hist = ctk.CTkScrollableFrame(
            tarjeta_historial, fg_color=t("INPUT_BG"), corner_radius=10, height=230
        )
        self.scroll_hist.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 14)
        )
        etiqueta(
            self.scroll_hist,
            "Use 'Consultar' para cargar el historial del período elegido.",
            12,
            t("MUTED"),
        ).pack(anchor="w", padx=12, pady=10)

    def _consultar_historial(self) -> None:
        """Consulta el historial de marcas del empleado en el rango indicado."""
        try:
            desde = datetime.date.fromisoformat(self.ent_hist_desde.get().strip())
            hasta = datetime.date.fromisoformat(self.ent_hist_hasta.get().strip())
            historial = reports.resumen_historico(self.db, self.user, desde, hasta)
        except ValueError as error:
            self.lbl_hist_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        for hijo in self.scroll_hist.winfo_children():
            hijo.destroy()
        extras = historial["extras_periodo"]
        aguinaldo = historial["aguinaldo_periodo"]
        self.lbl_hist_resultado.configure(
            text=(
                f"{len(historial['marcas'])} marcas · extra 50% {extras['texto_50']} · "
                f"extra 100% {extras['texto_100']} · aguinaldo Gs. "
                f"{aguinaldo['aguinaldo']:,.0f} ({aguinaldo['meses_periodo']} meses)"
            ),
            text_color=t("SUCCESS"),
        )
        if not historial["marcas"]:
            etiqueta(
                self.scroll_hist, "Sin marcas registradas en el período.", 12, t("MUTED")
            ).pack(anchor="w", padx=12, pady=10)
            return
        for marca in historial["marcas"]:
            fila = ctk.CTkFrame(self.scroll_hist, fg_color=t("CARD"), corner_radius=8)
            fila.pack(fill="x", pady=3)
            fila.grid_columnconfigure(1, weight=1)
            etiqueta(fila, marca["fecha"], 12, t("TEXT"), "bold").grid(
                row=0, column=0, sticky="w", padx=12, pady=8
            )
            etiqueta(
                fila,
                f"{marca['entrada']} → {marca['salida'] or '—'} · "
                f"ordinarias {marca['ordinarias']} · extra 50% {marca['extra_50']} · "
                f"extra 100% {marca['extra_100']}",
                11,
                t("MUTED"),
            ).grid(row=0, column=1, sticky="w", padx=6, pady=8)
            incidencias = []
            if marca["tardanza"]:
                incidencias.append("tardanza")
            if marca["feriado"]:
                incidencias.append("feriado")
            if marca["incidencia"]:
                incidencias.append(marca["incidencia"])
            if incidencias:
                etiqueta(fila, " · ".join(incidencias), 10, t("DANGER")).grid(
                    row=0, column=2, sticky="e", padx=12, pady=8
                )

    def _construir_grafico(self, master: ctk.CTkFrame) -> None:
        """Dibuja las horas ordinarias de cada marca del mes en curso."""
        figura = Figure(figsize=(5.8, 2.4), facecolor=t("CARD"))
        figura.subplots_adjust(left=0.06, right=0.97, top=0.9, bottom=0.28)
        eje = figura.add_subplot(111)
        eje.set_facecolor(t("CARD"))
        eje.grid(True, color=t("CARD_BORDER"), alpha=0.5, linestyle="--", linewidth=0.8)
        for borde in ("top", "right"):
            eje.spines[borde].set_visible(False)
        for borde in ("left", "bottom"):
            eje.spines[borde].set_color(t("CARD_BORDER"))
        dias = self.resumen["marcas_mes"]["dias"]
        horas = self.resumen["marcas_mes"]["ordinarias"]
        if not horas:
            eje.text(
                0.5, 0.5, "Sin marcas en el mes en curso",
                ha="center", va="center", color=t("MUTED"), fontsize=11,
                transform=eje.transAxes,
            )
        else:
            eje.bar(dias, horas, color=t("PRIMARY"), width=0.6, edgecolor=t("CARD_BORDER"))
            eje.set_xticks(dias)
            eje.set_xticklabels(dias, fontsize=8)
            eje.tick_params(colors=t("MUTED"), labelsize=8)
            eje.set_ylabel("Horas", fontsize=9, color=t("MUTED"))
        lienzo = FigureCanvasTkAgg(figura, master=master)
        lienzo.draw()
        lienzo.get_tk_widget().grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 12))

    @staticmethod
    def _descargar_pdf(solicitud_id: int) -> None:
        """Genera el PDF oficial del permiso y lo abre para su impresión."""
        import os
        try:
            ruta = reports.generar_pdf_permiso(solicitud_id)
        except ValueError as error:
            print(f"PDF no disponible: {error}")
            return
        os.startfile(ruta)

class PanelGestion(ctk.CTkFrame):
    """Entorno administrativo de dos columnas con accesos directos grandes.

    La columna izquierda concentra los botones de navegación de acceso
    rápido; la derecha despliega el panel elegido en línea, sin ventanas
    emergentes. La sección de Auditoría expone el log JSONB completo.
    """

    SECCIONES: List[tuple] = [
        ("▦", "Personal", "Gestión de Personal"),
        ("✦", "Justificaciones", "Permisos y PDFs"),
        ("▤", "Reportes", "Centro de Reportes"),
        ("✎", "Correcciones", "Solicitudes de Corrección"),
        ("◉", "Analítica", "Dashboard Analítico"),
        ("◈", "Auditoría", "Log JSONB de Auditoría"),
    ]

    def __init__(
        self, master: MarcacionApp, db: Database, actor: Dict, on_cerrar: Callable
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.on_cerrar = on_cerrar
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._construir_sidebar()
        self._construir_contenido()
        master.registrar_refresco_tema(self.dashboard_tab._refrescar)
        master.registrar_refresco_tema(self.auditoria_tab._refrescar)
        self._seleccionar(0)

    def _construir_sidebar(self) -> None:
        sidebar = tarjeta(self)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(24, 12), pady=24)
        sidebar.grid_columnconfigure(0, weight=1)
        etiqueta(sidebar, "Panel de Gestión", 18, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=16, pady=(18, 2)
        )
        etiqueta(
            sidebar,
            f"{self.actor['full_name']}\n{auth.get_role_name(self.db, self.actor)}",
            11,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))
        interruptor_tema(sidebar, self.master).grid(row=2, column=0, sticky="w", padx=16)
        self.botones_seccion: List[ctk.CTkButton] = []
        for indice, (icono, titulo, detalle) in enumerate(self.SECCIONES):
            boton = ctk.CTkButton(
                sidebar,
                text=f"{icono}   {titulo}",
                command=lambda i=indice: self._seleccionar(i),
                fg_color="transparent",
                hover_color=t("INPUT_BG"),
                text_color=t("MUTED"),
                font=(FONT, 14, "bold"),
                corner_radius=12,
                height=54,
                anchor="w",
            )
            boton.grid(row=3 + indice, column=0, sticky="ew", padx=10, pady=3)
            boton._rol = "plano"
            self.botones_seccion.append(boton)
        boton_secundario(sidebar, "Volver a Marcación", self.on_cerrar).grid(
            row=10, column=0, sticky="ew", padx=10, pady=(18, 14)
        )

    def _construir_contenido(self) -> None:
        contenido = ctk.CTkFrame(self, fg_color="transparent")
        contenido.grid(row=0, column=1, sticky="nsew", padx=(12, 24), pady=24)
        contenido.grid_columnconfigure(0, weight=1)
        contenido.grid_rowconfigure(0, weight=1)

        self.personal_tab = PersonalTab(
            contenido, self.db, self.actor, self._refrescar_empleados
        )
        self.justificaciones_tab = JustificacionesTab(contenido, self.db, self.actor)
        self.reportes_tab = ReportesTab(contenido, self.db, self.actor)
        self.correcciones_tab = CorreccionesTab(contenido, self.db, self.actor)
        self.dashboard_tab = DashboardTab(contenido, self.db)
        self.auditoria_tab = AuditoriaTab(contenido, self.db)
        self.pestanas = [
            self.personal_tab,
            self.justificaciones_tab,
            self.reportes_tab,
            self.correcciones_tab,
            self.dashboard_tab,
            self.auditoria_tab,
        ]
        for pestana in self.pestanas:
            pestana.grid(row=0, column=0, sticky="nsew")
            pestana.grid_remove()

    def _seleccionar(self, indice: int) -> None:
        """Cambia la sección activa y estiliza el botón del menú lateral."""
        for posicion, pestana in enumerate(self.pestanas):
            pestana.grid_remove()
        self.pestanas[indice].grid(row=0, column=0, sticky="nsew")
        for posicion, boton in enumerate(self.botones_seccion):
            seleccionado = posicion == indice
            boton.configure(
                fg_color=t("PRIMARY") if seleccionado else "transparent",
                text_color="white" if seleccionado else t("MUTED"),
                hover_color=t("PRIMARY_HOVER") if seleccionado else t("INPUT_BG"),
            )

    def _refrescar_empleados(self) -> None:
        self.justificaciones_tab.refrescar_empleados()


class AuditoriaTab(ctk.CTkFrame):
    """Bitácora de auditoría JSONB con los snapshots anterior y nuevo."""

    def __init__(self, master, db: Database) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(cabecera, "Log de Auditoría · JSONB", 16, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 2)
        )
        etiqueta(
            cabecera,
            "Eventos de RRHH/Admin con los valores anteriores y posteriores",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        """Reconstruye la bitácora con los últimos 60 eventos registrados."""
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        eventos = self.db.listar_auditoria()
        if not eventos:
            etiqueta(self.scroll, "Sin eventos de auditoría todavía.", 13, t("MUTED")).pack(pady=20)
            return
        for evento in eventos:
            fila = tarjeta(self.scroll)
            fila.pack(fill="x", pady=5)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"{evento['creado_en'].strftime('%d/%m/%Y %H:%M')} · {evento['accion']} · "
                f"tabla {evento['tabla']} #{evento['registro_id']} · {evento['full_name']}",
                13,
                t("TEXT"),
                "bold",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
            detalle = (
                f"Anterior: {evento['valores_anteriores']} | "
                f"Nuevo: {evento['valores_nuevos']}"
            )
            etiqueta(fila, detalle, 11, t("MUTED")).grid(
                row=1, column=0, sticky="w", padx=14, pady=(0, 10)
            )


class CorreccionesTab(ctk.CTkFrame):
    """Bandeja de reclamos web con aprobación/rechazo y auditoría JSONB."""

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(
            cabecera,
            "Reclamos de marcación fallida enviados desde la web",
            15,
            t("TEXT"),
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 4))
        etiqueta(
            cabecera,
            "Al aprobar, el marcaje se corrige en PostgreSQL y queda trazado en la auditoría",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=20)
        boton_refrescar = ctk.CTkButton(
            cabecera,
            text="Refrescar",
            command=self._refrescar,
            width=100,
            height=32,
            font=(FONT, 12),
            fg_color=t("PRIMARY"),
            hover_color=t("PRIMARY_HOVER"),
            corner_radius=8,
        )
        boton_refrescar.grid(row=0, column=1, rowspan=2, padx=16, sticky="e")
        self.lbl_resultado = etiqueta(cabecera, "", 12, t("SUCCESS"))
        self.lbl_resultado.grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 12))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        solicitudes = self.db.listar_solicitudes_correccion()
        if not solicitudes:
            etiqueta(self.scroll, "No hay solicitudes de corrección.", 13, t("MUTED")).pack(pady=20)
            return
        for solicitud in solicitudes:
            fila = tarjeta(self.scroll)
            fila.pack(fill="x", pady=5)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"#{solicitud['id']} · {solicitud['full_name']} ({solicitud['username']}) "
                f"· {solicitud['fecha_registro']}",
                14,
                t("TEXT"),
                "bold",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
            estado = solicitud["estado"]
            if solicitud["revisor"]:
                estado += f" · {solicitud['revisor']}"
            etiqueta(
                fila,
                f"{solicitud['tipo_marca']} a las {solicitud['hora_propuesta']} · {estado}",
                12,
                t("MUTED"),
            ).grid(row=1, column=0, sticky="w", padx=14)
            etiqueta(fila, f"Motivo: {solicitud['motivo']}", 12, t("TEXT")).grid(
                row=2, column=0, sticky="w", padx=14, pady=(2, 10)
            )
            if solicitud["estado"] == "Pendiente":
                boton_aprobar = ctk.CTkButton(
                    fila,
                    text="Aprobar",
                    width=90,
                    height=32,
                    font=(FONT, 12),
                    fg_color=t("SUCCESS"),
                    hover_color="#3BBF6B",
                    text_color="#0B1F14",
                    corner_radius=8,
                    command=partial(self._resolver, solicitud["id"], True),
                )
                boton_aprobar.grid(row=0, column=1, rowspan=3, padx=(0, 6), sticky="e")
                boton_rechazar = ctk.CTkButton(
                    fila,
                    text="Rechazar",
                    width=90,
                    height=32,
                    font=(FONT, 12),
                    fg_color="transparent",
                    hover_color=t("DANGER"),
                    border_width=1,
                    border_color=t("DANGER"),
                    text_color=t("DANGER"),
                    corner_radius=8,
                    command=partial(self._resolver, solicitud["id"], False),
                )
                boton_rechazar.grid(row=0, column=2, rowspan=3, padx=(0, 14), sticky="e")

    def _resolver(self, solicitud_id: int, aprobar: bool) -> None:
        try:
            estado = auth.aprobar_solicitud_correccion(
                self.db, self.actor, solicitud_id, aprobar
            )
        except ValueError as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(
            text=f"Solicitud #{solicitud_id} {estado.lower()} con auditoría.",
            text_color=t("SUCCESS"),
        )
        self._refrescar()


class DashboardTab(ctk.CTkFrame):
    """Analítica visual de RRHH: tardanzas del mes, horas extra por
    departamento y proyección del aguinaldo proporcional en Guaraníes."""

    def __init__(self, master: ctk.CTkFrame, db: Database) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(
            cabecera, "Dashboard Analítico de Recursos Humanos", 16, t("TEXT"), "bold"
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 2))
        self.lbl_actualizado = etiqueta(
            cabecera, "Cargando métricas…", 12, t("MUTED")
        )
        self.lbl_actualizado.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))
        ctk.CTkButton(
            cabecera,
            text="Actualizar",
            command=self._refrescar,
            width=110,
            height=34,
            font=(FONT, 12),
            fg_color=t("PRIMARY"),
            hover_color=t("PRIMARY_HOVER"),
            corner_radius=8,
        ).grid(row=0, column=1, rowspan=2, padx=16, sticky="e")

        self.area = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.area.grid(row=1, column=0, sticky="nsew")
        self.area.grid_columnconfigure(0, weight=1)
        self.area.grid_columnconfigure(1, weight=1)
        self._refrescar()

    def _refrescar(self) -> None:
        """Recarga los datos analíticos y redibuja los tres bloques."""
        for hijo in self.area.winfo_children():
            hijo.destroy()
        self.tardanzas = reports.obtener_metricas_tardanzas(self.db)
        self.extras = reports.obtener_horas_extra_por_departamento(self.db)
        self.aguinaldo = reports.obtener_proyeccion_aguinaldos_totales(self.db)
        self.lbl_actualizado.configure(
            text=f"Actualizado · {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        self._construir_tarjeta_aguinaldo(0, 0)
        self._construir_grafico_tardanzas(1, 0)
        self._construir_grafico_extras(1, 1)

    def _construir_tarjeta_aguinaldo(self, fila: int, columna: int) -> None:
        tarjeta_aguinaldo = tarjeta(self.area)
        tarjeta_aguinaldo.grid(
            row=fila, column=columna, columnspan=2, sticky="ew", pady=(0, 12)
        )
        tarjeta_aguinaldo.grid_columnconfigure(0, weight=1)
        etiqueta(
            tarjeta_aguinaldo,
            "AGUINALDO PROPORCIONAL ESTIMADO · LEY N.º 6380/2019",
            13,
            t("MUTED"),
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(18, 2))
        total = self.aguinaldo["total_acumulado_g"]
        millones = total / 1_000_000
        etiqueta(
            tarjeta_aguinaldo,
            f"Gs. {total:,}".replace(",", "."),
            38,
            t("TEXT"),
            "bold",
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(2, 0))
        etiqueta(
            tarjeta_aguinaldo,
            f"≈ {millones:,.2f} millones de Guaraníes acumulados",
            15,
            "#F5C26B",
            "bold",
        ).grid(row=2, column=0, sticky="w", padx=24, pady=(0, 4))
        resumen = (
            f"{self.aguinaldo['empleados']} empleados activos · "
            f"{self.aguinaldo['meses_transcurridos']} meses devengados · "
            f"Proyección anual Gs. {self.aguinaldo['total_anual_g']:,}".replace(",", ".")
        )
        etiqueta(tarjeta_aguinaldo, resumen, 13, t("MUTED")).grid(
            row=3, column=0, sticky="w", padx=24, pady=(0, 6)
        )
        partes = [
            f"{dep}: Gs. {datos['acumulado_g']:,}".replace(",", ".")
            for dep, datos in self.aguinaldo["por_departamento"].items()
        ]
        if partes:
            etiqueta(tarjeta_aguinaldo, " · ".join(partes), 12, t("MUTED")).grid(
                row=4, column=0, sticky="w", padx=24, pady=(0, 18)
            )

    def _crear_figura(self, ancho: float, alto: float) -> Figure:
        return Figure(figsize=(ancho, alto), facecolor=t("BG"))

    def _ajustar_figura(self, figura: Figure) -> None:
        figura.subplots_adjust(left=0.14, right=0.96, top=0.9, bottom=0.16)

    def _estilizar_ejes(self, eje) -> None:
        eje.set_facecolor(t("BG"))
        eje.grid(True, color="#34343B", alpha=0.35, linestyle="--", linewidth=0.8)
        for borde in ("top", "right"):
            eje.spines[borde].set_visible(False)
        for borde in ("left", "bottom"):
            eje.spines[borde].set_color("#34343B")

    def _construir_grafico_tardanzas(self, fila: int, columna: int) -> None:
        tarjeta_grafico = tarjeta(self.area)
        tarjeta_grafico.grid(
            row=fila, column=columna, sticky="nsew", padx=(0, 6), pady=(0, 12)
        )
        etiqueta(
            tarjeta_grafico,
            "Llegadas Tardías por Día · Mes en Curso",
            14,
            t("TEXT"),
            "bold",
        ).pack(anchor="w", padx=16, pady=(14, 0))
        figura = self._crear_figura(5.4, 3.2)
        self._ajustar_figura(figura)
        eje = figura.add_subplot(111)
        self._estilizar_ejes(eje)
        dias = [datetime.date.fromisoformat(d["fecha"]).day for d in self.tardanzas]
        cantidades = [d["cantidad"] for d in self.tardanzas]
        if not cantidades or max(cantidades) == 0:
            eje.text(
                0.5, 0.5, "Sin llegadas tardías registradas en el mes",
                ha="center", va="center", color=t("MUTED"), fontsize=12,
                transform=eje.transAxes,
            )
        else:
            eje.plot(
                dias, cantidades, color=t("PRIMARY"), linewidth=2.5,
                marker="o", markersize=5, markerfacecolor=t("TEXT"),
            )
            eje.fill_between(dias, cantidades, color=t("PRIMARY"), alpha=0.12)
            pico = max(cantidades)
            if pico > 0:
                dia_pico = dias[cantidades.index(pico)]
                eje.scatter([dia_pico], [pico], s=90, color=t("DANGER"), zorder=5)
                eje.annotate(
                    f"Pico: {pico}",
                    xy=(dia_pico, pico), xytext=(6, 12),
                    textcoords="offset points", color=t("DANGER"), fontsize=10, fontweight="bold",
                )
            eje.set_xlabel("Día del mes", fontsize=10)
            eje.set_ylabel("Cantidad de tardanzas", fontsize=10)
            eje.set_xticks(dias)
            eje.tick_params(labelsize=8)
        lienzo = FigureCanvasTkAgg(figura, master=tarjeta_grafico)
        lienzo.draw()
        lienzo.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(4, 12))

    def _construir_grafico_extras(self, fila: int, columna: int) -> None:
        tarjeta_grafico = tarjeta(self.area)
        tarjeta_grafico.grid(
            row=fila, column=columna, sticky="nsew", padx=(6, 0), pady=(0, 12)
        )
        etiqueta(
            tarjeta_grafico,
            "Horas Extra 50% vs 100% por Departamento",
            14,
            t("TEXT"),
            "bold",
        ).pack(anchor="w", padx=16, pady=(14, 0))
        figura = self._crear_figura(5.4, 3.2)
        self._ajustar_figura(figura)
        eje = figura.add_subplot(111)
        self._estilizar_ejes(eje)
        departamentos = [e["departamento"] for e in self.extras]
        if not departamentos:
            eje.text(
                0.5, 0.5, "Sin horas extra acumuladas todavía",
                ha="center", va="center", color=t("MUTED"), fontsize=12,
                transform=eje.transAxes,
            )
        else:
            posiciones = range(len(departamentos))
            ancho_barra = 0.38
            eje.bar(
                [p - ancho_barra / 2 for p in posiciones],
                [e["horas_50"] for e in self.extras],
                width=ancho_barra, color=t("PRIMARY"), label="Recargo 50%",
                edgecolor="#26262C",
            )
            eje.bar(
                [p + ancho_barra / 2 for p in posiciones],
                [e["horas_100"] for e in self.extras],
                width=ancho_barra, color="#F5C26B", label="Recargo 100%",
                edgecolor="#26262C",
            )
            for indice, extra in enumerate(self.extras):
                eje.text(
                    indice - ancho_barra / 2, extra["horas_50"] + 0.3,
                    f"{extra['horas_50']:.1f}", ha="center", va="bottom",
                    color=t("TEXT"), fontsize=9,
                )
                eje.text(
                    indice + ancho_barra / 2, extra["horas_100"] + 0.3,
                    f"{extra['horas_100']:.1f}", ha="center", va="bottom",
                    color=t("TEXT"), fontsize=9,
                )
            eje.set_xticks(list(posiciones))
            eje.set_xticklabels(departamentos, fontsize=8)
            eje.set_ylabel("Horas acumuladas", fontsize=10)
            eje.legend(loc="upper right", frameon=False, fontsize=9)
            eje.tick_params(labelsize=8)
        lienzo = FigureCanvasTkAgg(figura, master=tarjeta_grafico)
        lienzo.draw()
        lienzo.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(4, 12))


class PersonalTab(ctk.CTkFrame):
    """Alta de personal y listado con edición inline (sin ventanas emergentes)."""

    def __init__(
        self, master, db: Database, actor: Dict, on_cambio: Callable
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.on_cambio = on_cambio
        self.editando: Optional[Dict] = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._construir_formulario()
        self._construir_listado()

    def _construir_formulario(self) -> None:
        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        etiqueta(formulario, "Agregar empleado", 17, t("TEXT"), "bold").pack(
            anchor="w", padx=20, pady=(18, 14)
        )
        self.ent_usuario = entrada(formulario, "Usuario / cédula", ancho=280)
        self.ent_usuario.pack(pady=5)
        self.ent_nombre = entrada(formulario, "Nombre completo", ancho=280)
        self.ent_nombre.pack(pady=5)
        self.menu_rol = ctk.CTkOptionMenu(
            formulario,
            values=[r["nombre"] for r in self.db.list_roles()],
            font=(FONT, 14),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            width=280,
            height=40,
        )
        self.menu_rol.pack(pady=5)
        self.menu_vinculo = ctk.CTkOptionMenu(
            formulario,
            values=list(auth.TIPOS_VINCULO),
            font=(FONT, 14),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            width=280,
            height=40,
        )
        self.menu_vinculo.set("Funcionario")
        self.menu_vinculo.pack(pady=5)
        self.ent_salario = entrada(formulario, "Salario base (Gs.)", ancho=280)
        self.ent_salario.pack(pady=5)
        self.ent_clave = entrada(formulario, "Contraseña inicial", ancho=280)
        self.ent_clave.configure(show="•")
        self.ent_clave.pack(pady=5)
        boton_primario(formulario, "Agregar Empleado", self._agregar).pack(
            pady=(16, 6)
        )
        self.lbl_resultado = etiqueta(formulario, "", 12, t("SUCCESS"))
        self.lbl_resultado.pack(pady=(0, 16))

    def _construir_listado(self) -> None:
        listado = tarjeta(self)
        listado.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        listado.grid_columnconfigure(0, weight=1)
        listado.grid_rowconfigure(1, weight=1)
        etiqueta(listado, "Personal registrado", 17, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 12)
        )
        self.scroll = ctk.CTkScrollableFrame(
            listado, fg_color="transparent", corner_radius=0
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 16))
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        for usuario in self.db.list_users():
            vinculo = usuario.get("tipo_vinculo") or "Funcionario"
            fila = ctk.CTkFrame(self.scroll, fg_color=t("INPUT_BG"), corner_radius=10)
            fila.pack(fill="x", pady=4)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"{usuario['username']} · {usuario['full_name']} · "
                f"{usuario['role_name']} · Gs. {float(usuario['salario_mensual'] or 0):,.0f}",
                13,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            etiqueta(
                fila,
                f"Vínculo: {vinculo}",
                11,
                t("ACCENTO") if vinculo == "Pasante" else t("MUTED"),
            ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
            boton_editar = ctk.CTkButton(
                fila,
                text="Editar",
                width=70,
                height=30,
                font=(FONT, 12),
                fg_color=t("PRIMARY"),
                hover_color=t("PRIMARY_HOVER"),
                corner_radius=8,
                command=partial(self._editar, usuario),
            )
            boton_editar.grid(row=0, column=1, padx=(0, 6))
            boton_eliminar = ctk.CTkButton(
                fila,
                text="Eliminar",
                width=70,
                height=30,
                font=(FONT, 12),
                fg_color="transparent",
                hover_color=t("DANGER"),
                border_width=1,
                border_color=t("DANGER"),
                text_color=t("DANGER"),
                corner_radius=8,
                command=partial(self._eliminar, usuario),
            )
            boton_eliminar.grid(row=0, column=2, padx=(0, 10))
            if self.editando and self.editando["id"] == usuario["id"]:
                self._construir_editor_inline(self.scroll)

    def _construir_editor_inline(self, master) -> None:
        """Renderiza el editor embebido con salario, rol, vínculo y clave."""
        editor = tarjeta(master)
        editor.pack(fill="x", pady=(0, 8))
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_columnconfigure(1, weight=1)
        fila_1 = ctk.CTkFrame(editor, fg_color="transparent")
        fila_1.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 0))
        fila_1.grid_columnconfigure(0, weight=1)
        fila_1.grid_columnconfigure(1, weight=1)
        self.ent_ed_salario = entrada(fila_1, "Salario mensual (Gs.)", ancho=230)
        self.ent_ed_salario.insert(
            0, f"{float(self.editando['salario_mensual'] or 0):,.0f}"
        )
        self.ent_ed_salario.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.ent_ed_clave = entrada(fila_1, "Nueva contraseña (opcional)", ancho=230)
        self.ent_ed_clave.configure(show="•")
        self.ent_ed_clave.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        fila_2 = ctk.CTkFrame(editor, fg_color="transparent")
        fila_2.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 0))
        fila_2.grid_columnconfigure(0, weight=1)
        fila_2.grid_columnconfigure(1, weight=1)
        self.menu_ed_rol = ctk.CTkOptionMenu(
            fila_2,
            values=[r["nombre"] for r in self.db.list_roles()],
            font=(FONT, 13),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            height=36,
        )
        self.menu_ed_rol.set(self.editando["role_name"])
        self.menu_ed_rol.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.menu_ed_vinculo = ctk.CTkOptionMenu(
            fila_2,
            values=list(auth.TIPOS_VINCULO),
            font=(FONT, 13),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            height=36,
        )
        self.menu_ed_vinculo.set(self.editando.get("tipo_vinculo") or "Funcionario")
        self.menu_ed_vinculo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        fila_3 = ctk.CTkFrame(editor, fg_color="transparent")
        fila_3.grid(row=2, column=0, columnspan=2, pady=(10, 12))
        boton_primario(fila_3, "Guardar cambios", self._guardar_edicion).pack(
            side="left", padx=6
        )
        boton_secundario(fila_3, "Cancelar", self._cancelar_edicion).pack(
            side="left", padx=6
        )

    def _agregar(self) -> None:
        try:
            salario = float(self.ent_salario.get().replace(".", "") or 0)
        except ValueError:
            salario = 0.0
        try:
            auth.create_user(
                self.db,
                self.actor,
                self.ent_usuario.get().strip(),
                self.ent_clave.get(),
                self.ent_nombre.get().strip(),
                self.menu_rol.get(),
                salario,
                self.menu_vinculo.get(),
            )
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(text="Empleado agregado correctamente.", text_color=t("SUCCESS"))
        for campo in (self.ent_usuario, self.ent_nombre, self.ent_salario, self.ent_clave):
            campo.delete(0, "end")
        self._refrescar()
        self.on_cambio()

    def _editar(self, usuario: Dict) -> None:
        """Despliega el editor inline debajo de la fila del empleado."""
        if self.editando and self.editando["id"] == usuario["id"]:
            self.editando = None
        else:
            self.editando = usuario
        self._refrescar()

    def _guardar_edicion(self) -> None:
        salario_raw = self.ent_ed_salario.get().replace(",", "").strip()
        try:
            salario = float(salario_raw) if salario_raw else None
        except ValueError:
            salario = None
        clave = self.ent_ed_clave.get() or None
        try:
            auth.update_user(
                self.db,
                self.actor,
                self.editando["id"],
                password=clave,
                role_name=self.menu_ed_rol.get(),
                salario_mensual=salario,
                tipo_vinculo=self.menu_ed_vinculo.get(),
            )
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.editando = None
        self._refrescar()
        self.on_cambio()

    def _cancelar_edicion(self) -> None:
        self.editando = None
        self._refrescar()

    def _eliminar(self, usuario: Dict) -> None:
        try:
            auth.delete_user(self.db, self.actor, usuario["id"])
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(text="Empleado eliminado.", text_color=t("SUCCESS"))
        self._refrescar()
        self.on_cambio()



class JustificacionesTab(ctk.CTkFrame):
    """Panel de justificaciones aprobadas (Vacaciones/Reposo/Permiso)."""

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.empleados: List[Dict] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="ew")
        formulario.grid_columnconfigure(0, weight=1)
        etiqueta(formulario, "Registrar justificación aprobada", 17, t("TEXT"), "bold").grid(
            row=0, column=0, pady=(18, 14)
        )
        self.menu_empleado = ctk.CTkOptionMenu(
            formulario,
            values=[""],
            command=self._cambiar_empleado,
            font=(FONT, 14),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            width=420,
            height=40,
        )
        self.menu_empleado.grid(row=1, column=0, pady=5)
        self.menu_tipo = ctk.CTkOptionMenu(
            formulario,
            values=[""],
            command=self._cambiar_tipo,
            font=(FONT, 14),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            dropdown_hover_color=t("PRIMARY"),
            width=420,
            height=40,
        )
        self.menu_tipo.grid(row=2, column=0, pady=5)
        self.fila_inicio = campo_fecha(formulario, "Fecha inicio", ancho=310)
        self.fila_inicio.grid(row=3, column=0, pady=5)
        self.ent_inicio = self.fila_inicio.entrada
        self.fila_fin = campo_fecha(formulario, "Fecha fin · máx. hoy", ancho=310)
        self.fila_fin.grid(row=4, column=0, pady=5)
        self.ent_fin = self.fila_fin.entrada
        self.ent_horas = entrada(formulario, "Horas del permiso (ej. 2.5)", ancho=420)
        self.lbl_condiciones = etiqueta(formulario, "", 11, t("MUTED"))
        boton_primario(formulario, "Registrar Justificación", self._crear).grid(
            row=7, column=0, pady=(16, 8)
        )
        self.lbl_resultado = etiqueta(formulario, "", 13, t("SUCCESS"))
        self.lbl_resultado.grid(row=8, column=0, pady=(0, 18))
        etiqueta(formulario, "Disponibilidad por artículo", 14, t("TEXT"), "bold").grid(
            row=9, column=0, pady=(4, 6)
        )
        self.scroll_disp = ctk.CTkScrollableFrame(
            formulario, fg_color=t("INPUT_BG"), corner_radius=10, height=180
        )
        self.scroll_disp.grid(row=10, column=0, sticky="ew", padx=20, pady=(0, 18))
        self.refrescar_empleados()

        listado = tarjeta(self)
        listado.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        listado.grid_columnconfigure(0, weight=1)
        listado.grid_rowconfigure(1, weight=1)
        etiqueta(listado, "Justificaciones emitidas", 16, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=20, pady=(16, 8)
        )
        self.scroll_just = ctk.CTkScrollableFrame(
            listado, fg_color="transparent", corner_radius=0
        )
        self.scroll_just.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
        self._refrescar_lista()

    def _refrescar_lista(self) -> None:
        """Lista las justificaciones con su botón de descarga del PDF legal."""
        for hijo in self.scroll_just.winfo_children():
            hijo.destroy()
        for justificacion in self.db.list_justificaciones():
            fila = ctk.CTkFrame(self.scroll_just, fg_color=t("INPUT_BG"), corner_radius=10)
            fila.pack(fill="x", pady=4)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"#{justificacion['id']:03d} · {justificacion['tipo_permiso']} · "
                f"{justificacion['full_name']} ({justificacion['username']})",
                13,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            etiqueta(
                fila,
                f"{justificacion['fecha_inicio'].strftime('%d/%m/%Y')} → "
                f"{justificacion['fecha_fin'].strftime('%d/%m/%Y')}"
                + (
                    f" · {float(justificacion['horas_usadas'] or 0):g} h"
                    if float(justificacion.get("horas_usadas") or 0) > 0
                    else ""
                )
                + f" · Aprobado por {justificacion['aprobador']}",
                11,
                t("MUTED"),
            ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
            if justificacion.get("hash_legal"):
                etiqueta(
                    fila,
                    f"SHA-256 {justificacion['hash_legal'][:16]}…",
                    9,
                    t("SUCCESS"),
                ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
            boton_primario(
                fila,
                "Descargar PDF",
                partial(EmployeeDashboard._descargar_pdf, justificacion["id"]),
            ).grid(row=0, column=1, rowspan=2, padx=(0, 12))

    def refrescar_empleados(self) -> None:
        self.empleados = self.db.list_users()
        self.menu_empleado.configure(
            values=[f"{e['username']} ({e['full_name']})" for e in self.empleados]
        )
        if self.empleados:
            self.menu_empleado.set(
                f"{self.empleados[0]['username']} ({self.empleados[0]['full_name']})"
            )
        self._poblar_tipos()
        self._refrescar_disponibilidad()

    def _empleado_actual(self) -> Optional[Dict]:
        """Resuelve el empleado elegido en el menú."""
        seleccion = self.menu_empleado.get()
        return next(
            (e for e in self.empleados if f"{e['username']} ({e['full_name']})" == seleccion),
            None,
        )

    def _articulos_actuales(self) -> List[Dict]:
        """Artículos reglamentarios del vínculo del empleado seleccionado."""
        empleado = self._empleado_actual()
        if not empleado:
            return []
        return reglamento.articulos_aplicables(
            empleado.get("tipo_vinculo") or "Funcionario"
        )

    def _poblar_tipos(self) -> None:
        """Llena el menú de artículos según el vínculo del empleado elegido."""
        articulos = self._articulos_actuales()
        valores = [f"{a['articulo']} · {a['nombre']}" for a in articulos]
        self.menu_tipo.configure(values=valores)
        if valores:
            self.menu_tipo.set(valores[0])
        self._cambiar_tipo()

    def _cambiar_empleado(self, _seleccion: str = "") -> None:
        """Actualiza artículos y disponibilidad al cambiar de empleado."""
        self._poblar_tipos()
        self._refrescar_disponibilidad()

    def _cambiar_tipo(self, _seleccion: str = "") -> None:
        """Muestra las condiciones del artículo y el campo de horas si aplica."""
        articulo = self._articulo_seleccionado()
        if articulo is None:
            self.lbl_condiciones.configure(text="")
            self.ent_horas.grid_remove()
            return
        self.lbl_condiciones.configure(
            text=f"{articulo['reglamento']} · {articulo['condiciones']}"
        )
        self.lbl_condiciones.grid(row=6, column=0, pady=(6, 0), padx=24)
        if articulo["unidad"] == reglamento.UNIDAD_HORAS:
            self.ent_horas.grid(row=5, column=0, pady=5)
        else:
            self.ent_horas.grid_remove()

    def _articulo_seleccionado(self) -> Optional[Dict]:
        """Resuelve el artículo del menú actual."""
        seleccion = self.menu_tipo.get()
        return next(
            (a for a in self._articulos_actuales() if f"{a['articulo']} · {a['nombre']}" == seleccion),
            None,
        )

    def _refrescar_disponibilidad(self) -> None:
        """Panel usados/restantes de cada artículo del empleado seleccionado."""
        for hijo in self.scroll_disp.winfo_children():
            hijo.destroy()
        empleado = self._empleado_actual()
        if not empleado:
            return
        nombres_unidad = {"dias": "días", "horas": "horas", "veces": "veces"}
        for disp in reglamento.disponibilidad_permisos(self.db, empleado):
            fila = ctk.CTkFrame(self.scroll_disp, fg_color=t("CARD"), corner_radius=8)
            fila.pack(fill="x", pady=3)
            fila.grid_columnconfigure(1, weight=1)
            unidad = nombres_unidad.get(disp["unidad"], disp["unidad"])
            if disp["cuota"] is None:
                texto_cuota = "Sin límite"
            else:
                texto_cuota = (
                    f"{disp['usados']:g} de {disp['cuota']:g} {unidad} usados"
                )
            color = t("SUCCESS") if disp["disponible"] else t("DANGER")
            etiqueta(
                fila,
                f"{disp['articulo']} · {disp['nombre']}",
                12,
                t("TEXT"),
                "bold",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            etiqueta(fila, texto_cuota, 11, color).grid(
                row=0, column=1, sticky="e", padx=12, pady=(8, 0)
            )
            etiqueta(
                fila,
                (
                    f"Quedan {disp['restantes']:g} {unidad}"
                    if disp["restantes"] is not None
                    else ("Disponible" if disp["disponible"] else "No aplica")
                )
                + (
                    f" · {disp['usos']:g} de {disp['usos_max']:g} usos"
                    if disp.get("usos_max")
                    else ""
                ),
                10,
                t("MUTED"),
            ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

    def _crear(self) -> None:
        empleado = self._empleado_actual()
        if not empleado:
            self.lbl_resultado.configure(text="Seleccione un empleado.", text_color=t("DANGER"))
            return
        articulo = self._articulo_seleccionado()
        if articulo is None:
            self.lbl_resultado.configure(
                text="Seleccione el artículo del permiso.", text_color=t("DANGER")
            )
            return
        try:
            inicio = datetime.date.fromisoformat(self.ent_inicio.get().strip())
            fin = datetime.date.fromisoformat(self.ent_fin.get().strip())
            horas = 0.0
            if articulo["unidad"] == reglamento.UNIDAD_HORAS:
                horas = float(self.ent_horas.get().strip().replace(",", "."))
            justificacion_id = auth.crear_justificacion(
                self.db, self.actor, empleado["id"], articulo["tipo"], inicio, fin, horas
            )
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(
            text=f"Justificación #{justificacion_id} aprobada para {empleado['full_name']}.",
            text_color=t("SUCCESS"),
        )
        self._refrescar_lista()
        self._refrescar_disponibilidad()


class ReportesTab(ctk.CTkFrame):
    """Centro de reportes con exportación a Excel en un solo clic."""

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.grid_columnconfigure(0, weight=1)

        tarjeta_asistencia = tarjeta(self)
        tarjeta_asistencia.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        tarjeta_asistencia.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_asistencia, "Reporte Mensual de Asistencia", 17, t("TEXT"), "bold").grid(
            row=0, column=0, pady=(20, 4)
        )
        etiqueta(
            tarjeta_asistencia,
            "Desglose de horas ordinarias, extra 50% y extra 100% para contabilidad",
            13,
            t("MUTED"),
        ).grid(row=1, column=0)
        fila_periodo = ctk.CTkFrame(tarjeta_asistencia, fg_color="transparent")
        fila_periodo.grid(row=2, column=0, pady=16)
        self.ent_anio = entrada(fila_periodo, "Año (2026)", ancho=140)
        self.ent_anio.pack(side="left", padx=6)
        self.ent_mes = entrada(fila_periodo, "Mes (1-12)", ancho=120)
        self.ent_mes.pack(side="left", padx=6)
        boton_primario(
            tarjeta_asistencia, "Descargar Reporte Mensual (Excel)", self._exportar_asistencia
        ).grid(row=3, column=0, pady=(0, 8))

        tarjeta_aguinaldo = tarjeta(self)
        tarjeta_aguinaldo.grid(row=1, column=0, sticky="ew")
        tarjeta_aguinaldo.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_aguinaldo, "Proyección de Aguinaldos", 17, t("TEXT"), "bold").grid(
            row=0, column=0, pady=(20, 4)
        )
        etiqueta(
            tarjeta_aguinaldo,
            "Aguinaldo proporcional (13.º salario, Ley 6380/2019)",
            13,
            t("MUTED"),
        ).grid(row=1, column=0)
        self.ent_anio_agui = entrada(tarjeta_aguinaldo, "Año (2026)", ancho=140)
        self.ent_anio_agui.grid(row=2, column=0, pady=16)
        boton_primario(
            tarjeta_aguinaldo, "Proyectar Aguinaldos (Excel)", self._exportar_aguinaldo
        ).grid(row=3, column=0, pady=(0, 8))

        self.lbl_resultado = etiqueta(self, "", 13, t("SUCCESS"))
        self.lbl_resultado.grid(row=2, column=0, pady=(18, 6))

    def _periodo(self, entrada_anio: ctk.CTkEntry, entrada_mes: Optional[ctk.CTkEntry]) -> tuple:
        anio = int(entrada_anio.get().strip() or datetime.datetime.now().year)
        if entrada_mes is None:
            return (anio,)
        mes = int(entrada_mes.get().strip() or datetime.datetime.now().month)
        return (anio, mes)

    def _exportar_asistencia(self) -> None:
        try:
            anio, mes = self._periodo(self.ent_anio, self.ent_mes)
            ruta = reports.exportar_asistencia_mensual(self.db, self.actor, anio, mes)
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(text=f"Reporte exportado: {ruta}", text_color=t("SUCCESS"))

    def _exportar_aguinaldo(self) -> None:
        try:
            anio = self._periodo(self.ent_anio_agui, None)[0]
            ruta = reports.exportar_aguinaldo(self.db, self.actor, anio)
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(text=f"Aguinaldo exportado: {ruta}", text_color=t("SUCCESS"))


def main() -> None:
    """Punto de entrada de la interfaz gráfica."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = MarcacionApp()
    app.mainloop()


if __name__ == "__main__":
    main()