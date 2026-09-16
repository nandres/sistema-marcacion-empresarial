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
import os
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator, Dict, List, Optional

import jwt
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import clock_engine
import database
import facial
import notifications
import rate_limit
import registro
import reglamento
import reports

# Al importar y no al arrancar: gunicorn levanta este módulo en cada worker y
# las pruebas lo importan sin pasar por el ciclo de vida. Repetirlo no duplica
# manejadores.
registro.configurar()
_log = registro.obtener("web")

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
        pendientes = db.migraciones_pendientes()
        if pendientes:
            raise RuntimeError(
                "El esquema de la base no está al día; falta aplicar: "
                + ", ".join(pendientes)
                + ". Ejecutá 'python migrate.py' antes de iniciar el servidor."
            )
        _log.info(
            "esquema en versión %s · escuchando en el puerto %s",
            db.version_esquema(), os.getenv("PORT", "8000"),
        )
    finally:
        db.cerrar()
    # El bus de alertas vive en la memoria de un worker y el servidor corre
    # con varios: sin esta escucha, una alerta publicada en uno no llega a los
    # WebSockets conectados a los otros.
    escucha = notifications.EscuchaAlertas(_conexion_de_escucha)
    escucha.start()
    try:
        yield
    finally:
        escucha.detener()
        database.cerrar_pools()


def _conexion_de_escucha() -> Optional[database.Database]:
    """Conexión propia del hilo que escucha el canal de alertas."""
    try:
        db = database.Database()
        db.connect()
        return db
    except Exception:
        return None


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


RUTAS_CALLADAS: frozenset = frozenset({"/salud"})
"""Rutas que no se registran cuando salen bien.

El chequeo de salud corre cada treinta segundos para siempre y los estáticos
son varios por carga de página: registrarlos en verde enterraría todo lo demás.
Cuando fallan se registran igual, que es cuando importan.
"""


# Declarado después del de cabeceras para que quede por fuera: así alcanza
# también a lo que revienta ahí adentro.
@app.middleware("http")
async def registrar_peticion(request: Request, siguiente):
    """Una línea por petición, con un identificador que hilvana las suyas.

    Va en un middleware y no en cada endpoint porque tiene que alcanzar a lo
    que nunca llega a un endpoint: un 404, un cuerpo que no valida, una
    excepción levantada antes de entrar.
    """
    identificador = registro.abrir_peticion()
    inicio = perf_counter()
    try:
        respuesta = await siguiente(request)
    except Exception:
        _log.exception(
            "%s %s cortó sin llegar a responder", request.method, request.url.path
        )
        raise
    linea = (request.method, request.url.path, respuesta.status_code,
             (perf_counter() - inicio) * 1000)
    if respuesta.status_code >= 500:
        _log.error("%s %s -> %s en %.0f ms", *linea)
    elif respuesta.status_code >= 400:
        _log.warning("%s %s -> %s en %.0f ms", *linea)
    elif not (request.url.path in RUTAS_CALLADAS
              or request.url.path.startswith("/static/")):
        _log.info("%s %s -> %s en %.0f ms", *linea)
    # Devolverlo al cliente cierra el circuito: quien reporta "me dio error a
    # las 7:42" trae el identificador y el registro se busca por ahí.
    respuesta.headers["X-Peticion"] = identificador
    return respuesta


app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/salud")
def api_salud(respuesta: Response) -> Dict[str, str]:
    """Si el proceso atiende **y** la base contesta.

    El chequeo anterior pedía la portada, que arma el portal entero y no toca
    PostgreSQL: con la base caída el contenedor se seguía reportando sano y
    nadie lo reiniciaba. No devuelve versiones ni nombres de host porque lo
    consulta cualquiera, sin credenciales.
    """
    db = database.Database()
    try:
        db.connect()
        db.latido()
    except Exception as error:
        _log.error("la base no responde al chequeo de salud (%s)",
                   type(error).__name__)
        respuesta.status_code = 503
        return {"estado": "degradado"}
    finally:
        db.cerrar()
    return {"estado": "ok"}

