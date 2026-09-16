"""Interfaz de consola del Sistema de Marcación Empresarial para Paraguay.

Orquesta la autenticación RBAC, la marcación con reglas de la Ley N.º 213,
la gestión de usuarios con auditoría y la exportación de reportes mensuales.
El menú se adapta al rol del usuario conectado.

    python src/app.py                            # menú interactivo
    python src/app.py alta-empresa               # alta de un cliente nuevo
    python src/app.py verificar-comprobante [archivo]
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import auth
import reports
from clock_engine import MotorDeJornada
from database import Database, SinEmpresa


def menu_listar_usuarios(db: Database) -> None:
    """Muestra el listado de usuarios con su rol."""
    print("\n=== Usuarios registrados ===")
    for user in db.listar_usuarios():
        print(
            f"  #{user['id']} {user['username']} | {user['full_name']} | "
            f"{user['role_name']}"
        )


def menu_crear_usuario(db: Database, actor: dict) -> None:
    """Asiste la creación de un usuario desde consola."""
    print("\n=== Crear usuario ===")
    username = input("Nombre de usuario: ").strip()
    full_name = input("Nombre completo: ").strip()
    print("Roles disponibles:")
    for role in db.listar_roles():
        print(f"  - {role['nombre']}")
    role_name = input("Rol: ").strip()
    password = input("Contraseña: ")
    salario = _prompt_salario()
    try:
        user_id = auth.crear_usuario(
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


def menu_editar_usuario(db: Database, actor: dict) -> None:
    """Asiste la edición de un usuario desde consola."""
    print("\n=== Editar usuario ===")
    user_id = input("ID del usuario a editar: ").strip()
    target = db.usuario_por_id(int(user_id))
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
    for role in db.listar_roles():
        print(f"  - {role['nombre']}")
    role_input = input(f"Nuevo rol [{target['role_name']}]: ").strip() or None
    try:
        auth.actualizar_usuario(
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


def menu_eliminar_usuario(db: Database, actor: dict) -> None:
    """Asiste la eliminación de un usuario desde consola."""
    print("\n=== Eliminar usuario ===")
    user_id = input("ID del usuario a eliminar: ").strip()
    try:
        auth.eliminar_usuario(db, actor, int(user_id))
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print("Usuario eliminado.")


def menu_exportar_mes(db: Database, actor: dict) -> None:
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


def menu_crear_justificacion(db: Database, actor: dict) -> None:
    """Asiste la creación de una justificación aprobada."""
    from datetime import date

    print("\n=== Crear justificación ===")
    username = input("Empleado (nombre de usuario): ").strip()
    empleado = db.usuario_por_cedula(username)
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


def menu_exportar_aguinaldo(db: Database, actor: dict) -> None:
    """Asiste la exportación de la proyección de aguinaldo."""
    print("\n=== Exportar aguinaldo proporcional ===")
    try:
        anio = int(input("Año (ej. 2026): ").strip())
        ruta = reports.exportar_aguinaldo(db, actor, anio)
    except (ValueError, PermissionError) as error:
        print(error)
        return
    print(f"Aguinaldo exportado: {ruta}")


def pedir_primer_admin(db: Database) -> None:
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


def elegir_empresa(db: Database) -> None:
    """Deja la sesión de consola trabajando sobre una empresa concreta.

    Con un solo cliente alojado no hay nada que preguntar. Con varios, elegir
    mal significa administrar el personal equivocado, así que se pregunta.
    ``EMPRESA_ACTIVA`` en el ``.env`` responde por adelantado, que es lo que
    necesita un kiosco instalado en la sede de un cliente.
    """
    preferida = os.getenv("EMPRESA_ACTIVA", "").strip()
    if preferida:
        empresa = db.usar_empresa(preferida)
        print(f"Empresa: {empresa['razon_social']}")
        return
    alojadas = db.listar_empresas(incluir_inactivas=True)
    if len(alojadas) <= 1:
        return
    print("\n=== Empresas alojadas ===")
    for indice, empresa in enumerate(alojadas, start=1):
        estado = "" if empresa["activa"] else "  (suspendida)"
        print(f"  {indice}. {empresa['razon_social']} [{empresa['slug']}]{estado}")
    while True:
        elegida = input("Empresa (número o nombre corto): ").strip()
        if elegida.isdigit() and 1 <= int(elegida) <= len(alojadas):
            db.empresa_id = alojadas[int(elegida) - 1]["id"]
            return
        try:
            db.usar_empresa(elegida)
            return
        except SinEmpresa:
            print("No existe esa empresa.")


def alta_de_empresa(db: Database) -> None:
    """Aloja un cliente nuevo y le crea su primer administrador.

    Dar de alta una empresa es un acto de instalación, no una pantalla del
    producto: no existe ninguna sesión que pueda ver dos clientes a la vez, y
    ese es justamente el aislamiento que se ofrece.
    """
    print("\n=== Alojar una empresa nueva ===")
    slug = input("Nombre corto (sin espacios, p. ej. 'acme'): ").strip().lower()
    razon = input("Razón social: ").strip()
    ruc = input("RUC (opcional): ").strip()
    if not slug or not razon:
        print("El nombre corto y la razón social son obligatorios.")
        return
    if db.empresa_por_slug(slug):
        print(f"Ya hay una empresa con el nombre corto '{slug}'.")
        return
    empresa = db.crear_empresa(slug, razon, ruc)
    db.empresa_id = empresa["id"]
    db.initialize()
    db.empresa_id = empresa["id"]
    print(f"Empresa '{razon}' alojada. Ahora su primer administrador:")
    pedir_primer_admin(db)


def verificar_comprobante_impreso(origen: Optional[str]) -> int:
    """Comprueba un comprobante pegado por teclado o leído de un archivo.

    Es la contraparte del ticket que el kiosco entrega: sin ella la firma es
    decorativa, porque nadie tiene con qué desmentir un papel.
    """
    texto = Path(origen).read_text(encoding="utf-8") if origen else sys.stdin.read()
    veredicto = reports.verificar_comprobante(texto)
    print(veredicto["motivo"])
    if veredicto["valido"]:
        print(f"  Marcaje {veredicto['registro_id']} · {veredicto['tipo']} · "
              f"{veredicto['instante']}")
        return 0
    return 1


@dataclass(frozen=True)
class OpcionMenu:
    """Una entrada del menú: qué dice, quién la ve y qué hace."""

    etiqueta: str
    accion: Callable[[Database, dict, MotorDeJornada], None]
    roles: Optional[Tuple[str, ...]] = None

    def visible_para(self, rol: str) -> bool:
        return self.roles is None or rol in self.roles


def _marcar(tipo: str) -> Callable[[Database, dict, MotorDeJornada], None]:
    """Arma la acción de marcar entrada o salida, que solo difieren en eso."""
    def accion(db: Database, usuario: dict, motor: MotorDeJornada) -> None:
        try:
            auth.puede_marcar(db, usuario)
            marcaje_id, momento = (motor.marcar_entrada() if tipo == "ENTRADA"
                                   else motor.marcar_salida())
            print(f"{tipo.capitalize()} registrada correctamente.")
            print(reports.comprobante_marcacion(marcaje_id, momento, tipo))
        except (ValueError, PermissionError) as error:
            print(error)
    return accion


def _mostrar_total(db: Database, usuario: dict, motor: MotorDeJornada) -> None:
    total = motor.segundos_trabajados()
    print(f"Total acumulado: {motor.formatear_duracion(total)}")


OPCIONES: Tuple[OpcionMenu, ...] = (
    OpcionMenu("Marcar entrada", _marcar("ENTRADA")),
    OpcionMenu("Marcar salida", _marcar("SALIDA")),
    OpcionMenu("Registros de hoy",
               lambda db, usuario, motor: print(motor.resumen_de_hoy())),
    OpcionMenu("Total de horas trabajadas", _mostrar_total),
    OpcionMenu("Listar usuarios",
               lambda db, usuario, motor: menu_listar_usuarios(db),
               auth.ROLES_GESTION_USUARIOS),
    OpcionMenu("Crear usuario",
               lambda db, usuario, motor: menu_crear_usuario(db, usuario),
               auth.ROLES_GESTION_USUARIOS),
    OpcionMenu("Editar usuario",
               lambda db, usuario, motor: menu_editar_usuario(db, usuario),
               auth.ROLES_GESTION_USUARIOS),
    OpcionMenu("Eliminar usuario",
               lambda db, usuario, motor: menu_eliminar_usuario(db, usuario),
               (auth.ROLE_ADMIN,)),
    OpcionMenu("Crear justificación",
               lambda db, usuario, motor: menu_crear_justificacion(db, usuario),
               auth.ROLES_GESTION_USUARIOS),
    OpcionMenu("Exportar reporte mensual",
               lambda db, usuario, motor: menu_exportar_mes(db, usuario),
               auth.ROLES_REPORTES),
    OpcionMenu("Exportar aguinaldo proporcional",
               lambda db, usuario, motor: menu_exportar_aguinaldo(db, usuario),
               auth.ROLES_REPORTES),
)


def _menu_de(rol: str) -> List[Tuple[str, OpcionMenu]]:
    """Numera, en orden, solo lo que ese rol puede hacer.

    La numeración se calcula en lugar de escribirse. Antes el listado y el
    despacho eran dos series de números escritas a mano, y se habían
    desincronizado: el menú imprimía 5, 6, 7, 10, 9, 11, 8.
    """
    visibles = (opcion for opcion in OPCIONES if opcion.visible_para(rol))
    return [(str(numero), opcion)
            for numero, opcion in enumerate(visibles, start=1)]


def main() -> None:
    """Punto de entrada: inicializa la base de datos y lanza el menú."""
    # Verificar un comprobante no necesita base ni sesión: se resuelve con el
    # papel y la clave, que es justamente lo que lo hace comprobable.
    if len(sys.argv) > 1 and sys.argv[1] == "verificar-comprobante":
        raise SystemExit(verificar_comprobante_impreso(
            sys.argv[2] if len(sys.argv) > 2 else None
        ))

    db = Database()
    db.initialize()

    print("=== Sistema de Marcación ===")
    if len(sys.argv) > 1 and sys.argv[1] == "alta-empresa":
        alta_de_empresa(db)
        return
    elegir_empresa(db)
    if not db.listar_usuarios(incluir_bajas=True):
        pedir_primer_admin(db)

    user: Optional[dict] = None
    while user is None:
        user = auth.pedir_credenciales(db)
        if user is None:
            print("Credenciales incorrectas.")

    rol = auth.nombre_de_rol(db, user)
    motor = MotorDeJornada(db, user)
    print(f"\nBienvenido, {user['full_name']} ({rol}).")
    menu = _menu_de(rol)

    while True:
        print("\n--- Menú principal ---")
        for numero, opcion in menu:
            print(f"{numero}. {opcion.etiqueta}")
        print("0. Salir")

        elegida = input("Seleccione una opción: ").strip()
        if elegida == "0":
            print("Hasta pronto.")
            break
        despacho = dict(menu).get(elegida)
        if despacho is None:
            print("Opción no válida.")
            continue
        despacho.accion(db, user, motor)


if __name__ == "__main__":
    main()
