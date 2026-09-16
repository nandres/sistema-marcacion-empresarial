"""Panel de Recursos Humanos: una pestaña por cosa que RRHH hace en el día.

Cada pestaña es una pantalla independiente que recibe la base y el actor, y
resuelve un trámite completo: aprobar pendientes, dar de alta a alguien, armar
un turno, declarar el clima del día. `PanelGestion` solo las ordena y muestra
la que corresponde.

Vive aparte de la ventana principal por tamaño y por uso: son casi dos mil
líneas que solo ve Recursos Humanos, mientras que el kiosco —lo que mira todo
el mundo, todos los días— cabe en una pantalla.
"""

from __future__ import annotations

import datetime
import os
from functools import partial
from typing import Callable, Dict, List, Optional

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import auth
import facial
import notifications
import reglamento
import reports
import turnos
from database import Database
from interfaz import (
    FONT,
    RADIO,
    SERIF,
    boton_primario,
    boton_secundario,
    campo_fecha,
    entrada,
    etiqueta,
    interruptor_tema,
    t,
    tarjeta,
    titulo,
)


def descargar_pdf_permiso(db: Database, solicitud_id: int) -> None:
    """Genera el PDF oficial de un permiso y lo abre para imprimirlo.

    Vive en el módulo y no en una pantalla porque lo usan dos: el tablero del
    empleado y la bandeja de justificaciones. La conexión llega desde quien
    llama, que es la que sabe de qué empresa es el permiso.
    """
    try:
        ruta = reports.generar_pdf_permiso(db, solicitud_id)
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

    # Secciones numeradas como las de un formulario: el número es un asidero
    # estable para señalarlas por teléfono ("andá a la 4"), cosa que un ícono
    # decorativo no permite.
    SECCIONES: List[tuple] = [
        ("Personal", "Gestión de Personal"),
        ("Turnos", "Horarios y rotación"),
        ("Pedidos de permiso", "Bandeja del portal del empleado"),
        ("Justificaciones", "Permisos y PDFs"),
        ("Condiciones del día", "Tolerancias declaradas"),
        ("Reportes", "Centro de Reportes"),
        ("Correcciones", "Solicitudes de Corrección"),
        ("Analítica", "Dashboard Analítico"),
        ("Auditoría", "Log JSONB de Auditoría"),
        ("Alertas", "Notificaciones en Tiempo Real"),
    ]

    def __init__(
        self, master: ctk.CTk, db: Database, actor: Dict, on_cerrar: Callable
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.on_cerrar = on_cerrar
        self._parpadeando = False
        self.indice_activo = 0
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
        ctk.CTkLabel(
            sidebar, text="Panel de Gestión", font=(SERIF, 18, "bold"), text_color=t("TEXT")
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 2))
        etiqueta(
            sidebar,
            f"{self.actor['full_name']}\n{auth.get_role_name(self.db, self.actor)}",
            11,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))
        interruptor_tema(sidebar, self.master).grid(row=2, column=0, sticky="w", padx=16)
        self.botones_seccion: List[ctk.CTkButton] = []
        for indice, (nombre_seccion, _) in enumerate(self.SECCIONES):
            boton = ctk.CTkButton(
                sidebar,
                text=f"{indice + 1:02d}    {nombre_seccion}",
                command=lambda i=indice: self._seleccionar(i),
                fg_color="transparent",
                hover_color=t("INPUT_BG"),
                text_color=t("MUTED"),
                font=(FONT, 14),
                corner_radius=RADIO,
                height=46,
                anchor="w",
            )
            boton.grid(row=3 + indice, column=0, sticky="ew", padx=10, pady=3)
            boton._rol = "plano"
            self.botones_seccion.append(boton)
        boton_secundario(sidebar, "Volver a Marcación", self.on_cerrar).grid(
            row=3 + len(self.SECCIONES), column=0, sticky="ew", padx=10, pady=(18, 14)
        )

    def _construir_contenido(self) -> None:
        contenido = ctk.CTkFrame(self, fg_color="transparent")
        contenido.grid(row=0, column=1, sticky="nsew", padx=(12, 24), pady=24)
        contenido.grid_columnconfigure(0, weight=1)
        contenido.grid_rowconfigure(0, weight=1)

        self.personal_tab = PersonalTab(
            contenido, self.db, self.actor, self._refrescar_empleados
        )
        self.turnos_tab = TurnosTab(contenido, self.db, self.actor)
        self.solicitudes_tab = SolicitudesPermisoTab(contenido, self.db, self.actor)
        self.justificaciones_tab = JustificacionesTab(contenido, self.db, self.actor)
        self.condiciones_tab = CondicionesTab(contenido, self.db, self.actor)
        self.reportes_tab = ReportesTab(contenido, self.db, self.actor)
        self.correcciones_tab = CorreccionesTab(contenido, self.db, self.actor)
        self.dashboard_tab = DashboardTab(contenido, self.db)
        self.auditoria_tab = AuditoriaTab(contenido, self.db)
        self.alertas_tab = AlertasTab(contenido, self.db)
        # El orden replica al de SECCIONES: el índice del botón es el índice
        # de la pestaña.
        self.pestanas = [
            self.personal_tab,
            self.turnos_tab,
            self.solicitudes_tab,
            self.justificaciones_tab,
            self.condiciones_tab,
            self.reportes_tab,
            self.correcciones_tab,
            self.dashboard_tab,
            self.auditoria_tab,
            self.alertas_tab,
        ]
        for pestana in self.pestanas:
            pestana.grid(row=0, column=0, sticky="nsew")
            pestana.grid_remove()
        self._revision_alertas()
        notifications.BUS.suscribir(self._alerta_entrante)
        # El escritorio también escucha el canal: una alerta que nace en el
        # servidor web tiene que encender la campana del panel de gestión.
        self._escucha = notifications.EscuchaAlertas(self._conexion_de_escucha)
        self._escucha.start()

    @staticmethod
    def _conexion_de_escucha():
        """Conexión propia del hilo que escucha el canal de alertas."""
        try:
            db = Database()
            db.connect()
            return db
        except Exception:
            return None

    def _revision_alertas(self) -> None:
        """Revisa alertas no leídas y activa el parpadeo de la campana."""
        try:
            pendientes = self.db.listar_alertas(no_leidas=True)
            if pendientes and not self._parpadeando:
                self._parpadeando = True
                self._alternar_campana()
            elif not pendientes and self._parpadeando:
                self._detener_campana()
        except Exception:
            pass
        self.after(4000, self._revision_alertas)

    def _alerta_entrante(self, alerta: dict) -> None:
        """El bus de notificaciones avisa; se revisa en el hilo de la GUI.

        El bus es del proceso y no de la empresa, así que la campana solo
        parpadea por lo que pasa en la que esta instalación atiende.
        """
        if not notifications.es_de_la_empresa(alerta, self.db.empresa_id):
            return
        self.after(0, self._revision_alertas)

    def _alternar_campana(self) -> None:
        if not self._parpadeando:
            return
        boton = self.botones_seccion[-1]
        color = t("DANGER") if boton.cget("fg_color") == "transparent" else "transparent"
        boton.configure(fg_color=color)
        self.after(600, self._alternar_campana)

    def _detener_campana(self) -> None:
        self._parpadeando = False
        boton = self.botones_seccion[-1]
        if self.indice_activo == len(self.SECCIONES) - 1:
            boton.configure(fg_color=t("PRIMARY"), text_color=t("ON_PRIMARY"))
        else:
            boton.configure(fg_color="transparent")

    def _seleccionar(self, indice: int) -> None:
        """Cambia la sección activa y estiliza el botón del menú lateral."""
        self.indice_activo = indice
        for pestana in self.pestanas:
            pestana.grid_remove()
        self.pestanas[indice].grid(row=0, column=0, sticky="nsew")
        if indice == len(self.SECCIONES) - 1:
            self.alertas_tab._refrescar()
            self._detener_campana()
        for posicion, boton in enumerate(self.botones_seccion):
            seleccionado = posicion == indice
            boton.configure(
                fg_color=t("PRIMARY") if seleccionado else "transparent",
                text_color=t("ON_PRIMARY") if seleccionado else t("MUTED"),
                hover_color=t("PRIMARY_HOVER") if seleccionado else t("INPUT_BG"),
            )

    def _refrescar_empleados(self) -> None:
        self.justificaciones_tab.refrescar_empleados()
        self.turnos_tab._refrescar()