class LoginRequest(BaseModel):
    """Credenciales del empleado para emitir el token de acceso.

    ``empresa`` es el nombre corto del cliente y solo hace falta cuando la
    misma cédula trabaja en dos de los alojados acá.
    """

    cedula: str
    password: str
    empresa: str = ""


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


def _cliente(empresa_id: Optional[int] = None) -> database.Database:
    """Abre una conexión fresca por petición para evitar sesiones cruzadas.

    No aplica migraciones: el DDL vive en ``migrate.py`` y corre una sola vez
    antes de levantar el servidor. Ejecutarlo por petición tomaba locks
    exclusivos sobre ``justificaciones`` y serializaba el pico de marcación.

    La conexión nace **sin empresa**. Se la ata la sesión del usuario, así
    que un endpoint que se olvide de pasarla no devuelve los datos de todos
    los clientes: falla.
    """
    db = database.Database(empresa_id=empresa_id, agrupada=True)
    db.connect()
    return db


def _cliente_de(usuario: Dict[str, Any]) -> database.Database:
    """Conexión atada a la empresa del usuario autenticado."""
    return _cliente(usuario["empresa_id"])


def _empresa_del_host(request: Request) -> str:
    """Nombre corto del cliente según el subdominio por el que entró.

    ``acme.miapp.com.py`` identifica al cliente antes de que nadie escriba
    nada, que es como se espera que funcione un SaaS. Es una comodidad y no
    un control: quien entra por la dirección genérica sigue pudiendo acceder
    con sus credenciales, y el aislamiento lo siguen sosteniendo la empresa
    del token y las políticas de la base.

    Hace falta declarar ``DOMINIO_BASE``. Contar etiquetas no sirve: en
    Paraguay los dominios son ``.com.py``, así que ``miapp.com.py`` tiene tres
    partes sin tener ningún subdominio, y adivinar ahí manda a la gente al
    cliente equivocado. Sin la variable no se resuelve nada, que es lo que
    corresponde en una instalación de un solo cliente.
    """
    base = (os.getenv("DOMINIO_BASE") or "").strip().lower().lstrip(".")
    if not base:
        return ""
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    if not host.endswith("." + base):
        return ""
    etiqueta = host[: -(len(base) + 1)].split(".")[-1]
    return "" if etiqueta in ("www", "") else etiqueta


COOKIE_SESION: str = "marcacion_sesion"
"""Nombre de la cookie de sesión del navegador.

El token dejó de viajar en la URL del WebSocket y dejó de guardarse en
`localStorage`. En la URL quedaba escrito en los logs de acceso del servidor,
en el historial del navegador y en la cabecera `Referer` hacia cualquier
recurso externo; en `localStorage` lo alcanza cualquier script que llegue a
correr en la página.

La cookie es `HttpOnly` —ningún script la lee— y `SameSite=Strict`, que es lo
que la vuelve inmune a CSRF: el navegador no la manda en peticiones que nacen
de otro sitio. El encabezado `Authorization` se sigue aceptando para los
clientes que no son un navegador.
"""


