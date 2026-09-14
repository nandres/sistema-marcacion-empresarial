"""API del Sistema de Marcación: kiosco, portal del empleado y panel de RRHH.

Este módulo expone únicamente la API. La interfaz vive en ``src/static``
como HTML, CSS y JavaScript propios: tenerla incrustada en un ``f-string``
de Python hacía que los escapes se consumieran dos veces y que un error de
comillas rompiera el portal entero sin que fallara ninguna prueba de API.

La identidad del empleado se resuelve siempre desde el token; la cédula
nunca viaja en la URL. Cada petición abre y cierra su propia conexión a
PostgreSQL, y las migraciones corren aparte en ``migrate.py``.

Ejecución:
    python src/web_server.py          # http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import clock_engine
import database
import facial
import notifications
import rate_limit
import reglamento
import reports

ESTATICOS: Path = Path(__file__).resolve().parent / "static"

CABECERAS_SEGURIDAD: Dict[str, str] = {
    # Con la interfaz en archivos propios no queda script embebido, así que
    # script-src puede exigir 'self': un script inyectado no llega a ejecutarse.
    # connect-src cierra la salida hacia dominios externos.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Valida secretos y esquema al arrancar; no aplica DDL.

    Las migraciones corren en ``migrate.py`` como paso de despliegue. Aquí
    solo se verifica que el entorno esté listo, para fallar de inmediato y
    con un mensaje claro en lugar de hacerlo en la primera petición.
    """
    auth.verificar_secretos()
    db = database.Database()
    try:
        db.connect()
        if not db.esquema_listo():
            raise RuntimeError(
                "El esquema de la base no está completo. "
                "Ejecutá 'python migrate.py' antes de iniciar el servidor."
            )
    finally:
        db.cerrar()
    yield


