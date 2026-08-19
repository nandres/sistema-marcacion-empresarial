import sys

import auth
from clock_engine import ClockEngine
from database import Database


def list_users_menu(db):
    print("\n=== Usuarios registrados ===")
    for user in db.list_users():
        print(
            f"  #{user['id']} {user['username']} | {user['full_name']} | "
            f"{user['role_name']}"
        )


def create_user_menu(db, actor):
    print("\n=== Crear usuario ===")
    username = input("Nombre de usuario: ").strip()
    full_name = input("Nombre completo: ").strip()
    print("Roles disponibles:")
    for role in db.list_roles():
        print(f"  - {role['nombre']}")
    role_name = input("Rol: ").strip()
    password = input("Contraseña: ")
    try:
        user_id = auth.create_user(db, actor, username, password, full_name, role_name)
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Usuario #{user_id} creado.")


def update_user_menu(db, actor):
    print("\n=== Editar usuario ===")
    user_id = input("ID del usuario a editar: ").strip()
    target = db.get_user_by_id(int(user_id))
    if not target:
        print("Usuario no encontrado.")
        return
    print(f"Editando a {target['full_name']} ({target['role_name']})")
    full_name = input(f"Nuevo nombre completo [{target['full_name']}]: ").strip() or None
    password = input("Nueva contraseña (vacío para no cambiar): ").strip() or None
    print("Roles disponibles:")
    for role in db.list_roles():
        print(f"  - {role['nombre']}")
    role_input = input(f"Nuevo rol [{target['role_name']}]: ").strip() or None
    try:
        auth.update_user(
            db, actor, int(user_id), full_name=full_name, password=password, role_name=role_input
        )
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print("Usuario actualizado.")


def delete_user_menu(db, actor):
    print("\n=== Eliminar usuario ===")
    user_id = input("ID del usuario a eliminar: ").strip()
    try:
        auth.delete_user(db, actor, int(user_id))
    except PermissionError as error:
        print(error)
        return
    print("Usuario eliminado.")


def prompt_first_admin(db):
    print("=== Crear el primer Administrador ===")
    username = input("Nombre de usuario: ").strip()
    full_name = input("Nombre completo: ").strip()
    password = input("Contraseña: ")
    auth.create_user(db, None, username, password, full_name, auth.ROLE_ADMIN)
    print("Administrador creado.")


def main():
    db = Database()
    db.initialize()

    print("=== Sistema de Marcación ===")
    if not db.list_users():
        prompt_first_admin(db)

    user = None
    while user is None:
        user = auth.prompt_login(db)
        if user is None:
            print("Credenciales incorrectas.")

    role = auth.get_role_name(db, user)
    engine = ClockEngine(db, user)
    print(f"\nBienvenido, {user['full_name']} ({role}).")

    while True:
        print("\n--- Menú principal ---")
        print("1. Marcar entrada")
        print("2. Marcar salida")
        print("3. Registros de hoy")
        print("4. Total de horas trabajadas")
        if role in auth.ROLES_GESTION_USUARIOS:
            print("5. Listar usuarios")
            print("6. Crear usuario")
            print("7. Editar usuario")
        if role == auth.ROLE_ADMIN:
            print("8. Eliminar usuario")
        print("0. Salir")
        option = input("Seleccione una opción: ").strip()

        if option == "1":
            try:
                auth.can_register_marks(db, user)
                engine.clock_in()
                print("Entrada registrada correctamente.")
            except (ValueError, PermissionError) as error:
                print(error)
        elif option == "2":
            try:
                auth.can_register_marks(db, user)
                engine.clock_out()
                print("Salida registrada correctamente.")
            except (ValueError, PermissionError) as error:
                print(error)
        elif option == "3":
            print(engine.report_today())
        elif option == "4":
            total = engine.total_worked_seconds()
            print(f"Total acumulado: {engine.format_duration(total)}")
        elif option == "5" and role in auth.ROLES_GESTION_USUARIOS:
            list_users_menu(db)
        elif option == "6" and role in auth.ROLES_GESTION_USUARIOS:
            create_user_menu(db, user)
        elif option == "7" and role in auth.ROLES_GESTION_USUARIOS:
            update_user_menu(db, user)
        elif option == "8" and role == auth.ROLE_ADMIN:
            delete_user_menu(db, user)
        elif option == "0":
            print("Hasta pronto.")
            break
        else:
            print("Opción no válida.")


if __name__ == "__main__":
    main()