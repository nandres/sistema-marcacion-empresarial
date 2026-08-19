"""Interfaz de consola del Sistema de Marcación Empresarial para Paraguay.

Orquesta la autenticación RBAC, la marcación con reglas de la Ley N.º 213,
la gestión de usuarios con auditoría y la exportación de reportes mensuales.
El menú se adapta al rol del usuario conectado.
"""

from __future__ import annotations

from typing import Optional

import auth
import reports
from clock_engine import ClockEngine
from database import Database


def list_users_menu(db: Database) -> None:
    """Muestra el listado de usuarios con su rol."""
    print("\n=== Usuarios registrados ===")
    for user in db.list_users():
        print(
            f"  #{user['id']} {user['username']} | {user['full_name']} | "
            f"{user['role_name']}"
        )


def create_user_menu(db: Database, actor: dict) -> None:
    """Asiste la creación de un usuario desde consola."""
    print("\n=== Crear usuario ===")
    username = input("Nombre de usuario: ").strip()
    full_name = input("Nombre completo: ").strip()
    print("Roles disponibles:")
    for role in db.list_roles():
        print(f"  - {role['nombre']}")
    role_name = input("Rol: ").strip()
    password = input("Contraseña: ")
    salario = _prompt_salario()
    try:
        user_id = auth.create_user(
            db, actor, username, password, full_name, role_name, salario
        )
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Usuario #{user_id} creado.")


def _prompt_salario() -> float:
    """Solicita el salario mensual en guaraníes (0 si se omite)."""
    valor = input("Salario mensual (Gs., ej. 2500000) [0]: ").strip()
    return float(valor) if valor else 0.0


def update_user_menu(db: Database, actor: dict) -> None:
    """Asiste la edición de un usuario desde consola."""
    print("\n=== Editar usuario ===")
    user_id = input("ID del usuario a editar: ").strip()
    target = db.get_user_by_id(int(user_id))
    if not target:
        print("Usuario no encontrado.")
        return
    print(f"Editando a {target['full_name']} ({target['role_name']})")
    full_name = input(f"Nuevo nombre completo [{target['full_name']}]: ").strip() or None
    password = input("Nueva contraseña (vacío para no cambiar): ").strip() or None
    salario_actual = f"{float(target['salario_mensual'] or 0):,.0f}"
    salario_input = input(f"Nuevo salario mensual (Gs.) [{salario_actual}]: ").strip()
    salario = float(salario_input) if salario_input else None
    print("Roles disponibles:")
    for role in db.list_roles():
        print(f"  - {role['nombre']}")
    role_input = input(f"Nuevo rol [{target['role_name']}]: ").strip() or None
    try:
        auth.update_user(
            db,
            actor,
            int(user_id),
            full_name=full_name,
            password=password,
            role_name=role_input,
            salario_mensual=salario,
        )
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print("Usuario actualizado.")


def delete_user_menu(db: Database, actor: dict) -> None:
    """Asiste la eliminación de un usuario desde consola."""
    print("\n=== Eliminar usuario ===")
    user_id = input("ID del usuario a eliminar: ").strip()
    try:
        auth.delete_user(db, actor, int(user_id))
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print("Usuario eliminado.")


def export_monthly_menu(db: Database, actor: dict) -> None:
    """Asiste la exportación del reporte mensual de asistencia."""
    print("\n=== Exportar asistencia mensual ===")
    try:
        anio = int(input("Año (ej. 2026): ").strip())
        mes = int(input("Mes (1-12): ").strip())
        formato = input("Formato (xlsx/csv) [xlsx]: ").strip().lower() or "xlsx"
        ruta = reports.exportar_asistencia_mensual(db, actor, anio, mes, formato=formato)
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Reporte exportado: {ruta}")


def crear_justificacion_menu(db: Database, actor: dict) -> None:
    """Asiste la creación de una justificación aprobada."""
    from datetime import date

    print("\n=== Crear justificación ===")
    username = input("Empleado (nombre de usuario): ").strip()
    empleado = db.get_user_by_username(username)
    if not empleado:
        print("Empleado no encontrado.")
        return
    print(f"Tipos de permiso: {', '.join(auth.TIPOS_PERMISO)}")
    tipo = input("Tipo de permiso: ").strip()
    try:
        fecha_inicio = date.fromisoformat(input("Fecha inicio (AAAA-MM-DD): ").strip())
        fecha_fin = date.fromisoformat(input("Fecha fin (AAAA-MM-DD): ").strip())
        justificacion_id = auth.crear_justificacion(
            db, actor, empleado["id"], tipo, fecha_inicio, fecha_fin
        )
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Justificación #{justificacion_id} creada y aprobada.")


def export_aguinaldo_menu(db: Database, actor: dict) -> None:
    """Asiste la exportación de la proyección de aguinaldo."""
    print("\n=== Exportar aguinaldo proporcional ===")
    try:
        anio = int(input("Año (ej. 2026): ").strip())
        ruta = reports.exportar_aguinaldo(db, actor, anio)
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Aguinaldo exportado: {ruta}")


def prompt_first_admin(db: Database) -> None:
    """Crea el primer Administrador en el arranque inicial del sistema."""
    print("=== Crear el primer Administrador ===")
    username = input("Nombre de usuario: ").strip()
    full_name = input("Nombre completo: ").strip()
    password = input("Contraseña: ")
    try:
        auth.crear_primer_admin(db, username, password, full_name)
    except PermissionError as error:
        print(error)
        return
    print("Administrador creado.")


def main() -> None:
    """Punto de entrada: inicializa la base de datos y lanza el menú."""
    db = Database()
    db.initialize()

    print("=== Sistema de Marcación ===")
    if not db.list_users():
        prompt_first_admin(db)

    user: Optional[dict] = None
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
            print("10. Crear justificación")
        if role in auth.ROLES_REPORTES:
            print("9. Exportar reporte mensual")
            print("11. Exportar aguinaldo proporcional")
        if role == auth.ROLE_ADMIN:
            print("8. Eliminar usuario")
        print("0. Salir")
        option = input("Seleccione una opción: ").strip()

        if option == "1":
            try:
                auth.can_register_marks(db, user)
                entry_id, momento = engine.clock_in()
                print("Entrada registrada correctamente.")
                print(reports.comprobante_marcacion(entry_id, momento, "ENTRADA"))
            except (ValueError, PermissionError) as error:
                print(error)
        elif option == "2":
            try:
                auth.can_register_marks(db, user)
                entry_id, momento = engine.clock_out()
                print("Salida registrada correctamente.")
                print(reports.comprobante_marcacion(entry_id, momento, "SALIDA"))
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
        elif option == "9" and role in auth.ROLES_REPORTES:
            export_monthly_menu(db, user)
        elif option == "10" and role in auth.ROLES_GESTION_USUARIOS:
            crear_justificacion_menu(db, user)
        elif option == "11" and role in auth.ROLES_REPORTES:
            export_aguinaldo_menu(db, user)
        elif option == "8" and role == auth.ROLE_ADMIN:
            delete_user_menu(db, user)
        elif option == "0":
            print("Hasta pronto.")
            break
        else:
            print("Opción no válida.")


if __name__ == "__main__":
    main()