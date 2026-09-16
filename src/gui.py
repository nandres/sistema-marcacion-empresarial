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
import os
from functools import partial
from typing import Callable, Dict, List, Optional

import customtkinter as ctk
import matplotlib

matplotlib.use("TkAgg")
import psycopg2
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import auth
import clock_engine
import facial

# Tres tipografías con oficios distintos: serif para los títulos, grotesca
# para el cuerpo y monoespaciada para toda cifra, que es lo que mantiene las
# columnas de horas alineadas.
import interfaz
import notifications
import reports
import sync_worker
from clock_engine import ClockEngine
from database import Database
from gestion import PanelGestion, descargar_pdf_permiso
from interfaz import (
    DIAS,
    FONT,
    MESES,
    MONO,
    RADIO,
    TEMAS,
    _buscar_switches_tema,
    _recolorear,
    boton_primario,
    boton_secundario,
    campo_fecha,
    cifra,
    entrada,
    etiqueta,
    interruptor_tema,
    t,
    tarjeta,
    titulo,
)
from offline_queue import ColaOffline


class MarcacionApp(ctk.CTk):
    """Ventana principal que alterna entre recepción y gestión."""

    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.db.initialize()
        # El escritorio atiende a una sola empresa: la de la sede donde está
        # instalado. `EMPRESA_ACTIVA` la elige cuando la base aloja a varias.
        preferida = os.getenv("EMPRESA_ACTIVA", "").strip()
        if preferida:
            self.db.usar_empresa(preferida)
        self.actor: Optional[Dict] = None
        self.panel_gestion: Optional[ctk.CTkFrame] = None
        self.variable_tema = ctk.BooleanVar(value=False)
        self._refrescos_tema: List[Callable] = []
        self.cola = ColaOffline()
        self._configurar_ventana()
        self._construir_vista_publica()
        sync_worker.iniciar_hilo(self.cola, db=self.db, al_aviso=self._alerta_desde_hilo)
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

    def cambiar_tema(self) -> None:
        """Aplica el tema elegido a toda la jerarquía visual al instante."""
        interfaz.TEMA_ACTIVO = ("claro" if self.variable_tema.get()
                               else "oscuro")
        ctk.set_appearance_mode("light" if interfaz.TEMA_ACTIVO == "claro" else "dark")
        anterior = TEMAS["claro" if interfaz.TEMA_ACTIVO == "oscuro" else "oscuro"]
        self.configure(fg_color=t("BG"))
        _recolorear(self, anterior, TEMAS[interfaz.TEMA_ACTIVO])
        for refresco in list(self._refrescos_tema):
            try:
                refresco()
            except Exception:
                continue
        for interruptor in _buscar_switches_tema(self):
            interruptor.configure(
                text="Modo Oscuro" if interfaz.TEMA_ACTIVO == "oscuro" else "Modo Claro"
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
        titulo(cabecera, "Sistema de Marcación", 22).grid(
            row=0, column=0, sticky="w"
        )
        etiqueta(
            cabecera, "Cumplimiento Ley N.º 213/93 · Res. 3028/2024", 13, t("MUTED")
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
            "Marque su asistencia en el kiosco · el Portal del Empleado y la "
            "Gestión requieren usuario y contraseña",
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
        titulo(tarjeta_login, "Iniciar sesión", 20).grid(
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
        titulo(tarjeta_cambio, "Cambiar contraseña", 20).grid(
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
        # La condición del día ya no se declara acá: la fija Recursos Humanos
        # para toda la plantilla. El kiosco solo la informa.
        self.lbl_condicion = etiqueta(self.tarjeta_marcacion, "", 12, t("ACCENTO"))
        self.lbl_condicion.grid(row=3, column=0, pady=(0, 6))
        etiqueta(
            self.tarjeta_marcacion,
            "El sistema detecta automáticamente si corresponde Entrada o Salida",
            12,
            t("MUTED"),
        ).grid(row=4, column=0, pady=(0, 14))
        self._actualizar_condicion_dia()
        self.lbl_estado = etiqueta(self.tarjeta_marcacion, "", 14, t("SUCCESS"))
        self.lbl_estado.grid(row=5, column=0, pady=(0, 18))
        self.lbl_cola = etiqueta(self.tarjeta_marcacion, "", 12, t("MUTED"))
        self.lbl_cola.grid(row=6, column=0, pady=(0, 14))
        self._actualizar_lbl_cola()

    def _construir_tarjeta_ticket(self, master: ctk.CTkFrame) -> None:
        self.tarjeta_ticket = tarjeta(master)
        self.tarjeta_ticket.grid(row=2, column=0, sticky="ew")
        self.tarjeta_ticket.grid_columnconfigure(0, weight=1)
        etiqueta(self.tarjeta_ticket, "Último comprobante criptográfico",
                 13, t("MUTED")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(16, 10)
        )
        self.ticket_box = ctk.CTkTextbox(
            self.tarjeta_ticket,
            font=(MONO, 12),
            fg_color=t("INPUT_BG"),
            text_color=t("TEXT"),
            corner_radius=RADIO,
            height=150,
            wrap="word",
        )
        self.ticket_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

    def _actualizar_reloj(self) -> None:
        ahora = datetime.datetime.now()
        self.hora_actual = ahora.strftime("%H:%M:%S")
        self._dibujar_hora()
        self.lbl_fecha.configure(
            text=f"{DIAS[ahora.weekday()]}, {ahora.day} de "
                 f"{MESES[ahora.month - 1]} de {ahora.year}"
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
        decision = self._verificar_rostro(user)
        if not decision.permitir:
            self._mostrar_estado(decision.motivo, t("DANGER"))
            return
        engine = ClockEngine(self.db, user)
        try:
            entry_id, momento, tipo = engine.registrar_asistencia(decision.marca)
        except ValueError as error:
            self._mostrar_estado(str(error), t("DANGER"))
            return
        except (psycopg2.Error, OSError):
            self._marcar_offline(username, decision.marca)
            return
        ticket = reports.comprobante_marcacion(entry_id, momento, tipo)
        self.ticket_box.delete("1.0", "end")
        self.ticket_box.insert("1.0", ticket)
        self.entrada_id.delete(0, "end")
        self._mostrar_estado(
            f"{user['full_name']}: {tipo.lower()} registrada correctamente.", t("SUCCESS")
        )
        if user.get("email"):
            notifications.enviar_correo_ticket(user["email"], ticket)
        self._mostrar_panel_exito(tipo, ticket)

    def _verificar_con_gesto(self, user: Dict) -> facial.Resultado:
        """Pide un gesto, captura la secuencia y recién ahí compara la identidad.

        El orden importa: si la prueba de vida no pasa, la identidad no se
        evalúa. Que la cara sea la correcta es justamente lo que una foto
        garantiza, así que confirmarlo primero no aporta nada.
        """
        desafio = facial.desafio_al_azar()
        self._mostrar_estado(
            f"Mirá la cámara y {desafio}…", t("ACCENTO")
        )
        self.update()
        cuadros = facial.capturar_secuencia()
        vida = facial.prueba_de_vida(cuadros, desafio)
        if not vida.verificada:
            return vida
        conmarca = next(
            (c for c in reversed(cuadros) if facial._detectar_rostros(c)), None
        )
        return facial.validar(self.db, user["id"], conmarca)

    def _verificar_rostro(self, user: Dict) -> facial.Decision:
        """Corre el control biométrico y traduce el veredicto en una decisión.

        El rechazo se audita como fraude y siempre bloquea. La imposibilidad
        de verificar —sin cámara, sin OpenCV o sin foto de referencia— sigue
        la política de ``BIOMETRIA_OBLIGATORIA``: nunca se da por verificada.
        """
        if not facial.disponible():
            resultado = facial.Resultado(
                facial.NO_VERIFICABLE, "El motor de visión no está instalado."
            )
        elif facial.con_prueba_de_vida():
            resultado = self._verificar_con_gesto(user)
        else:
            resultado = facial.validar(
                self.db, user["id"], facial.capturar(segundos=1.0)
            )
        decision = facial.decidir(resultado)
        if resultado.rechazada:
            self.db.registrar_auditoria(
                user["id"],
                "FRAUDE",
                "users",
                user["id"],
                nuevos={
                    "motivo": "intento de suplantación facial en el kiosco",
                    "detalle": resultado.detalle,
                },
            )
            notifications.registrar_alerta(
                self.db,
                "fraude_facial",
                "alta",
                f"Intento de suplantación facial de {user['full_name']}.",
                resultado.detalle,
                usuario_id=user["id"],
            )
        elif not resultado.verificada:
            notifications.registrar_alerta(
                self.db,
                "marca_sin_verificar",
                decision.severidad,
                f"Marca sin verificación biométrica de {user['full_name']}.",
                resultado.detalle,
                usuario_id=user["id"],
            )
        return decision

    def _marcar_offline(self, username: str, verificacion: str = "") -> None:
        """Guarda la marcación en la cola local cuando PostgreSQL no responde.

        El veredicto del control biométrico se guarda con la marca: la cámara
        estaba acá y al sincronizar ya no, así que no se puede reconstruir.
        """
        momento = clock_engine.ahora_local()
        self.cola.encolar(username, momento, verificacion)
        self._mostrar_estado(
            "Servidor central no disponible: la marcación quedó guardada "
            "localmente y se sincronizará automáticamente.",
            t("DANGER"),
        )
        self._actualizar_lbl_cola()

    def _actualizar_condicion_dia(self) -> None:
        """Muestra en el kiosco la condición que RRHH declaró para hoy."""
        if not hasattr(self, "lbl_condicion"):
            return
        try:
            excepcion = clock_engine.condicion_declarada(
                self.db, datetime.date.today()
            )
        except Exception:
            excepcion = {"condicion": "", "tolerancia": datetime.timedelta(0)}
        if excepcion["condicion"]:
            minutos = int(excepcion["tolerancia"].total_seconds() // 60)
            self.lbl_condicion.configure(
                text=f"{excepcion['condicion']} · tolerancia declarada de {minutos} min"
            )
        else:
            self.lbl_condicion.configure(text="")
        self.after(300000, self._actualizar_condicion_dia)

    def _actualizar_lbl_cola(self) -> None:
        """Refleja en el kiosco cuántas marcaciones esperan sincronizar."""
        if not hasattr(self, "lbl_cola"):
            return
        pendientes = len(self.cola)
        if pendientes:
            self.lbl_cola.configure(
                text=f"Pendientes de sincronización: {pendientes} marcaciones",
                text_color=t("DANGER"),
            )
        else:
            self.lbl_cola.configure(text="", text_color=t("MUTED"))
        self.after(5000, self._actualizar_lbl_cola)

    def _alerta_desde_hilo(self, alerta: dict) -> None:
        """Presenta en el kiosco las alertas generadas por el hilo de sync."""
        self.after(0, lambda: self._mostrar_estado(alerta["mensaje"], t("DANGER")))

    def _mostrar_panel_exito(self, tipo: str, ticket: str) -> None:
        """Despliega el comprobante de la marca durante 5 segundos.

        Lo que la persona necesita confirmar es la hora que quedó grabada,
        no que la operación salió bien: por eso la hora va primero y en el
        cuerpo más grande de la pantalla.
        """
        if hasattr(self, "panel_exito"):
            self.panel_exito.destroy()
        self.panel_exito = tarjeta(self.frame_publico)
        self.panel_exito.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=24, pady=(0, 24))
        self.panel_exito.grid_columnconfigure(0, weight=1)
        self.panel_exito.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self.panel_exito,
            text=datetime.datetime.now().strftime("%H:%M"),
            font=(MONO, 58),
            text_color=t("TEXT"),
        ).grid(row=0, column=0, pady=(38, 0))
        ctk.CTkLabel(
            self.panel_exito,
            text=f"{tipo.upper()} REGISTRADA",
            font=(FONT, 13, "bold"),
            text_color=t("SUCCESS"),
        ).grid(row=1, column=0, pady=(8, 0))
        etiqueta(
            self.panel_exito, "Comprobante criptográfico · SHA-256", 11, t("MUTED")
        ).grid(row=2, column=0, pady=(14, 8))
        caja_ticket = ctk.CTkTextbox(
            self.panel_exito,
            font=(MONO, 11),
            fg_color=t("INPUT_BG"),
            text_color=t("MUTED"),
            corner_radius=RADIO,
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
        titulo(cabecera, f"Resumen de {user['full_name']}", 17).grid(
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
        fila_vacaciones = ctk.CTkFrame(tarjeta_vacaciones, fg_color="transparent")
        fila_vacaciones.grid(row=1, column=0, padx=18, sticky="w")
        cifra(fila_vacaciones, f"{vacaciones['disponibles']:.0f}", 26).pack(side="left")
        etiqueta(fila_vacaciones, "días disponibles", 13, t("MUTED")).pack(
            side="left", padx=(8, 0), pady=(8, 0)
        )
        etiqueta(
            tarjeta_vacaciones,
            f"Usufructuados {vacaciones['usadas']:.0f} de "
            f"{vacaciones['devengadas']:.0f} devengados",
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
        fila_permisos = ctk.CTkFrame(tarjeta_permisos, fg_color="transparent")
        fila_permisos.grid(row=1, column=0, padx=18, sticky="w")
        cifra(fila_permisos, str(self.resumen["permisos_mes"]["total"]), 26).pack(side="left")
        etiqueta(fila_permisos, "permisos utilizados", 13, t("MUTED")).pack(
            side="left", padx=(8, 0), pady=(8, 0)
        )
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
            etiqueta(self.area, "Permisos aprobados · descargue el PDF oficial",
                     13, t("MUTED")).grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(4, 6)
            )
            for permiso in permisos:
                fila = ctk.CTkFrame(self.area, fg_color=t("INPUT_BG"), corner_radius=RADIO)
                fila.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)
                fila.grid_columnconfigure(0, weight=1)
                etiqueta(
                    fila,
                    f"#{permiso['id']} · {permiso['tipo']} · "
                    f"{permiso['inicio']} al {permiso['fin']} · "
                    f"aprobó {permiso['aprobador']}",
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
                    text_color=t("ON_PRIMARY"),
                    corner_radius=RADIO,
                    command=partial(descargar_pdf_permiso, self.db, permiso["id"]),
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
        titulo(tarjeta_historial, "Historial de marcas", 16).grid(
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
            tarjeta_historial, fg_color=t("INPUT_BG"), corner_radius=RADIO, height=230
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
            fila = ctk.CTkFrame(self.scroll_hist, fg_color=t("CARD"), corner_radius=RADIO)
            fila.pack(fill="x", pady=3)
            fila.grid_columnconfigure(1, weight=1)
            etiqueta(fila, marca["fecha"], 12, t("TEXT"), "bold").grid(
                row=0, column=0, sticky="w", padx=12, pady=8
            )
            etiqueta(
                fila,
                f"{marca['entrada']} – {marca['salida'] or '—'} · "
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


def main() -> None:
    """Punto de entrada de la interfaz gráfica."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = MarcacionApp()
    app.mainloop()


if __name__ == "__main__":
    main()
