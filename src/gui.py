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
from functools import partial
from typing import Callable, Dict, List, Optional

import customtkinter as ctk
import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import auth
import reports
from clock_engine import ClockEngine
from database import Database

FONT = "Segoe UI"
MONO = "Consolas"

BG = "#0B0B0C"
CARD = "#15151A"
CARD_BORDER = "#1E1E24"
INPUT_BG = "#191920"
INPUT_BORDER = "#26262C"
PRIMARY = "#1A56DB"
PRIMARY_HOVER = "#2E66E8"
TEXT = "#F2F2EE"
MUTED = "#8E8E96"
SUCCESS = "#4ADE80"
DANGER = "#F0544F"
ACCENTO = "#F5C26B"

DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def tarjeta(master: ctk.CTkFrame, **kwargs) -> ctk.CTkFrame:
    """Crea una tarjeta flotante con la identidad visual del sistema."""
    return ctk.CTkFrame(
        master,
        fg_color=CARD,
        corner_radius=16,
        border_width=1,
        border_color=CARD_BORDER,
        **kwargs,
    )


def boton_primario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Botón de acción principal en azul eléctrico con transición al hover."""
    return ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color=PRIMARY,
        hover_color=PRIMARY_HOVER,
        text_color="white",
        font=(FONT, 15, "bold"),
        corner_radius=8,
        height=44,
    )


def boton_secundario(master, texto: str, comando: Callable) -> ctk.CTkButton:
    """Botón de contorno en azul para acciones secundarias."""
    return ctk.CTkButton(
        master,
        text=texto,
        command=comando,
        fg_color="transparent",
        hover_color=PRIMARY_HOVER,
        border_width=2,
        border_color=PRIMARY,
        text_color=PRIMARY,
        font=(FONT, 15, "bold"),
        corner_radius=8,
        height=44,
    )


def entrada(master, placeholder: str, ancho: int = 320) -> ctk.CTkEntry:
    """Campo de texto estilizado de la interfaz."""
    return ctk.CTkEntry(
        master,
        placeholder_text=placeholder,
        font=(FONT, 16),
        fg_color=INPUT_BG,
        border_color=INPUT_BORDER,
        text_color=TEXT,
        corner_radius=10,
        height=46,
        width=ancho,
    )


def etiqueta(master, texto: str, tamano: int = 14, color: str = TEXT, peso: str = "normal") -> ctk.CTkLabel:
    """Etiqueta tipográfica limpia del sistema."""
    return ctk.CTkLabel(
        master, text=texto, font=(FONT, tamano, peso), text_color=color
    )


class MarcacionApp(ctk.CTk):
    """Ventana principal que alterna entre recepción y gestión."""

    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.db.initialize()
        self.actor: Optional[Dict] = None
        self.panel_gestion: Optional[ctk.CTkFrame] = None
        self._configurar_ventana()
        self._construir_vista_publica()
        self._mostrar_dos_puntos = True
        self._actualizar_reloj()
        self.after(500, self._alternar_dos_puntos)

    def _configurar_ventana(self) -> None:
        self.title("Sistema de Marcación · Paraguay")
        self.geometry("1080x760")
        self.minsize(960, 680)
        self.configure(fg_color=BG)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

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
        etiqueta(cabecera, "Sistema de Marcación", 22, TEXT, "bold").grid(
            row=0, column=0, sticky="w"
        )
        etiqueta(
            cabecera, "Cumplimiento Ley N.º 213/93 · Paraguay", 13, MUTED
        ).grid(row=1, column=0, sticky="w")

        contenido = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        contenido.grid(row=1, column=0, sticky="nsew", padx=24, pady=24)
        contenido.grid_columnconfigure(0, weight=1)
        contenido.grid_rowconfigure(0, weight=1)

        self._construir_tarjeta_reloj(contenido)
        self._construir_tarjeta_marcacion(contenido)
        self._construir_tarjeta_ticket(contenido)

        self.pie = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        self.pie.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))
        self.pie.grid_columnconfigure(0, weight=1)
        boton_consulta = ctk.CTkButton(
            self.pie,
            text="Consultar mis Marcas Localmente",
            command=self._abrir_consulta_local,
            fg_color="transparent",
            hover_color=INPUT_BG,
            text_color=MUTED,
            font=(FONT, 12),
            corner_radius=8,
            width=200,
            height=32,
        )
        boton_consulta.grid(row=0, column=0, sticky="w")
        boton_acceso = ctk.CTkButton(
            self.pie,
            text="Acceso de Gestión",
            command=self._abrir_login,
            fg_color="transparent",
            hover_color=INPUT_BG,
            text_color=MUTED,
            font=(FONT, 12),
            corner_radius=8,
            width=140,
            height=32,
        )
        boton_acceso.grid(row=0, column=1, sticky="e")

    def _construir_tarjeta_reloj(self, master: ctk.CTkFrame) -> None:
        tarjeta_reloj = tarjeta(master)
        tarjeta_reloj.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        tarjeta_reloj.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_reloj, "Recepción · Marque su asistencia", 13, MUTED).grid(
            row=0, column=0, pady=(18, 0)
        )
        self.lbl_hora = ctk.CTkLabel(
            tarjeta_reloj,
            text="--:--:--",
            font=(MONO, 76, "bold"),
            text_color=TEXT,
        )
        self.lbl_hora.grid(row=1, column=0, pady=(4, 0))
        self.lbl_fecha = etiqueta(tarjeta_reloj, "", 16, MUTED)
        self.lbl_fecha.grid(row=2, column=0, pady=(0, 18))

    def _construir_tarjeta_marcacion(self, master: ctk.CTkFrame) -> None:
        self.tarjeta_marcacion = tarjeta(master)
        self.tarjeta_marcacion.grid(row=1, column=0, sticky="ew", pady=(0, 24))
        self.tarjeta_marcacion.grid_columnconfigure(0, weight=1)
        etiqueta(
            self.tarjeta_marcacion, "Ingrese su cédula o nombre de usuario", 16, TEXT
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
            text_color=MUTED,
            progress_color=PRIMARY,
            fg_color=INPUT_BG,
        ).pack(side="left")
        etiqueta(
            self.tarjeta_marcacion,
            "El sistema detecta automáticamente si corresponde Entrada o Salida",
            12,
            MUTED,
        ).grid(row=4, column=0, pady=(0, 14))
        self.lbl_estado = etiqueta(self.tarjeta_marcacion, "", 14, SUCCESS)
        self.lbl_estado.grid(row=5, column=0, pady=(0, 18))

    def _construir_tarjeta_ticket(self, master: ctk.CTkFrame) -> None:
        self.tarjeta_ticket = tarjeta(master)
        self.tarjeta_ticket.grid(row=2, column=0, sticky="ew")
        self.tarjeta_ticket.grid_columnconfigure(0, weight=1)
        etiqueta(self.tarjeta_ticket, "Último comprobante criptográfico", 13, MUTED).grid(
            row=0, column=0, sticky="w", padx=20, pady=(16, 10)
        )
        self.ticket_box = ctk.CTkTextbox(
            self.tarjeta_ticket,
            font=(MONO, 12),
            fg_color=INPUT_BG,
            text_color=TEXT,
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
            self._mostrar_estado("Ingrese su cédula o usuario.", DANGER)
            return
        user = self.db.get_user_by_username(username)
        if not user:
            self._mostrar_estado("Empleado no encontrado. Verifique su cédula.", DANGER)
            return
        engine = ClockEngine(self.db, user)
        try:
            entry_id, momento, tipo = engine.registrar_asistencia(
                es_dia_lluvioso=self.dia_lluvioso.get()
            )
        except ValueError as error:
            self._mostrar_estado(str(error), DANGER)
            return
        ticket = reports.comprobante_marcacion(entry_id, momento, tipo)
        self.ticket_box.delete("1.0", "end")
        self.ticket_box.insert("1.0", ticket)
        self.entrada_id.delete(0, "end")
        self._mostrar_estado(
            f"{user['full_name']}: {tipo.lower()} registrada correctamente.", SUCCESS
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
        etiqueta(self.panel_exito, "✓", 54, SUCCESS, "bold").grid(
            row=0, column=0, pady=(34, 0)
        )
        etiqueta(self.panel_exito, f"¡{tipo} Registrada!", 24, TEXT, "bold").grid(
            row=1, column=0, pady=(6, 0)
        )
        etiqueta(
            self.panel_exito, "Comprobante criptográfico · SHA-256", 12, MUTED
        ).grid(row=2, column=0, pady=(2, 10))
        caja_ticket = ctk.CTkTextbox(
            self.panel_exito,
            font=(MONO, 11),
            fg_color=INPUT_BG,
            text_color=MUTED,
            corner_radius=12,
            height=110,
            wrap="word",
        )
        caja_ticket.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 8))
        caja_ticket.insert("1.0", ticket)
        caja_ticket.configure(state="disabled")
        etiqueta(
            self.panel_exito, "Volviendo a recepción…", 11, MUTED
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
    def _abrir_login(self) -> None:
        LoginModal(self, self.db, self._ingresar_gestion)

    def _abrir_consulta_local(self) -> None:
        ConsultaLocalModal(self, self.db)

    def _ingresar_gestion(self, actor: Dict) -> None:
        self.actor = actor
        self.frame_publico.grid_forget()
        self.panel_gestion = PanelGestion(self, self.db, actor, self._volver_publico)
        self.panel_gestion.grid(row=0, column=0, sticky="nsew")

    def _volver_publico(self) -> None:
        if self.panel_gestion is not None:
            self.panel_gestion.destroy()
            self.panel_gestion = None
        self.actor = None
        self.frame_publico.grid(row=0, column=0, sticky="nsew")


class ConsultaLocalModal(ctk.CTkToplevel):
    """Autoservicio local: historial por rango de fechas y aguinaldo.

    El empleado digita su cédula y elige el período con accesos rápidos
    (Mes Actual, Últimos 3 Meses, Desde Enero) o los selectores manuales;
    el botón "Hoy" fija ambos extremos en la fecha actual del equipo.
    """

    def __init__(self, master: MarcacionApp, db: Database) -> None:
        super().__init__(master)
        self.db = db
        self.title("Consulta Local de Marcas")
        self.geometry("600x740")
        self.configure(fg_color=BG)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        tarjeta_consulta = tarjeta(self)
        tarjeta_consulta.pack(fill="both", expand=True, padx=20, pady=20)
        etiqueta(tarjeta_consulta, "Consulta Local de Marcas", 20, TEXT, "bold").pack(
            pady=(20, 4)
        )
        etiqueta(
            tarjeta_consulta, "Autoservicio del empleado en esta PC", 12, MUTED
        ).pack(pady=(0, 16))

        self.entrada_cedula = entrada(tarjeta_consulta, "Su cédula o usuario", ancho=420)
        self.entrada_cedula.pack(pady=5)
        fila_rango = ctk.CTkFrame(tarjeta_consulta, fg_color="transparent")
        fila_rango.pack(pady=5)
        self.entrada_desde = entrada(fila_rango, "Desde · 1 de enero", ancho=200)
        self.entrada_desde.insert(0, datetime.date(datetime.date.today().year, 1, 1).isoformat())
        self.entrada_desde.pack(side="left", padx=(0, 6))
        self.entrada_hasta = entrada(fila_rango, "Hasta · hoy", ancho=200)
        self.entrada_hasta.insert(0, datetime.date.today().isoformat())
        self.entrada_hasta.pack(side="left", padx=(6, 0))
        fila_accesos = ctk.CTkFrame(tarjeta_consulta, fg_color="transparent")
        fila_accesos.pack(pady=(10, 0))
        for texto, comando in (
            ("Mes Actual", self._mes_actual),
            ("Últimos 3 Meses", self._ultimos_3_meses),
            ("Desde Enero", self._desde_enero),
        ):
            ctk.CTkButton(
                fila_accesos,
                text=texto,
                command=comando,
                fg_color=INPUT_BG,
                hover_color=PRIMARY_HOVER,
                text_color=MUTED,
                font=(FONT, 12),
                corner_radius=8,
                width=130,
                height=36,
            ).pack(side="left", padx=4)
        fila_botones = ctk.CTkFrame(tarjeta_consulta, fg_color="transparent")
        fila_botones.pack(pady=(10, 0))
        boton_hoy = ctk.CTkButton(
            fila_botones,
            text="Hoy",
            command=self._hoy,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            text_color="white",
            font=(FONT, 14, "bold"),
            corner_radius=10,
            width=100,
            height=44,
        )
        boton_hoy.pack(side="left", padx=6)
        boton_primario(fila_botones, "Consultar Historial", self._consultar).pack(
            side="left", padx=6
        )
        self.lbl_error = etiqueta(tarjeta_consulta, "", 12, DANGER)
        self.lbl_error.pack(pady=(8, 4))
        self.texto_resultado = ctk.CTkTextbox(
            tarjeta_consulta,
            font=(MONO, 12),
            fg_color=INPUT_BG,
            text_color=TEXT,
            corner_radius=10,
            height=280,
            wrap="word",
        )
        self.texto_resultado.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.entrada_cedula.focus_set()
        self.attributes("-alpha", 0.0)
        self.after(10, lambda: self._desvanecer(0))

    def _desvanecer(self, paso: int) -> None:
        """Anima la entrada del modal con una transición suave de opacidad."""
        self.attributes("-alpha", min(1.0, 0.3 + paso * 0.14))
        if paso < 5:
            self.after(22, lambda: self._desvanecer(paso + 1))

    def _hoy(self) -> None:
        """Captura la fecha actual del sistema en ambos extremos y consulta."""
        hoy = datetime.date.today().isoformat()
        self.entrada_desde.delete(0, "end")
        self.entrada_desde.insert(0, hoy)
        self.entrada_hasta.delete(0, "end")
        self.entrada_hasta.insert(0, hoy)
        self._consultar()

    def _aplicar_rango(self, desde: datetime.date) -> None:
        """Autocompleta el rango hasta hoy y refresca la consulta al instante."""
        self.entrada_desde.delete(0, "end")
        self.entrada_desde.insert(0, desde.isoformat())
        self.entrada_hasta.delete(0, "end")
        self.entrada_hasta.insert(0, datetime.date.today().isoformat())
        self._consultar()

    def _mes_actual(self) -> None:
        """Rango del primer día del mes en curso hasta hoy."""
        hoy = datetime.date.today()
        self._aplicar_rango(datetime.date(hoy.year, hoy.month, 1))

    def _ultimos_3_meses(self) -> None:
        """Rango de los últimos tres meses (recortado a enero si cruza el año)."""
        hoy = datetime.date.today()
        mes_inicio = hoy.month - 2
        anio_inicio = hoy.year
        if mes_inicio < 1:
            mes_inicio += 12
            anio_inicio -= 1
        if anio_inicio < hoy.year:
            self._aplicar_rango(datetime.date(hoy.year, 1, 1))
            return
        self._aplicar_rango(datetime.date(anio_inicio, mes_inicio, 1))

    def _desde_enero(self) -> None:
        """Rango desde el 1 de enero del año en curso hasta hoy."""
        hoy = datetime.date.today()
        self._aplicar_rango(datetime.date(hoy.year, 1, 1))

    def _consultar(self) -> None:
        cedula = self.entrada_cedula.get().strip()
        if not cedula:
            self.lbl_error.configure(text="Ingrese su cédula o usuario.")
            return
        user = self.db.get_user_by_username(cedula)
        if not user:
            self.lbl_error.configure(text="Empleado no encontrado. Verifique su cédula.")
            return
        try:
            desde = datetime.date.fromisoformat(self.entrada_desde.get().strip())
            hasta = datetime.date.fromisoformat(self.entrada_hasta.get().strip())
        except ValueError:
            self.lbl_error.configure(text="Fechas inválidas. Use el formato AAAA-MM-DD.")
            return
        hoy = datetime.date.today()
        inicio_anio = datetime.date(hoy.year, 1, 1)
        if desde < inicio_anio:
            self.lbl_error.configure(text="El rango inicia en enero del año en curso.")
            return
        if hasta > hoy:
            self.lbl_error.configure(text="La fecha hasta no puede superar el día de hoy.")
            return
        if hasta < desde:
            self.lbl_error.configure(text="La fecha hasta no puede ser anterior a la de desde.")
            return
        try:
            resumen = reports.resumen_historico(self.db, user, desde, hasta)
        except ValueError as error:
            self.lbl_error.configure(text=str(error))
            return
        self.lbl_error.configure(text="")
        self.texto_resultado.delete("1.0", "end")
        self.texto_resultado.insert("1.0", self._formatear(resumen))

    @staticmethod
    def _formatear(resumen: Dict) -> str:
        """Convierte el histórico JSON en el reporte legible del modal."""
        def gs(valor: float) -> str:
            return f"Gs. {int(round(valor)):,}".replace(",", ".")

        lineas = [
            f"{resumen['nombre']} · {resumen['desde']} → {resumen['hasta']}",
            "=" * 44,
            "HISTORIAL DE MARCAS",
        ]
        if not resumen["marcas"]:
            lineas.append("  Sin marcas registradas en el período.")
        for marca in resumen["marcas"]:
            estado = marca["salida"] or "en curso"
            sufijo = f" [{marca['incidencia']}]" if marca["incidencia"] else ""
            sufijo += " [Feriado]" if marca["feriado"] else ""
            lineas.append(f"  {marca['fecha']} {marca['entrada']} → {estado}{sufijo}")
            lineas.append(
                f"    Ordinarias {marca['ordinarias']} | Extra 50% {marca['extra_50']} "
                f"| Extra 100% {marca['extra_100']}"
            )
        extras = resumen["extras_periodo"]
        aguinaldo = resumen["aguinaldo_periodo"]
        lineas.extend(
            [
                "HORAS EXTRA DEL PERÍODO",
                f"  Recargo 50%: {extras['texto_50']} ({extras['horas_50']:.2f} h)",
                f"  Recargo 100%: {extras['texto_100']} ({extras['horas_100']:.2f} h)",
                "AGUINALDO DEVENGADO (Ley 6380/2019)",
                f"  Meses del período: {aguinaldo['meses_periodo']}",
                f"  Valor horas extra: {gs(aguinaldo['valor_extras'])}",
                f"  TOTAL: {gs(aguinaldo['aguinaldo'])}",
            ]
        )
        return "\n".join(lineas)


class LoginModal(ctk.CTkToplevel):
    """Modal flotante de credenciales para el acceso de gestión."""

    def __init__(
        self, master: MarcacionApp, db: Database, on_success: Callable[[Dict], None]
    ) -> None:
        super().__init__(master)
        self.db = db
        self.on_success = on_success
        self.title("Acceso de Gestión")
        self.geometry("420x380")
        self.configure(fg_color=BG)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        tarjeta_login = tarjeta(self)
        tarjeta_login.pack(fill="both", expand=True, padx=24, pady=24)
        etiqueta(tarjeta_login, "Acceso de Gestión", 22, TEXT, "bold").pack(
            pady=(26, 4)
        )
        etiqueta(tarjeta_login, "Solo Recursos Humanos o Administrador", 13, MUTED).pack(
            pady=(0, 22)
        )
        self.entrada_usuario = entrada(tarjeta_login, "Usuario")
        self.entrada_usuario.pack(pady=6)
        self.entrada_clave = entrada(tarjeta_login, "Contraseña")
        self.entrada_clave.configure(show="•")
        self.entrada_clave.pack(pady=6)
        boton_primario(tarjeta_login, "Ingresar", self._ingresar).pack(pady=(18, 8))
        self.lbl_error = etiqueta(tarjeta_login, "", 13, DANGER)
        self.lbl_error.pack(pady=(0, 20))
        self.bind("<Return>", lambda _e: self._ingresar())
        self.entrada_usuario.focus_set()

    def _ingresar(self) -> None:
        usuario = self.entrada_usuario.get().strip()
        clave = self.entrada_clave.get()
        user = auth.authenticate(self.db, usuario, clave)
        if not user:
            self.lbl_error.configure(text="Credenciales incorrectas.")
            return
        rol = auth.get_role_name(self.db, user)
        if rol not in auth.ROLES_GESTION_USUARIOS:
            self.lbl_error.configure(text="Solo RRHH o Administrador pueden gestionar.")
            return
        self.destroy()
        self.on_success(user)


class PanelGestion(ctk.CTkFrame):
    """Panel protegido de RRHH/Administrador con navegación lateral minimalista."""

    SECCIONES: List[tuple] = [
        ("▦  Personal", "Gestión de Personal"),
        ("✦  Justificaciones", "Justificaciones y Permisos"),
        ("▤  Reportes", "Centro de Reportes"),
        ("✎  Correcciones", "Solicitudes de Corrección"),
        ("◉  Analítica", "Dashboard Analítico"),
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
        self._seleccionar(0)

    def _construir_sidebar(self) -> None:
        sidebar = tarjeta(self)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(24, 12), pady=24)
        sidebar.grid_columnconfigure(0, weight=1)
        etiqueta(sidebar, "Panel de Gestión", 17, TEXT, "bold").grid(
            row=0, column=0, sticky="w", padx=16, pady=(18, 2)
        )
        etiqueta(
            sidebar,
            f"{self.actor['full_name']}\n{auth.get_role_name(self.db, self.actor)}",
            11,
            MUTED,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 16))
        self.botones_seccion: List[ctk.CTkButton] = []
        for indice, (icono, titulo) in enumerate(self.SECCIONES):
            boton = ctk.CTkButton(
                sidebar,
                text=f"{icono}  {titulo}",
                command=lambda i=indice: self._seleccionar(i),
                fg_color="transparent",
                hover_color=INPUT_BG,
                text_color=MUTED,
                font=(FONT, 13),
                corner_radius=8,
                height=42,
                anchor="w",
            )
            boton.grid(row=2 + indice, column=0, sticky="ew", padx=10, pady=3)
            self.botones_seccion.append(boton)
        boton_secundario(sidebar, "Volver a Marcación", self.on_cerrar).grid(
            row=8, column=0, sticky="ew", padx=10, pady=(16, 14)
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
        self.pestanas = [
            self.personal_tab,
            self.justificaciones_tab,
            self.reportes_tab,
            self.correcciones_tab,
            self.dashboard_tab,
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
                fg_color=PRIMARY if seleccionado else "transparent",
                text_color="white" if seleccionado else MUTED,
                hover_color=PRIMARY_HOVER if seleccionado else INPUT_BG,
            )

    def _refrescar_empleados(self) -> None:
        self.justificaciones_tab.refrescar_empleados()


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
            TEXT,
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 4))
        etiqueta(
            cabecera,
            "Al aprobar, el marcaje se corrige en PostgreSQL y queda trazado en la auditoría",
            12,
            MUTED,
        ).grid(row=1, column=0, sticky="w", padx=20)
        boton_refrescar = ctk.CTkButton(
            cabecera,
            text="Refrescar",
            command=self._refrescar,
            width=100,
            height=32,
            font=(FONT, 12),
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            corner_radius=8,
        )
        boton_refrescar.grid(row=0, column=1, rowspan=2, padx=16, sticky="e")
        self.lbl_resultado = etiqueta(cabecera, "", 12, SUCCESS)
        self.lbl_resultado.grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 12))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        solicitudes = self.db.listar_solicitudes_correccion()
        if not solicitudes:
            etiqueta(self.scroll, "No hay solicitudes de corrección.", 13, MUTED).pack(pady=20)
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
                TEXT,
                "bold",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
            estado = solicitud["estado"]
            if solicitud["revisor"]:
                estado += f" · {solicitud['revisor']}"
            etiqueta(
                fila,
                f"{solicitud['tipo_marca']} a las {solicitud['hora_propuesta']} · {estado}",
                12,
                MUTED,
            ).grid(row=1, column=0, sticky="w", padx=14)
            etiqueta(fila, f"Motivo: {solicitud['motivo']}", 12, TEXT).grid(
                row=2, column=0, sticky="w", padx=14, pady=(2, 10)
            )
            if solicitud["estado"] == "Pendiente":
                boton_aprobar = ctk.CTkButton(
                    fila,
                    text="Aprobar",
                    width=90,
                    height=32,
                    font=(FONT, 12),
                    fg_color=SUCCESS,
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
                    hover_color=DANGER,
                    border_width=1,
                    border_color=DANGER,
                    text_color=DANGER,
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
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(
            text=f"Solicitud #{solicitud_id} {estado.lower()} con auditoría.",
            text_color=SUCCESS,
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
            cabecera, "Dashboard Analítico de Recursos Humanos", 16, TEXT, "bold"
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 2))
        self.lbl_actualizado = etiqueta(
            cabecera, "Cargando métricas…", 12, MUTED
        )
        self.lbl_actualizado.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))
        ctk.CTkButton(
            cabecera,
            text="Actualizar",
            command=self._refrescar,
            width=110,
            height=34,
            font=(FONT, 12),
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
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
            MUTED,
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(18, 2))
        total = self.aguinaldo["total_acumulado_g"]
        millones = total / 1_000_000
        etiqueta(
            tarjeta_aguinaldo,
            f"Gs. {total:,}".replace(",", "."),
            38,
            TEXT,
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
        etiqueta(tarjeta_aguinaldo, resumen, 13, MUTED).grid(
            row=3, column=0, sticky="w", padx=24, pady=(0, 6)
        )
        partes = [
            f"{dep}: Gs. {datos['acumulado_g']:,}".replace(",", ".")
            for dep, datos in self.aguinaldo["por_departamento"].items()
        ]
        if partes:
            etiqueta(tarjeta_aguinaldo, " · ".join(partes), 12, MUTED).grid(
                row=4, column=0, sticky="w", padx=24, pady=(0, 18)
            )

    def _crear_figura(self, ancho: float, alto: float) -> Figure:
        return Figure(figsize=(ancho, alto), facecolor=BG)

    def _ajustar_figura(self, figura: Figure) -> None:
        figura.subplots_adjust(left=0.14, right=0.96, top=0.9, bottom=0.16)

    def _estilizar_ejes(self, eje) -> None:
        eje.set_facecolor(BG)
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
            TEXT,
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
                ha="center", va="center", color=MUTED, fontsize=12,
                transform=eje.transAxes,
            )
        else:
            eje.plot(
                dias, cantidades, color=PRIMARY, linewidth=2.5,
                marker="o", markersize=5, markerfacecolor=TEXT,
            )
            eje.fill_between(dias, cantidades, color=PRIMARY, alpha=0.12)
            pico = max(cantidades)
            if pico > 0:
                dia_pico = dias[cantidades.index(pico)]
                eje.scatter([dia_pico], [pico], s=90, color=DANGER, zorder=5)
                eje.annotate(
                    f"Pico: {pico}",
                    xy=(dia_pico, pico), xytext=(6, 12),
                    textcoords="offset points", color=DANGER, fontsize=10, fontweight="bold",
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
            TEXT,
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
                ha="center", va="center", color=MUTED, fontsize=12,
                transform=eje.transAxes,
            )
        else:
            posiciones = range(len(departamentos))
            ancho_barra = 0.38
            eje.bar(
                [p - ancho_barra / 2 for p in posiciones],
                [e["horas_50"] for e in self.extras],
                width=ancho_barra, color=PRIMARY, label="Recargo 50%",
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
                    color=TEXT, fontsize=9,
                )
                eje.text(
                    indice + ancho_barra / 2, extra["horas_100"] + 0.3,
                    f"{extra['horas_100']:.1f}", ha="center", va="bottom",
                    color=TEXT, fontsize=9,
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
    """Formulario de alta de personal y listado con edición/eliminación."""

    def __init__(
        self, master, db: Database, actor: Dict, on_cambio: Callable
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.on_cambio = on_cambio
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._construir_formulario()
        self._construir_listado()

    def _construir_formulario(self) -> None:
        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        etiqueta(formulario, "Agregar empleado", 17, TEXT, "bold").pack(
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
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
            width=280,
            height=40,
        )
        self.menu_rol.pack(pady=5)
        self.menu_vinculo = ctk.CTkOptionMenu(
            formulario,
            values=list(auth.TIPOS_VINCULO),
            font=(FONT, 14),
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
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
        self.lbl_resultado = etiqueta(formulario, "", 12, SUCCESS)
        self.lbl_resultado.pack(pady=(0, 16))

    def _construir_listado(self) -> None:
        listado = tarjeta(self)
        listado.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        listado.grid_columnconfigure(0, weight=1)
        listado.grid_rowconfigure(1, weight=1)
        etiqueta(listado, "Personal registrado", 17, TEXT, "bold").grid(
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
            fila = ctk.CTkFrame(self.scroll, fg_color=INPUT_BG, corner_radius=10)
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
                ACCENTO if vinculo == "Pasante" else MUTED,
            ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
            boton_editar = ctk.CTkButton(
                fila,
                text="Editar",
                width=70,
                height=30,
                font=(FONT, 12),
                fg_color=PRIMARY,
                hover_color=PRIMARY_HOVER,
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
                hover_color=DANGER,
                border_width=1,
                border_color=DANGER,
                text_color=DANGER,
                corner_radius=8,
                command=partial(self._eliminar, usuario),
            )
            boton_eliminar.grid(row=0, column=2, padx=(0, 10))

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
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(text="Empleado agregado correctamente.", text_color=SUCCESS)
        for campo in (self.ent_usuario, self.ent_nombre, self.ent_salario, self.ent_clave):
            campo.delete(0, "end")
        self._refrescar()
        self.on_cambio()

    def _editar(self, usuario: Dict) -> None:
        EditEmployeeModal(self, self.db, self.actor, usuario, self._refrescar, self.on_cambio)

    def _eliminar(self, usuario: Dict) -> None:
        try:
            auth.delete_user(self.db, self.actor, usuario["id"])
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(text="Empleado eliminado.", text_color=SUCCESS)
        self._refrescar()
        self.on_cambio()


class EditEmployeeModal(ctk.CTkToplevel):
    """Modal para editar rol, salario o contraseña de un empleado."""

    def __init__(
        self,
        master: ctk.CTkFrame,
        db: Database,
        actor: Dict,
        usuario: Dict,
        on_guardar: Callable,
        on_cambio: Callable,
    ) -> None:
        super().__init__(master)
        self.db = db
        self.actor = actor
        self.usuario = usuario
        self.on_guardar = on_guardar
        self.on_cambio = on_cambio
        self.title(f"Editar: {usuario['username']}")
        self.geometry("380x460")
        self.configure(fg_color=BG)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.resizable(False, False)

        formulario = tarjeta(self)
        formulario.pack(fill="both", expand=True, padx=20, pady=20)
        etiqueta(formulario, f"Editando a {usuario['full_name']}", 17, TEXT, "bold").pack(
            pady=(18, 14)
        )
        self.ent_salario = entrada(formulario, "Salario mensual (Gs.)", ancho=300)
        self.ent_salario.insert(0, f"{float(usuario['salario_mensual'] or 0):,.0f}")
        self.ent_salario.pack(pady=5)
        self.menu_rol = ctk.CTkOptionMenu(
            formulario,
            values=[r["nombre"] for r in self.db.list_roles()],
            font=(FONT, 14),
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
            width=300,
            height=40,
        )
        self.menu_rol.set(usuario["role_name"])
        self.menu_rol.pack(pady=5)
        self.menu_vinculo = ctk.CTkOptionMenu(
            formulario,
            values=list(auth.TIPOS_VINCULO),
            font=(FONT, 14),
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
            width=300,
            height=40,
        )
        self.menu_vinculo.set(usuario.get("tipo_vinculo") or "Funcionario")
        self.menu_vinculo.pack(pady=5)
        self.ent_clave = entrada(formulario, "Nueva contraseña (opcional)", ancho=300)
        self.ent_clave.configure(show="•")
        self.ent_clave.pack(pady=5)
        boton_primario(formulario, "Guardar cambios", self._guardar).pack(pady=(18, 6))
        self.lbl_error = etiqueta(formulario, "", 12, DANGER)
        self.lbl_error.pack(pady=(0, 16))

    def _guardar(self) -> None:
        salario_raw = self.ent_salario.get().replace(",", "").strip()
        try:
            salario = float(salario_raw) if salario_raw else None
        except ValueError:
            salario = None
        clave = self.ent_clave.get() or None
        try:
            auth.update_user(
                self.db,
                self.actor,
                self.usuario["id"],
                password=clave,
                role_name=self.menu_rol.get(),
                salario_mensual=salario,
                tipo_vinculo=self.menu_vinculo.get(),
            )
        except (ValueError, PermissionError) as error:
            self.lbl_error.configure(text=str(error))
            return
        self.on_guardar()
        self.on_cambio()
        self.destroy()


class JustificacionesTab(ctk.CTkFrame):
    """Panel de justificaciones aprobadas (Vacaciones/Reposo/Permiso)."""

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.empleados: List[Dict] = []
        self.grid_columnconfigure(0, weight=1)

        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="ew")
        formulario.grid_columnconfigure(0, weight=1)
        etiqueta(formulario, "Registrar justificación aprobada", 17, TEXT, "bold").grid(
            row=0, column=0, pady=(18, 14)
        )
        self.menu_empleado = ctk.CTkOptionMenu(
            formulario,
            values=[""],
            font=(FONT, 14),
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
            width=420,
            height=40,
        )
        self.menu_empleado.grid(row=1, column=0, pady=5)
        self.menu_tipo = ctk.CTkOptionMenu(
            formulario,
            values=list(auth.TIPOS_PERMISO),
            font=(FONT, 14),
            fg_color=INPUT_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=PRIMARY,
            width=420,
            height=40,
        )
        self.menu_tipo.grid(row=2, column=0, pady=5)
        self.ent_inicio = entrada(formulario, "Fecha inicio (AAAA-MM-DD)", ancho=420)
        self.ent_inicio.grid(row=3, column=0, pady=5)
        self.ent_fin = entrada(formulario, "Fecha fin (AAAA-MM-DD)", ancho=420)
        self.ent_fin.grid(row=4, column=0, pady=5)
        boton_primario(formulario, "Registrar Justificación", self._crear).grid(
            row=5, column=0, pady=(16, 8)
        )
        self.lbl_resultado = etiqueta(formulario, "", 13, SUCCESS)
        self.lbl_resultado.grid(row=6, column=0, pady=(0, 18))
        self.refrescar_empleados()

    def refrescar_empleados(self) -> None:
        self.empleados = self.db.list_users()
        self.menu_empleado.configure(
            values=[f"{e['username']} ({e['full_name']})" for e in self.empleados]
        )
        if self.empleados:
            self.menu_empleado.set(
                f"{self.empleados[0]['username']} ({self.empleados[0]['full_name']})"
            )

    def _crear(self) -> None:
        seleccion = self.menu_empleado.get()
        empleado = next(
            (e for e in self.empleados if f"{e['username']} ({e['full_name']})" == seleccion),
            None,
        )
        if not empleado:
            self.lbl_resultado.configure(text="Seleccione un empleado.", text_color=DANGER)
            return
        try:
            inicio = datetime.date.fromisoformat(self.ent_inicio.get().strip())
            fin = datetime.date.fromisoformat(self.ent_fin.get().strip())
            justificacion_id = auth.crear_justificacion(
                self.db, self.actor, empleado["id"], self.menu_tipo.get(), inicio, fin
            )
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(
            text=f"Justificación #{justificacion_id} aprobada para {empleado['full_name']}.",
            text_color=SUCCESS,
        )


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
        etiqueta(tarjeta_asistencia, "Reporte Mensual de Asistencia", 17, TEXT, "bold").grid(
            row=0, column=0, pady=(20, 4)
        )
        etiqueta(
            tarjeta_asistencia,
            "Desglose de horas ordinarias, extra 50% y extra 100% para contabilidad",
            13,
            MUTED,
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
        etiqueta(tarjeta_aguinaldo, "Proyección de Aguinaldos", 17, TEXT, "bold").grid(
            row=0, column=0, pady=(20, 4)
        )
        etiqueta(
            tarjeta_aguinaldo,
            "Aguinaldo proporcional (13.º salario, Ley 6380/2019)",
            13,
            MUTED,
        ).grid(row=1, column=0)
        self.ent_anio_agui = entrada(tarjeta_aguinaldo, "Año (2026)", ancho=140)
        self.ent_anio_agui.grid(row=2, column=0, pady=16)
        boton_primario(
            tarjeta_aguinaldo, "Proyectar Aguinaldos (Excel)", self._exportar_aguinaldo
        ).grid(row=3, column=0, pady=(0, 8))

        self.lbl_resultado = etiqueta(self, "", 13, SUCCESS)
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
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(text=f"Reporte exportado: {ruta}", text_color=SUCCESS)

    def _exportar_aguinaldo(self) -> None:
        try:
            anio = self._periodo(self.ent_anio_agui, None)[0]
            ruta = reports.exportar_aguinaldo(self.db, self.actor, anio)
        except (ValueError, PermissionError) as error:
            self.lbl_resultado.configure(text=str(error), text_color=DANGER)
            return
        self.lbl_resultado.configure(text=f"Aguinaldo exportado: {ruta}", text_color=SUCCESS)


def main() -> None:
    """Punto de entrada de la interfaz gráfica."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = MarcacionApp()
    app.mainloop()


if __name__ == "__main__":
    main()