"""Autenticación y control de accesos basado en roles (RBAC) con bcrypt.

Las contraseñas se encriptan con bcrypt (sal aleatoria embebida en el hash)
y nunca se almacenan en texto plano en PostgreSQL. Las operaciones de
gestión de usuarios registran automáticamente un evento en
``logs_auditoria`` con quién, qué, cuándo y los valores anterior/nuevo.
"""

from __future__ import annotations

import getpass
import os
from datetime import date, datetime, time, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar

import bcrypt
import jwt

from clock_engine import (
    calcular_horas_paraguay,
    es_feriado_o_domingo,
    evaluar_asistencia,
    persistir_desglose,
    turno_vigente,
)
from database import Database, load_dotenv
import notifications
import reglamento
import turnos

SIN_CAMBIO: Any = object()
"""Centinela para distinguir 'no lo toques' de 'ponelo en nulo'."""

ROLE_ADMIN: str = "Administrador"
ROLE_RRHH: str = "Recursos Humanos"
ROLE_EMPLEADO: str = "Empleado"

ROLES_GESTION_USUARIOS: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_REPORTES: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_MARCAJES: tuple = (ROLE_ADMIN, ROLE_RRHH, ROLE_EMPLEADO)

TIPOS_PERMISO: tuple = reglamento.TIPOS_PERMISO

TIPOS_VINCULO: tuple = ("Pasante", "Funcionario")

JWT_EXPIRACION_HORAS: int = 8
JWT_ALGORITMO: str = "HS256"

LONGITUD_MINIMA_SECRETO: int = 32

# Tope de la tolerancia que Recursos Humanos puede declarar por día. Sin
# techo, una declaración de 8 horas anularía el control de asistencia.
TOLERANCIA_MAXIMA_DECLARABLE: int = 120


def secreto_requerido(variable: str) -> str:
    """Lee un secreto obligatorio del entorno o aborta la operación.

    Sin valor por defecto: una variable ausente detiene el proceso en lugar
    de degradarlo a una clave conocida. Un secreto de firma con respaldo
    hardcodeado permite falsificar tokens a cualquiera que lea el repositorio.

    Raises:
        RuntimeError: si la variable falta o es más corta que
            ``LONGITUD_MINIMA_SECRETO``.
    """
    load_dotenv()
    valor = (os.getenv(variable) or "").strip()
    if not valor:
        raise RuntimeError(
            f"Falta la variable de entorno {variable}. "
            f"Generá una con: python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
            f"y agregala al .env antes de iniciar el sistema."
        )
    if len(valor) < LONGITUD_MINIMA_SECRETO:
        raise RuntimeError(
            f"{variable} tiene {len(valor)} caracteres; se requieren al menos "
            f"{LONGITUD_MINIMA_SECRETO} para una firma HS256 sólida."
        )
    return valor


def verificar_secretos() -> None:
    """Valida los secretos obligatorios al arrancar (falla rápido y claro)."""
    secreto_requerido("JWT_SECRET_KEY")
    secreto_requerido("COMPROBANTE_CLAVE")


def _jwt_secret() -> str:
    """Clave de firma de tokens leída de ``JWT_SECRET_KEY`` en el ``.env``."""
    return secreto_requerido("JWT_SECRET_KEY")


def crear_token_acceso(usuario_id: int, rol: str, empresa_id: int) -> str:
    """Genera un token JWT firmado con vigencia de 8 horas.

    El token transporta el usuario, su rol y **su empresa** como claims
    verificables. La empresa viaja firmada y no se vuelve a preguntar: es lo
    que ata cada petición a un solo cliente sin que el navegador pueda
    elegirlo.
    """
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "rol": rol,
        "emp": int(empresa_id),
        "iat": ahora,
        "exp": ahora + timedelta(hours=JWT_EXPIRACION_HORAS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITMO)


def verificar_token_acceso(token: str) -> Dict[str, Any]:
    """Valida la firma y vigencia de un token y retorna sus claims.

    Eleva ``jwt.InvalidTokenError`` si el token está manipulado, vencido o
    firmado con otra clave; el llamador decide cómo traducirlo en un 401.
    """
    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITMO])

F = TypeVar("F", bound=Callable[..., Any])


def autorizado(*roles: str) -> Callable[[F], F]:
    """Decorador que exige un rol permitido al actor antes de ejecutar."""

    def decorador(func: F) -> F:
        @wraps(func)
        def envoltura(db: Database, actor: Dict, *args: Any, **kwargs: Any) -> Any:
            require_role(db, actor, roles)
            return func(db, actor, *args, **kwargs)

        return envoltura  # type: ignore[return-value]

    return decorador


