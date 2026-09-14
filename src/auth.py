"""Autenticación y control de accesos basado en roles (RBAC) con bcrypt.

Las contraseñas se encriptan con bcrypt (sal aleatoria embebida en el hash)
y nunca se almacenan en texto plano en PostgreSQL. Las operaciones de
gestión de usuarios registran automáticamente un evento en
``logs_auditoria`` con quién, qué, cuándo y los valores anterior/nuevo.
"""

from __future__ import annotations

import getpass
import os
from datetime import datetime, time, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

import bcrypt
import jwt

from clock_engine import (
    calcular_horas_paraguay,
    es_feriado_o_domingo,
    es_tardanza,
    persistir_desglose,
)
from database import Database, load_dotenv
import notifications
import reglamento

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


def crear_token_acceso(usuario_id: int, rol: str) -> str:
    """Genera un token JWT firmado con vigencia de 8 horas.

    El token transporta el identificador del usuario y su rol como claims
    verificables; expira automáticamente y debe enviarse en cada petición
    protegida dentro de la cabecera de autorización ``Bearer``.
    """
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "rol": rol,
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


def authenticate(db: Database, username: str, password: str) -> Optional[Dict]:
    """Autentica credenciales contra el hash bcrypt de la base de datos."""
    user = db.get_user_by_username(username)
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


def prompt_login(db: Database) -> Optional[Dict]:
    """Solicita credenciales por consola y autentica al usuario."""
    username = input("Usuario: ").strip()
    password = getpass.getpass("Contraseña: ")
    return authenticate(db, username, password)


def crear_primer_admin(
    db: Database, username: str, password: str, full_name: str
) -> int:
    """Crea el primer Administrador (bootstrap, solo con tabla de usuarios vacía)."""
    if db.list_users():
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
) -> int:
    """Crea un usuario auditando la acción; solo el Admin asigna otro Admin."""
    if role_name == ROLE_ADMIN:
        require_role(db, actor, (ROLE_ADMIN,))
    if tipo_vinculo not in TIPOS_VINCULO:
        raise ValueError(f"Tipo de vínculo inválido. Use: {', '.join(TIPOS_VINCULO)}")
    role = db.get_role_by_name(role_name)
    if not role:
        raise ValueError(f"El rol '{role_name}' no existe.")
    if db.get_user_by_username(username):
        raise ValueError("El usuario ya existe.")
    user_id = db.create_user(
        username,
        hash_password(password),
        full_name,
        role["id"],
        salario_mensual,
        tipo_vinculo,
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
) -> None:
    """Edita un usuario auditando los valores anterior y nuevo."""
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
    anterior = _valores_auditoria(target)
    password_hash = hash_password(password) if password else None
    db.update_user(
        user_id,
        full_name=full_name,
        password_hash=password_hash,
        role_id=role_id,
        salario_mensual=salario_mensual,
        tipo_vinculo=tipo_vinculo,
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
    if estado["restantes"] is not None and horas_usadas > estado["restantes"]:
        notifications.registrar_alerta(
            db,
            "cuota_bloqueada",
            "alta",
            f"Intento bloqueado: cuota insuficiente de '{tipo_permiso}' "
            f"para {empleado['full_name']}.",
            f"Quedan {estado['restantes']:g} {estado['unidad']} y se solicitaron "
            f"{horas_usadas:g} · actor {actor['full_name']}",
            usuario_id=empleado["id"],
        )
        raise ValueError(
            f"Solo quedan {estado['restantes']:g} horas disponibles de "
            f"'{tipo_permiso}' en el mes."
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
    tardanza = es_tardanza(instante)
    incidencia = "Llegada Tardía" if tardanza else ""
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