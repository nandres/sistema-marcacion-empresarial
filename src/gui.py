"""Interfaz premium en CustomTkinter del Sistema de Marcación (Ley 213/93).

Dos modos de uso:
- Modo Recepción (pantalla pública por defecto): reloj digital en tiempo
  real, marcación por cédula/usuario y ticket criptográfico de reports.py.
- Modo Gestión (RRHH/Administrador): acceso mediante modal de credenciales
  autenticado con auth.py y panel protegido de tres pestañas (personal,
  justificaciones y reportes).
"""

from __future__ import annotations

import datetime
from functools import partial
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

import auth
import reports
from clock_engine import ClockEngine
from database import Database

FONT = "Segoe UI"
MONO = "Consolas"

BG = "#121214"
CARD = "#1B1B1F"
CARD_BORDER = "#26262C"
INPUT_BG = "#232329"
INPUT_BORDER = "#34343B"
PRIMARY = "#1A56DB"
PRIMARY_HOVER = "#2E66E8"
TEXT = "#F2F2EE"
MUTED = "#8E8E96"
SUCCESS = "#4ADE80"
DANGER = "#F0544F"

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
        corner_radius=12,
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
        corner_radius=12,
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
        corner_radius=12,
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
        self._actualizar_reloj()

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

        pie = ctk.CTkFrame(self.frame_publico, fg_color="transparent")
        pie.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))
        pie.grid_columnconfigure(0, weight=1)
        boton_consulta = ctk.CTkButton(
            pie,
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
            pie,
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
        etiqueta(tarjeta_reloj, "Recepción · Marque su entrada", 13, MUTED).grid(
            row=0, column=0, pady=(18, 0)
        )
        self.lbl_hora = ctk.CTkLabel(
            tarjeta_reloj,
            text="--:--:--",
            font=(FONT, 76, "bold"),
            text_color=TEXT,
        )
        self.lbl_hora.grid(row=1, column=0, pady=(4, 0))
        self.lbl_fecha = etiqueta(tarjeta_reloj, "", 16, MUTED)
        self.lbl_fecha.grid(row=2, column=0, pady=(0, 18))

    def _construir_tarjeta_marcacion(self, master: ctk.CTkFrame) -> None:
        tarjeta_marcacion = tarjeta(master)
        tarjeta_marcacion.grid(row=1, column=0, sticky="ew", pady=(0, 24))
        tarjeta_marcacion.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_marcacion, "Ingrese su cédula o nombre de usuario", 16, TEXT).grid(
            row=0, column=0, pady=(22, 12)
        )
        self.entrada_id = entrada(tarjeta_marcacion, "Ej. 1234567 o juan")
        self.entrada_id.grid(row=1, column=0, pady=(0, 16))
        self.entrada_id.bind("<Return>", lambda _e: self._marcar())
        boton_primario(tarjeta_marcacion, "REGISTRAR ASISTENCIA", self._marcar).grid(
            row=2, column=0, pady=(0, 6)
        )
        etiqueta(
            tarjeta_marcacion,
            "El sistema detecta automáticamente si corresponde Entrada o Salida",
            12,
            MUTED,
        ).grid(row=3, column=0, pady=(0, 14))
        self.lbl_estado = etiqueta(tarjeta_marcacion, "", 14, SUCCESS)
        self.lbl_estado.grid(row=4, column=0, pady=(0, 18))

    def _construir_tarjeta_ticket(self, master: ctk.CTkFrame) -> None:
        tarjeta_ticket = tarjeta(master)
        tarjeta_ticket.grid(row=2, column=0, sticky="ew")
        tarjeta_ticket.grid_columnconfigure(0, weight=1)
        etiqueta(tarjeta_ticket, "Último comprobante criptográfico", 13, MUTED).grid(
            row=0, column=0, sticky="w", padx=20, pady=(16, 10)
        )
        self.ticket_box = ctk.CTkTextbox(
            tarjeta_ticket,
            font=(MONO, 12),
            fg_color=INPUT_BG,
            text_color=TEXT,
            corner_radius=10,
            height=150,
            wrap="word",
        )
        self.ticket_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

    def _actualizar_reloj(self) -> None:
        ahora = datetime.datetime.now()
        self.lbl_hora.configure(text=ahora.strftime("%H:%M:%S"))
        self.lbl_fecha.configure(
            text=f"{DIAS[ahora.weekday()]}, {ahora.day} de {MESES[ahora.month - 1]} de {ahora.year}"
        )
        self.after(1000, self._actualizar_reloj)

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
            entry_id, momento, tipo = engine.registrar_asistencia()
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
    """Autoservicio local: marcas del día, horas extra del mes y aguinaldo.

    El empleado digita su cédula y pulsa "Hoy" para capturar la fecha actual
    de su equipo y ver al instante su historial diario sin salir de la PC.
    """

    def __init__(self, master: MarcacionApp, db: Database) -> None:
        super().__init__(master)
        self.db = db
        self.title("Consulta Local de Marcas")
        self.geometry("500x640")
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

        self.entrada_cedula = entrada(tarjeta_consulta, "Su cédula o usuario", ancho=380)
        self.entrada_cedula.pack(pady=5)
        fila_fecha = ctk.CTkFrame(tarjeta_consulta, fg_color="transparent")
        fila_fecha.pack(pady=5)
        self.entrada_fecha = entrada(fila_fecha, "AAAA-MM-DD", ancho=280)
        self.entrada_fecha.insert(0, datetime.date.today().isoformat())
        self.entrada_fecha.pack(side="left", padx=(0, 8))
        boton_hoy = ctk.CTkButton(
            fila_fecha,
            text="Hoy",
            command=self._hoy,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            text_color="white",
            font=(FONT, 14, "bold"),
            corner_radius=10,
            width=80,
            height=46,
        )
        boton_hoy.pack(side="left")
        boton_primario(tarjeta_consulta, "Consultar", self._consultar).pack(pady=(16, 6))
        self.lbl_error = etiqueta(tarjeta_consulta, "", 12, DANGER)
        self.lbl_error.pack(pady=(0, 6))
        self.texto_resultado = ctk.CTkTextbox(
            tarjeta_consulta,
            font=(MONO, 12),
            fg_color=INPUT_BG,
            text_color=TEXT,
            corner_radius=10,
            height=250,
            wrap="word",
        )
        self.texto_resultado.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.entrada_cedula.focus_set()

    def _hoy(self) -> None:
        """Captura la fecha actual del sistema y ejecuta la consulta."""
        self.entrada_fecha.delete(0, "end")
        self.entrada_fecha.insert(0, datetime.date.today().isoformat())
        self._consultar()

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
            fecha = datetime.date.fromisoformat(self.entrada_fecha.get().strip())
        except ValueError:
            self.lbl_error.configure(text="Fecha inválida. Use el formato AAAA-MM-DD.")
            return
        resumen = reports.resumen_consulta(self.db, user, fecha)
        self.lbl_error.configure(text="")
        self.texto_resultado.delete("1.0", "end")
        self.texto_resultado.insert("1.0", self._formatear(resumen))

    @staticmethod
    def _formatear(resumen: Dict) -> str:
        """Convierte el resumen JSON en el reporte legible del modal."""
        def gs(valor: float) -> str:
            return f"Gs. {int(round(valor)):,}".replace(",", ".")

        lineas = [f"{resumen['nombre']} · {resumen['fecha']}", "=" * 40]
        lineas.append("MARCAS DEL DÍA")
        if not resumen["marcas_dia"]:
            lineas.append("  Sin marcas registradas este día.")
        for marca in resumen["marcas_dia"]:
            estado = marca["salida"] or "en curso"
            etiquetas = []
            if marca["tardanza"]:
                etiquetas.append("Tardanza")
            if marca["feriado"]:
                etiquetas.append("Feriado")
            sufijo = f" [{', '.join(etiquetas)}]" if etiquetas else ""
            lineas.append(
                f"  #{marca['id']} Entrada {marca['entrada']} → Salida {estado}{sufijo}"
            )
            lineas.append(
                f"    Ordinarias {marca['ordinarias']} | Extra 50% {marca['extra_50']} "
                f"| Extra 100% {marca['extra_100']}"
            )
        extras = resumen["extras_mes"]
        lineas.extend(
            [
                "HORAS EXTRA DEL MES",
                f"  Recargo 50%: {extras['texto_50']} ({extras['horas_50']:.2f} h)",
                f"  Recargo 100%: {extras['texto_100']} ({extras['horas_100']:.2f} h)",
                "AGUINALDO PROPORCIONAL (Ley 6380/2019)",
            ]
        )
        aguinaldo = resumen["aguinaldo"]
        if aguinaldo is None:
            lineas.append("  Sin proyección para este año.")
        else:
            lineas.extend(
                [
                    f"  Salario mensual: {gs(aguinaldo['salario_mensual'])}",
                    f"  Meses trabajados: {aguinaldo['meses_trabajados']}",
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
    """Panel protegido de tres pestañas para RRHH/Administrador."""

    def __init__(
        self, master: MarcacionApp, db: Database, actor: Dict, on_cerrar: Callable
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.on_cerrar = on_cerrar
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        cabecera.grid_columnconfigure(0, weight=1)
        etiqueta(cabecera, "Panel de Gestión", 22, TEXT, "bold").grid(
            row=0, column=0, sticky="w"
        )
        etiqueta(
            cabecera,
            f"{actor['full_name']} · {auth.get_role_name(db, actor)}",
            13,
            MUTED,
        ).grid(row=1, column=0, sticky="w")
        boton_secundario(cabecera, "Volver a Marcación", on_cerrar).grid(
            row=0, column=1, rowspan=2, sticky="e"
        )

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=CARD,
            segmented_button_fg_color=INPUT_BG,
            segmented_button_selected_color=PRIMARY,
            segmented_button_selected_hover_color=PRIMARY_HOVER,
            segmented_button_unselected_color=INPUT_BG,
            segmented_button_unselected_hover_color=INPUT_BG,
            text_color=TEXT,
            corner_radius=12,
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=24, pady=24)

        tab_personal = self.tabs.add("Gestión de Personal")
        tab_justificaciones = self.tabs.add("Justificaciones y Permisos")
        tab_reportes = self.tabs.add("Centro de Reportes")

        self.personal_tab = PersonalTab(tab_personal, db, actor, self._refrescar_empleados)
        self.personal_tab.pack(fill="both", expand=True, padx=12, pady=12)
        self.justificaciones_tab = JustificacionesTab(tab_justificaciones, db, actor)
        self.justificaciones_tab.pack(fill="both", expand=True, padx=12, pady=12)
        self.reportes_tab = ReportesTab(tab_reportes, db, actor)
        self.reportes_tab.pack(fill="both", expand=True, padx=12, pady=12)

    def _refrescar_empleados(self) -> None:
        self.justificaciones_tab.refrescar_empleados()


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
            fila = ctk.CTkFrame(self.scroll, fg_color=INPUT_BG, corner_radius=10)
            fila.pack(fill="x", pady=4)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"{usuario['username']} · {usuario['full_name']} · "
                f"{usuario['role_name']} · Gs. {float(usuario['salario_mensual'] or 0):,.0f}",
                13,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=10)
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
        self.geometry("380x400")
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