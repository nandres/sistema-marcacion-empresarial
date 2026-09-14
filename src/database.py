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
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2.extras import Json, RealDictCursor

import biometria
import reglamento

# Lista SQL de tipos de permiso válidos (catálogo reglamentario + histórico)
_TIPOS_SQL: str = ", ".join("'%s'" % t for t in reglamento.TIPOS_PERMISO_CHECK)

DEFAULT_CONFIG: Dict[str, str] = {
    "dbname": "marcacion",
    "user": "postgres",
    "password": "",
    "host": "localhost",
    "port": "5432",
}

ROLES_INICIALES: Tuple[str, ...] = ("Administrador", "Recursos Humanos", "Empleado")

LOCK_TIMEOUT_DDL: str = "10s"
"""Espera máxima de las migraciones por un lock de tabla antes de abortar."""


def load_dotenv(path: str = ".env") -> None:
    """Carga las variables ``KEY=VALUE`` del archivo indicado al entorno.

    Respeta las variables ya definidas en el entorno real (no las pisa)
    y descarta comentarios y líneas vacías. Si la ruta relativa no existe
    en el directorio actual, cae a la raíz del proyecto: el archivo vive
    junto a ``src/`` y el directorio de trabajo cambia según cómo se
    invoque la aplicación (``python src/app.py`` o el ``WORKDIR`` del
    contenedor).
    """
    env_path = Path(path)
    if not env_path.is_absolute() and not env_path.exists():
        env_path = Path(__file__).resolve().parent.parent / path
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _config_por_url(url: str) -> Dict[str, str]:
    """Descompone una ``DATABASE_URL`` estándar en parámetros de conexión.

    Acepta los esquemas ``postgresql://`` y ``postgres://`` usados por
    plataformas como Render o Railway y decodifica los valores escapados
    (por ejemplo ``%40`` en contraseñas).
    """
    parsed = urlparse(url)
    return {
        "dbname": unquote(parsed.path.lstrip("/")) or DEFAULT_CONFIG["dbname"],
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or DEFAULT_CONFIG["host"],
        "port": str(parsed.port or DEFAULT_CONFIG["port"]),
    }


