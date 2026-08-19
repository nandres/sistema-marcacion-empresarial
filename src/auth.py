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

from clock_engine import calcular_horas_paraguay, es_feriado_o_domingo, es_tardanza
from database import Database, load_dotenv

ROLE_ADMIN: str = "Administrador"
ROLE_RRHH: str = "Recursos Humanos"
ROLE_EMPLEADO: str = "Empleado"

ROLES_GESTION_USUARIOS: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_REPORTES: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_MARCAJES: tuple = (ROLE_ADMIN, ROLE_RRHH, ROLE_EMPLEADO)

TIPOS_PERMISO: tuple = ("Vacaciones", "Reposo", "Permiso", "Permiso por Examen")

TIPOS_VINCULO: tuple = ("Pasante", "Funcionario")

JWT_EXPIRACION_HORAS: int = 8
JWT_ALGORITMO: str = "HS256"


def _jwt_secret() -> str:
    """Clave de firma de tokens leída de ``JWT_SECRET_KEY`` en el ``.env``."""
    load_dotenv()
    return os.getenv("JWT_SECRET_KEY", "clave-de-desarrollo-no-usar-en-produccion")


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


@autorizado(ROLE_ADMIN, ROLE_RRHH)
def crear_justificacion(
    db: Database,
    actor: Dict,
    empleado_id: int,
    tipo_permiso: str,
    fecha_inicio: Any,
    fecha_fin: Any,
) -> int:
    """Crea una justificación aprobada para un empleado (solo RRHH/Admin).

    El actor que la crea queda registrado como ``aprobado_por`` y la
    operación se audita en ``logs_auditoria``.
    """
    if tipo_permiso not in TIPOS_PERMISO:
        raise ValueError(
            f"Tipo de permiso inválido. Use: {', '.join(TIPOS_PERMISO)}"
        )
    if fecha_fin < fecha_inicio:
        raise ValueError("La fecha de fin no puede ser anterior al inicio.")
    if not db.get_user_by_id(empleado_id):
        raise ValueError("El empleado no existe.")
    justificacion_id = db.crear_justificacion(
        empleado_id, tipo_permiso, fecha_inicio, fecha_fin, actor["id"]
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
        },
    )
    return justificacion_id


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
    instante = datetime.combine(solicitud["fecha_registro"], solicitud["hora_propuesta"])
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
        "horas_extra_50": str(marcaje["horas_extra_50"] or "00:00:00"),
        "horas_extra_100": str(marcaje["horas_extra_100"] or "00:00:00"),
        "tipo_incidencia": marcaje.get("tipo_incidencia") or "",
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
    feriado = es_feriado_o_domingo(marcaje["hora_entrada"])
    desglose = calcular_horas_paraguay(marcaje["hora_entrada"], instante, feriado)
    db.close_clock_out(
        marcaje["id"],
        instante,
        feriado,
        desglose["horas_ordinarias"],
        desglose["horas_extra_50"],
        desglose["horas_extra_100"],
        marcaje.get("tipo_incidencia") or "",
    )
    nuevos = dict(
        anterior,
        hora_salida=instante.isoformat(),
        es_feriado=feriado,
        horas_ordinarias=str(desglose["horas_ordinarias"]),
        horas_extra_50=str(desglose["horas_extra_50"]),
        horas_extra_100=str(desglose["horas_extra_100"]),
    )
    db.registrar_auditoria(
        actor["id"], "ACTUALIZAR", "marcajes", marcaje["id"],
        anterior=anterior, nuevos=nuevos,
    )