def hash_password(password: str) -> str:
    """Encripta la contraseña con bcrypt y retorna el hash en texto seguro."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    """Verifica la contraseña contra un hash bcrypt almacenado."""
    return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))


def get_role_name(db: Database, user: Dict) -> str:
    """Resuelve el nombre del rol del usuario autenticado."""
    if "role_name" in user:
        return user["role_name"]
    return db.get_user_by_id(user["id"])["role_name"]


def require_role(db: Database, user: Dict, allowed_roles: tuple) -> str:
    """Valida el rol del usuario antes de tocar la base de datos.

    Raises:
        PermissionError: si el rol no figura entre los permitidos.
    """
    role = get_role_name(db, user)
    if role not in allowed_roles:
        raise PermissionError(
            f"Rol '{role}' no autorizado. Requiere: {', '.join(allowed_roles)}"
        )
    return role


def authenticate(
    db: Database, username: str, password: str, empresa: Optional[str] = None
) -> Optional[Dict]:
    """Autentica credenciales y deja la conexión atada a la empresa del usuario.

    La pantalla de acceso no sabe de qué cliente es quien escribe, así que la
    cédula se busca en todas las empresas y **la contraseña decide**: se
    compara contra cada candidato y entra el que coincide. Hacerlo en ese
    orden importa, porque preguntar primero "¿en qué empresa estás?" le diría
    a cualquiera en qué clientes existe una cédula sin saber su clave.

    ``empresa`` acota la búsqueda por slug cuando el mismo documento trabaja
    en dos clientes alojados acá y las dos contraseñas coinciden, que es el
    único caso donde la cédula sola no alcanza.

    Un empleado dado de baja no entra ni marca aunque su contraseña siga
    siendo válida: la baja conserva el legajo, no el acceso. Una empresa
    suspendida tampoco deja entrar a los suyos.
    """
    candidatos = db.buscar_credenciales(username)
    if empresa:
        objetivo = empresa.strip().lower()
        candidatos = [c for c in candidatos if c["empresa_slug"] == objetivo]
    validos = [c for c in candidatos if verify_password(password, c["password_hash"])]
    if len(validos) != 1:
        return None
    credencial = validos[0]
    if credencial["activo"] is False or credencial["empresa_activa"] is False:
        return None
    # Resuelta la empresa, el legajo completo se lee por el camino normal, ya
    # acotado: la excepción que cruza empresas se limita a decidir quién entra.
    db.empresa_id = credencial["empresa_id"]
    usuario = db.get_user_by_id(credencial["id"])
    if not usuario:
        return None
    alojada = db.get_empresa(credencial["empresa_id"])
    usuario["empresa_slug"] = alojada["slug"]
    usuario["empresa_nombre"] = alojada["razon_social"]
    return usuario


def prompt_login(db: Database) -> Optional[Dict]:
    """Solicita credenciales por consola y autentica al usuario."""
    username = input("Usuario: ").strip()
    password = getpass.getpass("Contraseña: ")
    return authenticate(db, username, password)


def crear_primer_admin(
    db: Database, username: str, password: str, full_name: str
) -> int:
    """Crea el primer Administrador de la empresa activa.

    El bootstrap es por empresa y no por instalación: alojar un cliente nuevo
    tiene que poder crearle su administrador aunque los otros clientes ya
    tengan el suyo.
    """
    if db.list_users(incluir_bajas=True):
        raise PermissionError("El administrador inicial ya fue creado.")
    role = db.get_role_by_name(ROLE_ADMIN)
    return db.create_user(username, hash_password(password), full_name, role["id"])


def _valores_auditoria(user: Dict) -> Dict[str, Any]:
    """Snapshot de un usuario sin datos sensibles (hash excluido)."""
    return {
        "username": user["username"],
        "full_name": user["full_name"],
        "role_id": user["role_id"],
        "role_name": user.get("role_name"),
        "salario_mensual": float(user.get("salario_mensual") or 0),
    }


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def create_user(
    db: Database,
    actor: Dict,
    username: str,
    password: str,
    full_name: str,
    role_name: str,
    salario_mensual: float = 0.0,
    tipo_vinculo: str = "Funcionario",
    fecha_ingreso: Optional[Any] = None,
    turno_id: Optional[int] = None,
) -> int:
    """Crea un usuario auditando la acción; solo el Admin asigna otro Admin.

    ``fecha_ingreso`` es la del contrato: de ella dependen los días de
    vacaciones y los meses de aguinaldo. Si se omite, se asume hoy.
    """
    if role_name == ROLE_ADMIN:
        require_role(db, actor, (ROLE_ADMIN,))
    if tipo_vinculo not in TIPOS_VINCULO:
        raise ValueError(f"Tipo de vínculo inválido. Use: {', '.join(TIPOS_VINCULO)}")
    role = db.get_role_by_name(role_name)
    if not role:
        raise ValueError(f"El rol '{role_name}' no existe.")
    if db.get_user_by_username(username):
        raise ValueError("El usuario ya existe.")
    if turno_id is not None and not db.get_turno(turno_id):
        raise ValueError("El turno indicado no existe.")
    # El cupo del plan se comprueba al dar de alta y no al facturar: enterarse
    # un mes después de que el cliente se pasó no sirve para nada.
    empresa = db.get_empresa(db.empresa)
    cupo = (empresa or {}).get("max_empleados")
    if cupo is not None and db.contar_empleados() >= int(cupo):
        raise ValueError(
            f"La empresa llegó a su tope de {cupo} empleados activos. "
            f"Dá de baja a alguien o ampliá el plan contratado."
        )
    user_id = db.create_user(
        username,
        hash_password(password),
        full_name,
        role["id"],
        salario_mensual,
        tipo_vinculo,
        fecha_ingreso,
        turno_id,
    )
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "users",
        user_id,
        nuevos={
            "username": username,
            "full_name": full_name,
            "role_id": role["id"],
            "salario_mensual": salario_mensual,
            "tipo_vinculo": tipo_vinculo,
            "fecha_ingreso": str(fecha_ingreso) if fecha_ingreso else "hoy",
            "turno_id": turno_id,
        },
    )
    return user_id


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def update_user(
    db: Database,
    actor: Dict,
    user_id: int,
    full_name: Optional[str] = None,
    password: Optional[str] = None,
    role_name: Optional[str] = None,
    salario_mensual: Optional[float] = None,
    tipo_vinculo: Optional[str] = None,
    fecha_ingreso: Optional[Any] = None,
    turno_id: Any = SIN_CAMBIO,
) -> None:
    """Edita un usuario auditando los valores anterior y nuevo.

    ``turno_id`` en nulo devuelve el legajo al turno predeterminado; omitirlo
    deja el que tenga.
    """
    if role_name == ROLE_ADMIN:
        require_role(db, actor, (ROLE_ADMIN,))
    if tipo_vinculo is not None and tipo_vinculo not in TIPOS_VINCULO:
        raise ValueError(f"Tipo de vínculo inválido. Use: {', '.join(TIPOS_VINCULO)}")
    target = db.get_user_by_id(user_id)
    if not target:
        raise ValueError("El usuario no existe.")
    role_id = None
    if role_name is not None:
        role = db.get_role_by_name(role_name)
        if not role:
            raise ValueError(f"El rol '{role_name}' no existe.")
        role_id = role["id"]
    limpiar_turno = turno_id is not SIN_CAMBIO and turno_id is None
    if turno_id is SIN_CAMBIO:
        turno_id = None
    elif turno_id is not None and not db.get_turno(turno_id):
        raise ValueError("El turno indicado no existe.")
    anterior = _valores_auditoria(target)
    password_hash = hash_password(password) if password else None
    db.update_user(
        user_id,
        full_name=full_name,
        password_hash=password_hash,
        role_id=role_id,
        salario_mensual=salario_mensual,
        tipo_vinculo=tipo_vinculo,
        fecha_ingreso=fecha_ingreso,
        turno_id=turno_id,
        limpiar_turno=limpiar_turno,
    )
    nuevos = _valores_auditoria(
        {
            **target,
            "full_name": full_name if full_name is not None else target["full_name"],
            "role_id": role_id if role_id is not None else target["role_id"],
            "salario_mensual": (
                salario_mensual
                if salario_mensual is not None
                else target["salario_mensual"]
            ),
            "tipo_vinculo": (
                tipo_vinculo
                if tipo_vinculo is not None
                else target.get("tipo_vinculo", "Funcionario")
            ),
        }
    )
    db.registrar_auditoria(
        actor["id"], "ACTUALIZAR", "users", user_id, anterior=anterior, nuevos=nuevos
    )


def cambiar_clave(db: Database, user: Dict, clave_actual: str, clave_nueva: str) -> None:
    """Cambia la contraseña del propio usuario verificando la actual.

    No requiere rol administrativo: la verificación de la clave vigente
    basta como prueba de identidad. El evento queda en ``logs_auditoria``
    sin exponer los hashes.

    Raises:
        ValueError: si la clave actual no coincide o la nueva es muy corta.
    """
    if not verify_password(clave_actual, user["password_hash"]):
        raise ValueError("La contraseña actual no es correcta.")
    if len(clave_nueva) < 6:
        raise ValueError("La contraseña nueva debe tener al menos 6 caracteres.")
    db.update_user(user["id"], password_hash=hash_password(clave_nueva))
    db.registrar_auditoria(
        user["id"],
        "ACTUALIZAR",
        "users",
        user["id"],
        anterior={"password_hash": "(oculto)"},
        nuevos={"password_hash": "(cambiado)"},
    )


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def crear_justificacion(
    db: Database,
    actor: Dict,
    empleado_id: int,
    tipo_permiso: str,
    fecha_inicio: Any,
    fecha_fin: Any,
    horas_usadas: float = 0.0,
    permitir_futuro: bool = False,
) -> int:
    """Crea una justificación aprobada para un empleado (solo RRHH/Admin).

    Valida el artículo reglamentario del catálogo (según el vínculo del
    empleado), que las fechas no excedan el día de hoy, y que el empleado
    aún tenga disponibilidad de la cuota (días, horas o veces) del artículo
    en su período de cómputo vigente.

    ``permitir_futuro`` levanta el tope del día de hoy. La carga directa de
    RRHH registra ausencias ya ocurridas y conserva el tope; la aprobación
    de una solicitud presentada por el empleado no puede tenerlo, porque un
    permiso se pide antes de tomarlo.

    El actor que la crea queda registrado como ``aprobado_por`` y la
    operación se audita en ``logs_auditoria``.
    """
    if tipo_permiso not in TIPOS_PERMISO:
        raise ValueError(
            f"Tipo de permiso inválido. Use: {', '.join(TIPOS_PERMISO)}"
        )
    empleado = db.get_user_by_id(empleado_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    articulo = reglamento.encontrar_articulo(
        tipo_permiso, empleado.get("tipo_vinculo") or "Funcionario"
    )
    if articulo is None:
        raise ValueError(
            f"El permiso '{tipo_permiso}' no aplica al vínculo "
            f"'{empleado.get('tipo_vinculo') or 'Funcionario'}'."
        )
    if fecha_fin < fecha_inicio:
        raise ValueError("La fecha de fin no puede ser anterior al inicio.")
    hoy = datetime.now().date()
    if fecha_fin > hoy and not permitir_futuro:
        raise ValueError(
            f"La fecha de fin no puede superar el día de hoy ({hoy.isoformat()})."
        )
    if articulo["unidad"] == reglamento.UNIDAD_HORAS:
        if horas_usadas <= 0:
            raise ValueError("Debe indicar la cantidad de horas del permiso.")
    elif horas_usadas:
        raise ValueError("Este permiso no admite cantidad de horas.")

    disponibilidad = reglamento.disponibilidad_permisos(db, empleado, hoy)
    estado = next(
        (d for d in disponibilidad if d["tipo"] == tipo_permiso), None
    )
    if estado is None:
        raise ValueError("No se encontró disponibilidad para el permiso.")
    if not estado["disponible"]:
        detalle = f"{estado['usados']:g} {estado['unidad']} usados de {estado['cuota']:g}"
        if estado.get("usos_max"):
            detalle += f" y {estado['usos']:g} de {estado['usos_max']:g} usos"
        notifications.registrar_alerta(
            db,
            "cuota_bloqueada",
            "alta",
            f"Intento bloqueado: cuota agotada de '{tipo_permiso}' "
            f"para {empleado['full_name']}.",
            f"{detalle} ({estado['periodo']}) · solicitó {horas_usadas:g} h · "
            f"actor {actor['full_name']}",
            usuario_id=empleado["id"],
        )
        raise ValueError(
            f"Cuota agotada de '{tipo_permiso}': {detalle} ({estado['periodo']})."
        )
    # Lo que consume el permiso se mide antes de emitirlo, tanto si se cuenta
    # en horas como en días. Validando solo las horas, un artículo de cinco
    # días al año admitía una justificación de enero a diciembre: la cuota
    # recién frenaba el intento **siguiente**, con los 365 días ya emitidos.
    pedido = (
        float(horas_usadas)
        if articulo["unidad"] == reglamento.UNIDAD_HORAS
        else reglamento.dias_de_cuota(articulo, fecha_inicio, fecha_fin, hoy)
    )
    if estado["restantes"] is not None and pedido > estado["restantes"]:
        notifications.registrar_alerta(
            db,
            "cuota_bloqueada",
            "alta",
            f"Intento bloqueado: cuota insuficiente de '{tipo_permiso}' "
            f"para {empleado['full_name']}.",
            f"Quedan {estado['restantes']:g} {estado['unidad']} y se solicitaron "
            f"{pedido:g} · actor {actor['full_name']}",
            usuario_id=empleado["id"],
        )
        raise ValueError(
            f"Solo quedan {estado['restantes']:g} {estado['unidad']} disponibles "
            f"de '{tipo_permiso}' ({estado['periodo']}) y se pidieron {pedido:g}."
        )

    justificacion_id = db.crear_justificacion(
        empleado_id,
        tipo_permiso,
        fecha_inicio,
        fecha_fin,
        actor["id"],
        horas_usadas=horas_usadas,
    )
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "justificaciones",
        justificacion_id,
        nuevos={
            "usuario_id": empleado_id,
            "tipo_permiso": tipo_permiso,
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
            "aprobado_por": actor["id"],
            "horas_usadas": horas_usadas,
        },
    )
    return justificacion_id


MOTIVO_MINIMO: int = 10


def solicitar_permiso(
    db: Database,
    empleado: Dict,
    tipo_permiso: str,
    fecha_inicio: Any,
    fecha_fin: Any,
    horas_solicitadas: float,
    motivo: str,
) -> Dict[str, Any]:
    """Registra el pedido de permiso que el empleado presenta desde el portal.

    Valida contra el mismo catálogo reglamentario que usa RRHH —artículo
    aplicable al vínculo, coherencia de fechas, unidad de medida y cuota
    disponible descontando lo que ya reservan otros pedidos pendientes—, de
    modo que a la bandeja de Recursos Humanos solo lleguen solicitudes que
    cumplen el artículo invocado. Aprobar deja de ser una tarea de control
    aritmético y pasa a ser una decisión.

    Returns:
        La solicitud creada con el artículo y las condiciones que la rigen.

    Raises:
        ValueError: Con el motivo exacto del rechazo, en lenguaje del
            reglamento, para que el empleado pueda corregir el pedido.
    """
    vinculo = empleado.get("tipo_vinculo") or "Funcionario"
    articulo = reglamento.encontrar_articulo(tipo_permiso, vinculo)
    if articulo is None:
        raise ValueError(
            f"El permiso '{tipo_permiso}' no corresponde al vínculo '{vinculo}'."
        )
    if fecha_fin < fecha_inicio:
        raise ValueError("La fecha de fin no puede ser anterior al inicio.")
    motivo = (motivo or "").strip()
    if len(motivo) < MOTIVO_MINIMO:
        raise ValueError(
            f"Contá el motivo del pedido (al menos {MOTIVO_MINIMO} caracteres)."
        )

    por_horas = articulo["unidad"] == reglamento.UNIDAD_HORAS
    if por_horas:
        if horas_solicitadas <= 0:
            raise ValueError("Indicá cuántas horas necesitás.")
        if fecha_fin != fecha_inicio:
            raise ValueError(
                "Un permiso por horas se toma en un solo día: igualá las fechas."
            )
    elif horas_solicitadas:
        raise ValueError("Este permiso se mide en días; no lleva cantidad de horas.")

    hoy = datetime.now().date()
    estado = next(
        (
            d
            for d in reglamento.disponibilidad_permisos(db, empleado, hoy)
            if d["tipo"] == tipo_permiso
        ),
        None,
    )
    if estado is None:
        raise ValueError("No se encontró la disponibilidad del permiso.")
    if not estado["solicitable"]:
        raise ValueError(_detalle_sin_cupo(estado))

    pedido = float(horas_solicitadas) if por_horas else float(
        (fecha_fin - fecha_inicio).days + 1
    )
    disponible = estado["restantes_efectivos"]
    if disponible is not None and pedido > disponible:
        unidad = estado["unidad"]
        raise ValueError(
            f"Pediste {pedido:g} {unidad} y te quedan {disponible:g} "
            f"de '{estado['nombre']}' ({estado['articulo']}, {estado['periodo']})."
        )

    solicitud_id = db.crear_solicitud_permiso(
        empleado["id"],
        tipo_permiso,
        fecha_inicio,
        fecha_fin,
        horas_solicitadas,
        motivo,
    )
    notifications.registrar_alerta(
        db,
        "permiso_solicitado",
        "baja",
        f"{empleado['full_name']} solicitó {estado['nombre']}.",
        f"{articulo['articulo']} · {fecha_inicio.isoformat()} al "
        f"{fecha_fin.isoformat()} · solicitud #{solicitud_id}",
        usuario_id=empleado["id"],
    )
    return {
        "id": solicitud_id,
        "estado": "Pendiente",
        "articulo": articulo["articulo"],
        "nombre": articulo["nombre"],
        "condiciones": articulo["condiciones"],
    }


def _detalle_sin_cupo(estado: Dict[str, Any]) -> str:
    """Explica el agotamiento citando el artículo y lo ya comprometido."""
    partes = [
        f"{estado['usados']:g} {estado['unidad']} usados de {estado['cuota']:g}"
    ]
    if estado["pendientes"]:
        partes.append(f"{estado['pendientes']:g} en pedidos sin resolver")
    if estado.get("usos_max"):
        partes.append(f"{estado['usos']:g} de {estado['usos_max']:g} usos")
    return (
        f"Cuota agotada de '{estado['nombre']}' ({estado['articulo']}): "
        f"{' · '.join(partes)} en el período {estado['periodo']}."
    )


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def resolver_solicitud_permiso(
    db: Database,
    actor: Dict,
    solicitud_id: int,
    aprobar: bool,
    observacion: str = "",
) -> Dict[str, Any]:
    """Aprueba o rechaza una solicitud de permiso del portal.

    Al aprobar emite la justificación oficial —la misma que hasta ahora
    cargaba RRHH a mano— y la ata a la solicitud, de modo que el PDF del
    permiso queda disponible para el empleado sin ningún paso extra. La
    cuota se vuelve a verificar en este momento y no en el del pedido,
    porque entre uno y otro pudo aprobarse otra solicitud.
    """
    solicitud = db.get_solicitud_permiso(solicitud_id)
    if not solicitud:
        raise ValueError(f"La solicitud #{solicitud_id} no existe.")
    if solicitud["estado"] != "Pendiente":
        raise ValueError(
            f"La solicitud #{solicitud_id} ya fue {solicitud['estado'].lower()}."
        )

    justificacion_id: Optional[int] = None
    if aprobar:
        justificacion_id = crear_justificacion(
            db,
            actor,
            solicitud["usuario_id"],
            solicitud["tipo_permiso"],
            solicitud["fecha_inicio"],
            solicitud["fecha_fin"],
            float(solicitud["horas_solicitadas"] or 0),
            permitir_futuro=True,
        )
    estado = "Aprobado" if aprobar else "Rechazado"
    if not db.resolver_solicitud_permiso(
        solicitud_id, estado, actor["id"], observacion.strip(), justificacion_id
    ):
        raise ValueError("La solicitud fue resuelta por otra persona mientras tanto.")
    db.registrar_auditoria(
        actor["id"],
        "RESOLVER",
        "solicitudes_permiso",
        solicitud_id,
        anterior={"estado": "Pendiente"},
        nuevos={
            "estado": estado,
            "justificacion_id": justificacion_id,
            "observacion": observacion.strip(),
        },
    )
    notifications.registrar_alerta(
        db,
        "permiso_resuelto",
        "baja" if aprobar else "media",
        f"Tu permiso de {solicitud['tipo_permiso']} fue {estado.lower()}.",
        observacion.strip()
        or f"{solicitud['fecha_inicio']} al {solicitud['fecha_fin']}",
        usuario_id=solicitud["usuario_id"],
    )
    return {
        "id": solicitud_id,
        "estado": estado,
        "justificacion_id": justificacion_id,
    }


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def declarar_condicion_dia(
    db: Database,
    actor: Dict,
    fecha: Any,
    condicion: str,
    tolerancia_min: int,
    nota: str = "",
) -> Dict[str, Any]:
    """Declara la condición excepcional de un día para toda la plantilla.

    Es el reemplazo de la casilla que el propio empleado marcaba en el
    kiosco: la tolerancia climática de la Res. 3028/2024 la reconoce la
    empresa, no quien llega tarde. Queda firmada y auditada.
    """
    condicion = (condicion or "").strip()
    if not condicion:
        raise ValueError("Indicá qué condición se declara para el día.")
    if not 0 <= tolerancia_min <= TOLERANCIA_MAXIMA_DECLARABLE:
        raise ValueError(
            f"La tolerancia debe estar entre 0 y {TOLERANCIA_MAXIMA_DECLARABLE} minutos."
        )
    fila = db.declarar_condicion_dia(
        fecha, condicion, int(tolerancia_min), actor["id"], nota.strip()
    )
    db.registrar_auditoria(
        actor["id"],
        "DECLARAR",
        "condiciones_dia",
        0,
        nuevos={
            "fecha": fecha.isoformat(),
            "condicion": condicion,
            "tolerancia_min": int(tolerancia_min),
            "nota": nota.strip(),
        },
    )
    notifications.registrar_alerta(
        db,
        "condicion_dia",
        "media",
        f"{condicion} declarada para el {fecha.isoformat()}.",
        f"Tolerancia de {tolerancia_min} min para toda la plantilla · "
        f"firmó {actor['full_name']}",
    )
    return fila


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def dar_de_baja(
    db: Database, actor: Dict, user_id: int, fecha_baja: Optional[Any] = None
) -> Dict[str, Any]:
    """Da de baja a un empleado conservando su historial.

    Es lo que corresponde cuando alguien deja la empresa: pierde el acceso y
    sale de la nómina, pero sus marcajes, justificaciones y comprobantes
    siguen existiendo. Borrarlo destruiría el respaldo de liquidaciones ya
    pagadas, que es justo lo que un archivo laboral tiene que poder exhibir.
    """
    empleado = db.get_user_by_id(user_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    if int(user_id) == int(actor["id"]):
        raise ValueError("No podés darte de baja a vos mismo.")
    if empleado.get("activo") is False:
        raise ValueError(f"{empleado['full_name']} ya estaba dado de baja.")
    baja = fecha_baja or datetime.now().date()
    db.cambiar_estado_usuario(user_id, False, baja)
    # La plantilla facial se conserva mientras hay una relación laboral que la
    # justifique. Terminada la relación, el fin que legitimaba el tratamiento
    # desaparece y el dato se destruye (Ley 6534/2020). El resto del legajo se
    # conserva porque lo exige el archivo laboral.
    biometria_borrada = db.tiene_foto(user_id)
    if biometria_borrada:
        db.eliminar_foto(user_id)
    db.registrar_auditoria(
        actor["id"], "BAJA", "users", user_id,
        anterior={"activo": True},
        nuevos={
            "activo": False,
            "fecha_baja": baja.isoformat(),
            "biometria_eliminada": biometria_borrada,
        },
    )
    return {
        "id": user_id,
        "nombre": empleado["full_name"],
        "fecha_baja": baja,
        "biometria_eliminada": biometria_borrada,
    }


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def reincorporar(db: Database, actor: Dict, user_id: int) -> Dict[str, Any]:
    """Reincorpora a un empleado dado de baja, devolviéndole el acceso."""
    empleado = db.get_user_by_id(user_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    if empleado.get("activo") is not False:
        raise ValueError(f"{empleado['full_name']} ya está activo.")
    db.cambiar_estado_usuario(user_id, True)
    db.registrar_auditoria(
        actor["id"], "REINCORPORAR", "users", user_id,
        anterior={"activo": False},
        nuevos={"activo": True},
    )
    return {"id": user_id, "nombre": empleado["full_name"]}


@autorizado(ROLE_ADMIN,)
def delete_user(db: Database, actor: Dict, user_id: int) -> None:
    """Elimina un usuario (solo Admin) auditando los valores previos."""
    if user_id == actor["id"]:
        raise ValueError("No puede eliminarse a sí mismo.")
    target = db.get_user_by_id(user_id)
    if not target:
        raise ValueError("El usuario no existe.")
    db.delete_user(user_id)
    db.registrar_auditoria(
        actor["id"], "ELIMINAR", "users", user_id, anterior=_valores_auditoria(target)
    )


def can_register_marks(db: Database, user: Dict) -> bool:
    """Autoriza el registro de marcas a cualquier rol autenticado."""
    require_role(db, user, ROLES_MARCAJES)
    return True


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def aprobar_solicitud_correccion(
    db: Database, actor: Dict, solicitud_id: int, aprobar: bool
) -> str:
    """Resuelve un reclamo de marcación fallida y aplica la corrección.

    Al aprobar se materializa la marca propuesta en ``marcajes``:
    - ``Entrada``: ajusta la hora del marcaje existente de esa fecha o lo
      crea retroactivamente si no existe.
    - ``Salida``: cierra el marcaje abierto de esa fecha liquidando el
      desglose legal con ``calcular_horas_paraguay``.

    Tanto la corrección del marcaje como el cambio de estado de la
    solicitud quedan trazados en ``logs_auditoria`` (JSONB con los valores
    anterior y nuevo) para blindar la operación frente a fraudes.

    Returns:
        El estado final de la solicitud (``Aprobado`` o ``Rechazado``).
    """
    solicitud = db.get_solicitud_correccion(solicitud_id)
    if not solicitud:
        raise ValueError("La solicitud no existe.")
    if solicitud["estado"] != "Pendiente":
        raise ValueError("La solicitud ya fue resuelta.")
    estado_final = "Aprobado" if aprobar else "Rechazado"
    if aprobar:
        _aplicar_correccion_marcaje(db, actor, solicitud)
    db.actualizar_estado_solicitud(solicitud_id, estado_final, actor["id"])
    db.registrar_auditoria(
        actor["id"],
        "ACTUALIZAR",
        "solicitudes_correccion",
        solicitud_id,
        anterior={"estado": "Pendiente", "revisado_por": None},
        nuevos={"estado": estado_final, "revisado_por": actor["id"]},
    )
    return estado_final


def _aplicar_correccion_marcaje(
    db: Database, actor: Dict, solicitud: Dict
) -> None:
    """Materializa la marca aprobada en la tabla ``marcajes`` con auditoría."""
    # RRHH tipea una hora de reloj local; los marcajes se guardan en
    # TIMESTAMPTZ, así que el instante se ancla a la zona horaria acá y no
    # viaja naive hasta chocar contra un valor aware de la base.
    instante = datetime.combine(
        solicitud["fecha_registro"], solicitud["hora_propuesta"]
    ).astimezone()
    if solicitud["tipo_marca"] == "Entrada":
        _corregir_entrada(db, actor, solicitud, instante)
    else:
        _corregir_salida(db, actor, solicitud, instante)


def _valores_marcaje(marcaje: Dict) -> Dict[str, Any]:
    """Serializa un marcaje para los valores anterior/nuevo de la auditoría."""
    def formato(instante: Any) -> Optional[str]:
        return instante.isoformat() if instante else None

    return {
        "id": marcaje["id"],
        "user_id": marcaje["user_id"],
        "hora_entrada": formato(marcaje["hora_entrada"]),
        "hora_salida": formato(marcaje["hora_salida"]),
        "es_feriado": bool(marcaje["es_feriado"]),
        "es_tardanza": bool(marcaje["es_tardanza"]),
        "horas_ordinarias": str(marcaje["horas_ordinarias"] or "00:00:00"),
        "horas_nocturnas": str(marcaje.get("horas_nocturnas") or "00:00:00"),
        "horas_extra_50": str(marcaje["horas_extra_50"] or "00:00:00"),
        "horas_extra_100": str(marcaje["horas_extra_100"] or "00:00:00"),
        "tipo_incidencia": marcaje.get("tipo_incidencia") or "",
        "tipo_jornada": marcaje.get("tipo_jornada") or "",
    }


def _corregir_entrada(
    db: Database, actor: Dict, solicitud: Dict, instante: datetime
) -> None:
    """Crea o ajusta la entrada de la fecha reclamada según la hora propuesta."""
    marcajes = db.get_entries_by_date(solicitud["usuario_id"], instante.date())
    # La corrección se evalúa con la misma regla que la marcación en vivo: si
    # aplicara su propia gracia, corregir una marca a la hora exacta a la que
    # se fichó podría convertir un día normal en una llegada tardía.
    evaluacion = evaluar_asistencia(db, solicitud["usuario_id"], instante)
    tardanza = evaluacion["estado"] != "Normal"
    incidencia = evaluacion["estado"] if tardanza else ""
    if marcajes:
        marcaje = marcajes[0]
        anterior = _valores_marcaje(marcaje)
        db.actualizar_hora_entrada(marcaje["id"], instante, tardanza, incidencia)
        nuevos = dict(
            anterior,
            hora_entrada=instante.isoformat(),
            es_tardanza=tardanza,
            tipo_incidencia=incidencia,
        )
        db.registrar_auditoria(
            actor["id"], "ACTUALIZAR", "marcajes", marcaje["id"],
            anterior=anterior, nuevos=nuevos,
        )
        return
    marcaje_id = db.insertar_marcaje_registro(
        solicitud["usuario_id"], instante, tardanza, es_feriado_o_domingo(instante)
    )
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "marcajes",
        marcaje_id,
        nuevos={
            "usuario_id": solicitud["usuario_id"],
            "hora_entrada": instante.isoformat(),
            "es_tardanza": tardanza,
            "es_feriado": es_feriado_o_domingo(instante),
        },
    )


def _corregir_salida(
    db: Database, actor: Dict, solicitud: Dict, instante: datetime
) -> None:
    """Cierra el marcaje abierto de la fecha reclamada con la hora propuesta."""
    abiertos = [
        m
        for m in db.get_entries_by_date(solicitud["usuario_id"], instante.date())
        if m["hora_salida"] is None
    ]
    if not abiertos:
        raise ValueError(
            "No hay una entrada abierta en la fecha reclamada para cerrar."
        )
    marcaje = abiertos[0]
    anterior = _valores_marcaje(marcaje)
    desglose = calcular_horas_paraguay(marcaje["hora_entrada"], instante)
    persistir_desglose(
        db, marcaje["id"], instante, desglose, marcaje.get("tipo_incidencia") or ""
    )
    nuevos = dict(
        anterior,
        hora_salida=instante.isoformat(),
        es_feriado=desglose.toca_descanso,
        horas_ordinarias=str(desglose.horas_ordinarias),
        horas_nocturnas=str(desglose.horas_nocturnas),
        horas_extra_50=str(desglose.horas_extra_50),
        horas_extra_100=str(desglose.horas_extra_100),
        tipo_jornada=desglose.tipo_jornada,
    )
    db.registrar_auditoria(
        actor["id"], "ACTUALIZAR", "marcajes", marcaje["id"],
        anterior=anterior, nuevos=nuevos,
    )

# --- Turnos -----------------------------------------------------------------

TOLERANCIA_MAXIMA_TURNO: int = 60
"""Tope de la gracia propia de un turno; más que eso ya no es tolerancia."""


def _turno_como_dict(fila: Dict[str, Any]) -> Dict[str, Any]:
    """Proyecta una fila de turno con sus derivados para la UI y las APIs."""
    proyectado = turnos.desde_fila(fila).como_dict()
    proyectado["dotacion"] = int(fila.get("dotacion") or 0)
    proyectado["asignados"] = int(fila.get("asignados") or 0)
    return proyectado


@autorizado(ROLE_ADMIN, ROLE_RRHH, ROLE_EMPLEADO)
def listar_turnos(
    db: Database, actor: Dict, incluir_inactivos: bool = False
) -> List[Dict[str, Any]]:
    """Catálogo de turnos de la empresa con su horario y su dotación."""
    return [_turno_como_dict(fila) for fila in db.listar_turnos(incluir_inactivos)]


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def crear_turno(
    db: Database,
    actor: Dict,
    nombre: str,
    tramos: Any,
    dias: Any = turnos.MASCARA_LUNES_VIERNES,
    sucursal: str = turnos.SUCURSAL_PREDETERMINADA,
    tolerancia_min: Optional[int] = None,
) -> Dict[str, Any]:
    """Da de alta un turno validando que describa una jornada realizable."""
    nombre = (nombre or "").strip()
    if len(nombre) < 3:
        raise ValueError("El turno necesita un nombre de al menos 3 caracteres.")
    if db.get_turno_por_nombre(nombre):
        raise ValueError(f"Ya existe un turno llamado '{nombre}'.")
    definidos = turnos.construir_tramos(_tramos_de_entrada(tramos))
    mascara = turnos.normalizar_mascara(dias)
    tolerancia = _tolerancia_de_turno(tolerancia_min)
    turno_id = db.crear_turno(
        nombre,
        [(t.entrada, t.salida) for t in definidos],
        mascara,
        (sucursal or turnos.SUCURSAL_PREDETERMINADA).strip(),
        tolerancia,
    )
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "turnos",
        turno_id,
        nuevos={
            "nombre": nombre,
            "dias": mascara,
            "sucursal": sucursal,
            "tolerancia_min": tolerancia,
            "tramos": [t.etiqueta() for t in definidos],
        },
    )
    return _turno_como_dict(db.get_turno(turno_id))


# --------------------------------------------- Dispositivos de marcación


VARIABLE_DISPOSITIVO_OBLIGATORIO: str = "DISPOSITIVO_OBLIGATORIO"


def dispositivo_obligatorio() -> bool:
    """Si una marca sin puesto identificado se rechaza.

    Viene apagado: una instalación existente no tiene puestos dados de alta, y
    encenderlo de golpe dejaría a todo el mundo sin poder marcar.
    """
    return (os.getenv(VARIABLE_DISPOSITIVO_OBLIGATORIO, "") or "").strip().lower() in (
        "1", "true", "si", "sí", "yes",
    )


def huella_de_token(token: str) -> str:
    """Hash con el que se guarda y se busca un token de dispositivo.

    El token es una credencial: guardarlo en claro convierte una lectura de la
    tabla en la capacidad de fabricar marcas desde cualquier lado. Se guarda
    el hash, igual que una contraseña, y se compara por hash.
    """
    import hashlib

    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def registrar_dispositivo(
    db: Database, actor: Dict, nombre: str, ubicacion: str = ""
) -> Dict[str, Any]:
    """Da de alta un puesto de marcación y devuelve su token una sola vez.

    El token se muestra acá y no se vuelve a poder leer: en la base queda su
    hash. Si se pierde, se revoca el puesto y se da de alta otro, que es más
    seguro que poder recuperarlo.
    """
    import secrets

    nombre = (nombre or "").strip()
    if len(nombre) < 3:
        raise ValueError("El puesto necesita un nombre de al menos 3 caracteres.")

    token = secrets.token_urlsafe(32)
    dispositivo_id = db.crear_dispositivo(
        nombre, (ubicacion or "").strip(), huella_de_token(token), actor["id"]
    )
    db.registrar_auditoria(
        actor["id"], "CREAR", "dispositivos", dispositivo_id,
        nuevos={"nombre": nombre, "ubicacion": ubicacion},
    )
    return {
        "id": dispositivo_id,
        "nombre": nombre,
        "ubicacion": ubicacion,
        "token": token,
        "aviso": "Guardá este token ahora: no se vuelve a mostrar.",
    }


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def listar_dispositivos(
    db: Database, actor: Dict, incluir_inactivos: bool = False
) -> List[Dict[str, Any]]:
    """Puestos de marcación con su actividad."""
    return db.listar_dispositivos(incluir_inactivos)


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def revocar_dispositivo(db: Database, actor: Dict, dispositivo_id: int) -> bool:
    """Deja fuera de servicio un puesto sin borrar el origen de sus marcas."""
    existentes = {d["id"]: d for d in db.listar_dispositivos(incluir_inactivos=True)}
    if dispositivo_id not in existentes:
        raise ValueError("Ese puesto de marcación no existe.")
    db.revocar_dispositivo(dispositivo_id)
    db.registrar_auditoria(
        actor["id"], "ACTUALIZAR", "dispositivos", dispositivo_id,
        anterior={"activo": True}, nuevos={"activo": False},
    )
    return True


def resolver_dispositivo(db: Database, token: str) -> Optional[Dict[str, Any]]:
    """Identifica el puesto que presenta ese token, si sigue habilitado.

    Devuelve ``None`` tanto para un token desconocido como para uno revocado:
    desde afuera no hay forma de distinguir un puesto dado de baja de uno que
    nunca existió, que es lo que corresponde para una credencial.
    """
    if not (token or "").strip():
        return None
    dispositivo = db.dispositivo_por_token(huella_de_token(token))
    if not dispositivo or not dispositivo.get("activo"):
        return None
    return dispositivo


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def actualizar_turno(
    db: Database,
    actor: Dict,
    turno_id: int,
    nombre: Optional[str] = None,
    tramos: Any = None,
    dias: Any = None,
    sucursal: Optional[str] = None,
    tolerancia_min: Any = SIN_CAMBIO,
) -> Dict[str, Any]:
    """Modifica un turno vigente.

    El cambio rige hacia adelante: los marcajes ya liquidados conservan la
    incidencia que se les calculó con el horario vigente ese día.
    """
    actual = db.get_turno(turno_id)
    if not actual:
        raise ValueError("El turno no existe.")
    anterior = _turno_como_dict(actual)
    if nombre is not None:
        nombre = nombre.strip()
        existente = db.get_turno_por_nombre(nombre)
        if existente and existente["id"] != turno_id:
            raise ValueError(f"Ya existe un turno llamado '{nombre}'.")
    definidos = (
        turnos.construir_tramos(_tramos_de_entrada(tramos))
        if tramos is not None
        else None
    )
    mascara = turnos.normalizar_mascara(dias) if dias is not None else None
    limpiar = tolerancia_min is not SIN_CAMBIO and tolerancia_min in (None, "")
    tolerancia = (
        None
        if limpiar or tolerancia_min is SIN_CAMBIO
        else _tolerancia_de_turno(tolerancia_min)
    )
    db.actualizar_turno(
        turno_id,
        nombre=nombre,
        tramos=[(t.entrada, t.salida) for t in definidos] if definidos else None,
        dias=mascara,
        sucursal=sucursal.strip() if sucursal is not None else None,
        tolerancia_min=tolerancia,
        limpiar_tolerancia=limpiar,
    )
    actualizado = _turno_como_dict(db.get_turno(turno_id))
    db.registrar_auditoria(
        actor["id"],
        "ACTUALIZAR",
        "turnos",
        turno_id,
        anterior=anterior,
        nuevos=actualizado,
    )
    return actualizado


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def retirar_turno(db: Database, actor: Dict, turno_id: int) -> str:
    """Saca un turno de circulación, o lo borra si nunca se usó.

    No se retira un turno con gente adentro: el horario de esa gente pasaría
    en silencio a ser otro y sus tardanzas se medirían contra una hora que
    nadie les comunicó.
    """
    turno = db.get_turno(turno_id)
    if not turno:
        raise ValueError("El turno no existe.")
    if turno["predeterminado"]:
        raise ValueError(
            "El turno predeterminado no se puede retirar: designá otro antes."
        )
    dotacion = db.contar_personal_en_turno(turno_id)
    if dotacion:
        raise ValueError(
            f"{dotacion} empleado(s) dependen de este turno. Reasignalos antes "
            f"de retirarlo."
        )
    uso_historico = any(
        a["turno_id"] == turno_id for a in db.listar_asignaciones_turno()
    )
    if uso_historico:
        db.cambiar_estado_turno(turno_id, False)
        resultado = "retirado"
    else:
        db.eliminar_turno(turno_id)
        resultado = "eliminado"
    db.registrar_auditoria(
        actor["id"],
        "ELIMINAR",
        "turnos",
        turno_id,
        anterior={"nombre": turno["nombre"], "resultado": resultado},
    )
    return resultado


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def designar_turno_predeterminado(
    db: Database, actor: Dict, turno_id: int
) -> Dict[str, Any]:
    """Elige el turno que rige para quien no tiene ninguno asignado."""
    turno = db.get_turno(turno_id)
    if not turno:
        raise ValueError("El turno no existe.")
    if not turno["activo"]:
        raise ValueError("Un turno retirado no puede ser el predeterminado.")
    db.marcar_turno_predeterminado(turno_id)
    db.registrar_auditoria(
        actor["id"],
        "ACTUALIZAR",
        "turnos",
        turno_id,
        nuevos={"predeterminado": True, "nombre": turno["nombre"]},
    )
    return _turno_como_dict(db.get_turno(turno_id))


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def asignar_turno_base(
    db: Database, actor: Dict, usuario_id: int, turno_id: Optional[int]
) -> Dict[str, Any]:
    """Fija el turno de contrato de un legajo."""
    empleado = db.get_user_by_id(usuario_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    if turno_id is not None:
        turno = db.get_turno(turno_id)
        if not turno:
            raise ValueError("El turno no existe.")
        if not turno["activo"]:
            raise ValueError("No se puede asignar un turno retirado.")
    db.asignar_turno_base(usuario_id, turno_id)
    db.registrar_auditoria(
        actor["id"],
        "ACTUALIZAR",
        "users",
        usuario_id,
        anterior={"turno_id": empleado.get("turno_id")},
        nuevos={"turno_id": turno_id},
    )
    return turno_de_empleado(db, actor, usuario_id)


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def rotar_turno(
    db: Database,
    actor: Dict,
    usuario_id: int,
    turno_id: int,
    desde: Any,
    hasta: Any = None,
    motivo: str = "",
) -> Dict[str, Any]:
    """Asigna un turno con vigencia: la forma de rotar sin tocar el legajo.

    Al vencer la asignación el empleado vuelve solo a su turno de contrato,
    sin que nadie tenga que acordarse de deshacer el cambio.
    """
    empleado = db.get_user_by_id(usuario_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    turno = db.get_turno(turno_id)
    if not turno:
        raise ValueError("El turno no existe.")
    if not turno["activo"]:
        raise ValueError("No se puede rotar a un turno retirado.")
    desde = _como_fecha(desde, "desde")
    hasta = _como_fecha(hasta, "hasta") if hasta else None
    if hasta and hasta < desde:
        raise ValueError("La vigencia termina antes de empezar.")
    asignacion_id = db.crear_asignacion_turno(
        usuario_id, turno_id, desde, hasta, (motivo or "").strip(), actor["id"]
    )
    vigencia = f"Desde {desde.isoformat()}"
    vigencia += f" hasta {hasta.isoformat()}" if hasta else " sin fecha de fin"
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "asignaciones_turno",
        asignacion_id,
        nuevos={
            "usuario_id": usuario_id,
            "turno": turno["nombre"],
            "desde": desde.isoformat(),
            "hasta": hasta.isoformat() if hasta else None,
            "motivo": motivo,
        },
    )
    notifications.registrar_alerta(
        db,
        "rotacion_turno",
        "baja",
        f"{empleado['full_name']} pasa al turno {turno['nombre']}.",
        f"{vigencia} · firmó {actor['full_name']}",
        usuario_id=usuario_id,
    )
    return turno_de_empleado(db, actor, usuario_id)


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def revocar_rotacion(db: Database, actor: Dict, asignacion_id: int) -> bool:
    """Cancela una rotación; el empleado vuelve a su turno de contrato."""
    if not db.eliminar_asignacion_turno(asignacion_id):
        raise ValueError("La asignación no existe.")
    db.registrar_auditoria(actor["id"], "ELIMINAR", "asignaciones_turno", asignacion_id)
    return True


def turno_de_empleado(
    db: Database, actor: Dict, usuario_id: int, dia: Any = None
) -> Dict[str, Any]:
    """Horario vigente de un empleado, con sus rotaciones programadas.

    Cada uno consulta el suyo; el de otro, solo administración.
    """
    ajeno = actor["id"] != usuario_id
    if ajeno and actor.get("role_name") not in ROLES_GESTION_USUARIOS:
        raise PermissionError("No tiene permiso para consultar turnos ajenos.")
    dia = _como_fecha(dia, "día") if dia else date.today()
    vigente = turno_vigente(db, usuario_id, dia)
    proyectado = vigente.como_dict()
    proyectado["fecha"] = dia.isoformat()
    proyectado["trabaja_hoy"] = vigente.trabaja(dia)
    ciclo = db.get_ciclo_de(usuario_id)
    proyectado["ciclo"] = (
        {
            "nombre": ciclo["nombre"],
            "secuencia": " → ".join(t["turno_nombre"] for t in ciclo["turnos"]),
            "dias_por_tramo": int(ciclo["dias_por_tramo"]),
            "posicion": ciclo["posicion"],
        }
        if ciclo
        else None
    )
    proyectado["rotaciones"] = [
        {
            "id": a["id"],
            "turno": a["turno_nombre"],
            "desde": a["desde"].isoformat(),
            "hasta": a["hasta"].isoformat() if a["hasta"] else None,
            "motivo": a["motivo"],
        }
        for a in db.listar_asignaciones_turno(usuario_id, solo_vigentes=True)
    ]
    return proyectado


def _tramos_de_entrada(tramos: Any) -> List[Any]:
    """Acepta la forma cómoda para cada llamador y la normaliza a una lista."""
    if tramos is None:
        raise ValueError("Indicá al menos un tramo horario.")
    if isinstance(tramos, dict):
        return [tramos]
    return list(tramos)


def _tolerancia_de_turno(valor: Any) -> Optional[int]:
    """Valida la gracia propia del turno; ``None`` deja vigente la del vínculo."""
    if valor is None or valor == "":
        return None
    try:
        minutos = int(valor)
    except (TypeError, ValueError):
        raise ValueError("La tolerancia del turno se expresa en minutos enteros.")
    if not 0 <= minutos <= TOLERANCIA_MAXIMA_TURNO:
        raise ValueError(
            f"La tolerancia del turno debe estar entre 0 y "
            f"{TOLERANCIA_MAXIMA_TURNO} minutos."
        )
    return minutos


def _como_fecha(valor: Any, campo: str) -> date:
    """Convierte a ``date`` lo que llega de un formulario o de una API."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ValueError(f"Fecha inválida en '{campo}': se espera AAAA-MM-DD.")


