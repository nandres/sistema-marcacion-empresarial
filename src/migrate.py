"""Aplica el esquema de PostgreSQL. Paso de despliegue, previo al servidor.

Las migraciones toman locks exclusivos de tabla, así que no pueden correr
dentro del ciclo de una petición HTTP ni en cada ciclo de un worker: bajo
concurrencia las peticiones se serializan esperando el lock. Este módulo
concentra el DDL en un único paso explícito que se ejecuta una vez, antes
de levantar los procesos que atienden tráfico.

Uso:
    python migrate.py          # aplica el esquema y sale
"""

from __future__ import annotations

import sys

import psycopg2

import auth
import biometria
from database import Database


def aplicar() -> int:
    """Crea la base si falta, aplica el esquema y valida los secretos.

    Returns:
        Cantidad de plantillas faciales que quedaron cifradas en esta corrida.
    """
    auth.verificar_secretos()
    db = Database()
    try:
        db.initialize()
        # Una instalación anterior guardaba las fotos en claro. Migrarlas es
        # parte del despliegue y no una tarea que alguien deba recordar.
        if biometria.configurada():
            return biometria.migrar_fotos(db)
        return 0
    finally:
        db.cerrar()


def main() -> int:
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
    print("Esquema aplicado correctamente.")
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