app = FastAPI(
    title="Sistema de Marcación · Portal, Kiosco y Gestión",
    description="Kiosco de marcación, tablero del empleado y panel de Recursos Humanos.",
    version="3.2.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def aplicar_cabeceras_seguridad(request: Request, siguiente):
    respuesta = await siguiente(request)
    respuesta.headers.update(CABECERAS_SEGURIDAD)
    return respuesta


app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")

class LoginRequest(BaseModel):
    """Credenciales del empleado para emitir el token de acceso."""

    cedula: str
    password: str


class ConsultaRequest(BaseModel):
    """Rango de fechas de la consulta; la identidad viaja en el token."""

    desde: str = ""
    hasta: str = ""
    fecha: str = ""


class ReclamoRequest(BaseModel):
    """Datos de la corrección solicitada; la identidad viaja en el token."""

    tipo_marca: str
    fecha: str
    hora_propuesta: str
    motivo: str


def _cliente() -> database.Database:
    """Abre una conexión fresca por petición para evitar sesiones cruzadas.

    No aplica migraciones: el DDL vive en ``migrate.py`` y corre una sola vez
    antes de levantar el servidor. Ejecutarlo por petición tomaba locks
    exclusivos sobre ``justificaciones`` y serializaba el pico de marcación.
    """
    db = database.Database()
    db.connect()
    return db


def _usuario_autenticado(
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Extrae y valida el Bearer Token, devolviendo el usuario autenticado."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Se requiere un token de acceso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    db = _cliente()
    try:
        claims = auth.verificar_token_acceso(token)
        usuario = db.get_user_by_id(int(claims["sub"]))
        if not usuario:
            raise ValueError("Usuario del token inexistente.")
        return usuario
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(
            status_code=401,
            detail="Sesión inválida o expirada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    finally:
        db.cerrar()


def _usuario_por_token_query(token: str) -> Dict[str, Any]:
    """Resuelve el usuario desde un token recibido por query string (PDFs)."""
    db = _cliente()
    try:
        claims = auth.verificar_token_acceso(token)
        usuario = db.get_user_by_id(int(claims["sub"]))
        if not usuario:
            raise ValueError("Usuario del token inexistente.")
        return usuario
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")
    finally:
        db.cerrar()


def _alerta_json(alerta: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte a JSON puro (send_json de WebSocket usa json.dumps plano)."""
    salida = dict(alerta)
    if isinstance(salida.get("creado_en"), datetime.datetime):
        salida["creado_en"] = salida["creado_en"].isoformat()
    return salida


@app.websocket("/ws/alertas")
async def ws_alertas(websocket: WebSocket, token: str = "") -> None:
    """Push en tiempo real: cada alerta publicada en el bus llega al cliente.

    Un empleado recibe solo sus propias alertas (marcación con incidencia);
    las alertas globales (cuota bloqueada, fraude) llegan a todos los
    conectados autenticados. Se entrega el historial no leído al conectar.
    """
    try:
        usuario = _usuario_por_token_query(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    db = _cliente()
    try:
        pendientes = db.listar_alertas(no_leidas=True, limite=20)
        for alerta in pendientes:
            if (
                alerta.get("usuario_id") is None
                or int(alerta.get("usuario_id") or 0) == int(usuario["id"])
            ):
                await websocket.send_json(_alerta_json(alerta))
    finally:
        db.cerrar()

    def remitente(alerta: Dict[str, Any]) -> None:
        if (
            alerta.get("usuario_id") is None
            or int(alerta.get("usuario_id") or 0) == int(usuario["id"])
        ):
            try:
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json(_alerta_json(alerta)), loop
                )
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    notifications.BUS.suscribir(remitente)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        notifications.BUS.desuscribir(remitente)


class AlertaRequest(BaseModel):
    tipo: str
    severidad: str = "media"
    mensaje: str
    detalle: str = ""
    usuario_id: Optional[int] = None


@app.post("/api/alertas")
def api_publicar_alerta(
    payload: AlertaRequest,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Publica una alerta desde el escritorio (kiosco u otro origen).

    Restringido a Recursos Humanos: una alerta se difunde a todos los
    clientes conectados, de modo que sin control de rol cualquier empleado
    podía inyectar contenido en la pantalla del resto y fabricar evidencia
    de fraude contra un tercero.
    """
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return notifications.registrar_alerta(
            db,
            payload.tipo,
            payload.severidad,
            payload.mensaje.strip(),
            payload.detalle.strip(),
            payload.usuario_id,
        )
    finally:
        db.cerrar()


@app.get("/api/alertas")
def api_listar_alertas(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Lista de alertas para el Panel de Gestión (RRHH/Administrador)."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")
    db = _cliente()
    try:
        return {
            "alertas": db.listar_alertas(limite=60),
            "no_leidas": len(db.listar_alertas(no_leidas=True)),
        }
    finally:
        db.cerrar()


@app.post("/api/alertas/leidas")
def api_marcar_alertas_leidas(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Marca todas las alertas como leídas (Panel de Gestión)."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")
    db = _cliente()
    try:
        return {"marcadas": db.marcar_alertas_leidas()}
    finally:
        db.cerrar()


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    """Entrega la aplicación web (kiosco, portal y panel de gestión)."""
    return FileResponse(ESTATICOS / "index.html", media_type="text/html")


def _frenar(request: Request, cedula: str) -> str:
    """Aplica el freno de intentos y devuelve la clave con que se contabiliza.

    Se cuenta por cédula **y** por origen: por cédula para que un diccionario
    contra una persona se agote, y por IP para que recorrer la plantilla
    entera probando una contraseña común tampoco salga gratis.
    """
    origen = request.client.host if request.client else "desconocido"
    for clave in (f"u:{cedula.lower()}", f"ip:{origen}"):
        try:
            rate_limit.ACCESO.verificar(clave)
        except rate_limit.LimiteExcedido as limite:
            raise HTTPException(status_code=429, detail=str(limite))
    return f"u:{cedula.lower()}"


def _registrar_fallo(request: Request, cedula: str) -> None:
    """Contabiliza el intento fallido en las dos dimensiones."""
    origen = request.client.host if request.client else "desconocido"
    rate_limit.ACCESO.fallo(f"u:{cedula.lower()}")
    rate_limit.ACCESO.fallo(f"ip:{origen}")


def _limpiar_freno(request: Request, cedula: str) -> None:
    """Una autenticación válida cierra el episodio de esa identidad."""
    origen = request.client.host if request.client else "desconocido"
    rate_limit.ACCESO.exito(f"u:{cedula.lower()}")
    rate_limit.ACCESO.exito(f"ip:{origen}")


@app.post("/api/login")
def api_login(payload: LoginRequest, request: Request) -> Dict[str, Any]:
    """Valida credenciales con bcrypt y emite el JWT de acceso."""
    cedula = payload.cedula.strip()
    _frenar(request, cedula)
    db = _cliente()
    try:
        user = auth.authenticate(db, cedula, payload.password)
        if not user:
            _registrar_fallo(request, cedula)
            raise HTTPException(status_code=401, detail="Cédula o contraseña incorrectas.")
        _limpiar_freno(request, cedula)
        rol = auth.get_role_name(db, user)
        token = auth.crear_token_acceso(user["id"], rol)
        return {
            "token": token,
            "rol": rol,
            "nombre": user["full_name"],
            "vigencia_horas": auth.JWT_EXPIRACION_HORAS,
        }
    finally:
        db.cerrar()


@app.get("/api/resumen")
def api_resumen(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Tablero personal: vacaciones, permisos del mes, marcas y horas extra."""
    db = _cliente()
    try:
        return reports.resumen_empleado(db, usuario)
    finally:
        db.cerrar()


@app.get("/api/permiso/{solicitud_id}/pdf")
def api_permiso_pdf(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Entrega el PDF oficial del permiso si pertenece al usuario autenticado.

    La credencial viaja en la cabecera y no en la query string: un token en
    la URL queda escrito en los registros de acceso y en el historial del
    navegador. El cliente descarga el archivo por ``fetch`` y lo guarda como
    blob.
    """
    db = _cliente()
    try:
        justificacion = db.get_justificacion(solicitud_id)
        if not justificacion or justificacion["usuario_id"] != usuario["id"]:
            raise HTTPException(status_code=404, detail="Permiso no encontrado.")
    finally:
        db.cerrar()
    try:
        ruta = Path(reports.generar_pdf_permiso(solicitud_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(
        ruta,
        media_type="application/pdf",
        filename=ruta.name,
    )


@app.post("/api/consulta")
def api_consulta(
    payload: ConsultaRequest, usuario: Dict[str, Any] = Depends(_usuario_autenticado)
) -> Dict[str, Any]:
    """Historial del empleado autenticado: rango completo o un día puntual."""
    db = _cliente()
    try:
        if payload.fecha:
            try:
                puntual = datetime.date.fromisoformat(payload.fecha.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
            return reports.resumen_consulta(db, usuario, puntual)
        try:
            desde = datetime.date.fromisoformat(payload.desde.strip())
            hasta = datetime.date.fromisoformat(payload.hasta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Rango inválido. Use AAAA-MM-DD.")
        try:
            return reports.resumen_historico(db, usuario, desde, hasta)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()


class SolicitudPermisoRequest(BaseModel):
    """Pedido de permiso del empleado; la identidad viaja en el token."""

    tipo_permiso: str
    fecha_inicio: str
    fecha_fin: str
    horas_solicitadas: float = 0.0
    motivo: str


def _fecha(valor: str, campo: str) -> datetime.date:
    """Convierte una fecha ISO del cliente o devuelve un 422 con el campo."""
    try:
        return datetime.date.fromisoformat(valor.strip())
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422, detail=f"{campo} inválida. Use AAAA-MM-DD."
        )


@app.get("/api/permisos/catalogo")
def api_permisos_catalogo(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Artículos que el empleado puede invocar, con su saldo real.

    El portal arma el formulario con esto: el empleado elige un artículo del
    reglamento que le aplica y ve en el mismo lugar cuánto le queda y qué
    condiciones exige, en vez de pedir "permiso" a secas y esperar a que
    RRHH le explique por qué no correspondía.
    """
    db = _cliente()
    try:
        disponibilidad = reglamento.disponibilidad_permisos(db, usuario)
        return {
            "vinculo": usuario.get("tipo_vinculo") or "Funcionario",
            "articulos": [
                {
                    "tipo": d["tipo"],
                    "articulo": d["articulo"],
                    "nombre": d["nombre"],
                    "reglamento": d["reglamento"],
                    "unidad": d["unidad"],
                    "periodo": d["periodo"],
                    "cuota": d["cuota"],
                    "restantes": d["restantes_efectivos"],
                    "pendientes": d["pendientes"],
                    "solicitable": d["solicitable"],
                    "condiciones": d["condiciones"],
                }
                for d in disponibilidad
            ],
        }
    finally:
        db.cerrar()


@app.post("/api/permisos/solicitar")
def api_permisos_solicitar(
    payload: SolicitudPermisoRequest,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Presenta una solicitud de permiso validada contra el reglamento."""
    db = _cliente()
    try:
        inicio = _fecha(payload.fecha_inicio, "Fecha de inicio")
        fin = _fecha(payload.fecha_fin, "Fecha de fin")
        try:
            solicitud = auth.solicitar_permiso(
                db,
                usuario,
                payload.tipo_permiso,
                inicio,
                fin,
                payload.horas_solicitadas,
                payload.motivo,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        solicitud["mensaje"] = (
            f"Solicitud #{solicitud['id']} enviada a Recursos Humanos."
        )
        return solicitud
    finally:
        db.cerrar()


@app.get("/api/permisos/solicitudes")
def api_permisos_solicitudes(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Solicitudes presentadas por el empleado, con su estado actual."""
    db = _cliente()
    try:
        return db.listar_solicitudes_permiso(usuario["id"])
    finally:
        db.cerrar()


@app.get("/api/horas-extra/pdf")
def api_horas_extra_pdf(
    anio: int,
    mes: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Planilla de horas extraordinarias del propio empleado."""
    if not 1 <= mes <= 12:
        raise HTTPException(status_code=422, detail="Mes fuera de rango.")
    db = _cliente()
    try:
        ruta = Path(reports.generar_pdf_horas_extra(db, usuario, anio, mes))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name)


@app.get("/api/constancia/pdf")
def api_constancia_pdf(
    desde: str,
    hasta: str,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Constancia de asistencia del propio empleado, sin pasar por ventanilla."""
    inicio = _fecha(desde, "Fecha de inicio")
    fin = _fecha(hasta, "Fecha de fin")
    db = _cliente()
    try:
        ruta = Path(reports.generar_pdf_constancia(db, usuario, inicio, fin))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name)


@app.post("/api/reclamo")
def api_reclamo(
    payload: ReclamoRequest, usuario: Dict[str, Any] = Depends(_usuario_autenticado)
) -> Dict[str, Any]:
    """Registra una solicitud de corrección en estado Pendiente."""
    db = _cliente()
    try:
        if payload.tipo_marca not in ("Entrada", "Salida"):
            raise HTTPException(status_code=422, detail="Tipo de marca inválido.")
        try:
            fecha = datetime.date.fromisoformat(payload.fecha.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
        try:
            hora = datetime.time.fromisoformat(payload.hora_propuesta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Hora inválida. Use HH:MM.")
        motivo = payload.motivo.strip()
        if len(motivo) < 10:
            raise HTTPException(
                status_code=422, detail="Explique el motivo (mínimo 10 caracteres)."
            )
        solicitud_id = db.crear_solicitud_correccion(
            usuario["id"], fecha, payload.tipo_marca, hora, motivo
        )
        return {
            "id": solicitud_id,
            "estado": "Pendiente",
            "mensaje": f"Solicitud #{solicitud_id} enviada a Recursos Humanos.",
        }
    finally:
        db.cerrar()


class MarcarRequest(BaseModel):
    cedula: str
    password: str


class PersonalNuevo(BaseModel):
    username: str
    password: str
    full_name: str
    role_name: str
    salario_mensual: float = 0.0
    tipo_vinculo: str = "Funcionario"
    fecha_ingreso: Optional[str] = None


class PersonalEditar(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    role_name: Optional[str] = None
    salario_mensual: Optional[float] = None
    tipo_vinculo: Optional[str] = None
    fecha_ingreso: Optional[str] = None


class JustificacionRRHH(BaseModel):
    empleado_id: int
    tipo_permiso: str
    fecha_inicio: str
    fecha_fin: str
    horas_usadas: float = 0.0


def _exigir_rrhh(usuario: Dict[str, Any]) -> None:
    """Exige rol de Recursos Humanos o Administrador para el Panel de Gestión."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")


def _personal_publico(fila: Dict[str, Any]) -> Dict[str, Any]:
    """Filtra los campos de un empleado sin exponer credenciales."""
    return {
        k: fila[k]
        for k in (
            "id",
            "username",
            "full_name",
            "role_name",
            "salario_mensual",
            "tipo_vinculo",
            "activo",
            "fecha_ingreso",
            "fecha_baja",
            "creado_en",
        )
        if k in fila
    }


@app.get("/api/condicion-hoy")
def api_condicion_hoy() -> Dict[str, Any]:
    """Condición excepcional vigente hoy, para informarla en el kiosco.

    No expone datos personales: es el mismo cartel que la empresa colgaría
    en la puerta. La declara Recursos Humanos y no quien marca.
    """
    db = _cliente()
    try:
        excepcion = clock_engine.condicion_declarada(db, datetime.date.today())
        return {
            "condicion": excepcion["condicion"],
            "tolerancia_min": int(excepcion["tolerancia"].total_seconds() // 60),
        }
    finally:
        db.cerrar()


@app.post("/api/marcar")
def api_marcar(payload: MarcarRequest, request: Request) -> Dict[str, Any]:
    """Kiosco web: registra entrada/salida con cédula y contraseña.

    El navegador no tiene acceso a la cámara del kiosco, así que la marca
    nace como no verificable. Con ``BIOMETRIA_OBLIGATORIA`` activo la vía web
    queda cerrada y solo marca el kiosco físico, que sí tiene cámara.
    """
    cedula = payload.cedula.strip()
    _frenar(request, cedula)
    db = _cliente()
    try:
        usuario = auth.authenticate(db, cedula, payload.password)
        if not usuario:
            _registrar_fallo(request, cedula)
            raise HTTPException(
                status_code=401, detail="Cédula o contraseña incorrectas."
            )
        _limpiar_freno(request, cedula)
        decision = facial.decidir(
            facial.Resultado(
                facial.NO_VERIFICABLE,
                "Marcación por navegador, sin captura biométrica.",
            )
        )
        if not decision.permitir:
            raise HTTPException(
                status_code=403,
                detail="La verificación biométrica es obligatoria: marcá en el kiosco.",
            )
        engine = clock_engine.ClockEngine(db, usuario)
        try:
            registro_id, momento, tipo = engine.registrar_asistencia(decision.marca)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        if tipo == "ENTRADA":
            notifications.registrar_alerta(
                db,
                "marca_sin_verificar",
                decision.severidad,
                f"Marca por navegador sin verificación biométrica de "
                f"{usuario['full_name']}.",
                decision.motivo,
                usuario_id=usuario["id"],
            )
        return {
            "nombre": usuario["full_name"],
            "tipo": tipo,
            "hora": momento.strftime("%H:%M:%S"),
            "momento": momento.isoformat(),
            "ticket": reports.comprobante_marcacion(registro_id, momento, tipo),
        }
    finally:
        db.cerrar()


@app.get("/api/panel/resumen")
def api_panel_resumen(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Resumen operativo del Panel de Gestión (RRHH/Administrador)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "personal": len(db.list_users()),
            "marcas_hoy": db.count_marcajes_hoy(),
            "justificaciones": db.contar_justificaciones(),
            "correcciones_pendientes": sum(
                1
                for c in db.list_solicitudes_correccion()
                if c.get("estado") == "Pendiente"
            ),
            "permisos_pendientes": len(
                db.listar_solicitudes_permiso(solo_pendientes=True)
            ),
            "alertas_no_leidas": len(db.listar_alertas(no_leidas=True)),
            "marcas_sin_verificar": db.contar_marcas_sin_verificar(),
            "condicion_hoy": clock_engine.condicion_declarada(
                db, datetime.date.today()
            )["condicion"],
        }
    finally:
        db.cerrar()


@app.get("/api/panel/personal")
def api_panel_personal(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Lista de empleados (sin credenciales) y roles disponibles."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "roles": [r["nombre"] for r in db.list_roles()],
            "personal": [
                _personal_publico(u) for u in db.list_users(incluir_bajas=True)
            ],
        }
    finally:
        db.cerrar()


@app.post("/api/panel/personal")
def api_panel_personal_crear(
    payload: PersonalNuevo,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Crea un empleado desde el Panel de Gestión."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            nuevo_id = auth.create_user(
                db,
                usuario,
                payload.username.strip(),
                payload.password,
                payload.full_name.strip(),
                payload.role_name,
                payload.salario_mensual,
                payload.tipo_vinculo,
                _fecha(payload.fecha_ingreso, "Fecha de ingreso")
                if payload.fecha_ingreso else None,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"id": nuevo_id, "mensaje": "Personal creado correctamente."}
    finally:
        db.cerrar()


@app.put("/api/panel/personal/{user_id}")
def api_panel_personal_editar(
    user_id: int,
    payload: PersonalEditar,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Actualiza los datos de un empleado."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            auth.update_user(
                db,
                usuario,
                user_id,
                full_name=payload.full_name.strip() if payload.full_name else None,
                password=payload.password or None,
                role_name=payload.role_name or None,
                salario_mensual=payload.salario_mensual,
                tipo_vinculo=payload.tipo_vinculo or None,
                fecha_ingreso=_fecha(payload.fecha_ingreso, "Fecha de ingreso")
                if payload.fecha_ingreso else None,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"mensaje": "Personal actualizado correctamente."}
    finally:
        db.cerrar()


@app.post("/api/panel/personal/{user_id}/baja")
def api_panel_personal_baja(
    user_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Da de baja a un empleado conservando su historial de marcajes."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            baja = auth.dar_de_baja(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {
            "mensaje": f"{baja['nombre']} dado de baja el {baja['fecha_baja']}. "
            "Su historial se conserva."
        }
    finally:
        db.cerrar()


@app.post("/api/panel/personal/{user_id}/reincorporar")
def api_panel_personal_reincorporar(
    user_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Reincorpora a un empleado dado de baja."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            alta = auth.reincorporar(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"mensaje": f"{alta['nombre']} reincorporado."}
    finally:
        db.cerrar()


@app.delete("/api/panel/personal/{user_id}")
def api_panel_personal_eliminar(
    user_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Elimina un empleado (queda auditado en logs_auditoria)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            auth.delete_user(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"mensaje": "Personal eliminado correctamente."}
    finally:
        db.cerrar()


@app.get("/api/panel/justificaciones")
def api_panel_justificaciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Justificaciones emitidas, empleados y catálogo de permisos."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "justificaciones": db.list_justificaciones(),
            "personal": [_personal_publico(u) for u in db.list_users()],
            "tipos": list(auth.TIPOS_PERMISO),
        }
    finally:
        db.cerrar()


@app.post("/api/panel/justificaciones")
def api_panel_justificaciones_crear(
    payload: JustificacionRRHH,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Emitir una justificación oficial (valida reglamento y cuotas)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            inicio = datetime.date.fromisoformat(payload.fecha_inicio.strip())
            fin = datetime.date.fromisoformat(payload.fecha_fin.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Fechas inválidas. Use AAAA-MM-DD.")
        try:
            solicitud_id = auth.crear_justificacion(
                db,
                usuario,
                payload.empleado_id,
                payload.tipo_permiso,
                inicio,
                fin,
                payload.horas_usadas,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {
            "id": solicitud_id,
            "mensaje": f"Justificación #{solicitud_id} emitida y aprobada.",
        }
    finally:
        db.cerrar()


@app.get("/api/panel/justificaciones/{solicitud_id}/pdf")
def api_panel_justificaciones_pdf(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Descarga del PDF oficial de una justificación (solo RRHH/Admin)."""
    _exigir_rrhh(usuario)
    try:
        ruta = reports.generar_pdf_permiso(solicitud_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return FileResponse(
        ruta, media_type="application/pdf", filename=Path(ruta).name
    )


@app.get("/api/panel/correcciones")
def api_panel_correcciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Solicitudes de corrección de marcaje con su estado."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.list_solicitudes_correccion()
    finally:
        db.cerrar()


def _resolver_correccion(
    solicitud_id: int, aprobar: bool, usuario: Dict[str, Any]
) -> Dict[str, Any]:
    db = _cliente()
    try:
        try:
            estado = auth.aprobar_solicitud_correccion(db, usuario, solicitud_id, aprobar)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"estado": estado, "mensaje": f"Solicitud #{solicitud_id} {estado.lower()}."}
    finally:
        db.cerrar()


@app.post("/api/panel/correcciones/{solicitud_id}/aprobar")
def api_panel_correcciones_aprobar(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Aprueba la corrección: materializa la marca propuesta."""
    _exigir_rrhh(usuario)
    return _resolver_correccion(solicitud_id, True, usuario)


@app.post("/api/panel/correcciones/{solicitud_id}/rechazar")
def api_panel_correcciones_rechazar(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Rechaza la corrección sin tocar los marcajes."""
    _exigir_rrhh(usuario)
    return _resolver_correccion(solicitud_id, False, usuario)


class ResolucionPermiso(BaseModel):
    aprobar: bool
    observacion: str = ""


class CondicionDia(BaseModel):
    fecha: str
    condicion: str
    tolerancia_min: int = 30
    nota: str = ""


@app.get("/api/panel/solicitudes-permiso")
def api_panel_solicitudes_permiso(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Bandeja de pedidos de permiso presentados desde el portal."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.listar_solicitudes_permiso()
    finally:
        db.cerrar()


@app.post("/api/panel/solicitudes-permiso/{solicitud_id}/resolver")
def api_panel_resolver_permiso(
    solicitud_id: int,
    payload: ResolucionPermiso,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Aprueba o rechaza un pedido; aprobar emite la justificación oficial."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            resultado = auth.resolver_solicitud_permiso(
                db, usuario, solicitud_id, payload.aprobar, payload.observacion
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        resultado["mensaje"] = (
            f"Solicitud #{solicitud_id} {resultado['estado'].lower()}."
        )
        return resultado
    finally:
        db.cerrar()


@app.get("/api/panel/condiciones")
def api_panel_condiciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Condiciones excepcionales declaradas, con quién firmó cada una."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.listar_condiciones_dia()
    finally:
        db.cerrar()


@app.post("/api/panel/condiciones")
def api_panel_condiciones_declarar(
    payload: CondicionDia,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Declara la condición de un día para toda la plantilla."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        fecha = _fecha(payload.fecha, "Fecha")
        try:
            auth.declarar_condicion_dia(
                db, usuario, fecha, payload.condicion,
                payload.tolerancia_min, payload.nota,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {
            "mensaje": f"{payload.condicion} declarada para el {fecha.isoformat()}."
        }
    finally:
        db.cerrar()


@app.delete("/api/panel/condiciones/{fecha}")
def api_panel_condiciones_revocar(
    fecha: str,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Revoca la condición de un día; vuelve a regir la tolerancia ordinaria."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        dia = _fecha(fecha, "Fecha")
        if not db.borrar_condicion_dia(dia):
            raise HTTPException(status_code=404, detail="Ese día no tiene condición declarada.")
        db.registrar_auditoria(
            usuario["id"], "REVOCAR", "condiciones_dia", 0,
            anterior={"fecha": dia.isoformat()},
        )
        return {"mensaje": f"Condición del {dia.isoformat()} revocada."}
    finally:
        db.cerrar()


@app.get("/api/panel/horas-extra/{user_id}/pdf")
def api_panel_horas_extra_pdf(
    user_id: int,
    anio: int,
    mes: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Planilla de horas extraordinarias de cualquier empleado (RRHH/Admin)."""
    _exigir_rrhh(usuario)
    if not 1 <= mes <= 12:
        raise HTTPException(status_code=422, detail="Mes fuera de rango.")
    db = _cliente()
    try:
        empleado = db.get_user_by_id(user_id)
        if not empleado:
            raise HTTPException(status_code=404, detail="Empleado no encontrado.")
        ruta = Path(reports.generar_pdf_horas_extra(db, empleado, anio, mes))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name)


@app.get("/api/panel/auditoria")
def api_panel_auditoria(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Bitácora de auditoría para trazabilidad (RRHH/Administrador)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.listar_auditoria(limite=100)
    finally:
        db.cerrar()


def main() -> None:
    """Levanta el servidor web del Portal del Empleado.

    Respeta ``HOST`` y ``PORT`` del entorno para adaptarse a los proxies
    de plataformas cloud (Render, Railway) sin modificar el código.

    Aplica el esquema antes de levantar el proceso para que el arranque en
    desarrollo siga siendo un solo comando. En producción esa migración es
    un paso propio del despliegue (ver el ``CMD`` del Dockerfile), porque
    con varios workers el DDL concurrente se bloquea entre sí.
    """
    import os

    import uvicorn

    import migrate

    migrate.aplicar()

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()