def _asegurada(request: Request) -> bool:
    """Indica si la cookie puede marcarse ``Secure`` sin romper el acceso.

    Sobre HTTP plano una cookie ``Secure`` no se guarda y nadie entraría. Se
    marca cuando la conexión es HTTPS, contemplando el encabezado que pone el
    proxy de las plataformas cloud, que son las que terminan el TLS.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _abrir_sesion(respuesta: Response, request: Request, token: str) -> None:
    """Deja la sesión del navegador en una cookie que ningún script puede leer."""
    respuesta.set_cookie(
        COOKIE_SESION,
        token,
        max_age=auth.JWT_EXPIRACION_HORAS * 3600,
        httponly=True,
        samesite="strict",
        secure=_asegurada(request),
        path="/",
    )


def _cerrar_sesion(respuesta: Response) -> None:
    """Borra la cookie de sesión."""
    respuesta.delete_cookie(COOKIE_SESION, path="/", samesite="strict")


def _token_de(request: Request, authorization: Optional[str]) -> str:
    """Toma el token del encabezado o, si no viene, de la cookie de sesión.

    El encabezado tiene prioridad porque es lo que usan los clientes que no
    son un navegador (la suite de pruebas, un kiosco propio, una integración).
    """
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(COOKIE_SESION, "")


def _usuario_del_token(token: str) -> Dict[str, Any]:
    """Resuelve el usuario de un token firmado, o eleva 401.

    La empresa sale del token y acota la búsqueda del propio usuario: si el
    identificador y la empresa no se corresponden, el token no resuelve a
    nadie en lugar de resolver a alguien de otro cliente.
    """
    sin_sesion = HTTPException(
        status_code=401,
        detail="Sesión inválida o expirada.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Se requiere un token de acceso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = auth.verificar_token_acceso(token)
    except jwt.InvalidTokenError:
        raise sin_sesion from None
    db = _cliente(claims.get("emp"))
    try:
        usuario = db.get_user_by_id(int(claims["sub"]))
        if not usuario:
            raise ValueError("Usuario del token inexistente.")
        return usuario
    except (ValueError, KeyError):
        raise sin_sesion from None
    finally:
        db.cerrar()


def _usuario_autenticado(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Resuelve el usuario de la petición, por encabezado o por cookie."""
    return _usuario_del_token(_token_de(request, authorization))