class AlertasTab(ctk.CTkFrame):
    """Centro de notificaciones en vivo (cuota bloqueada, tardanzas, fraude)."""

    def __init__(self, master, db: Database) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        titulo(cabecera, "Notificaciones en Tiempo Real", 16).grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 2)
        )
        etiqueta(
            cabecera,
            "Alertas de cuota bloqueada, tardanzas injustificadas y fraude facial",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 6))
        boton_secundario(cabecera, "Marcar todas como leídas", self._marcar_leidas).grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 14)
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        alertas = self.db.listar_alertas(limite=60)
        if not alertas:
            etiqueta(self.scroll, "No hay notificaciones por el momento.", 13, t("MUTED")).pack(
                pady=20
            )
            return
        for alerta in alertas:
            fila = tarjeta(self.scroll)
            fila.pack(fill="x", pady=5)
            fila.grid_columnconfigure(0, weight=1)
            colores = {
                "alta": t("DANGER"),
                "media": t("ACCENTO"),
                "baja": t("MUTED"),
            }
            color = colores.get(alerta["severidad"], t("MUTED"))
            sin_leer = not alerta["leida"]
            etiqueta(
                fila,
                alerta["mensaje"],
                13,
                color,
                "bold" if sin_leer else "normal",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            if sin_leer:
                etiqueta(fila, "SIN LEER", 9, color, "bold").grid(
                    row=0, column=1, sticky="e", padx=14, pady=(10, 0)
                )
            if alerta.get("detalle"):
                etiqueta(fila, alerta["detalle"], 11, t("MUTED")).grid(
                    row=1, column=0, sticky="w", padx=14, pady=(2, 0)
                )
            etiqueta(
                fila,
                f"{alerta['tipo']} · {alerta['creado_en'].strftime('%d/%m/%Y %H:%M')}",
                10,
                t("MUTED"),
            ).grid(row=2, column=0, sticky="w", padx=14, pady=(2, 10))

    def _marcar_leidas(self) -> None:
        self.db.marcar_alertas_leidas()
        self._refrescar()


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
        titulo(cabecera, "Log de Auditoría · JSONB", 16).grid(
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
            etiqueta(self.scroll, "Sin eventos de auditoría todavía.",
                     13, t("MUTED")).pack(pady=20)
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
            text_color=t("ON_PRIMARY"),
            corner_radius=RADIO,
        )
        boton_refrescar.grid(row=0, column=1, rowspan=2, padx=16, sticky="e")
        self.lbl_resultado = etiqueta(cabecera, "", 12, t("SUCCESS"))
        self.lbl_resultado.grid(row=2, column=0, columnspan=2, sticky="w",
                                padx=20, pady=(2, 12))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        solicitudes = self.db.listar_solicitudes_correccion()
        if not solicitudes:
            etiqueta(self.scroll, "No hay solicitudes de corrección.",
                     13, t("MUTED")).pack(pady=20)
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
                    hover_color=t("PRIMARY_HOVER"),
                    text_color=t("ON_PRIMARY"),
                    corner_radius=RADIO,
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
                    corner_radius=RADIO,
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


class SolicitudesPermisoTab(ctk.CTkFrame):
    """Bandeja de pedidos de permiso presentados por el personal.

    Los pedidos llegan validados contra el catálogo reglamentario, así que
    acá no se controlan cuotas: se decide. Al aprobar se emite la
    justificación oficial y su PDF sin ningún paso adicional.
    """

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = tarjeta(self)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecera.grid_columnconfigure(0, weight=1)
        titulo(cabecera, "Pedidos de permiso del personal", 16).grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 4)
        )
        etiqueta(
            cabecera,
            "Validados contra el reglamento al presentarse · aprobar emite la justificación",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=20)
        ctk.CTkButton(
            cabecera,
            text="Refrescar",
            command=self._refrescar,
            width=100,
            height=32,
            font=(FONT, 12),
            fg_color=t("PRIMARY"),
            hover_color=t("PRIMARY_HOVER"),
            text_color=t("ON_PRIMARY"),
            corner_radius=RADIO,
        ).grid(row=0, column=1, rowspan=2, padx=16, sticky="e")
        self.lbl_resultado = etiqueta(cabecera, "", 12, t("SUCCESS"))
        self.lbl_resultado.grid(
            row=2, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 12)
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        solicitudes = self.db.listar_solicitudes_permiso()
        if not solicitudes:
            etiqueta(self.scroll, "No hay pedidos de permiso.", 13, t("MUTED")).pack(pady=20)
            return
        for solicitud in solicitudes:
            self._pintar_solicitud(solicitud)

    def _pintar_solicitud(self, solicitud: Dict) -> None:
        fila = tarjeta(self.scroll)
        fila.pack(fill="x", pady=5)
        fila.grid_columnconfigure(0, weight=1)
        cantidad = (
            f"{float(solicitud['horas_solicitadas']):g} h"
            if float(solicitud["horas_solicitadas"] or 0)
            else f"{(solicitud['fecha_fin'] - solicitud['fecha_inicio']).days + 1} días"
        )
        etiqueta(
            fila,
            f"#{solicitud['id']} · {solicitud['full_name']} · "
            f"{solicitud['tipo_permiso']} · {cantidad}",
            14,
            t("TEXT"),
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        estado = solicitud["estado"]
        if solicitud["revisor"]:
            estado += f" por {solicitud['revisor']}"
        etiqueta(
            fila,
            f"{solicitud['fecha_inicio']} – {solicitud['fecha_fin']} · "
            f"{solicitud['tipo_vinculo'] or 'Funcionario'} · {estado}",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, sticky="w", padx=14)
        detalle = f"Motivo: {solicitud['motivo']}"
        if solicitud["observacion"]:
            detalle += f"\nRespuesta: {solicitud['observacion']}"
        etiqueta(fila, detalle, 12, t("TEXT")).grid(
            row=2, column=0, sticky="w", padx=14, pady=(2, 10)
        )
        if solicitud["estado"] != "Pendiente":
            return
        ctk.CTkButton(
            fila,
            text="Aprobar",
            width=90,
            height=32,
            font=(FONT, 12),
            fg_color=t("SUCCESS"),
            hover_color=t("PRIMARY_HOVER"),
            text_color=t("ON_PRIMARY"),
            corner_radius=RADIO,
            command=partial(self._resolver, solicitud["id"], True),
        ).grid(row=0, column=1, rowspan=3, padx=(0, 6), sticky="e")
        ctk.CTkButton(
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
            corner_radius=RADIO,
            command=partial(self._resolver, solicitud["id"], False),
        ).grid(row=0, column=2, rowspan=3, padx=(0, 14), sticky="e")

    def _resolver(self, solicitud_id: int, aprobar: bool) -> None:
        observacion = ""
        if not aprobar:
            observacion = (
                ctk.CTkInputDialog(
                    text="Motivo del rechazo (lo ve el empleado):",
                    title="Rechazar pedido",
                ).get_input()
                or ""
            ).strip()
            if len(observacion) < 10:
                self.lbl_resultado.configure(
                    text="Explicá el motivo del rechazo (al menos 10 caracteres).",
                    text_color=t("DANGER"),
                )
                return
        try:
            resultado = auth.resolver_solicitud_permiso(
                self.db, self.actor, solicitud_id, aprobar, observacion
            )
        except ValueError as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        mensaje = f"Pedido #{solicitud_id} {resultado['estado'].lower()}."
        if resultado["justificacion_id"]:
            mensaje += f" Justificación #{resultado['justificacion_id']} emitida."
        self.lbl_resultado.configure(text=mensaje, text_color=t("SUCCESS"))
        self._refrescar()


class CondicionesTab(ctk.CTkFrame):
    """Declaración de condiciones excepcionales del día.

    Reemplaza a la casilla que el propio empleado marcaba en el kiosco: la
    tolerancia climática de la Res. 3028/2024 la reconoce la empresa para
    toda la plantilla, queda firmada y es auditable.
    """

    CONDICIONES = (
        ("Lluvia intensa", 30),
        ("Corte de rutas o manifestación", 30),
        ("Corte de energía", 30),
        ("Paro de transporte público", 60),
    )

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        formulario.grid_columnconfigure(1, weight=1)
        titulo(formulario, "Declarar una condición del día", 16).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(14, 2)
        )
        etiqueta(
            formulario,
            "Alcanza a toda la plantilla y se suma a la tolerancia ordinaria",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 12))

        self.campo_fecha = campo_fecha(formulario, "AAAA-MM-DD")
        self.campo_fecha.grid(row=2, column=0, sticky="w", padx=(20, 8), pady=(0, 10))
        self.campo_fecha.entrada.insert(0, datetime.date.today().isoformat())

        self.menu_condicion = ctk.CTkOptionMenu(
            formulario,
            values=[c[0] for c in self.CONDICIONES],
            font=(FONT, 13),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            command=self._sugerir_tolerancia,
            width=220,
        )
        self.menu_condicion.grid(row=2, column=1, sticky="w", pady=(0, 10))

        self.entrada_tolerancia = entrada(formulario, "Minutos", ancho=110)
        self.entrada_tolerancia.grid(row=2, column=2, sticky="w", padx=8, pady=(0, 10))
        self.entrada_tolerancia.insert(0, "30")

        self.entrada_nota = entrada(formulario, "Nota interna (opcional)", ancho=420)
        self.entrada_nota.grid(
            row=3, column=0, columnspan=2, sticky="w", padx=(20, 8), pady=(0, 12)
        )
        boton_primario(formulario, "Declarar", self._declarar).grid(
            row=3, column=2, sticky="w", padx=8, pady=(0, 12)
        )
        self.lbl_resultado = etiqueta(formulario, "", 12, t("SUCCESS"))
        self.lbl_resultado.grid(
            row=4, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 12)
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _sugerir_tolerancia(self, elegida: str) -> None:
        """Precarga la tolerancia habitual de la condición sin fijarla."""
        minutos = dict(self.CONDICIONES).get(elegida, 30)
        self.entrada_tolerancia.delete(0, "end")
        self.entrada_tolerancia.insert(0, str(minutos))

    def _declarar(self) -> None:
        try:
            fecha = datetime.date.fromisoformat(self.campo_fecha.entrada.get().strip())
        except ValueError:
            self.lbl_resultado.configure(
                text="Fecha inválida. Use AAAA-MM-DD.", text_color=t("DANGER")
            )
            return
        try:
            minutos = int(self.entrada_tolerancia.get().strip())
        except ValueError:
            self.lbl_resultado.configure(
                text="La tolerancia se expresa en minutos enteros.", text_color=t("DANGER")
            )
            return
        try:
            auth.declarar_condicion_dia(
                self.db,
                self.actor,
                fecha,
                self.menu_condicion.get(),
                minutos,
                self.entrada_nota.get().strip(),
            )
        except ValueError as error:
            self.lbl_resultado.configure(text=str(error), text_color=t("DANGER"))
            return
        self.lbl_resultado.configure(
            text=f"{self.menu_condicion.get()} declarada para el {fecha.isoformat()}.",
            text_color=t("SUCCESS"),
        )
        self._refrescar()

    def _revocar(self, fecha: datetime.date) -> None:
        self.db.borrar_condicion_dia(fecha)
        self.db.registrar_auditoria(
            self.actor["id"], "REVOCAR", "condiciones_dia", 0,
            anterior={"fecha": fecha.isoformat()},
        )
        self.lbl_resultado.configure(
            text=f"Condición del {fecha.isoformat()} revocada.", text_color=t("MUTED")
        )
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        condiciones = self.db.listar_condiciones_dia()
        if not condiciones:
            etiqueta(self.scroll, "Ningún día con condición declarada.",
                     13, t("MUTED")).pack(
                pady=20
            )
            return
        for condicion in condiciones:
            fila = tarjeta(self.scroll)
            fila.pack(fill="x", pady=5)
            fila.grid_columnconfigure(0, weight=1)
            etiqueta(
                fila,
                f"{condicion['fecha']} · {condicion['condicion']} · "
                f"{condicion['tolerancia_min']} min",
                14,
                t("TEXT"),
                "bold",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
            detalle = f"Firmó {condicion['declarante']}"
            if condicion["nota"]:
                detalle += f" · {condicion['nota']}"
            etiqueta(fila, detalle, 12, t("MUTED")).grid(
                row=1, column=0, sticky="w", padx=14, pady=(0, 10)
            )
            ctk.CTkButton(
                fila,
                text="Revocar",
                width=90,
                height=32,
                font=(FONT, 12),
                fg_color="transparent",
                hover_color=t("DANGER"),
                border_width=1,
                border_color=t("DANGER"),
                text_color=t("DANGER"),
                corner_radius=RADIO,
                command=partial(self._revocar, condicion["fecha"]),
            ).grid(row=0, column=1, rowspan=2, padx=(0, 14), sticky="e")


class TurnosTab(ctk.CTkFrame):
    """Definición de turnos y asignación de la plantilla.

    Un turno es el horario contra el que se mide la puntualidad. Antes había
    uno solo para toda la empresa, fijado en el `.env`, así que dos turnos
    que se relevan o la jornada partida del comercio no se podían modelar.

    La asignación es en dos niveles: el turno del legajo es el de contrato y
    la rotación lo desplaza por un período, de modo que al vencer la persona
    vuelve sola a su horario sin que nadie tenga que deshacer nada.
    """

    DIAS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")

    def __init__(self, master, db: Database, actor: Dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.actor = actor
        self.turnos: List[Dict] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        formulario = tarjeta(self)
        formulario.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        formulario.grid_columnconfigure(5, weight=1)
        titulo(formulario, "Nuevo turno", 16).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(14, 2)
        )
        etiqueta(
            formulario,
            "El horario contra el que se miden las tardanzas de quien lo tenga asignado",
            12,
            t("MUTED"),
        ).grid(row=1, column=0, columnspan=6, sticky="w", padx=20, pady=(0, 12))

        self.entrada_nombre = entrada(formulario, "Nombre (Mañana, Noche…)", ancho=230)
        self.entrada_nombre.grid(row=2, column=0, columnspan=2, sticky="w",
                                 padx=(20, 8), pady=(0, 10))
        self.entrada_sucursal = entrada(formulario, "Sucursal", ancho=190)
        self.entrada_sucursal.grid(row=2, column=2, columnspan=2, sticky="w",
                                   padx=8, pady=(0, 10))
        self.entrada_sucursal.insert(0, turnos.SUCURSAL_PREDETERMINADA)
        self.entrada_tolerancia = entrada(formulario, "Tolerancia (min)", ancho=150)
        self.entrada_tolerancia.grid(row=2, column=4, sticky="w", padx=8, pady=(0, 10))

        etiqueta(formulario, "Entrada", 12, t("MUTED")).grid(
            row=3, column=0, sticky="w", padx=(20, 8)
        )
        etiqueta(formulario, "Salida", 12, t("MUTED")).grid(row=3, column=1, sticky="w")
        self.entrada_desde = entrada(formulario, "HH:MM", ancho=110)
        self.entrada_desde.grid(row=4, column=0, sticky="w", padx=(20, 8), pady=(0, 10))
        self.entrada_desde.insert(0, "08:00")
        self.entrada_hasta = entrada(formulario, "HH:MM", ancho=110)
        self.entrada_hasta.grid(row=4, column=1, sticky="w", pady=(0, 10))
        self.entrada_hasta.insert(0, "16:00")

        self.var_partida = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            formulario,
            text="Jornada partida",
            variable=self.var_partida,
            command=self._alternar_partida,
            font=(FONT, 13),
            text_color=t("TEXT"),
            fg_color=t("PRIMARY"),
            hover_color=t("PRIMARY_HOVER"),
            border_color=t("INPUT_BORDER"),
            corner_radius=RADIO,
        ).grid(row=4, column=2, sticky="w", padx=8, pady=(0, 10))

        self.entrada_desde2 = entrada(formulario, "HH:MM", ancho=110)
        self.entrada_desde2.insert(0, "14:00")
        self.entrada_hasta2 = entrada(formulario, "HH:MM", ancho=110)
        self.entrada_hasta2.insert(0, "18:00")

        self.dias_marcados: List[ctk.BooleanVar] = []
        fila_dias = ctk.CTkFrame(formulario, fg_color="transparent")
        fila_dias.grid(row=5, column=0, columnspan=6, sticky="w", padx=(16, 8), pady=(0, 10))
        for indice, nombre in enumerate(self.DIAS):
            marcado = ctk.BooleanVar(value=indice < 5)
            ctk.CTkCheckBox(
                fila_dias,
                text=nombre,
                variable=marcado,
                width=64,
                font=(FONT, 12),
                text_color=t("TEXT"),
                fg_color=t("PRIMARY"),
                hover_color=t("PRIMARY_HOVER"),
                border_color=t("INPUT_BORDER"),
                corner_radius=RADIO,
            ).pack(side="left", padx=4)
            self.dias_marcados.append(marcado)

        boton_primario(formulario, "Crear turno", self._crear).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=(20, 8), pady=(0, 12)
        )
        self.lbl_resultado = etiqueta(formulario, "", 12, t("SUCCESS"))
        self.lbl_resultado.grid(
            row=6, column=2, columnspan=4, sticky="w", padx=8, pady=(0, 12)
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self._refrescar()

    def _alternar_partida(self) -> None:
        """Muestra la segunda franja solo cuando la jornada es partida."""
        if self.var_partida.get():
            self.entrada_desde2.grid(row=4, column=3, sticky="w", padx=8, pady=(0, 10))
            self.entrada_hasta2.grid(row=4, column=4, sticky="w", padx=8, pady=(0, 10))
        else:
            self.entrada_desde2.grid_remove()
            self.entrada_hasta2.grid_remove()

    def _mascara(self) -> str:
        return "".join("1" if var.get() else "0" for var in self.dias_marcados)

    def _avisar(self, texto: str, color: str) -> None:
        self.lbl_resultado.configure(text=texto, text_color=color)

    def _crear(self) -> None:
        tramos = [
            {"entrada": self.entrada_desde.get().strip(),
             "salida": self.entrada_hasta.get().strip()}
        ]
        if self.var_partida.get():
            tramos.append(
                {"entrada": self.entrada_desde2.get().strip(),
                 "salida": self.entrada_hasta2.get().strip()}
            )
        tolerancia = self.entrada_tolerancia.get().strip()
        try:
            creado = auth.crear_turno(
                self.db,
                self.actor,
                self.entrada_nombre.get().strip(),
                tramos,
                self._mascara(),
                self.entrada_sucursal.get().strip() or turnos.SUCURSAL_PREDETERMINADA,
                tolerancia or None,
            )
        except ValueError as error:
            self._avisar(str(error), t("DANGER"))
            return
        self._avisar(
            f"{creado['nombre']} · {creado['horario']} · {creado['dias_texto']}",
            t("SUCCESS"),
        )
        self.entrada_nombre.delete(0, "end")
        self._refrescar()

    def _retirar(self, turno_id: int) -> None:
        try:
            resultado = auth.retirar_turno(self.db, self.actor, turno_id)
        except ValueError as error:
            self._avisar(str(error), t("DANGER"))
            return
        self._avisar(f"Turno {resultado}.", t("MUTED"))
        self._refrescar()

    def _hacer_predeterminado(self, turno_id: int) -> None:
        try:
            turno = auth.designar_turno_predeterminado(self.db, self.actor, turno_id)
        except ValueError as error:
            self._avisar(str(error), t("DANGER"))
            return
        self._avisar(f"{turno['nombre']} es el turno predeterminado.", t("SUCCESS"))
        self._refrescar()

    def _asignar(self, usuario_id: int, nombre_turno: str) -> None:
        elegido = next(
            (x["id"] for x in self.turnos if x["nombre"] == nombre_turno), None
        )
        try:
            auth.asignar_turno_base(self.db, self.actor, usuario_id, elegido)
        except ValueError as error:
            self._avisar(str(error), t("DANGER"))
            return
        self._avisar(f"Turno de contrato actualizado a {nombre_turno}.", t("SUCCESS"))
        self._refrescar()

    def _refrescar(self) -> None:
        for hijo in self.scroll.winfo_children():
            hijo.destroy()
        self.turnos = auth.listar_turnos(self.db, self.actor, incluir_inactivos=True)
        activos = [x for x in self.turnos if x["activo"]]

        titulo(self.scroll, "Turnos definidos", 15).pack(anchor="w", pady=(4, 8))
        for turno in self.turnos:
            self._fila_turno(turno)

        titulo(self.scroll, "Quién trabaja en qué turno", 15).pack(
            anchor="w", pady=(18, 4)
        )
        etiqueta(
            self.scroll,
            "El turno de contrato rige mientras no haya una rotación vigente",
            12,
            t("MUTED"),
        ).pack(anchor="w", pady=(0, 8))
        vigentes = self.db.turnos_vigentes_de_la_plantilla(datetime.date.today())
        for persona in self.db.list_users():
            self._fila_persona(persona, vigentes.get(persona["id"]) or {}, activos)

    def _fila_turno(self, turno: Dict) -> None:
        fila = tarjeta(self.scroll)
        fila.pack(fill="x", pady=5)
        fila.grid_columnconfigure(0, weight=1)
        marcas = []
        if turno["predeterminado"]:
            marcas.append("predeterminado")
        if turno["partida"]:
            marcas.append("jornada partida")
        if turno["nocturno"]:
            marcas.append("nocturno")
        if not turno["activo"]:
            marcas.append("retirado")
        etiqueta(
            fila,
            f"{turno['nombre']} · {turno['horario']}",
            14,
            t("TEXT") if turno["activo"] else t("MUTED"),
            "bold",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        detalle = (
            f"{turno['dias_texto']} · {turno['horas_previstas']} h previstas · "
            f"{turno['sucursal']} · dotación {max(turno['dotacion'], turno['asignados'])}"
        )
        if turno["tolerancia_min"] is not None:
            detalle += f" · tolerancia propia {turno['tolerancia_min']} min"
        if marcas:
            detalle += " · " + ", ".join(marcas)
        etiqueta(fila, detalle, 12, t("MUTED")).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 10)
        )
        acciones = ctk.CTkFrame(fila, fg_color="transparent")
        acciones.grid(row=0, column=1, rowspan=2, padx=(0, 14), sticky="e")
        if turno["activo"] and not turno["predeterminado"]:
            ctk.CTkButton(
                acciones,
                text="Predeterminado",
                width=130,
                height=32,
                font=(FONT, 12),
                fg_color="transparent",
                hover_color=t("INPUT_BG"),
                border_width=1,
                border_color=t("INPUT_BORDER"),
                text_color=t("TEXT"),
                corner_radius=RADIO,
                command=partial(self._hacer_predeterminado, turno["id"]),
            ).pack(side="left", padx=4)
        if not turno["predeterminado"]:
            ctk.CTkButton(
                acciones,
                text="Retirar",
                width=90,
                height=32,
                font=(FONT, 12),
                fg_color="transparent",
                hover_color=t("DANGER"),
                border_width=1,
                border_color=t("DANGER"),
                text_color=t("DANGER"),
                corner_radius=RADIO,
                command=partial(self._retirar, turno["id"]),
            ).pack(side="left", padx=4)

    def _fila_persona(self, persona: Dict, vigente: Dict, activos: List[Dict]) -> None:
        fila = tarjeta(self.scroll)
        fila.pack(fill="x", pady=4)
        fila.grid_columnconfigure(0, weight=1)
        etiqueta(fila, persona["full_name"], 14, t("TEXT"), "bold").grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 2)
        )
        hoy = vigente.get("turno_nombre") or "—"
        origen = vigente.get("origen") or "predeterminado"
        leyenda = {
            "asignacion": "por rotación vigente",
            "legajo": "por su contrato",
            "predeterminado": "sin turno propio en el legajo",
        }[origen]
        etiqueta(fila, f"Hoy: {hoy} · {leyenda}", 12, t("MUTED")).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 10)
        )
        nombres = [x["nombre"] for x in activos] or ["—"]
        actual = persona.get("turno_nombre") or nombres[0]
        menu = ctk.CTkOptionMenu(
            fila,
            values=nombres,
            font=(FONT, 12),
            fg_color=t("INPUT_BG"),
            button_color=t("PRIMARY"),
            button_hover_color=t("PRIMARY_HOVER"),
            text_color=t("TEXT"),
            dropdown_fg_color=t("INPUT_BG"),
            width=210,
            command=partial(self._asignar, persona["id"]),
        )
        menu.set(actual if actual in nombres else nombres[0])
        menu.grid(row=0, column=1, rowspan=2, padx=(0, 14), sticky="e")


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
        titulo(cabecera, "Dashboard Analítico de Recursos Humanos", 16).grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 2))
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
            text_color=t("ON_PRIMARY"),
            corner_radius=RADIO,
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
            f"{millones:,.2f} millones de Guaraníes acumulados (estimado)",
            15,
            t("ACCENTO"),
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
        eje.grid(True, color=t("CARD_BORDER"), alpha=0.6, linestyle="--", linewidth=0.8)
        for borde in ("top", "right"):
            eje.spines[borde].set_visible(False)
        for borde in ("left", "bottom"):
            eje.spines[borde].set_color(t("CARD_BORDER"))

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
                    textcoords="offset points", color=t("DANGER"),
                    fontsize=10, fontweight="bold",
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
                edgecolor=t("CARD"),
            )
            eje.bar(
                [p + ancho_barra / 2 for p in posiciones],
                [e["horas_100"] for e in self.extras],
                width=ancho_barra, color=t("ACCENTO"), label="Recargo 100%",
                edgecolor=t("CARD"),
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
        titulo(formulario, "Agregar empleado", 17).pack(
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
        titulo(listado, "Personal registrado", 17).grid(
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
            fila = ctk.CTkFrame(self.scroll, fg_color=t("INPUT_BG"), corner_radius=RADIO)
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
                text_color=t("ON_PRIMARY"),
                corner_radius=RADIO,
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
                corner_radius=RADIO,
                command=partial(self._eliminar, usuario),
            )
            boton_eliminar.grid(row=0, column=2, padx=(0, 10))
            tiene_foto = self.db.tiene_foto(usuario["id"])
            boton_foto = ctk.CTkButton(
                fila,
                text="Foto cargada" if tiene_foto else "Foto",
                width=70,
                height=30,
                font=(FONT, 12),
                fg_color="transparent",
                hover_color=t("ACCENTO"),
                border_width=1,
                border_color=t("ACCENTO") if tiene_foto else t("MUTED"),
                text_color=t("ACCENTO") if tiene_foto else t("MUTED"),
                corner_radius=RADIO,
                command=partial(self._registrar_foto, usuario),
            )
            boton_foto.grid(row=0, column=3, padx=(0, 10))
            if self.editando and self.editando["id"] == usuario["id"]:
                self._construir_editor_inline(self.scroll)

    def _registrar_foto(self, usuario: Dict) -> None:
        """Captura y guarda la foto biométrica del empleado (kiosco webcam)."""
        if not facial.disponible():
            self.lbl_resultado.configure(
                text="Visión por computadora no disponible en este equipo.",
                text_color=t("DANGER"),
            )
            return
        self.lbl_resultado.configure(
            text=f"Mirando a la cámara… ({usuario['full_name']})", text_color=t("TEXT")
        )
        frame = facial.capturar(segundos=2.5)
        if frame is None:
            self.lbl_resultado.configure(
                text="No se detectó un rostro o no hay cámara. Reintente.",
                text_color=t("DANGER"),
            )
            return
        ok, detalle = facial.registrar_foto(self.db, usuario["id"], frame)
        self.lbl_resultado.configure(
            text=detalle, text_color=t("SUCCESS") if ok else t("DANGER")
        )
        self._refrescar()

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
        self.lbl_resultado.configure(text="Empleado agregado correctamente.",
                                     text_color=t("SUCCESS"))
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
        titulo(formulario, "Registrar justificación aprobada", 17).grid(
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
            formulario, fg_color=t("INPUT_BG"), corner_radius=RADIO, height=180
        )
        self.scroll_disp.grid(row=10, column=0, sticky="ew", padx=20, pady=(0, 18))
        self.refrescar_empleados()

        listado = tarjeta(self)
        listado.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        listado.grid_columnconfigure(0, weight=1)
        listado.grid_rowconfigure(1, weight=1)
        titulo(listado, "Justificaciones emitidas", 16).grid(
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
            fila = ctk.CTkFrame(self.scroll_just, fg_color=t("INPUT_BG"), corner_radius=RADIO)
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
                f"{justificacion['fecha_inicio'].strftime('%d/%m/%Y')} – "
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
                partial(descargar_pdf_permiso, self.db, justificacion["id"]),
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
            (a for a in self._articulos_actuales()
             if f"{a['articulo']} · {a['nombre']}" == seleccion),
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
            fila = ctk.CTkFrame(self.scroll_disp, fg_color=t("CARD"), corner_radius=RADIO)
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
        titulo(tarjeta_asistencia, "Reporte Mensual de Asistencia", 17).grid(
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
        titulo(tarjeta_aguinaldo, "Proyección de Aguinaldos", 17).grid(
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

    def _periodo(self, entrada_anio: ctk.CTkEntry,
                 entrada_mes: Optional[ctk.CTkEntry]) -> tuple:
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
        self.lbl_resultado.configure(text=f"Aguinaldo exportado: {ruta}",
                                     text_color=t("SUCCESS"))
