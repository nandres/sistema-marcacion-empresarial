"""Aplica el esquema de PostgreSQL. Paso de despliegue, previo al servidor.

Las migraciones toman locks exclusivos de tabla, así que no pueden correr
dentro del ciclo de una petición HTTP ni en cada ciclo de un worker: bajo
concurrencia las peticiones se serializan esperando el lock. Este módulo
concentra el DDL en un único paso explícito que se ejecuta una vez, antes
de levantar los procesos que atienden tráfico.

Uso:
    python migrate.py            # aplica el esquema y sale
    python migrate.py estado     # dice en qué versión está esta base
    python migrate.py rol-app    # crea el rol restringido del servicio
"""

from __future__ import annotations

import os
import secrets
import sys

import psycopg2

import auth
import biometria
import database
import registro
from database import Database

ROL_APLICACION: str = "marcacion_app"


def aplicar() -> int:
    """Crea la base si falta, aplica el esquema y valida los secretos.

    Returns:
        Cantidad de plantillas faciales que quedaron cifradas en esta corrida.
    """
    auth.verificar_secretos()
    db = Database()
    try:
        db.migrar()
        # Una instalación anterior guardaba las fotos en claro. Migrarlas es
        # parte del despliegue y no una tarea que alguien deba recordar.
        if biometria.configurada():
            return biometria.migrar_fotos(db)
        return 0
    finally:
        db.cerrar()


def informar_estado() -> int:
    """Dice qué versión tiene esta base y qué le falta, sin tocar nada.

    Con un solo cliente la pregunta no existe. Con varios, instalados en
    momentos distintos y actualizados cuando cada uno pudo, *¿qué versión
    corre acá?* es la primera pregunta de cualquier incidente, y hasta ahora
    solo se podía contestar mirando el código que uno cree haberle instalado.
    """
    db = Database()
    try:
        db.connect()
        version = db.version_esquema()
        pendientes = db.migraciones_pendientes()
        historial = db.historial_esquema()
    finally:
        db.cerrar()

    print(f"Esquema de la base: versión {version}"
          if version is not None else
          "Esquema de la base: sin migrar (no hay registro de migraciones)")
    print(f"Esperado por este código: versión {database.ESQUEMA_VERSION}")

    if historial:
        print("\nAplicado:")
        for paso in historial:
            print(f"  {paso['aplicada_en']:%Y-%m-%d %H:%M}  {paso['nombre']}")

    if pendientes:
        print("\nPendiente:")
        for nombre in pendientes:
            print(f"  {nombre}")
        print("\nEl servidor no va a arrancar hasta correr 'python migrate.py'.")
        return 1

    print("\nAl día: esta base puede atender tráfico con este código.")
    return 0


def informar_aislamiento() -> None:
    """Dice si las políticas por fila alcanzan al rol configurado.

    Una política instalada pero inerte se parece demasiado a una que protege,
    así que el estado se dice en voz alta en cada migración.
    """
    db = Database()
    try:
        db.connect()
        estado = db.rls_efectiva()
    finally:
        db.cerrar()
    if estado["activa"]:
        print(
            f"Aislamiento por fila ACTIVO: {estado['politicas']} políticas "
            f"sobre el rol '{estado['rol']}'."
        )
        return
    print(
        f"Aviso: las {estado['politicas']} políticas de aislamiento NO alcanzan "
        f"al rol '{estado['rol']}', porque puede saltearlas.\n"
        f"El aislamiento entre empresas queda sostenido solo por la aplicación. "
        f"Para que además lo imponga la base:\n"
        f"  python src/migrate.py rol-app\n"
        f"y usá en el .env el DB_USER que imprime.",
        file=sys.stderr,
    )


def crear_rol_app() -> int:
    """Crea el rol restringido con el que debería correr el servicio."""
    nombre = os.getenv("DB_APP_USER", ROL_APLICACION)
    db = Database()
    try:
        db.connect()
        existia = db._execute(
            "SELECT 1 AS hay FROM pg_roles WHERE rolname = %s", (nombre,),
            fetch="one",
        )
        # Sobre un rol que ya está, la contraseña solo se cambia si se pide:
        # rotarla sola dejaría sin base al servicio que esté corriendo.
        password = os.getenv("DB_APP_PASSWORD") or (
            None if existia else secrets.token_urlsafe(24)
        )
        db.crear_rol_de_aplicacion(nombre, password)
    finally:
        db.cerrar()
    print(f"Rol '{nombre}' listo: sin DDL, sin superusuario, sin BYPASSRLS.")
    if password is None:
        print(
            "El rol ya existía: se repasaron sus permisos y se dejó la "
            "contraseña como estaba.\n"
            "Para cambiarla: DB_APP_PASSWORD=... python src/migrate.py rol-app"
        )
        return 0
    print("Poné esto en el .env del proceso que atiende tráfico:")
    print(f"  DB_USER={nombre}")
    print(f"  DB_PASSWORD={password}")
    print(
        "Guardá la contraseña ahora: no vuelve a mostrarse. El rol "
        "administrador se sigue usando solo para migrar."
    )
    return 0


def main() -> int:
    registro.configurar()
    if len(sys.argv) > 1 and sys.argv[1] == "estado":
        try:
            return informar_estado()
        except Exception as error:
            print(f"No se pudo leer el estado del esquema: {error}", file=sys.stderr)
            return 1
    if len(sys.argv) > 1 and sys.argv[1] == "rol-app":
        try:
            return crear_rol_app()
        except Exception as error:
            print(f"No se pudo crear el rol: {error}", file=sys.stderr)
            return 1
    try:
        cifradas = aplicar()
    except psycopg2.errors.LockNotAvailable:
        print(
            "Migración fallida: otra sesión mantiene una transacción abierta "
            "sobre las tablas a migrar.\n"
            "Cerrá la aplicación de escritorio (o el proceso que quedó colgado) "
            "y volvé a intentar. Para identificarla:\n"
            "  SELECT pid, state, query FROM pg_stat_activity "
            "WHERE state = 'idle in transaction';",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(f"Migración fallida: {error}", file=sys.stderr)
        return 1
    print(f"Esquema aplicado correctamente (versión {database.ESQUEMA_VERSION}).")
    informar_aislamiento()
    if cifradas:
        print(f"Plantillas faciales cifradas en reposo: {cifradas}.")
    elif not biometria.configurada():
        print(
            f"Aviso: {biometria.VARIABLE_CLAVE} no está configurada. "
            "El registro de fotos biométricas va a fallar hasta definirla.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
