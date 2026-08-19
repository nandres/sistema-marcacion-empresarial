"""Autenticación y control de accesos basado en roles (RBAC) con bcrypt.

Las contraseñas se encriptan con bcrypt (sal aleatoria embebida en el hash)
y nunca se almacenan en texto plano en PostgreSQL. Las operaciones de
gestión de usuarios registran automáticamente un evento en
``logs_auditoria`` con quién, qué, cuándo y los valores anterior/nuevo.
"""

from __future__ import annotations

import getpass
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

import bcrypt

from database import Database

ROLE_ADMIN: str = "Administrador"
ROLE_RRHH: str = "Recursos Humanos"
ROLE_EMPLEADO: str = "Empleado"

ROLES_GESTION_USUARIOS: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_REPORTES: tuple = (ROLE_ADMIN, ROLE_RRHH)
ROLES_MARCAJES: tuple = (ROLE_ADMIN, ROLE_RRHH, ROLE_EMPLEADO)

TIPOS_PERMISO: tuple = ("Vacaciones", "Reposo", "Permiso")

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
) -> int:
    """Crea un usuario auditando la acción; solo el Admin asigna otro Admin."""
    if role_name == ROLE_ADMIN:
        require_role(db, actor, (ROLE_ADMIN,))
    role = db.get_role_by_name(role_name)
    if not role:
        raise ValueError(f"El rol '{role_name}' no existe.")
    if db.get_user_by_username(username):
        raise ValueError("El usuario ya existe.")
    user_id = db.create_user(
        username, hash_password(password), full_name, role["id"], salario_mensual
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
) -> None:
    """Edita un usuario auditando los valores anterior y nuevo."""
    if role_name == ROLE_ADMIN:
        require_role(db, actor, (ROLE_ADMIN,))
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