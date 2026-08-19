import os
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

DEFAULT_CONFIG = {
    "dbname": "marcacion",
    "user": "postgres",
    "password": "",
    "host": "localhost",
    "port": "5432",
}

ROLES_INICIALES = ("Administrador", "Recursos Humanos", "Empleado")


def load_dotenv(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_config():
    load_dotenv()
    return {
        "dbname": os.getenv("DB_NAME", DEFAULT_CONFIG["dbname"]),
        "user": os.getenv("DB_USER", DEFAULT_CONFIG["user"]),
        "password": os.getenv("DB_PASSWORD", DEFAULT_CONFIG["password"]),
        "host": os.getenv("DB_HOST", DEFAULT_CONFIG["host"]),
        "port": os.getenv("DB_PORT", DEFAULT_CONFIG["port"]),
    }


class Database:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.connection = None

    def connect(self):
        self.connection = psycopg2.connect(**self.config)
        self.connection.autocommit = False
        return self.connection

    def initialize(self):
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
                role_id INTEGER NOT NULL REFERENCES roles (id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS marcajes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                hora_entrada TIMESTAMPTZ NOT NULL,
                hora_salida TIMESTAMPTZ,
                es_feriado BOOLEAN NOT NULL DEFAULT FALSE,
                horas_ordinarias INTERVAL NOT NULL DEFAULT '0 seconds',
                horas_extra_50 INTERVAL NOT NULL DEFAULT '0 seconds',
                horas_extra_100 INTERVAL NOT NULL DEFAULT '0 seconds',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_marcajes_user_fecha
            ON marcajes (user_id, hora_entrada)
            """
        )
        for nombre in ROLES_INICIALES:
            cursor.execute(
                "INSERT INTO roles (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre,),
            )
        self.connection.commit()

    def _execute(self, query, params=None, fetch="none"):
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params or ())
        if fetch == "one":
            return cursor.fetchone()
        if fetch == "all":
            return cursor.fetchall()
        return cursor

    def create_user(self, username, password_hash, full_name, role_id):
        cursor = self._execute(
            """
            INSERT INTO users (username, password_hash, full_name, role_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (username, password_hash, full_name, role_id),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_user_by_username(self, username):
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.username = %s
            """,
            (username,),
            fetch="one",
        )

    def get_user_by_id(self, user_id):
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.id = %s
            """,
            (user_id,),
            fetch="one",
        )

    def list_users(self):
        return self._execute(
            """
            SELECT u.id, u.username, u.full_name, r.nombre AS role_name, u.created_at
            FROM users u JOIN roles r ON r.id = u.role_id
            ORDER BY u.id
            """,
            fetch="all",
        )

    def update_user(self, user_id, full_name=None, password_hash=None, role_id=None):
        updates = []
        params = []
        if full_name is not None:
            updates.append("full_name = %s")
            params.append(full_name)
        if password_hash is not None:
            updates.append("password_hash = %s")
            params.append(password_hash)
        if role_id is not None:
            updates.append("role_id = %s")
            params.append(role_id)
        if not updates:
            return False
        params.append(user_id)
        self._execute(
            f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params)
        )
        self.connection.commit()
        return True

    def delete_user(self, user_id):
        self._execute("DELETE FROM users WHERE id = %s", (user_id,))
        self.connection.commit()

    def get_role_by_name(self, nombre):
        return self._execute(
            "SELECT * FROM roles WHERE nombre = %s", (nombre,), fetch="one"
        )

    def list_roles(self):
        return self._execute("SELECT * FROM roles ORDER BY id", fetch="all")

    def open_clock_in(self, user_id, hora_entrada):
        cursor = self._execute(
            """
            INSERT INTO marcajes (user_id, hora_entrada)
            VALUES (%s, %s)
            RETURNING id
            """,
            (user_id, hora_entrada),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def close_clock_out(
        self,
        entry_id,
        hora_salida,
        es_feriado,
        horas_ordinarias,
        horas_extra_50,
        horas_extra_100,
    ):
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

    def get_open_entry(self, user_id):
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

    def get_entries_by_date(self, user_id, date):
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s AND hora_entrada::date = %s
            ORDER BY hora_entrada
            """,
            (user_id, date),
            fetch="all",
        )

    def get_all_entries(self, user_id):
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s
            ORDER BY hora_entrada DESC
            """,
            (user_id,),
            fetch="all",
        )

    def get_marcajes_month(self, anio, mes):
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