def load_config() -> Dict[str, str]:
    """Construye el diccionario de conexión a partir del entorno y del ``.env``.

    Si existe ``DATABASE_URL`` (estándar de los servidores cloud) tiene
    prioridad absoluta; de lo contrario cae a las variables por separado
    (``DB_HOST``, ``DB_NAME``, etc.) para el desarrollo local.
    """
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if url:
        return _config_por_url(url)
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

    def cerrar(self) -> None:
        """Cierra la conexión activa liberando el socket de PostgreSQL."""
        if self.connection is not None:
            self.connection.close()
            self.connection = None

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
        # Una petición de lock exclusivo en espera bloquea también a los
        # lectores que llegan detrás. Si otra sesión mantiene una transacción
        # abierta, conviene abortar la migración antes que encolar la base.
        cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT_DDL}'")
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
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS biometrico_id INTEGER"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_biometrico "
            "ON users (biometrico_id) WHERE biometrico_id IS NOT NULL"
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "departamento VARCHAR(80) NOT NULL DEFAULT 'General'"
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "email VARCHAR(200) NOT NULL DEFAULT ''"
        )
        cursor.execute(
            """
            UPDATE users u
            SET departamento = CASE
                WHEN r.nombre IN ('Administrador', 'Recursos Humanos')
                    THEN 'Dirección y Administración'
                ELSE 'Operaciones'
            END
            FROM roles r
            WHERE r.id = u.role_id AND u.departamento = 'General'
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_departamento "
            "ON users (departamento)"
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "tipo_vinculo VARCHAR(20) NOT NULL DEFAULT 'Funcionario'"
        )
        # La antigüedad define los días de vacaciones (12/20/30) y los meses de
        # aguinaldo. Calcularla desde `created_at` significaba que al migrar la
        # plantilla todo el mundo pasaba a tener cero años de servicio.
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS fecha_ingreso DATE")
        cursor.execute(
            "UPDATE users SET fecha_ingreso = created_at::date "
            "WHERE fecha_ingreso IS NULL"
        )
        # Baja lógica: un empleado que se va deja de operar pero sus marcajes
        # tienen que sobrevivir para el archivo laboral.
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "activo BOOLEAN NOT NULL DEFAULT TRUE"
        )
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS fecha_baja DATE")
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
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "tolerancia_aplicada BOOLEAN NOT NULL DEFAULT FALSE"
        )
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "condicion_climatica VARCHAR(30) NOT NULL DEFAULT ''"
        )
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "tipo_incidencia VARCHAR(50) NOT NULL DEFAULT ''"
        )
        # Art. 232: las horas ordinarias nocturnas llevan un recargo del 30 %,
        # así que se liquidan aparte de las ordinarias diurnas.
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "horas_nocturnas INTERVAL NOT NULL DEFAULT '0 seconds'"
        )
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "tipo_jornada VARCHAR(20) NOT NULL DEFAULT ''"
        )
        # El resultado de la verificación biométrica se guarda en la marca en
        # lugar de descartarse: una marca que el motor no pudo verificar no es
        # lo mismo que una verificada, y RRHH necesita distinguirlas.
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "verificacion_facial VARCHAR(20) NOT NULL DEFAULT 'No verificada'"
        )
        # Una entrada que nadie cerró no se puede inventar, pero tampoco puede
        # dejar al empleado sin marcar el resto de su vida laboral: se marca
        # como abandonada, sale del camino y queda para que RRHH la corrija.
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS "
            "abandonado BOOLEAN NOT NULL DEFAULT FALSE"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_marcajes_abiertos "
            "ON marcajes (user_id, hora_entrada DESC) "
            "WHERE hora_salida IS NULL AND NOT abandonado"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_marcajes_analitica "
            "ON marcajes (es_tardanza, hora_entrada)"
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
                    CHECK (tipo_permiso IN (""" + _TIPOS_SQL + """)),
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                aprobado_por INTEGER NOT NULL REFERENCES users (id),
                horas_usadas NUMERIC(4, 1) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS sync_id VARCHAR(64)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_marcajes_sync_id "
            "ON marcajes (sync_id) WHERE sync_id IS NOT NULL"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alertas (
                id SERIAL PRIMARY KEY,
                tipo VARCHAR(50) NOT NULL,
                severidad VARCHAR(20) NOT NULL DEFAULT 'media',
                mensaje TEXT NOT NULL,
                detalle TEXT NOT NULL DEFAULT '',
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                leida BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alertas_leida
            ON alertas (leida, creado_en)
            """
        )
        cursor.execute(
            "ALTER TABLE alertas ADD COLUMN IF NOT EXISTS "
            "usuario_id INTEGER REFERENCES users (id) ON DELETE SET NULL"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fotos (
                user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
                imagen BYTEA NOT NULL,
                actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            ALTER TABLE justificaciones DROP CONSTRAINT IF EXISTS
            justificaciones_tipo_permiso_check
            """
        )
        cursor.execute(
            """
            ALTER TABLE justificaciones ADD CONSTRAINT
            justificaciones_tipo_permiso_check
            CHECK (tipo_permiso IN (""" + _TIPOS_SQL + """))
            """
        )
        cursor.execute(
            """
            ALTER TABLE justificaciones ADD COLUMN IF NOT EXISTS
            horas_usadas NUMERIC(4, 1) NOT NULL DEFAULT 0
            """
        )
        cursor.execute(
            """
            ALTER TABLE justificaciones ADD COLUMN IF NOT EXISTS
            hash_legal VARCHAR(64) NOT NULL DEFAULT ''
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_justificaciones_usuario_fechas
            ON justificaciones (usuario_id, fecha_inicio, fecha_fin)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS solicitudes_correccion (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                fecha_registro DATE NOT NULL,
                tipo_marca VARCHAR(20) NOT NULL
                    CHECK (tipo_marca IN ('Entrada', 'Salida')),
                hora_propuesta TIME NOT NULL,
                motivo TEXT NOT NULL,
                estado VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
                    CHECK (estado IN ('Pendiente', 'Aprobado', 'Rechazado')),
                revisado_por INTEGER REFERENCES users (id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_solicitudes_estado
            ON solicitudes_correccion (estado, fecha_registro)
            """
        )
        # La condición climática del día la declara Recursos Humanos para toda
        # la plantilla. Antes la marcaba el propio empleado en el kiosco, que
        # es justamente quien se beneficia de la tolerancia que activa.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS condiciones_dia (
                fecha DATE PRIMARY KEY,
                condicion VARCHAR(40) NOT NULL,
                tolerancia_min INTEGER NOT NULL DEFAULT 0,
                nota TEXT NOT NULL DEFAULT '',
                declarado_por INTEGER NOT NULL REFERENCES users (id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Solicitudes que el empleado presenta desde el portal. Se validan
        # contra el catálogo reglamentario antes de guardarse, de modo que a
        # RRHH solo le llegan pedidos que ya cumplen el artículo invocado.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS solicitudes_permiso (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                tipo_permiso VARCHAR(50) NOT NULL
                    CHECK (tipo_permiso IN (""" + _TIPOS_SQL + """)),
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                horas_solicitadas NUMERIC(4, 1) NOT NULL DEFAULT 0,
                motivo TEXT NOT NULL,
                estado VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
                    CHECK (estado IN ('Pendiente', 'Aprobado', 'Rechazado')),
                observacion TEXT NOT NULL DEFAULT '',
                resuelto_por INTEGER REFERENCES users (id),
                resuelto_en TIMESTAMPTZ,
                justificacion_id INTEGER REFERENCES justificaciones (id)
                    ON DELETE SET NULL,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_solicitudes_permiso_bandeja
            ON solicitudes_permiso (estado, fecha_inicio, id)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_solicitudes_permiso_usuario
            ON solicitudes_permiso (usuario_id, creado_en DESC)
            """
        )
        for nombre in ROLES_INICIALES:
            cursor.execute(
                "INSERT INTO roles (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre,),
            )
        self.connection.commit()

    def esquema_listo(self) -> bool:
        """Indica si las tablas base ya existen sobre la conexión activa.

        Permite que los procesos de larga vida (servidor web) verifiquen el
        estado del esquema al arrancar en lugar de aplicar el DDL completo,
        que toma locks exclusivos y no puede correr dentro de una petición.
        """
        fila = self._execute(
            """
            SELECT COUNT(*) AS presentes
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('roles', 'users', 'marcajes', 'logs_auditoria',
                                 'justificaciones', 'solicitudes_correccion', 'alertas',
                                 'condiciones_dia', 'solicitudes_permiso')
            """,
            fetch="one",
        )
        return int(fila["presentes"]) == 9

    def _execute(
        self, query: str, params: Optional[Tuple[Any, ...]] = None, fetch: str = "none"
    ) -> Any:
        """Ejecuta una consulta con cursor de diccionario y opción de fetch.

        Una consulta que falla aborta la transacción en PostgreSQL: todo lo
        que venga después sobre la misma conexión revienta con
        ``InFailedSqlTransaction`` aunque sea válido. Deshacer acá deja la
        conexión utilizable, de modo que un error puntual no arrastre al
        resto de la petición ni al proceso de larga vida que la reutiliza.
        """
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(query, params or ())
        except psycopg2.Error:
            try:
                self.connection.rollback()
            except psycopg2.Error:
                pass
            raise
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

    def listar_auditoria(self, limite: int = 60) -> List[Dict[str, Any]]:
        """Devuelve los eventos más recientes del registro de auditoría.

        Incluye el nombre del actor y los snapshots JSONB anterior/nuevo
        para la revisión de trazabilidad de Recursos Humanos.
        """
        return self._execute(
            """
            SELECT a.id, a.accion, a.tabla, a.registro_id,
                   a.valores_anteriores, a.valores_nuevos, a.creado_en,
                   u.username, u.full_name
            FROM logs_auditoria a
            JOIN users u ON u.id = a.usuario_id
            ORDER BY a.creado_en DESC
            LIMIT %s
            """,
            (limite,),
            fetch="all",
        )

    def actualizar_hash_justificacion(
        self, justificacion_id: int, hash_legal: str
    ) -> None:
        """Persiste el hash SHA-256 del permiso para su validación legal."""
        self._execute(
            "UPDATE justificaciones SET hash_legal = %s WHERE id = %s",
            (hash_legal, justificacion_id),
        )
        self.connection.commit()

    def create_user(
        self,
        username: str,
        password_hash: str,
        full_name: str,
        role_id: int,
        salario_mensual: float = 0.0,
        tipo_vinculo: str = "Funcionario",
        fecha_ingreso: Optional[Any] = None,
    ) -> int:
        """Inserta un usuario y retorna su identificador.

        ``fecha_ingreso`` es la del contrato, no la del alta en el sistema:
        de ella dependen los días de vacaciones y los meses de aguinaldo. Si
        no se indica, se asume que el empleado ingresa hoy.
        """
        cursor = self._execute(
            """
            INSERT INTO users (username, password_hash, full_name, role_id,
                               salario_mensual, tipo_vinculo, fecha_ingreso)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE))
            RETURNING id
            """,
            (username, password_hash, full_name, role_id, salario_mensual,
             tipo_vinculo, fecha_ingreso),
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

    def list_users(self, incluir_bajas: bool = False) -> List[Dict[str, Any]]:
        """Lista el personal con su rol, salario, vínculo y fecha de ingreso.

        Por defecto devuelve solo la plantilla activa: una baja conserva sus
        marcajes para el archivo laboral pero no forma parte de la nómina.
        """
        filtro = "" if incluir_bajas else "WHERE u.activo"
        return self._execute(
            f"""
            SELECT u.id, u.username, u.full_name, u.salario_mensual,
                   u.tipo_vinculo, r.nombre AS role_name, u.created_at,
                   u.fecha_ingreso, u.activo, u.fecha_baja
            FROM users u JOIN roles r ON r.id = u.role_id
            {filtro}
            ORDER BY u.id
            """,
            fetch="all",
        )

    def cambiar_estado_usuario(
        self, user_id: int, activo: bool, fecha_baja: Optional[Any] = None
    ) -> bool:
        """Da de baja o reincorpora a un empleado sin tocar sus marcajes."""
        cursor = self._execute(
            """
            UPDATE users
            SET activo = %s, fecha_baja = %s
            WHERE id = %s
            """,
            (activo, fecha_baja if not activo else None, user_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def update_user(
        self,
        user_id: int,
        full_name: Optional[str] = None,
        password_hash: Optional[str] = None,
        role_id: Optional[int] = None,
        salario_mensual: Optional[float] = None,
        tipo_vinculo: Optional[str] = None,
        fecha_ingreso: Optional[Any] = None,
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
        if tipo_vinculo is not None:
            updates.append("tipo_vinculo = %s")
            params.append(tipo_vinculo)
        if fecha_ingreso is not None:
            updates.append("fecha_ingreso = %s")
            params.append(fecha_ingreso)
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

    def asignar_biometrico_id(self, user_id: int, biometrico_id: int) -> None:
        """Asocia el identificador biométrico del reloj al usuario."""
        self._execute(
            "UPDATE users SET biometrico_id = %s WHERE id = %s",
            (biometrico_id, user_id),
        )
        self.connection.commit()

    def get_role_by_name(self, nombre: str) -> Optional[Dict[str, Any]]:
        """Busca un rol por su nombre canónico."""
        return self._execute(
            "SELECT * FROM roles WHERE nombre = %s", (nombre,), fetch="one"
        )

    def list_roles(self) -> List[Dict[str, Any]]:
        """Lista todos los roles registrados."""
        return self._execute("SELECT * FROM roles ORDER BY id", fetch="all")

    def open_clock_in(
        self,
        user_id: int,
        hora_entrada: datetime,
        es_tardanza: bool,
        tipo_incidencia: str = "",
        tolerancia_aplicada: bool = False,
        condicion_climatica: str = "",
        sync_id: Optional[str] = None,
        verificacion_facial: str = "No verificada",
    ) -> Optional[int]:
        """Abre un marcaje de entrada con su estado, incidencia y contexto.

        ``tolerancia_aplicada`` indica si se consumió la gracia ordinaria o
        climática de la Res. 3028/2024; ``condicion_climatica`` documenta la
        condición que Recursos Humanos declaró para ese día, y
        ``verificacion_facial`` el resultado del control biométrico
        (``Verificada``, ``No verificada`` u ``Omitida``).

        Si se provee ``sync_id`` (marcación offline) el inserto es
        idempotente: ante un duplicado retorna ``None`` en lugar de crear
        una segunda fila.
        """
        if sync_id:
            cursor = self._execute(
                """
                INSERT INTO marcajes (user_id, hora_entrada, es_tardanza,
                                      tipo_incidencia, tolerancia_aplicada,
                                      condicion_climatica, verificacion_facial,
                                      sync_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sync_id) WHERE sync_id IS NOT NULL DO NOTHING
                RETURNING id
                """,
                (user_id, hora_entrada, es_tardanza, tipo_incidencia,
                 tolerancia_aplicada, condicion_climatica, verificacion_facial,
                 sync_id),
            )
        else:
            cursor = self._execute(
                """
                INSERT INTO marcajes (user_id, hora_entrada, es_tardanza,
                                      tipo_incidencia, tolerancia_aplicada,
                                      condicion_climatica, verificacion_facial)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, hora_entrada, es_tardanza, tipo_incidencia,
                 tolerancia_aplicada, condicion_climatica, verificacion_facial),
            )
        self.connection.commit()
        fila = cursor.fetchone()
        return fila["id"] if fila else None

    def contar_tardanzas_mes(self, user_id: int, fecha: datetime.date) -> int:
        """Cuenta las llegadas tardías del usuario dentro del mes indicado."""
        primer_dia = fecha.replace(day=1)
        siguiente_mes = (primer_dia.replace(day=28) + timedelta(days=4)).replace(day=1)
        fila = self._execute(
            """
            SELECT COUNT(*) AS total
            FROM marcajes
            WHERE user_id = %s AND es_tardanza = TRUE
              AND hora_entrada >= %s AND hora_entrada < %s
            """,
            (user_id, datetime.combine(primer_dia, time.min),
             datetime.combine(siguiente_mes, time.min)),
            fetch="one",
        )
        return int(fila["total"])

    def close_clock_out(
        self,
        entry_id: int,
        hora_salida: datetime,
        es_feriado: bool,
        horas_ordinarias: Any,
        horas_extra_50: Any,
        horas_extra_100: Any,
        tipo_incidencia: str = "",
        horas_nocturnas: Any = timedelta(0),
        tipo_jornada: str = "",
    ) -> None:
        """Cierra un marcaje persistiendo el desglose horario y la incidencia.

        ``horas_nocturnas`` es el subconjunto de las ordinarias trabajado en
        horario nocturno, que la nómina liquida con el recargo del 30 %.
        """
        self._execute(
            """
            UPDATE marcajes
            SET hora_salida = %s,
                es_feriado = %s,
                horas_ordinarias = %s,
                horas_extra_50 = %s,
                horas_extra_100 = %s,
                tipo_incidencia = %s,
                horas_nocturnas = %s,
                tipo_jornada = %s
            WHERE id = %s
            """,
            (
                hora_salida,
                es_feriado,
                horas_ordinarias,
                horas_extra_50,
                horas_extra_100,
                tipo_incidencia,
                horas_nocturnas,
                tipo_jornada,
                entry_id,
            ),
        )
        self.connection.commit()

    def get_open_entry(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retorna el marcaje abierto más reciente del usuario, si existe.

        Los abandonados no cuentan como abiertos: ya salieron del circuito de
        marcación y esperan la corrección de Recursos Humanos.
        """
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s AND hora_salida IS NULL AND NOT abandonado
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
            fetch="one",
        )

    def abandonar_jornadas_vencidas(
        self, user_id: int, limite: datetime, incidencia: str
    ) -> List[Dict[str, Any]]:
        """Saca del circuito **todas** las entradas sin cierre anteriores al límite.

        No inventa la hora de salida —nadie la registró— pero deja constancia
        de la incidencia para que aparezcan en la bandeja de correcciones.

        Las descarta todas de una vez y no de a una: un empleado que estuvo
        de licencia puede acumular varias, y liberar solo la última lo dejaba
        igual de trabado en la siguiente marcación.
        """
        filas = self._execute(
            """
            UPDATE marcajes
            SET abandonado = TRUE, tipo_incidencia = %s
            WHERE user_id = %s
              AND hora_salida IS NULL
              AND NOT abandonado
              AND hora_entrada < %s
            RETURNING id, hora_entrada
            """,
            (incidencia, user_id, limite),
            fetch="all",
        )
        self.connection.commit()
        return filas

    def listar_marcajes_abandonados(self, limite: int = 50) -> List[Dict[str, Any]]:
        """Entradas sin cierre que esperan corrección, con su empleado."""
        return self._execute(
            """
            SELECT m.id, m.user_id, m.hora_entrada, m.tipo_incidencia,
                   u.full_name, u.username
            FROM marcajes m
            JOIN users u ON u.id = m.user_id
            WHERE m.abandonado AND m.hora_salida IS NULL
            ORDER BY m.hora_entrada DESC
            LIMIT %s
            """,
            (limite,),
            fetch="all",
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
        horas_usadas: float = 0.0,
    ) -> int:
        """Registra una justificación aprobada por RRHH/Administrador.

        ``horas_usadas`` solo es significativo para permisos medidos en
        horas (p. ej. salidas personales del Art. 18); para el resto de
        los artículos queda en cero.
        """
        cursor = self._execute(
            """
            INSERT INTO justificaciones
                (usuario_id, tipo_permiso, fecha_inicio, fecha_fin,
                 aprobado_por, horas_usadas)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usuario_id,
                tipo_permiso,
                fecha_inicio,
                fecha_fin,
                aprobado_por,
                horas_usadas,
            ),
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

    def list_justificaciones(
        self, usuario_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Lista las justificaciones con datos del empleado y del aprobador.

        Filtrar por empleado en la base y no en Python importa: el tablero
        personal calcula la disponibilidad de cada artículo en cada carga, y
        sin el filtro traía el historial completo de toda la plantilla para
        descartar el 99 % en memoria.
        """
        filtro = "WHERE j.usuario_id = %s" if usuario_id is not None else ""
        return self._execute(
            f"""
            SELECT j.*, u.username, u.full_name, a.username AS aprobador
            FROM justificaciones j
            JOIN users u ON u.id = j.usuario_id
            JOIN users a ON a.id = j.aprobado_por
            {filtro}
            ORDER BY j.fecha_inicio
            """,
            (usuario_id,) if usuario_id is not None else None,
            fetch="all",
        )

    def get_justificacion(self, justificacion_id: int) -> Optional[Dict[str, Any]]:
        """Recupera una justificación puntual sin recorrer toda la tabla."""
        return self._execute(
            """
            SELECT j.*, u.username, u.full_name, u.tipo_vinculo,
                   u.departamento, a.username AS aprobador
            FROM justificaciones j
            JOIN users u ON u.id = j.usuario_id
            JOIN users a ON a.id = j.aprobado_por
            WHERE j.id = %s
            """,
            (justificacion_id,),
            fetch="one",
        )

    def contar_justificaciones(self) -> int:
        """Total de justificaciones emitidas, sin traerlas todas."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM justificaciones", fetch="one"
        )
        return int(fila["total"])

    def crear_alerta(
        self,
        tipo: str,
        severidad: str,
        mensaje: str,
        detalle: str = "",
        usuario_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Persiste una notificación activa para Recursos Humanos."""
        cursor = self._execute(
            """
            INSERT INTO alertas (tipo, severidad, mensaje, detalle, usuario_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, creado_en
            """,
            (tipo, severidad, mensaje, detalle, usuario_id),
        )
        self.connection.commit()
        fila = cursor.fetchone()
        return {
            "id": fila["id"],
            "tipo": tipo,
            "severidad": severidad,
            "mensaje": mensaje,
            "detalle": detalle,
            "usuario_id": usuario_id,
            "creado_en": fila["creado_en"],
            "leida": False,
        }

    def listar_alertas(self, limite: int = 60, no_leidas: bool = False) -> List[Dict[str, Any]]:
        """Lista las alertas activas, de la más reciente a la más antigua."""
        consulta = (
            "SELECT * FROM alertas WHERE leida = FALSE ORDER BY creado_en DESC LIMIT %s"
            if no_leidas
            else "SELECT * FROM alertas ORDER BY creado_en DESC LIMIT %s"
        )
        return self._execute(consulta, (limite,), fetch="all")

    def list_solicitudes_correccion(self) -> List[Dict[str, Any]]:
        """Lista las solicitudes de corrección con datos del solicitante."""
        return self._execute(
            """
            SELECT s.*, u.username, u.full_name, r.username AS revisor
            FROM solicitudes_correccion s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.revisado_por
            ORDER BY s.id DESC
            """,
            fetch="all",
        )

    def contar_marcas_sin_verificar(self, dias: int = 7) -> int:
        """Marcas recientes que el control biométrico no pudo verificar.

        Es el indicador que reemplaza al silencio anterior: antes esas marcas
        se daban por buenas y no quedaba rastro de que nadie las comprobó.
        """
        fila = self._execute(
            """
            SELECT COUNT(*) AS total
            FROM marcajes
            WHERE verificacion_facial <> 'Verificada'
              AND hora_entrada >= NOW() - make_interval(days => %s)
            """,
            (dias,),
            fetch="one",
        )
        return int(fila["total"])

    def count_marcajes_hoy(self) -> int:
        """Cantidad de marcajes con entrada registrada en la fecha actual."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM marcajes WHERE hora_entrada::date = CURRENT_DATE",
            fetch="one",
        )
        return int(fila["total"]) if fila else 0

    def marcar_alertas_leidas(self) -> int:
        """Marca todas las alertas como leídas y devuelve la cantidad."""
        cursor = self._execute(
            "UPDATE alertas SET leida = TRUE WHERE leida = FALSE RETURNING id"
        )
        self.connection.commit()
        return len(cursor.fetchall())

    def limpiar_marcajes_prueba(self, user_id: int, desde: Any, hasta: Any) -> None:
        """Helper de tests: elimina marcajes de un rango de fechas."""
        self._execute(
            "DELETE FROM marcajes WHERE user_id = %s "
            "AND hora_entrada::date BETWEEN %s AND %s",
            (user_id, desde, hasta),
        )
        self.connection.commit()

    def guardar_foto(self, user_id: int, imagen_jpg: bytes) -> None:
        """Almacena (o reemplaza) la plantilla facial del usuario, cifrada.

        El dato biométrico es de categoría especial bajo la Ley 6534/2020 y
        nunca toca la base en claro: se sella con AES-256-GCM antes de salir
        del proceso. Ver ``src/biometria.py``.
        """
        self._execute(
            """
            INSERT INTO fotos (user_id, imagen) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET imagen = EXCLUDED.imagen,
                                                actualizado_en = NOW()
            """,
            (user_id, psycopg2.Binary(biometria.cifrar(imagen_jpg, user_id))),
        )
        self.connection.commit()

    def get_foto(self, user_id: int) -> Optional[bytes]:
        """Retorna los bytes JPEG de la foto del usuario, si existe."""
        fila = self._execute(
            "SELECT imagen FROM fotos WHERE user_id = %s", (user_id,), fetch="one"
        )
        if not fila or fila["imagen"] is None:
            return None
        return biometria.descifrar(bytes(fila["imagen"]), user_id)

    def tiene_foto(self, user_id: int) -> bool:
        """Indica si el usuario tiene una foto biométrica registrada."""
        fila = self._execute(
            "SELECT 1 AS existe FROM fotos WHERE user_id = %s", (user_id,), fetch="one"
        )
        return fila is not None

    def list_fotos(self, descifrar: bool = True) -> List[Dict[str, Any]]:
        """Lista las plantillas faciales para entrenar el modelo.

        ``descifrar=False`` devuelve el contenido tal como está guardado, que
        es lo que necesita la migración de fotos antiguas; una plantilla que
        no supera la verificación de integridad se descarta en lugar de
        entrenar el modelo con basura.
        """
        filas = self._execute(
            "SELECT user_id, imagen FROM fotos ORDER BY user_id", fetch="all"
        )
        if not descifrar:
            return filas
        abiertas: List[Dict[str, Any]] = []
        for fila in filas:
            imagen = biometria.descifrar(bytes(fila["imagen"]), fila["user_id"])
            if imagen is not None:
                abiertas.append({"user_id": fila["user_id"], "imagen": imagen})
        return abiertas

    def eliminar_foto(self, user_id: int) -> None:
        """Elimina la foto biométrica del usuario."""
        self._execute("DELETE FROM fotos WHERE user_id = %s", (user_id,))
        self.connection.commit()

    def get_horas_extra_year(self, anio: int) -> List[Dict[str, Any]]:
        """Acumula por empleado las horas con recargo del año (suma de INTERVAL).

        Incluye las horas ordinarias nocturnas, que llevan el recargo del
        30 % del Art. 232 y por lo tanto integran la remuneración anual.
        """
        inicio = datetime(anio, 1, 1)
        fin = datetime(anio + 1, 1, 1)
        return self._execute(
            """
            SELECT user_id,
                   COALESCE(SUM(horas_extra_50), INTERVAL '0 seconds') AS extra_50,
                   COALESCE(SUM(horas_extra_100), INTERVAL '0 seconds') AS extra_100,
                   COALESCE(SUM(horas_nocturnas), INTERVAL '0 seconds') AS nocturnas
            FROM marcajes
            WHERE hora_entrada >= %s AND hora_entrada < %s
            GROUP BY user_id
            """,
            (inicio, fin),
            fetch="all",
        )

    def get_marcajes_rango(self, user_id: int, desde: Any, hasta: Any) -> List[Dict[str, Any]]:
        """Lista los marcajes de un empleado dentro de un rango de fechas."""
        inicio = datetime.combine(desde, time.min)
        fin = datetime.combine(hasta, time.max)
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE user_id = %s AND hora_entrada BETWEEN %s AND %s
            ORDER BY hora_entrada
            """,
            (user_id, inicio, fin),
            fetch="all",
        )

    def crear_solicitud_correccion(
        self,
        usuario_id: int,
        fecha_registro: Any,
        tipo_marca: str,
        hora_propuesta: Any,
        motivo: str,
    ) -> int:
        """Registra un reclamo de marcación fallida en estado Pendiente."""
        cursor = self._execute(
            """
            INSERT INTO solicitudes_correccion
                (usuario_id, fecha_registro, tipo_marca, hora_propuesta, motivo)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, fecha_registro, tipo_marca, hora_propuesta, motivo),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_solicitud_correccion(self, solicitud_id: int) -> Optional[Dict[str, Any]]:
        """Retorna una solicitud de corrección con datos del solicitante."""
        return self._execute(
            """
            SELECT s.*, u.username, u.full_name, r.username AS revisor
            FROM solicitudes_correccion s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.revisado_por
            WHERE s.id = %s
            """,
            (solicitud_id,),
            fetch="one",
        )

    def listar_solicitudes_correccion(self) -> List[Dict[str, Any]]:
        """Lista los reclamos ordenados por antigüedad y estado."""
        return self._execute(
            """
            SELECT s.*, u.username, u.full_name, r.username AS revisor
            FROM solicitudes_correccion s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.revisado_por
            ORDER BY (s.estado = 'Pendiente') DESC, s.fecha_registro, s.id
            """,
            fetch="all",
        )

    def actualizar_estado_solicitud(
        self, solicitud_id: int, estado: str, revisado_por: int
    ) -> bool:
        """Marca una solicitud como Aprobada o Rechazada con su revisor."""
        cursor = self._execute(
            """
            UPDATE solicitudes_correccion
            SET estado = %s, revisado_por = %s
            WHERE id = %s
            """,
            (estado, revisado_por, solicitud_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Condición climática del día (declarada por Recursos Humanos)
    # ------------------------------------------------------------------

    def declarar_condicion_dia(
        self,
        fecha: Any,
        condicion: str,
        tolerancia_min: int,
        declarado_por: int,
        nota: str = "",
    ) -> Dict[str, Any]:
        """Fija la condición excepcional de un día para toda la plantilla.

        Es un upsert por fecha: redeclarar el mismo día reemplaza la
        condición anterior y deja registrado quién la firmó.
        """
        cursor = self._execute(
            """
            INSERT INTO condiciones_dia
                (fecha, condicion, tolerancia_min, nota, declarado_por)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fecha) DO UPDATE SET
                condicion = EXCLUDED.condicion,
                tolerancia_min = EXCLUDED.tolerancia_min,
                nota = EXCLUDED.nota,
                declarado_por = EXCLUDED.declarado_por,
                creado_en = NOW()
            RETURNING *
            """,
            (fecha, condicion, tolerancia_min, nota, declarado_por),
        )
        self.connection.commit()
        return cursor.fetchone()

    def get_condicion_dia(self, fecha: Any) -> Optional[Dict[str, Any]]:
        """Condición declarada para una fecha, o ``None`` si el día es normal."""
        return self._execute(
            "SELECT * FROM condiciones_dia WHERE fecha = %s",
            (fecha,),
            fetch="one",
        )

    def listar_condiciones_dia(self, limite: int = 30) -> List[Dict[str, Any]]:
        """Últimas condiciones declaradas, con el nombre de quien las firmó."""
        return self._execute(
            """
            SELECT c.*, u.full_name AS declarante
            FROM condiciones_dia c
            JOIN users u ON u.id = c.declarado_por
            ORDER BY c.fecha DESC
            LIMIT %s
            """,
            (limite,),
            fetch="all",
        )

    def borrar_condicion_dia(self, fecha: Any) -> bool:
        """Revoca la condición de un día; el día vuelve a ser normal."""
        cursor = self._execute(
            "DELETE FROM condiciones_dia WHERE fecha = %s", (fecha,)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Solicitudes de permiso presentadas por el empleado
    # ------------------------------------------------------------------

    def crear_solicitud_permiso(
        self,
        usuario_id: int,
        tipo_permiso: str,
        fecha_inicio: Any,
        fecha_fin: Any,
        horas_solicitadas: float,
        motivo: str,
    ) -> int:
        """Registra un pedido de permiso del empleado en estado Pendiente."""
        cursor = self._execute(
            """
            INSERT INTO solicitudes_permiso
                (usuario_id, tipo_permiso, fecha_inicio, fecha_fin,
                 horas_solicitadas, motivo)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, tipo_permiso, fecha_inicio, fecha_fin,
             horas_solicitadas, motivo),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_solicitud_permiso(self, solicitud_id: int) -> Optional[Dict[str, Any]]:
        """Devuelve una solicitud con los datos del solicitante y del revisor."""
        return self._execute(
            """
            SELECT s.*, u.username, u.full_name, u.tipo_vinculo,
                   r.full_name AS revisor
            FROM solicitudes_permiso s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.resuelto_por
            WHERE s.id = %s
            """,
            (solicitud_id,),
            fetch="one",
        )

    def listar_solicitudes_permiso(
        self, usuario_id: Optional[int] = None, solo_pendientes: bool = False
    ) -> List[Dict[str, Any]]:
        """Bandeja de solicitudes: del empleado indicado o de toda la plantilla.

        Las pendientes encabezan la lista porque son las accionables; el
        resto queda por fecha de pedido descendente.
        """
        condiciones: List[str] = []
        parametros: List[Any] = []
        if usuario_id is not None:
            condiciones.append("s.usuario_id = %s")
            parametros.append(usuario_id)
        if solo_pendientes:
            condiciones.append("s.estado = 'Pendiente'")
        filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
        return self._execute(
            f"""
            SELECT s.*, u.username, u.full_name, u.tipo_vinculo,
                   r.full_name AS revisor
            FROM solicitudes_permiso s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.resuelto_por
            {filtro}
            ORDER BY (s.estado = 'Pendiente') DESC, s.creado_en DESC
            """,
            tuple(parametros) or None,
            fetch="all",
        )

    def resolver_solicitud_permiso(
        self,
        solicitud_id: int,
        estado: str,
        resuelto_por: int,
        observacion: str = "",
        justificacion_id: Optional[int] = None,
    ) -> bool:
        """Cierra una solicitud y la ata a la justificación que la materializa.

        Solo avanza sobre solicitudes en estado Pendiente: así dos revisores
        simultáneos no pueden aprobar dos veces el mismo pedido.
        """
        cursor = self._execute(
            """
            UPDATE solicitudes_permiso
            SET estado = %s, resuelto_por = %s, resuelto_en = NOW(),
                observacion = %s, justificacion_id = %s
            WHERE id = %s AND estado = 'Pendiente'
            """,
            (estado, resuelto_por, observacion, justificacion_id, solicitud_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def insertar_marcaje_registro(
        self, user_id: int, hora_entrada: datetime, es_tardanza: bool, es_feriado: bool
    ) -> int:
        """Inserta un marcaje retroactivo (aprobación de reclamo de entrada)."""
        cursor = self._execute(
            """
            INSERT INTO marcajes
                (user_id, hora_entrada, es_tardanza, es_feriado)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, hora_entrada, es_tardanza, es_feriado),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def actualizar_hora_entrada(
        self, entry_id: int, hora_entrada: datetime, es_tardanza: bool, tipo_incidencia: str = ""
    ) -> None:
        """Corrige la hora de entrada de un marcaje (aprobación de reclamo)."""
        self._execute(
            """
            UPDATE marcajes
            SET hora_entrada = %s, es_tardanza = %s, tipo_incidencia = %s
            WHERE id = %s
            """,
            (hora_entrada, es_tardanza, tipo_incidencia, entry_id),
        )
        self.connection.commit()

    def get_metricas_tardanzas(
        self, desde: datetime.date, hasta: datetime.date
    ) -> List[Dict[str, Any]]:
        """Cantidad de llegadas tardías por día dentro de un rango."""
        cursor = self._execute(
            """
            SELECT DATE(hora_entrada AT TIME ZONE 'America/Asuncion') AS fecha,
                   COUNT(*) AS cantidad
            FROM marcajes
            WHERE es_tardanza = TRUE
              AND hora_entrada >= %s AND hora_entrada < %s
            GROUP BY DATE(hora_entrada AT TIME ZONE 'America/Asuncion')
            ORDER BY fecha
            """,
            (
                datetime.combine(desde, time.min),
                datetime.combine(hasta + timedelta(days=1), time.min),
            ),
            fetch="all",
        )
        return [dict(fila) for fila in cursor]

    def get_horas_extra_por_departamento(self) -> List[Dict[str, Any]]:
        """Horas extra al 50% y 100% acumuladas por departamento."""
        cursor = self._execute(
            """
            SELECT u.departamento,
                   EXTRACT(EPOCH FROM SUM(m.horas_extra_50)) / 3600.0 AS horas_50,
                   EXTRACT(EPOCH FROM SUM(m.horas_extra_100)) / 3600.0 AS horas_100
            FROM marcajes m
            JOIN users u ON u.id = m.user_id
            GROUP BY u.departamento
            ORDER BY (EXTRACT(EPOCH FROM SUM(m.horas_extra_50)) +
                      EXTRACT(EPOCH FROM SUM(m.horas_extra_100))) DESC
            """,
            fetch="all",
        )
        return [dict(fila) for fila in cursor]

    def get_proyeccion_aguinaldos(self) -> List[Dict[str, Any]]:
        """Salarios y departamento de cada empleado para proyectar aguinaldos."""
        cursor = self._execute(
            """
            SELECT id, full_name, departamento, salario_mensual
            FROM users
            WHERE salario_mensual > 0
            ORDER BY departamento, full_name
            """,
            fetch="all",
        )
        return [dict(fila) for fila in cursor]