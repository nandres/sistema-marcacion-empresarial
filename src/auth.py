import getpass
import hashlib
import hmac
import os

from database import Database

PBKDF2_ITERATIONS = 100_000

ROLE_ADMIN = "Administrador"
ROLE_RRHH = "Recursos Humanos"
ROLE_EMPLEADO = "Empleado"

ROLES_GESTION_USUARIOS = (ROLE_ADMIN, ROLE_RRHH)
ROLES_CREACION_ADMIN = (ROLE_ADMIN,)


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return salt.hex() + ":" + digest.hex()


def verify_password(password, stored):
    salt_hex, digest_hex = stored.split(":")
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def get_role_name(db, user):
    return user["role_name"] if "role_name" in user else db.get_user_by_id(user["id"])["role_name"]


def require_role(db, user, allowed_roles):
    role = get_role_name(db, user)
    if role not in allowed_roles:
        raise PermissionError(
            f"Rol '{role}' no autorizado. Requiere: {', '.join(allowed_roles)}"
        )
    return role


def authenticate(db, username, password):
    user = db.get_user_by_username(username)
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


def prompt_login(db):
    username = input("Usuario: ").strip()
    password = getpass.getpass("Contraseña: ")
    return authenticate(db, username, password)


def create_user(db, actor, username, password, full_name, role_name):
    require_role(db, actor, ROLES_GESTION_USUARIOS)
    if role_name == ROLE_ADMIN:
        require_role(db, actor, ROLES_CREACION_ADMIN)
    role = db.get_role_by_name(role_name)
    if not role:
        raise ValueError(f"El rol '{role_name}' no existe.")
    if db.get_user_by_username(username):
        raise ValueError("El usuario ya existe.")
    return db.create_user(username, hash_password(password), full_name, role["id"])


def update_user(db, actor, user_id, full_name=None, password=None, role_name=None):
    require_role(db, actor, ROLES_GESTION_USUARIOS)
    if role_name == ROLE_ADMIN:
        require_role(db, actor, ROLES_CREACION_ADMIN)
    role_id = None
    if role_name is not None:
        role = db.get_role_by_name(role_name)
        if not role:
            raise ValueError(f"El rol '{role_name}' no existe.")
        role_id = role["id"]
    password_hash = hash_password(password) if password else None
    db.update_user(
        user_id,
        full_name=full_name,
        password_hash=password_hash,
        role_id=role_id,
    )


def delete_user(db, actor, user_id):
    require_role(db, actor, (ROLE_ADMIN,))
    db.delete_user(user_id)


def can_register_marks(db, user):
    require_role(db, user, (ROLE_ADMIN, ROLE_RRHH, ROLE_EMPLEADO))
    return True