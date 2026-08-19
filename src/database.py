"""Capa de persistencia PostgreSQL del Sistema de Marcación Empresarial.

Responsabilidades:
- Carga de credenciales desde el archivo ``.env``.
- Creación automática de la base de datos ``marcacion`` si el usuario
  de PostgreSQL posee permisos, y del esquema relacional en caso de
  que la base ya exista.
- Esquema de roles, usuarios, marcajes y el registro ``logs_auditoria``
  que documenta toda operación administrativa (quién, qué, cuándo y
  valores anterior/nuevo) para prevenir fraudes internos.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import Json, RealDictCursor

DEFAULT_CONFIG: Dict[str, str] = {
    "dbname": "marcacion",
    "user": "postgres",
    "password": "",
    "host": "localhost",
    "port": "5432",
}

ROLES_INICIALES: Tuple[str, ...] = ("Administrador", "Recursos Humanos", "Empleado")


def load_dotenv(path: str = ".env") -> None:
    """Carga las variables ``KEY=VALUE`` del archivo indicado al entorno.

    Respeta las variables ya definidas en el entorno real (no las pisa)
    y descarta comentarios y líneas vacías.
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_config() -> Dict[str, str]:
    """Construye el diccionario de conexión a partir del entorno y del ``.env``."""
    load_dotenv()
    return {
        "dbname": os.getenv("DB_NAME", DEFAULT_CONFIG["dbname"]),
        "user": os.getenv("DB_USER", DEFAULT_CONFIG["user"]),
        "password": os.getenv("DB_PASSWORD", DEFAULT_CONFIG["password"]),
        "host": os.getenv("DB_HOST", DEFAULT_CONFIG["host"]),
        "port": os.getenv("DB_PORT", DEFAULT_CONFIG["port"]),
    }