def _alerta_json(alerta: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte a JSON puro (send_json de WebSocket usa json.dumps plano)."""
    salida = dict(alerta)
    if isinstance(salida.get("creado_en"), datetime.datetime):
        salida["creado_en"] = salida["creado_en"].isoformat()
    return salida


@app.websocket("/ws/alertas")
async def ws_alertas(websocket: WebSocket) -> None:
    """Push en tiempo real: cada alerta publicada en el bus llega al cliente.

    Un empleado recibe solo sus propias alertas (marcación con incidencia);
    las alertas globales (cuota bloqueada, fraude) llegan a todos los
    conectados autenticados. Se entrega el historial no leído al conectar.
    """
    # El navegador no puede poner encabezados en el saludo de un WebSocket,
    # así que acá la cookie es el único camino: es justamente lo que reemplaza
    # al token en la URL, que quedaba escrito en los logs del servidor.
    try:
        usuario = _usuario_del_token(websocket.cookies.get(COOKIE_SESION, ""))
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    db = _cliente_de(usuario)
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
        # El bus vive en el proceso y lo comparten todos los clientes
        # alojados: la empresa se comprueba antes que el destinatario.
        if not notifications.es_de_la_empresa(alerta, usuario["empresa_id"]):
            return
        if (
            alerta.get("usuario_id") is None
            or int(alerta.get("usuario_id") or 0) == int(usuario["id"])
        ):
            # El navegador pudo haberse ido entre el filtro y el envío; una
            # alerta que no llega a un socket muerto no es una falla.
            with suppress(Exception):
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json(_alerta_json(alerta)), loop
                )

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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
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
            _log.warning("freno de intentos activo sobre %s desde %s", clave, origen)
            raise HTTPException(status_code=429, detail=str(limite)) from None
    return f"u:{cedula.lower()}"


def _registrar_fallo(request: Request, cedula: str) -> None:
    """Contabiliza el intento fallido en las dos dimensiones y lo deja anotado.

    La cédula entra al registro y la contraseña no, ni siquiera su longitud:
    sin saber contra qué identidad se probó, una racha de intentos fallidos no
    se distingue de un empleado que olvidó su clave.
    """
    origen = request.client.host if request.client else "desconocido"
    rate_limit.ACCESO.fallo(f"u:{cedula.lower()}")
    rate_limit.ACCESO.fallo(f"ip:{origen}")
    _log.warning("credenciales rechazadas para %r desde %s", cedula, origen)


def _limpiar_freno(request: Request, cedula: str) -> None:
    """Una autenticación válida cierra el episodio de esa identidad."""
    origen = request.client.host if request.client else "desconocido"
    rate_limit.ACCESO.exito(f"u:{cedula.lower()}")
    rate_limit.ACCESO.exito(f"ip:{origen}")


@app.post("/api/login")
def api_login(
    payload: LoginRequest, request: Request, respuesta: Response
) -> Dict[str, Any]:
    """Valida credenciales con bcrypt y abre la sesión.

    Devuelve el token para los clientes que no son un navegador y además lo
    deja en una cookie ``HttpOnly``: en el navegador el token no vuelve a
    pasar por JavaScript ni por ninguna URL.
    """
    cedula = payload.cedula.strip()
    _frenar(request, cedula)
    db = _cliente()
    try:
        user = auth.authenticate(
            db, cedula, payload.password,
            payload.empresa or _empresa_del_host(request),
        )
        if not user:
            _registrar_fallo(request, cedula)
            raise HTTPException(status_code=401, detail="Cédula o contraseña incorrectas.")
        _limpiar_freno(request, cedula)
        rol = auth.get_role_name(db, user)
        token = auth.crear_token_acceso(user["id"], rol, user["empresa_id"])
        _abrir_sesion(respuesta, request, token)
        return {
            "token": token,
            "rol": rol,
            "nombre": user["full_name"],
            "empresa": user.get("empresa_nombre", ""),
            "vigencia_horas": auth.JWT_EXPIRACION_HORAS,
        }
    finally:
        db.cerrar()


@app.post("/api/logout")
def api_logout(respuesta: Response) -> Dict[str, str]:
    """Cierra la sesión borrando la cookie.

    Hace falta un endpoint porque la cookie es ``HttpOnly``: el navegador no
    puede borrarla por su cuenta, que es exactamente la propiedad que la
    protege de un script inyectado.
    """
    _cerrar_sesion(respuesta)
    return {"mensaje": "Sesión cerrada."}


@app.get("/api/sesion")
def api_sesion(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Quién es el usuario de la sesión en curso.

    Con la sesión en una cookie que ningún script lee, al recargar la página
    el navegador no sabe quién es: lo pregunta acá.
    """
    db = _cliente_de(usuario)
    try:
        empresa = db.get_empresa(usuario["empresa_id"])
        return {
            "nombre": usuario["full_name"],
            "rol": usuario["role_name"],
            "empresa": empresa["razon_social"] if empresa else "",
        }
    finally:
        db.cerrar()


@app.get("/api/resumen")
def api_resumen(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Tablero personal: vacaciones, permisos del mes, marcas y horas extra."""
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        justificacion = db.get_justificacion(solicitud_id)
        if not justificacion or justificacion["usuario_id"] != usuario["id"]:
            raise HTTPException(status_code=404, detail="Permiso no encontrado.")
        ruta = Path(reports.generar_pdf_permiso(db, solicitud_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    finally:
        db.cerrar()
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
    db = _cliente_de(usuario)
    try:
        if payload.fecha:
            try:
                puntual = datetime.date.fromisoformat(payload.fecha.strip())
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="Fecha inválida. Use AAAA-MM-DD."
                ) from None
            return reports.resumen_consulta(db, usuario, puntual)
        try:
            desde = datetime.date.fromisoformat(payload.desde.strip())
            hasta = datetime.date.fromisoformat(payload.hasta.strip())
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Rango inválido. Use AAAA-MM-DD."
            ) from None
        try:
            return reports.resumen_historico(db, usuario, desde, hasta)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
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
        ) from None


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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
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
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        ruta = Path(reports.generar_pdf_horas_extra(db, usuario, anio, mes))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
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
    db = _cliente_de(usuario)
    try:
        ruta = Path(reports.generar_pdf_constancia(db, usuario, inicio, fin))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        db.cerrar()
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name)


@app.post("/api/reclamo")
def api_reclamo(
    payload: ReclamoRequest, usuario: Dict[str, Any] = Depends(_usuario_autenticado)
) -> Dict[str, Any]:
    """Registra una solicitud de corrección en estado Pendiente."""
    db = _cliente_de(usuario)
    try:
        if payload.tipo_marca not in ("Entrada", "Salida"):
            raise HTTPException(status_code=422, detail="Tipo de marca inválido.")
        try:
            fecha = datetime.date.fromisoformat(payload.fecha.strip())
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Fecha inválida. Use AAAA-MM-DD."
            ) from None
        try:
            hora = datetime.time.fromisoformat(payload.hora_propuesta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Hora inválida. Use HH:MM.") from None
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
    empresa: str = ""


class PersonalNuevo(BaseModel):
    username: str
    password: str
    full_name: str
    role_name: str
    salario_mensual: float = 0.0
    tipo_vinculo: str = "Funcionario"
    fecha_ingreso: Optional[str] = None
    turno_id: Optional[int] = None


class PersonalEditar(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    role_name: Optional[str] = None
    salario_mensual: Optional[float] = None
    tipo_vinculo: Optional[str] = None
    fecha_ingreso: Optional[str] = None
    turno_id: Optional[int] = None


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
            "turno_id",
            "turno_nombre",
            "ciclo_id",
            "ciclo_posicion",
        )
        if k in fila
    }


@app.get("/api/condicion-hoy")
def api_condicion_hoy(request: Request, empresa: str = "") -> Dict[str, Any]:
    """Condición excepcional vigente hoy, para informarla en el kiosco.

    No expone datos personales: es el mismo cartel que la empresa colgaría
    en la puerta. La declara Recursos Humanos y no quien marca.

    Es el único cartel que se lee sin sesión, así que la empresa llega por
    ``?empresa=<slug>``. Con una sola empresa alojada se resuelve sola; con
    varias y sin indicación no se muestra nada, porque adivinar sería mostrar
    el cartel de otro cliente.
    """
    db = _cliente()
    try:
        alojadas = db.listar_empresas()
        elegida_por_host = empresa or _empresa_del_host(request)
        if elegida_por_host:
            elegida = db.get_empresa_por_slug(elegida_por_host)
        elif len(alojadas) == 1:
            elegida = alojadas[0]
        else:
            elegida = None
        if not elegida:
            return {"condicion": "", "tolerancia_min": 0}
        db.empresa_id = elegida["id"]
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
        # El puesto se identifica antes que la persona, y de él sale la
        # empresa: un token de dispositivo dice de qué cliente es el kiosco
        # con más certeza que el nombre del host, que cualquiera puede fijar.
        dispositivo = auth.resolver_dispositivo(db, request.headers.get("X-Dispositivo", ""))
        if dispositivo is None and auth.dispositivo_obligatorio():
            _log.warning(
                "marca rechazada: puesto no habilitado desde %s",
                request.client.host if request.client else "desconocido",
            )
            raise HTTPException(
                status_code=403,
                detail="Este puesto no está habilitado para marcar. "
                       "Pedile a Recursos Humanos que lo dé de alta.",
            )
        empresa_pedida = payload.empresa or _empresa_del_host(request)
        if dispositivo is not None:
            db.empresa_id = dispositivo["empresa_id"]
            empresa_pedida = ""
        usuario = auth.authenticate(
            db, cedula, payload.password, empresa_pedida,
        )
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
            raise HTTPException(status_code=400, detail=str(error)) from None
        if dispositivo is not None:
            db.asignar_dispositivo_a_marcaje(registro_id, dispositivo["id"])
            db.marcar_dispositivo_visto(dispositivo["id"])
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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        vigentes = db.turnos_vigentes_de_la_plantilla(datetime.date.today())
        return {
            "roles": [r["nombre"] for r in db.list_roles()],
            "turnos": auth.listar_turnos(db, usuario),
            "personal": [
                dict(
                    _personal_publico(u),
                    turno_hoy=(vigentes.get(u["id"]) or {}).get("turno_nombre"),
                    turno_origen=(vigentes.get(u["id"]) or {}).get("origen"),
                )
                for u in db.list_users(incluir_bajas=True)
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
    db = _cliente_de(usuario)
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
                payload.turno_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
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
                turno_id=(
                    payload.turno_id
                    if "turno_id" in payload.model_fields_set
                    else auth.SIN_CAMBIO
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
    try:
        try:
            baja = auth.dar_de_baja(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
    try:
        try:
            alta = auth.reincorporar(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
    try:
        try:
            auth.delete_user(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return {"mensaje": "Personal eliminado correctamente."}
    finally:
        db.cerrar()


@app.get("/api/panel/justificaciones")
def api_panel_justificaciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Justificaciones emitidas, empleados y catálogo de permisos."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        try:
            inicio = datetime.date.fromisoformat(payload.fecha_inicio.strip())
            fin = datetime.date.fromisoformat(payload.fecha_fin.strip())
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Fechas inválidas. Use AAAA-MM-DD."
            ) from None
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
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
    try:
        ruta = reports.generar_pdf_permiso(db, solicitud_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        db.cerrar()
    return FileResponse(
        ruta, media_type="application/pdf", filename=Path(ruta).name
    )


@app.get("/api/panel/correcciones")
def api_panel_correcciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Solicitudes de corrección de marcaje con su estado."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return db.list_solicitudes_correccion()
    finally:
        db.cerrar()


def _resolver_correccion(
    solicitud_id: int, aprobar: bool, usuario: Dict[str, Any]
) -> Dict[str, Any]:
    db = _cliente_de(usuario)
    try:
        try:
            estado = auth.aprobar_solicitud_correccion(db, usuario, solicitud_id, aprobar)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        try:
            resultado = auth.resolver_solicitud_permiso(
                db, usuario, solicitud_id, payload.aprobar, payload.observacion
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
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
    db = _cliente_de(usuario)
    try:
        fecha = _fecha(payload.fecha, "Fecha")
        try:
            auth.declarar_condicion_dia(
                db, usuario, fecha, payload.condicion,
                payload.tolerancia_min, payload.nota,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
    db = _cliente_de(usuario)
    try:
        dia = _fecha(fecha, "Fecha")
        if not db.borrar_condicion_dia(dia):
            raise HTTPException(
                status_code=404, detail="Ese día no tiene condición declarada."
            )
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
    db = _cliente_de(usuario)
    try:
        empleado = db.get_user_by_id(user_id)
        if not empleado:
            raise HTTPException(status_code=404, detail="Empleado no encontrado.")
        ruta = Path(reports.generar_pdf_horas_extra(db, empleado, anio, mes))
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        db.cerrar()
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name)


@app.get("/api/panel/auditoria")
def api_panel_auditoria(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Bitácora de auditoría para trazabilidad (RRHH/Administrador)."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return db.listar_auditoria(limite=100)
    finally:
        db.cerrar()


class TramoHorario(BaseModel):
    entrada: str
    salida: str


class TurnoNuevo(BaseModel):
    nombre: str
    tramos: List[TramoHorario]
    dias: str = "1111100"
    sucursal: str = "Casa Central"
    tolerancia_min: Optional[int] = None


class TurnoEditar(BaseModel):
    """Cambio de turno; ``vigente_desde`` decide a partir de cuándo rige.

    Vacío significa hoy. Una fecha pasada reescribe la liquidación de ese
    período, así que se pide explícita y nunca se asume.
    """

    nombre: Optional[str] = None
    tramos: Optional[List[TramoHorario]] = None
    dias: Optional[str] = None
    sucursal: Optional[str] = None
    tolerancia_min: Optional[int] = None
    borrar_tolerancia: bool = False
    vigente_desde: Optional[str] = None


class TurnoBase(BaseModel):
    turno_id: Optional[int] = None


class RotacionTurno(BaseModel):
    turno_id: int
    desde: str
    hasta: Optional[str] = None
    motivo: str = ""


@app.get("/api/turno")
def api_turno_propio(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Horario del empleado autenticado, con sus rotaciones vigentes."""
    db = _cliente_de(usuario)
    try:
        return auth.turno_de_empleado(db, usuario, usuario["id"])
    finally:
        db.cerrar()


@app.get("/api/panel/turnos")
def api_panel_turnos(
    incluir_inactivos: bool = False,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Catálogo de turnos con su horario, sus días y su dotación."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return auth.listar_turnos(db, usuario, incluir_inactivos)
    finally:
        db.cerrar()


@app.get("/api/panel/turnos/{turno_id}/historial")
def api_panel_turno_historial(
    turno_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Las definiciones que tuvo el turno y desde cuándo rigió cada una."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.historial_turno(db, usuario, turno_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
    finally:
        db.cerrar()


@app.post("/api/panel/turnos")
def api_panel_turnos_crear(
    payload: TurnoNuevo,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Da de alta un turno."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.crear_turno(
                db,
                usuario,
                payload.nombre,
                [t.model_dump() for t in payload.tramos],
                payload.dias,
                payload.sucursal,
                payload.tolerancia_min,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.put("/api/panel/turnos/{turno_id}")
def api_panel_turnos_editar(
    turno_id: int,
    payload: TurnoEditar,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Modifica un turno; el cambio rige hacia adelante."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.actualizar_turno(
                db,
                usuario,
                turno_id,
                nombre=payload.nombre,
                tramos=(
                    [t.model_dump() for t in payload.tramos]
                    if payload.tramos is not None
                    else None
                ),
                dias=payload.dias,
                sucursal=payload.sucursal,
                tolerancia_min=(
                    None if payload.borrar_tolerancia
                    else (payload.tolerancia_min
                          if payload.tolerancia_min is not None
                          else auth.SIN_CAMBIO)
                ),
                vigente_desde=payload.vigente_desde,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.post("/api/panel/turnos/{turno_id}/predeterminado")
def api_panel_turnos_predeterminado(
    turno_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Designa el turno que rige para quien no tiene ninguno asignado."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.designar_turno_predeterminado(db, usuario, turno_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.delete("/api/panel/turnos/{turno_id}")
def api_panel_turnos_retirar(
    turno_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Retira un turno de circulación, o lo borra si nunca se usó."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            resultado = auth.retirar_turno(db, usuario, turno_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return {"mensaje": f"Turno {resultado}."}
    finally:
        db.cerrar()


@app.get("/api/panel/personal/{user_id}/turno")
def api_panel_turno_empleado(
    user_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Horario vigente de un empleado con sus rotaciones programadas."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.turno_de_empleado(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
    finally:
        db.cerrar()


@app.post("/api/panel/personal/{user_id}/turno")
def api_panel_turno_base(
    user_id: int,
    payload: TurnoBase,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Fija el turno de contrato de un legajo."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.asignar_turno_base(db, usuario, user_id, payload.turno_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.post("/api/panel/personal/{user_id}/rotacion")
def api_panel_rotacion(
    user_id: int,
    payload: RotacionTurno,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Programa una rotación con vigencia sobre el turno de contrato."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.rotar_turno(
                db, usuario, user_id, payload.turno_id,
                payload.desde, payload.hasta, payload.motivo,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.delete("/api/panel/rotaciones/{asignacion_id}")
def api_panel_rotacion_revocar(
    asignacion_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Cancela una rotación; el empleado vuelve a su turno de contrato."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            auth.revocar_rotacion(db, usuario, asignacion_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return {"mensaje": "Rotación cancelada."}
    finally:
        db.cerrar()


class CicloNuevo(BaseModel):
    nombre: str
    turnos: List[int]
    dias_por_tramo: int = 7
    ancla: Optional[str] = None


class CicloDeEmpleado(BaseModel):
    ciclo_id: Optional[int] = None
    posicion: int = 0


@app.get("/api/panel/ciclos")
def api_panel_ciclos(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Ciclos de rotación con su secuencia y su dotación."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return auth.listar_ciclos(db, usuario)
    finally:
        db.cerrar()


@app.post("/api/panel/ciclos")
def api_panel_ciclos_crear(
    payload: CicloNuevo,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Define una rotación automática entre dos o más turnos."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.crear_ciclo(
                db, usuario, payload.nombre, payload.turnos,
                payload.dias_por_tramo, payload.ancla,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.delete("/api/panel/ciclos/{ciclo_id}")
def api_panel_ciclos_eliminar(
    ciclo_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, str]:
    """Elimina un ciclo que no tenga gente rotando con él."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            auth.eliminar_ciclo(db, usuario, ciclo_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return {"mensaje": "Ciclo eliminado."}
    finally:
        db.cerrar()


class DispositivoNuevo(BaseModel):
    nombre: str
    ubicacion: str = ""


@app.get("/api/panel/dispositivos")
def api_panel_dispositivos(
    incluir_inactivos: bool = False,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Puestos de marcación dados de alta, con su actividad."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return auth.listar_dispositivos(db, usuario, incluir_inactivos)
    finally:
        db.cerrar()


@app.post("/api/panel/dispositivos")
def api_panel_dispositivos_crear(
    payload: DispositivoNuevo,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Da de alta un puesto y devuelve su token una sola vez.

    El token viaja en esta respuesta y no vuelve a poder leerse: en la base
    queda solo su hash. Si se pierde se revoca el puesto y se crea otro.
    """
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.registrar_dispositivo(
                db, usuario, payload.nombre, payload.ubicacion
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.delete("/api/panel/dispositivos/{dispositivo_id}")
def api_panel_dispositivos_revocar(
    dispositivo_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, str]:
    """Deja un puesto fuera de servicio sin borrar el origen de sus marcas."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            auth.revocar_dispositivo(db, usuario, dispositivo_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return {"mensaje": "Puesto de marcación revocado."}
    finally:
        db.cerrar()


@app.get("/api/panel/empresa")
def api_panel_empresa(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Datos del cliente alojado y cuánto de su plan lleva usado."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        empresa = db.get_empresa(usuario["empresa_id"]) or {}
        empleados = db.contar_empleados()
        cupo = empresa.get("max_empleados")
        return {
            "razon_social": empresa.get("razon_social", ""),
            "slug": empresa.get("slug", ""),
            "ruc": empresa.get("ruc", ""),
            "empleados": empleados,
            "max_empleados": cupo,
            "disponibles": None if cupo is None else max(0, int(cupo) - empleados),
        }
    finally:
        db.cerrar()


@app.post("/api/panel/personal/{user_id}/ciclo")
def api_panel_ciclo_empleado(
    user_id: int,
    payload: CicloDeEmpleado,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Pone (o saca) a un empleado de un ciclo de rotación."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        try:
            return auth.asignar_ciclo(
                db, usuario, user_id, payload.ciclo_id, payload.posicion
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    finally:
        db.cerrar()


@app.get("/api/rotacion")
def api_rotacion_propia(
    semanas: int = 6,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Qué turno le toca al empleado en los próximos tramos de su ciclo."""
    db = _cliente_de(usuario)
    try:
        return auth.calendario_de_rotacion(db, usuario, usuario["id"], semanas)
    finally:
        db.cerrar()


@app.get("/api/panel/personal/{user_id}/rotacion")
def api_panel_rotacion_empleado(
    user_id: int,
    semanas: int = 8,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Calendario de rotación de un empleado, para el panel."""
    _exigir_rrhh(usuario)
    db = _cliente_de(usuario)
    try:
        return auth.calendario_de_rotacion(db, usuario, user_id, semanas)
    finally:
        db.cerrar()


def main() -> None:
    """Levanta el servidor web del Portal del Empleado.

    Respeta ``HOST`` y ``PORT`` del entorno para adaptarse a los proxies
    de plataformas cloud (Render, Railway) sin modificar el código.

    Sobre una base sin migrar aplica el esquema, para que el arranque en
    desarrollo siga siendo un solo comando. Sobre una ya migrada no toca
    nada: en producción la migración es un paso propio del despliegue (ver
    el ``CMD`` del Dockerfile) y el rol que atiende tráfico no tiene —ni
    debe tener— permiso para alterar tablas.
    """
    import os

    import psycopg2
    import uvicorn

    import migrate

    db = database.Database()
    try:
        db.connect()
        listo = db.esquema_listo()
    finally:
        db.cerrar()
    if not listo:
        try:
            migrate.aplicar()
        except psycopg2.errors.InsufficientPrivilege:
            print(
                "El esquema no está aplicado y este rol no puede crearlo.\n"
                "Corré la migración con el rol administrador:\n"
                "  python src/migrate.py",
                file=sys.stderr,
            )
            raise SystemExit(1) from None

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