# --- Ciclos de rotación -----------------------------------------------------

MIN_TURNOS_EN_CICLO: int = 2
MAX_TURNOS_EN_CICLO: int = 6


def _ciclo_como_dict(fila: Dict[str, Any]) -> Dict[str, Any]:
    """Proyecta un ciclo para la UI y las APIs."""
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "dias_por_tramo": int(fila["dias_por_tramo"]),
        "ancla": fila["ancla"].isoformat(),
        "activo": bool(fila.get("activo", True)),
        "dotacion": int(fila.get("dotacion") or 0),
        "turnos": [
            {"orden": t["orden"], "id": t["turno_id"], "nombre": t["turno_nombre"]}
            for t in fila.get("turnos", [])
        ],
        "secuencia": " → ".join(t["turno_nombre"] for t in fila.get("turnos", [])),
    }


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def listar_ciclos(
    db: Database, actor: Dict, incluir_inactivos: bool = False
) -> List[Dict[str, Any]]:
    """Ciclos de rotación definidos, con su secuencia y su dotación."""
    return [_ciclo_como_dict(f) for f in db.listar_ciclos(incluir_inactivos)]


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def crear_ciclo(
    db: Database,
    actor: Dict,
    nombre: str,
    turnos_ids: List[int],
    dias_por_tramo: int = 7,
    ancla: Any = None,
) -> Dict[str, Any]:
    """Define una rotación automática: qué turnos, en qué orden y cada cuánto.

    El ancla es el día en que el ciclo empieza a correr. No describe lo que
    pasó antes, así que una fecha anterior cae al turno de contrato: un
    calendario que inventara el pasado ensuciaría la liquidación ya hecha.
    """
    nombre = (nombre or "").strip()
    if len(nombre) < 3:
        raise ValueError("El ciclo necesita un nombre de al menos 3 caracteres.")
    ids = [int(t) for t in (turnos_ids or [])]
    if not MIN_TURNOS_EN_CICLO <= len(ids) <= MAX_TURNOS_EN_CICLO:
        raise ValueError(
            f"Un ciclo alterna entre {MIN_TURNOS_EN_CICLO} y "
            f"{MAX_TURNOS_EN_CICLO} turnos; se recibieron {len(ids)}."
        )
    if len(set(ids)) != len(ids):
        raise ValueError("Un turno no puede aparecer dos veces en el mismo ciclo.")
    for turno_id in ids:
        turno = db.get_turno(turno_id)
        if not turno:
            raise ValueError("Uno de los turnos del ciclo no existe.")
        if not turno["activo"]:
            raise ValueError(
                f"El turno '{turno['nombre']}' está retirado y no puede entrar "
                f"en un ciclo."
            )
    try:
        dias = int(dias_por_tramo)
    except (TypeError, ValueError):
        raise ValueError("Los días por tramo se expresan en número entero.")
    if not 1 <= dias <= 60:
        raise ValueError("Los días por tramo van de 1 a 60.")
    inicio = _como_fecha(ancla, "ancla") if ancla else date.today()

    ciclo_id = db.crear_ciclo(nombre, ids, dias, inicio)
    db.registrar_auditoria(
        actor["id"],
        "CREAR",
        "ciclos_rotacion",
        ciclo_id,
        nuevos={
            "nombre": nombre,
            "turnos": ids,
            "dias_por_tramo": dias,
            "ancla": inicio.isoformat(),
        },
    )
    return _ciclo_como_dict(db.get_ciclo(ciclo_id))


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def eliminar_ciclo(db: Database, actor: Dict, ciclo_id: int) -> bool:
    """Elimina un ciclo que no tenga gente adentro."""
    ciclo = db.get_ciclo(ciclo_id)
    if not ciclo:
        raise ValueError("El ciclo no existe.")
    dotacion = db.contar_personal_en_ciclo(ciclo_id)
    if dotacion:
        raise ValueError(
            f"{dotacion} empleado(s) rotan con este ciclo. Sacalos antes de "
            f"eliminarlo."
        )
    db.eliminar_ciclo(ciclo_id)
    db.registrar_auditoria(
        actor["id"], "ELIMINAR", "ciclos_rotacion", ciclo_id,
        anterior={"nombre": ciclo["nombre"]},
    )
    return True


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def asignar_ciclo(
    db: Database,
    actor: Dict,
    usuario_id: int,
    ciclo_id: Optional[int],
    posicion: int = 0,
) -> Dict[str, Any]:
    """Pone a un empleado a rotar, en el tramo del ciclo que le toca arrancar.

    La posición es lo que hace que dos personas del mismo ciclo estén siempre
    en turnos distintos: sin ella, todo el equipo rotaría en bloque y no
    quedaría nadie cubriendo el otro turno.
    """
    empleado = db.get_user_by_id(usuario_id)
    if not empleado:
        raise ValueError("El empleado no existe.")
    if ciclo_id is not None:
        ciclo = db.get_ciclo(ciclo_id)
        if not ciclo:
            raise ValueError("El ciclo no existe.")
        if not 0 <= int(posicion) < len(ciclo["turnos"]):
            raise ValueError(
                f"La posición va de 0 a {len(ciclo['turnos']) - 1} en este ciclo."
            )
    db.asignar_ciclo(usuario_id, ciclo_id, posicion)
    db.registrar_auditoria(
        actor["id"], "ACTUALIZAR", "users", usuario_id,
        anterior={"ciclo_id": empleado.get("ciclo_id")},
        nuevos={"ciclo_id": ciclo_id, "ciclo_posicion": int(posicion)},
    )
    return turno_de_empleado(db, actor, usuario_id)