class Database:
    """Interfaz de acceso a datos sobre PostgreSQL (psycopg2)."""

    def __init__(self, config: Optional[Dict[str, str]] = None) -> None:
        self.config: Dict[str, str] = config or load_config()
        self.connection: Optional[psycopg2.connection] = None

    def connect(self) -> psycopg2.connection:
        """Abre y retorna la conexión con la base configurada."""
        self.connection = psycopg2.connect(client_encoding="UTF8", **self.config)
        self.connection.autocommit = False
        return self.connection

    def ensure_database(self) -> None:
        """Garantiza que la base de datos configurada exista.

        Estrategia en tres pasos:
        1. Intenta conectar directo a la base objetivo (ya existe).
        2. Si falla (base inexistente, SQLSTATE 3D000, o mensaje FATAL
           del servidor sin decodificar en UTF-8), conecta a la base de
           mantenimiento ``postgres`` y la crea en UTF-8.
        3. Si el usuario no tiene permisos, eleva un error claro con el
           SQL exacto para crearla manualmente en pgAdmin o DBeaver.
        """
        nombre = self.config["dbname"]
        if not nombre.replace("_", "").isalnum():
            raise ValueError("DB_NAME contiene caracteres no permitidos.")
        try:
            conexion = psycopg2.connect(**self.config, connect_timeout=5)
            conexion.close()
            return
        except (psycopg2.OperationalError, UnicodeDecodeError) as error:
            if isinstance(error, psycopg2.OperationalError) and getattr(
                error, "pgcode", None
            ) not in (None, "3D000"):
                raise
        try:
            conexion = psycopg2.connect(
                **{**self.config, "dbname": "postgres"}, connect_timeout=5
            )
            conexion.autocommit = True
            cursor = conexion.cursor()
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,))
            if cursor.fetchone() is None:
                cursor.execute(
                    f'CREATE DATABASE "{nombre}" ENCODING \'UTF8\' TEMPLATE template0'
                )
            cursor.close()
            conexion.close()
        except psycopg2.Error as error:
            raise RuntimeError(
                f"No se pudo crear la base de datos '{nombre}' automáticamente.\n"
                f"Causa: {error}\n"
                f"Solución manual (pgAdmin/DBeaver): ejecuta  "
                f"CREATE DATABASE {nombre} ENCODING 'UTF8' TEMPLATE template0;  "
                f"con un usuario con permisos y vuelve a ejecutar la app."
            ) from error

    def initialize(self) -> None:
        """Garantiza la base de datos y construye todo el esquema relacional."""
        self.ensure_database()
        self.connect()
        cursor = self.connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(50) UNIQUE NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name VARCHAR(200) NOT NULL,
                salario_mensual NUMERIC(12,2) NOT NULL DEFAULT 0,
                role_id INTEGER NOT NULL REFERENCES roles (id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "salario_mensual NUMERIC(12,2) NOT NULL DEFAULT 0"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS marcajes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                hora_entrada TIMESTAMPTZ NOT NULL,
                hora_salida TIMESTAMPTZ,
                es_feriado BOOLEAN NOT NULL DEFAULT FALSE,
                es_tardanza BOOLEAN NOT NULL DEFAULT FALSE,
                horas_ordinarias INTERVAL NOT NULL DEFAULT '0 seconds',
                horas_extra_50 INTERVAL NOT NULL DEFAULT '0 seconds',
                horas_extra_100 INTERVAL NOT NULL DEFAULT '0 seconds',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS logs_auditoria (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES users (id),
                accion VARCHAR(50) NOT NULL,
                tabla VARCHAR(50) NOT NULL,
                registro_id INTEGER NOT NULL,
                valores_anteriores JSONB,
                valores_nuevos JSONB,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_marcajes_user_fecha
            ON marcajes (user_id, hora_entrada)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auditoria_usuario
            ON logs_auditoria (usuario_id, creado_en)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS justificaciones (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                tipo_permiso VARCHAR(50) NOT NULL
                    CHECK (tipo_permiso IN ('Vacaciones', 'Reposo', 'Permiso')),
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                aprobado_por INTEGER NOT NULL REFERENCES users (id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_justificaciones_usuario_fechas
            ON justificaciones (usuario_id, fecha_inicio, fecha_fin)
            """
        )
        for nombre in ROLES_INICIALES:
            cursor.execute(
                "INSERT INTO roles (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre,),
            )
        self.connection.commit()

    def _execute(
        self, query: str, params: Optional[Tuple[Any, ...]] = None, fetch: str = "none"
    ) -> Any:
        """Ejecuta una consulta con cursor de diccionario y opción de fetch."""
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params or ())
        if fetch == "one":
            return cursor.fetchone()
        if fetch == "all":
            return cursor.fetchall()
        return cursor

    def registrar_auditoria(
        self,
        usuario_id: int,
        accion: str,
        tabla: str,
        registro_id: int,
        anterior: Optional[Dict[str, Any]] = None,
        nuevos: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persiste un evento de auditoría con los valores previo y posterior.

        ``valores_anteriores`` y ``valores_nuevos`` se almacenan como JSONB
        para permitir consultas flexibles de trazabilidad.
        """
        cursor = self._execute(
            """
            INSERT INTO logs_auditoria
                (usuario_id, accion, tabla, registro_id, valores_anteriores, valores_nuevos)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usuario_id,
                accion,
                tabla,
                registro_id,
                Json(anterior) if anterior is not None else None,
                Json(nuevos) if nuevos is not None else None,
            ),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def create_user(
        self,
        username: str,
        password_hash: str,
        full_name: str,
        role_id: int,
        salario_mensual: float = 0.0,
    ) -> int:
        """Inserta un usuario y retorna su identificador."""
        cursor = self._execute(
            """
            INSERT INTO users (username, password_hash, full_name, role_id, salario_mensual)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (username, password_hash, full_name, role_id, salario_mensual),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Busca un usuario por nombre de acceso, incluyendo su rol."""
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.username = %s
            """,
            (username,),
            fetch="one",
        )

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Busca un usuario por identificador, incluyendo su rol."""
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.id = %s
            """,
            (user_id,),
            fetch="one",
        )

    def list_users(self) -> List[Dict[str, Any]]:
        """Lista todos los usuarios con su rol y salario mensual."""
        return self._execute(
            """
            SELECT u.id, u.username, u.full_name, u.salario_mensual,
                   r.nombre AS role_name, u.created_at
            FROM users u JOIN roles r ON r.id = u.role_id
            ORDER BY u.id
            """,
            fetch="all",
        )

    def update_user(
        self,
        user_id: int,
        full_name: Optional[str] = None,
        password_hash: Optional[str] = None,
        role_id: Optional[int] = None,
        salario_mensual: Optional[float] = None,
    ) -> bool:
        """Actualiza los campos provistos de un usuario y retorna si hubo cambios."""
        updates: List[str] = []
        params: List[Any] = []
        if full_name is not None:
            updates.append("full_name = %s")
            params.append(full_name)
        if password_hash is not None:
            updates.append("password_hash = %s")
            params.append(password_hash)
        if role_id is not None:
            updates.append("role_id = %s")
            params.append(role_id)
        if salario_mensual is not None:
            updates.append("salario_mensual = %s")
            params.append(salario_mensual)
        if not updates:
            return False
        params.append(user_id)
        self._execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params))
        self.connection.commit()
        return True

    def delete_user(self, user_id: int) -> None:
        """Elimina un usuario; sus marcajes se borran en cascada."""
        self._execute("DELETE FROM users WHERE id = %s", (user_id,))
        self.connection.commit()

    def get_role_by_name(self, nombre: str) -> Optional[Dict[str, Any]]:
        """Busca un rol por su nombre canónico."""
        return self._execute(
            "SELECT * FROM roles WHERE nombre = %s", (nombre,), fetch="one"
        )

    def list_roles(self) -> List[Dict[str, Any]]:
        """Lista todos los roles registrados."""
        return self._execute("SELECT * FROM roles ORDER BY id", fetch="all")

    def open_clock_in(self, user_id: int, hora_entrada: datetime, es_tardanza: bool) -> int:
        """Abre un marcaje de entrada con su estado de tardanza."""
        cursor = self._execute(
            """
            INSERT INTO marcajes (user_id, hora_entrada, es_tardanza)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (user_id, hora_entrada, es_tardanza),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def close_clock_out(
        self,
        entry_id: int,
        hora_salida: datetime,
        es_feriado: bool,
        horas_ordinarias: Any,
        horas_extra_50: Any,
        horas_extra_100: Any,
    ) -> None:
        """Cierra un marcaje persistendo el desglose horario legal."""
        self._execute(
            """
            UPDATE marcajes
            SET hora_salida = %s,
                es_feriado = %s,
                horas_ordinarias = %s,
                horas_extra_50 = %s,
                horas_extra_100 = %s
            WHERE id = %s
            """,
            (
                hora_salida,
                es_feriado,
                horas_ordinarias,
                horas_extra_50,
                horas_extra_100,
                entry_id,
            ),
        )
        self.connection.commit()

    def get_open_entry(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retorna el marcaje abierto más reciente del usuario, si existe."""
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s AND hora_salida IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
            fetch="one",
        )

    def get_entries_by_date(self, user_id: int, date) -> List[Dict[str, Any]]:
        """Lista los marcajes de un usuario para una fecha concreta."""
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s AND hora_entrada::date = %s
            ORDER BY hora_entrada
            """,
            (user_id, date),
            fetch="all",
        )

    def get_all_entries(self, user_id: int) -> List[Dict[str, Any]]:
        """Lista todos los marcajes de un usuario, del más reciente al más antiguo."""
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s
            ORDER BY hora_entrada DESC
            """,
            (user_id,),
            fetch="all",
        )

    def get_marcajes_month(self, anio: int, mes: int) -> List[Dict[str, Any]]:
        """Lista los marcajes de todos los empleados dentro de un mes calendario."""
        inicio = datetime(anio, mes, 1)
        if mes == 12:
            fin = datetime(anio + 1, 1, 1)
        else:
            fin = datetime(anio, mes + 1, 1)
        return self._execute(
            """
            SELECT m.*, u.username, u.full_name
            FROM marcajes m JOIN users u ON u.id = m.user_id
            WHERE m.hora_entrada >= %s AND m.hora_entrada < %s
            ORDER BY u.username, m.hora_entrada
            """,
            (inicio, fin),
            fetch="all",
        )

    def crear_justificacion(
        self,
        usuario_id: int,
        tipo_permiso: str,
        fecha_inicio: Any,
        fecha_fin: Any,
        aprobado_por: int,
    ) -> int:
        """Registra una justificación aprobada por RRHH/Administrador."""
        cursor = self._execute(
            """
            INSERT INTO justificaciones
                (usuario_id, tipo_permiso, fecha_inicio, fecha_fin, aprobado_por)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, tipo_permiso, fecha_inicio, fecha_fin, aprobado_por),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_justificacion_por_fecha(
        self, usuario_id: int, fecha: Any
    ) -> Optional[Dict[str, Any]]:
        """Retorna la justificación aprobada que cubre una fecha, si existe."""
        return self._execute(
            """
            SELECT * FROM justificaciones
            WHERE usuario_id = %s
              AND aprobado_por IS NOT NULL
              AND %s BETWEEN fecha_inicio AND fecha_fin
            ORDER BY id DESC
            LIMIT 1
            """,
            (usuario_id, fecha),
            fetch="one",
        )

    def list_justificaciones(self) -> List[Dict[str, Any]]:
        """Lista las justificaciones con datos del empleado y del aprobador."""
        return self._execute(
            """
            SELECT j.*, u.username, u.full_name, a.username AS aprobador
            FROM justificaciones j
            JOIN users u ON u.id = j.usuario_id
            JOIN users a ON a.id = j.aprobado_por
            ORDER BY j.fecha_inicio
            """,
            fetch="all",
        )

    def get_horas_extra_year(self, anio: int) -> List[Dict[str, Any]]:
        """Acumula por empleado las horas extra del año (suma de INTERVAL)."""
        inicio = datetime(anio, 1, 1)
        fin = datetime(anio + 1, 1, 1)
        return self._execute(
            """
            SELECT user_id,
                   COALESCE(SUM(horas_extra_50), INTERVAL '0 seconds') AS extra_50,
                   COALESCE(SUM(horas_extra_100), INTERVAL '0 seconds') AS extra_100
            FROM marcajes
            WHERE hora_entrada >= %s AND hora_entrada < %s
            GROUP BY user_id
            """,
            (inicio, fin),
            fetch="all",
        )