def calendario_de_rotacion(
    db: Database, actor: Dict, usuario_id: int, semanas: int = 6
) -> List[Dict[str, Any]]:
    """Qué turno le toca a alguien en los próximos tramos.

    El ciclo se calcula y no se materializa, así que sin esta proyección el
    empleado no tendría dónde ver cuándo le toca la noche. Una rotación que
    la persona no puede consultar es una rotación que va a preguntar por
    teléfono todas las semanas.
    """
    ajeno = actor["id"] != usuario_id
    if ajeno and actor.get("role_name") not in ROLES_GESTION_USUARIOS:
        raise PermissionError("No tiene permiso para consultar turnos ajenos.")
    ciclo = db.get_ciclo_de(usuario_id)
    if not ciclo:
        return []
    paso = int(ciclo["dias_por_tramo"])
    hoy = date.today()
    # El calendario arranca en el tramo en curso, no hoy: lo que interesa es
    # desde cuándo rige cada turno, no en qué día se abrió la pantalla.
    transcurridos = (hoy - ciclo["ancla"]).days
    inicio = ciclo["ancla"] + timedelta(days=(transcurridos // paso) * paso)
    proyeccion: List[Dict[str, Any]] = []
    for tramo in range(max(1, semanas)):
        desde = inicio + timedelta(days=paso * tramo)
        turno = turno_vigente(db, usuario_id, desde)
        proyeccion.append(
            {
                "desde": desde.isoformat(),
                "hasta": (desde + timedelta(days=paso - 1)).isoformat(),
                "turno": turno.nombre,
                "horario": turno.etiqueta(),
                "origen": turno.origen,
                "en_curso": tramo == 0,
            }
        )
    return proyeccion
