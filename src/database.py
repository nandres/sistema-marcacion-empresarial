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
import sys
import threading
from datetime import date, datetime, time, timedelta
from time import monotonic, sleep
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.pool
from psycopg2.extras import Json, RealDictCursor

import biometria
import reglamento
import registro
import turnos as turnos_dominio

_log = registro.obtener("database")

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

ESQUEMA_VERSION: int = 2
"""Versión del esquema base.

Subirla obliga a volver a migrar antes de que el servidor acepte tráfico. Se
sube cuando el DDL base agrega algo que el código nuevo da por existente: una
tabla, una columna, un índice sin el cual una consulta se cae.
"""

TABLA_MIGRACIONES: str = "esquema_migraciones"

PASOS_UNICOS: Tuple[Tuple[str, str], ...] = (
    (
        "2026-09-versiones-de-turno",
        """
        INSERT INTO turno_versiones
            (turno_id, vigente_desde, dias, tolerancia_min, empresa_id)
        SELECT t.id, t.creado_en::date, t.dias, t.tolerancia_min, t.empresa_id
          FROM turnos t
         WHERE NOT EXISTS (
               SELECT 1 FROM turno_versiones v WHERE v.turno_id = t.id);

        UPDATE turno_tramos tr
           SET version_id = v.id
          FROM turno_versiones v
         WHERE v.turno_id = tr.turno_id
           AND tr.version_id IS NULL
           AND v.vigente_desde = (SELECT MIN(x.vigente_desde)
                                    FROM turno_versiones x
                                   WHERE x.turno_id = tr.turno_id);
        """,
    ),
)
"""Pasos que corren exactamente una vez, en orden, después del esquema base.

El DDL base es idempotente y se repite sin consecuencias: agregar una tabla o
una columna no necesita nada de esto. Acá van los que **no** se pueden repetir
—rellenar una columna nueva a partir de las viejas, corregir filas cargadas
mal, cambiar un tipo—, cada uno con un nombre que queda anotado en la base
para que la migración siguiente sepa que ya pasó.

    PASOS_UNICOS = (
        ("2026-10-normalizar-cedulas",
         "UPDATE users SET username = lower(trim(username))"),
    )
"""

TABLAS_DE_EMPRESA: Tuple[str, ...] = (
    "users",
    "marcajes",
    "justificaciones",
    "alertas",
    "fotos",
    "solicitudes_correccion",
    "condiciones_dia",
    "solicitudes_permiso",
    "turnos",
    "turno_versiones",
    "turno_tramos",
    "asignaciones_turno",
    "logs_auditoria",
    "ciclos_rotacion",
    "ciclo_turnos",
    "dispositivos",
)
"""Tablas cuyas filas pertenecen a un cliente.

Agregar una tabla al esquema obliga a decidir si entra acá.
``tests/test_multiempresa.py`` falla si queda una tabla de datos sin
``empresa_id`` o una consulta que las toque sin acotar."""

SLUG_EMPRESA_BASE: str = "principal"
RAZON_SOCIAL_BASE: str = "Empresa"


POOL_MINIMO: int = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAXIMO: int = int(os.getenv("DB_POOL_MAX", "10"))
POOL_ESPERA: float = float(os.getenv("DB_POOL_ESPERA", "10"))
"""Segundos que una petición espera un lugar en el pool antes de rendirse."""

_POOLS: Dict[Tuple, Any] = {}
_POOL_BLOQUEO = threading.Lock()


def _pool_de(config: Dict[str, str]) -> Any:
    """Pool de conexiones del proceso para esa configuración.

    Abrir y cerrar una conexión por petición cuesta un saludo TCP y una
    autenticación cada vez. Con un cliente no se nota; con varios alojados y
    el pico de las ocho de la mañana, sí.

    Solo lo usa el proceso que atiende tráfico. Una conexión de vida larga
    —el escritorio, el hilo que escucha las alertas, una migración— se queda
    con un lugar del pool durante horas y se lo saca a las peticiones, así
    que esas siguen abriendo la suya.
    """
    clave = tuple(sorted(config.items()))
    with _POOL_BLOQUEO:
        if clave not in _POOLS:
            _POOLS[clave] = psycopg2.pool.ThreadedConnectionPool(
                POOL_MINIMO, POOL_MAXIMO, client_encoding="UTF8", **config
            )
        return _POOLS[clave]


class PoolAgotado(RuntimeError):
    """Todas las conexiones están ocupadas y la espera se agotó."""


def _como_hora(valor: Any) -> Any:
    """Normaliza a ``time`` lo que puede llegar como texto desde una API."""
    if isinstance(valor, str):
        partes = valor.strip().split(":")
        return time(int(partes[0]), int(partes[1]) if len(partes) > 1 else 0)
    return valor


def _tomar_del_pool(config: Dict[str, str]) -> Any:
    """Saca una conexión del pool, esperando si están todas ocupadas.

    ``getconn`` no espera: cuando el pool llega a su tope lanza excepción en
    el acto. Con el pico de las ocho de la mañana eso significa que las
    primeras ``DB_POOL_MAX`` personas marcan y el resto recibe un error, que
    es la peor forma posible de quedarse corto: una marca que no entró no se
    recupera sola, y quien la intentó ya se fue a trabajar.

    Una petición dura milisegundos, así que esperar un turno breve entrega la
    marca en lugar de rechazarla. El tope existe para que una base caída se
    note como una caída y no como un servidor colgado.

    Se consulta por sondeo y no con un semáforo a propósito: un semáforo que
    se desincronice del pool —una devolución que no ocurre por una excepción
    en el camino— deja el proceso bloqueado para siempre, que es peor que el
    problema que viene a resolver.
    """
    pool = _pool_de(config)
    arranque = monotonic()
    limite = arranque + POOL_ESPERA
    pausa = 0.005
    hubo_espera = False
    while True:
        try:
            conexion = pool.getconn()
            # Una espera que termina bien no rompe nada, pero es el aviso de
            # que el pool quedó chico: conviene verla en el registro antes de
            # que el pico siguiente la convierta en marcas rechazadas.
            if hubo_espera:
                _log.warning(
                    "el pool de %s conexiones se agotó; la petición esperó %.0f ms",
                    POOL_MAXIMO, (monotonic() - arranque) * 1000,
                )
            return conexion
        except psycopg2.pool.PoolError:
            hubo_espera = True
            if monotonic() >= limite:
                _log.error(
                    "pool agotado: %s conexiones ocupadas durante %.0f s seguidos",
                    POOL_MAXIMO, POOL_ESPERA,
                )
                raise PoolAgotado(
                    f"Las {POOL_MAXIMO} conexiones siguen ocupadas después de "
                    f"{POOL_ESPERA:g} s. Subí DB_POOL_MAX, o revisá si algo "
                    f"está reteniendo conexiones sin devolverlas."
                )
            sleep(pausa)
            pausa = min(pausa * 2, 0.05)


def cerrar_pools() -> None:
    """Cierra todos los pools del proceso. Para el apagado ordenado."""
    with _POOL_BLOQUEO:
        for pool in _POOLS.values():
            try:
                pool.closeall()
            except Exception:
                continue
        _POOLS.clear()

class SinEmpresa(RuntimeError):
    """Se intentó leer o escribir datos de cliente sin una empresa activa.

    Es deliberadamente un error y no un recorrido de toda la tabla: una
    consulta sin arrendatario es un defecto, y devolver los datos de todos
    los clientes es exactamente la falla que este error existe para impedir.
    """



def _raices_de_configuracion() -> List[Path]:
    """Dónde buscar el ``.env``, en orden de preferencia.

    Empaquetado, ``__file__`` apunta a la carpeta temporal donde el ejecutable
    se descomprime, que se borra al salir: buscar ahí no encuentra nunca la
    configuración del cliente y, si la encontrara, sería una copia que viajó
    dentro del instalador. Por eso lo primero en ese caso es el directorio del
    ejecutable, que es donde el instalador deja el archivo para que se edite.
    """
    raices: List[Path] = []
    if getattr(sys, "frozen", False):
        raices.append(Path(sys.executable).resolve().parent)
    raices.append(Path(__file__).resolve().parent.parent)
    return raices


def load_dotenv(path: str = ".env") -> None:
    """Carga las variables ``KEY=VALUE`` del archivo indicado al entorno.

    Respeta las variables ya definidas en el entorno real (no las pisa)
    y descarta comentarios y líneas vacías. Si la ruta relativa no existe
    en el directorio actual, cae a la raíz del proyecto: el archivo vive
    junto a ``src/`` y el directorio de trabajo cambia según cómo se
    invoque la aplicación (``python src/app.py``, el ``WORKDIR`` del
    contenedor, o un acceso directo al kiosco empaquetado).
    """
    env_path = Path(path)
    if not env_path.is_absolute() and not env_path.exists():
        for raiz in _raices_de_configuracion():
            candidato = raiz / path
            if candidato.exists():
                env_path = candidato
                break
        else:
            return
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

    def __init__(
        self,
        config: Optional[Dict[str, str]] = None,
        empresa_id: Optional[int] = None,
        agrupada: bool = False,
    ) -> None:
        self.config: Dict[str, str] = config or load_config()
        self.connection: Optional[psycopg2.connection] = None
        self._empresa_id: Optional[int] = empresa_id
        self.agrupada: bool = agrupada

    @property
    def empresa_id(self) -> Optional[int]:
        """Empresa a la que apunta la conexión, o ``None`` si no se fijó."""
        return self._empresa_id

    @empresa_id.setter
    def empresa_id(self, valor: Optional[int]) -> None:
        """Fija la empresa y la propaga a la sesión de PostgreSQL.

        El valor no se queda en Python: viaja a ``app.empresa_id``, que es lo
        que leen las políticas de seguridad por fila. Así el aislamiento no
        depende solo de que la consulta esté bien escrita.
        """
        self._empresa_id = valor
        self._publicar_empresa()

    def _publicar_empresa(self) -> None:
        """Deja la empresa activa en el contexto de la sesión de la base."""
        if self.connection is None or self.connection.closed:
            return
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.empresa_id', %s, false)",
                ("" if self._empresa_id is None else str(self._empresa_id),),
            )

    @property
    def empresa(self) -> int:
        """Empresa activa de la conexión; exigirla es lo que evita la fuga.

        Las consultas de datos de cliente la interpolan siempre. Si nadie la
        fijó, la alternativa sería devolver las filas de todos los clientes,
        así que el acceso falla en lugar de ampliarse.
        """
        if self._empresa_id is None:
            raise SinEmpresa(
                "La conexión no tiene empresa activa. Fijala con "
                "`db.empresa_id` antes de leer o escribir datos de cliente."
            )
        return self._empresa_id

    def connect(self) -> psycopg2.connection:
        """Toma una conexión —del pool o propia— con la empresa ya publicada."""
        if self.agrupada:
            self.connection = _tomar_del_pool(self.config)
        else:
            self.connection = psycopg2.connect(
                client_encoding="UTF8", **self.config
            )
        self.connection.autocommit = False
        self._publicar_empresa()
        return self.connection

    def cerrar(self) -> None:
        """Suelta la conexión: al pool si venía de ahí, o cerrándola.

        Una conexión que vuelve al pool arrastra su estado, y el estado que
        más importa acá es la empresa: sin limpiarla, la petición siguiente
        heredaría la del cliente anterior. Se deshace la transacción y se
        borra el contexto antes de devolverla.
        """
        if self.connection is None:
            return
        if self.agrupada and not self.connection.closed:
            try:
                self.connection.rollback()
                self._empresa_id = None
                self._publicar_empresa()
                self.connection.commit()
            except Exception:
                pass
            try:
                _pool_de(self.config).putconn(self.connection)
            except Exception:
                self.connection.close()
        else:
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
        """Conecta y, solo si hace falta, construye el esquema relacional.

        Sobre una base ya migrada no se toca el esquema. Hay dos motivos y
        los dos importan: el DDL pide locks exclusivos aunque no tenga nada
        que hacer, y el rol con el que corre el servicio no tiene —ni debe
        tener— permiso para alterar tablas.

        La migración explícita vive en ``migrar()``, que es lo que ejecuta
        ``migrate.py`` como paso de despliegue.
        """
        self.ensure_database()
        self.connect()
        if self.esquema_listo() and self._adoptar_empresa_base():
            return
        self.migrar()

    def migrar(self) -> None:
        """Aplica el esquema completo. Paso de despliegue, no de arranque."""
        if self.connection is None or self.connection.closed:
            self.ensure_database()
            self.connect()
        cursor = self.connection.cursor()
        # Una petición de lock exclusivo en espera bloquea también a los
        # lectores que llegan detrás. Si otra sesión mantiene una transacción
        # abierta, conviene abortar la migración antes que encolar la base.
        cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT_DDL}'")
        # La empresa es el arrendatario: cada fila de datos de cliente le
        # pertenece a una y solo a una. Los roles, en cambio, son el mismo
        # catálogo para todas y no llevan `empresa_id`.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS empresas (
                id SERIAL PRIMARY KEY,
                slug VARCHAR(40) UNIQUE NOT NULL,
                razon_social VARCHAR(200) NOT NULL,
                ruc VARCHAR(20) NOT NULL DEFAULT '',
                activa BOOLEAN NOT NULL DEFAULT TRUE,
                max_empleados INTEGER,
                creada_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Una instalación anterior no tiene la columna del plan contratado.
        cursor.execute(
            "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS max_empleados INTEGER"
        )
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
        # La hora de entrada dejó de ser una constante del proceso. Un turno
        # agrupa uno o más tramos (la jornada partida tiene dos), los días de
        # la semana que cubre y la sucursal donde rige.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS turnos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(60) UNIQUE NOT NULL,
                sucursal VARCHAR(80) NOT NULL DEFAULT 'Casa Central',
                dias CHAR(7) NOT NULL DEFAULT '1111100',
                tolerancia_min INTEGER,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                predeterminado BOOLEAN NOT NULL DEFAULT FALSE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_turno_predeterminado "
            "ON turnos (predeterminado) WHERE predeterminado"
        )
        # Lo que un turno **es** —su nombre, su sucursal, si está activo— vive
        # en `turnos` y no cambia de significado con el tiempo. Lo que un turno
        # **dice** —qué días cubre, a qué hora, con cuánta tolerancia— vive
        # acá, fechado. Editar un horario ya no pisa el anterior: abre una
        # versión nueva y la anterior queda rigiendo su propio pasado.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS turno_versiones (
                id SERIAL PRIMARY KEY,
                turno_id INTEGER NOT NULL REFERENCES turnos (id) ON DELETE CASCADE,
                vigente_desde DATE NOT NULL,
                dias CHAR(7) NOT NULL DEFAULT '1111100',
                tolerancia_min INTEGER,
                creado_por INTEGER REFERENCES users (id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (turno_id, vigente_desde)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS turno_tramos (
                id SERIAL PRIMARY KEY,
                turno_id INTEGER NOT NULL REFERENCES turnos (id) ON DELETE CASCADE,
                orden SMALLINT NOT NULL,
                hora_entrada TIME NOT NULL,
                hora_salida TIME NOT NULL,
                UNIQUE (turno_id, orden)
            )
            """
        )
        cursor.execute(
            "ALTER TABLE turno_tramos ADD COLUMN IF NOT EXISTS version_id INTEGER "
            "REFERENCES turno_versiones (id) ON DELETE CASCADE"
        )
        # Los tramos pasan a ser únicos por versión y no por turno: la
        # restricción vieja permitía un solo tramo 1 por turno, que es
        # exactamente lo que impide tener dos versiones del mismo horario.
        cursor.execute(
            "ALTER TABLE turno_tramos "
            "DROP CONSTRAINT IF EXISTS turno_tramos_turno_id_orden_key"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tramos_version_orden "
            "ON turno_tramos (version_id, orden)"
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS turno_id INTEGER "
            "REFERENCES turnos (id) ON DELETE SET NULL"
        )
        # La rotación no se modela pisando el legajo: se asigna un turno con
        # vigencia y, al vencer, el empleado vuelve solo al turno de contrato.
        # Un ciclo de rotación es una lista ordenada de turnos y cada cuántos
        # días se avanza. Se calcula al vuelo en lugar de materializar las
        # asignaciones semana por semana: así no hay nada que regenerar y el
        # calendario sigue siendo correcto en cualquier fecha futura.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ciclos_rotacion (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(60) NOT NULL,
                dias_por_tramo SMALLINT NOT NULL DEFAULT 7,
                ancla DATE NOT NULL,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (dias_por_tramo BETWEEN 1 AND 60)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ciclo_turnos (
                id SERIAL PRIMARY KEY,
                ciclo_id INTEGER NOT NULL
                    REFERENCES ciclos_rotacion (id) ON DELETE CASCADE,
                orden SMALLINT NOT NULL,
                turno_id INTEGER NOT NULL REFERENCES turnos (id) ON DELETE CASCADE,
                UNIQUE (ciclo_id, orden)
            )
            """
        )
        # La posición es lo que hace que dos personas del mismo ciclo estén
        # siempre en turnos distintos y roten juntas.
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS ciclo_id INTEGER "
            "REFERENCES ciclos_rotacion (id) ON DELETE SET NULL"
        )
        cursor.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "ciclo_posicion SMALLINT NOT NULL DEFAULT 0"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS asignaciones_turno (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                turno_id INTEGER NOT NULL REFERENCES turnos (id) ON DELETE CASCADE,
                desde DATE NOT NULL,
                hasta DATE,
                motivo VARCHAR(120) NOT NULL DEFAULT '',
                asignado_por INTEGER REFERENCES users (id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (hasta IS NULL OR hasta >= desde)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_asignaciones_vigencia "
            "ON asignaciones_turno (usuario_id, desde DESC, hasta)"
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
        # Identidad del puesto donde se marca. Sin esto una marcación no tiene
        # origen, y "marqué desde casa" no es un hecho que se pueda comprobar
        # ni desmentir. El token no se guarda: se guarda su hash, por el mismo
        # motivo que una contraseña.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS dispositivos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(80) NOT NULL,
                ubicacion VARCHAR(120) NOT NULL DEFAULT '',
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_por INTEGER REFERENCES users (id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ultimo_visto TIMESTAMPTZ
            )
            """
        )
        cursor.execute(
            "ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS dispositivo_id "
            "INTEGER REFERENCES dispositivos (id) ON DELETE SET NULL"
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_marcajes_dispositivo
            ON marcajes (dispositivo_id, hora_entrada DESC)
            """
        )
        for nombre in ROLES_INICIALES:
            cursor.execute(
                "INSERT INTO roles (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre,),
            )
        empresa_base = self._aplicar_arrendamiento(cursor)
        self._sembrar_turno_predeterminado(cursor, empresa_base)
        self._aplicar_politicas_rls(cursor)
        self._anotar_migraciones(cursor)
        self.connection.commit()
        self._adoptar_empresa_base()

    def _anotar_migraciones(self, cursor: Any) -> List[str]:
        """Corre los pasos de una sola vez y deja sellada la versión aplicada.

        Va dentro de la misma transacción que el resto del DDL a propósito: si
        algo falla, la base no puede quedar declarando una versión que en
        realidad no terminó de aplicarse.
        """
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLA_MIGRACIONES} (
                nombre VARCHAR(80) PRIMARY KEY,
                aplicada_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(f"SELECT nombre FROM {TABLA_MIGRACIONES}")
        hechos = {fila[0] for fila in cursor.fetchall()}

        corridos: List[str] = []
        for nombre, sentencia in PASOS_UNICOS:
            if nombre in hechos:
                continue
            cursor.execute(sentencia)
            cursor.execute(
                f"INSERT INTO {TABLA_MIGRACIONES} (nombre) VALUES (%s)", (nombre,)
            )
            corridos.append(nombre)
            _log.info("paso de migración aplicado: %s", nombre)

        cursor.execute(
            f"INSERT INTO {TABLA_MIGRACIONES} (nombre) VALUES (%s) "
            f"ON CONFLICT (nombre) DO NOTHING",
            (f"base:{ESQUEMA_VERSION}",),
        )
        return corridos

    def _aplicar_arrendamiento(self, cursor: Any) -> int:
        """Ata cada tabla de datos de cliente a una empresa.

        Se aplica al final y no tabla por tabla a propósito: separar el
        arrendamiento del resto del esquema deja la historia completa en un
        solo lugar, que es donde hay que mirar cuando se agrega una tabla
        nueva y hay que decidir si lleva ``empresa_id``.

        Returns:
            El identificador de la empresa que quedó con los datos previos a
            la migración, que es también la que usa una instalación de un
            solo cliente.
        """
        cursor.execute("SELECT id FROM empresas ORDER BY id LIMIT 1")
        fila = cursor.fetchone()
        if fila:
            empresa_base = fila[0]
        else:
            cursor.execute(
                """
                INSERT INTO empresas (slug, razon_social)
                VALUES (%s, %s) RETURNING id
                """,
                (SLUG_EMPRESA_BASE, os.getenv("EMPRESA_NOMBRE", RAZON_SOCIAL_BASE)),
            )
            empresa_base = cursor.fetchone()[0]

        # `ADD COLUMN IF NOT EXISTS` toma un lock exclusivo aunque no tenga
        # nada que agregar, y este DDL corre en cada arranque. Se pregunta
        # primero al catálogo: sobre una base ya migrada no se toca una tabla.
        cursor.execute(
            """
            SELECT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'empresa_id'
            """
        )
        ya_migradas = {fila[0] for fila in cursor.fetchall()}
        pendientes = [t for t in TABLAS_DE_EMPRESA if t not in ya_migradas]
        if not pendientes:
            return empresa_base

        for tabla in pendientes:
            cursor.execute(
                f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS empresa_id INTEGER"
            )
            # Lo que ya estaba en la base es de la empresa que venía usando la
            # instalación: una migración no puede dejar datos sin dueño.
            cursor.execute(
                f"UPDATE {tabla} SET empresa_id = %s WHERE empresa_id IS NULL",
                (empresa_base,),
            )
            cursor.execute(f"ALTER TABLE {tabla} ALTER COLUMN empresa_id SET NOT NULL")
            cursor.execute(
                f"""
                DO $$ BEGIN
                    ALTER TABLE {tabla} ADD CONSTRAINT {tabla}_empresa_fk
                        FOREIGN KEY (empresa_id) REFERENCES empresas (id)
                        ON DELETE CASCADE;
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{tabla}_empresa "
                f"ON {tabla} (empresa_id)"
            )

        # Lo que era único en toda la base pasa a serlo dentro de la empresa:
        # dos clientes distintos pueden tener un turno "Mañana", y la cédula
        # de alguien que trabaja en los dos no puede bloquear el alta.
        cursor.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_username_key")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_empresa_username "
            "ON users (empresa_id, lower(username))"
        )
        cursor.execute("ALTER TABLE turnos DROP CONSTRAINT IF EXISTS turnos_nombre_key")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_turnos_empresa_nombre "
            "ON turnos (empresa_id, lower(nombre))"
        )
        cursor.execute("DROP INDEX IF EXISTS idx_turno_predeterminado")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_turno_predeterminado "
            "ON turnos (empresa_id) WHERE predeterminado"
        )
        cursor.execute(
            "ALTER TABLE condiciones_dia DROP CONSTRAINT IF EXISTS condiciones_dia_pkey"
        )
        cursor.execute(
            """
            DO $$ BEGIN
                ALTER TABLE condiciones_dia ADD PRIMARY KEY (empresa_id, fecha);
            EXCEPTION WHEN invalid_table_definition THEN NULL;
            END $$
            """
        )
        return empresa_base

    def _sembrar_turno_predeterminado(self, cursor: Any, empresa_id: int) -> None:
        """Deja a la empresa con un turno usable desde la primera marcación.

        Una instalación que todavía no definió turnos tiene que seguir
        funcionando, así que el esquema siembra la jornada administrativa con
        la hora de ``JORNADA_INICIO``. Acá el valor sí está disponible: el
        DDL corre después de ``load_config()``, que es lo que no ocurría
        cuando la hora era una constante congelada al importar el módulo.
        """
        # El cursor del DDL es de tuplas, no de diccionarios: acá se indexa
        # por posición y no por nombre de columna.
        cursor.execute("SELECT COUNT(*) FROM turnos WHERE empresa_id = %s", (empresa_id,))
        if cursor.fetchone()[0]:
            return
        respaldo = turnos_dominio.de_respaldo()
        tramo = respaldo.tramos[0]
        cursor.execute(
            """
            INSERT INTO turnos (nombre, dias, predeterminado, empresa_id)
            VALUES (%s, %s, TRUE, %s)
            RETURNING id
            """,
            (respaldo.nombre, respaldo.dias, empresa_id),
        )
        turno_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO turno_versiones
                (turno_id, vigente_desde, dias, tolerancia_min, empresa_id)
            VALUES (%s, CURRENT_DATE, %s, NULL, %s)
            RETURNING id
            """,
            (turno_id, respaldo.dias, empresa_id),
        )
        version_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO turno_tramos
                (turno_id, version_id, orden, hora_entrada, hora_salida, empresa_id)
            VALUES (%s, %s, 1, %s, %s, %s)
            """,
            (turno_id, version_id, tramo.entrada, tramo.salida, empresa_id),
        )

    def _aplicar_politicas_rls(self, cursor: Any) -> None:
        """Hace que PostgreSQL imponga el aislamiento, no solo la aplicación.

        Cada tabla de datos de cliente queda con una política que la acota a
        la empresa publicada en ``app.empresa_id``. Con eso, una consulta a
        la que se le olvidó el ``WHERE`` no devuelve las filas de los demás
        clientes: devuelve ninguna.

        El contexto vacío se compara contra ``NULL``, así que una conexión
        que no declaró su empresa no ve nada. Es la misma postura que toma la
        aplicación con ``SinEmpresa``, sostenida un piso más abajo.

        ``FORCE`` extiende la política al dueño de las tablas. A un
        superusuario no lo alcanza —PostgreSQL lo exceptúa siempre—, y por eso
        el proceso que atiende tráfico tiene que conectar con el rol
        restringido que crea ``migrate.py rol-app``.
        """
        contexto = "NULLIF(current_setting('app.empresa_id', true), '')::int"
        for tabla in TABLAS_DE_EMPRESA:
            cursor.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
            cursor.execute(f"ALTER TABLE {tabla} FORCE ROW LEVEL SECURITY")
            cursor.execute(f"DROP POLICY IF EXISTS {tabla}_empresa ON {tabla}")
            cursor.execute(
                f"""
                CREATE POLICY {tabla}_empresa ON {tabla}
                USING (empresa_id = {contexto})
                WITH CHECK (empresa_id = {contexto})
                """
            )

        # La búsqueda de credenciales es la única lectura que cruza empresas
        # a propósito: la pantalla de acceso todavía no sabe de qué cliente es
        # quien escribe. Va en una función con los privilegios de su dueño,
        # que es el camino que PostgreSQL ofrece para una excepción acotada,
        # y devuelve lo mínimo para decidir el login: ni el nombre, ni el
        # rol, ni el salario. El resto se lee ya acotado a la empresa que
        # resultó ganadora.
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION credenciales_por_usuario(p_username text)
            RETURNS TABLE (
                id integer,
                empresa_id integer,
                password_hash text,
                activo boolean,
                empresa_slug text,
                empresa_activa boolean
            )
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $fn$
                SELECT u.id, u.empresa_id, u.password_hash::text, u.activo,
                       e.slug::text, e.activa
                FROM users u
                JOIN empresas e ON e.id = u.empresa_id
                WHERE lower(u.username) = lower(p_username)
                ORDER BY u.id
            $fn$
            """
        )

        # El token del kiosco es la segunda —y última— lectura que cruza
        # empresas a propósito, por el mismo motivo que el login: el puesto se
        # identifica antes de que nadie sepa de qué cliente es. Devuelve lo
        # mínimo para resolverlo y nada más.
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION dispositivo_por_token(p_hash text)
            RETURNS TABLE (
                id integer,
                empresa_id integer,
                nombre text,
                ubicacion text,
                activo boolean
            )
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $fn$
                SELECT d.id, d.empresa_id, d.nombre::text, d.ubicacion::text,
                       (d.activo AND e.activa) AS activo
                FROM dispositivos d
                JOIN empresas e ON e.id = d.empresa_id
                WHERE d.token_hash = p_hash
            $fn$
            """
        )

    def rls_efectiva(self) -> Dict[str, Any]:
        """Informa si las políticas de la base realmente alcanzan a esta conexión.

        Un superusuario las esquiva por diseño de PostgreSQL. Decirlo es
        parte del control: una política instalada pero inerte se parece
        demasiado a una que protege.
        """
        fila = self._execute(
            """
            SELECT rolsuper, rolbypassrls, current_user AS rol
            FROM pg_roles WHERE rolname = current_user
            """,
            fetch="one",
        )
        politicas = self._execute(
            "SELECT COUNT(*) AS total FROM pg_policies WHERE schemaname = 'public'",
            fetch="one",
        )
        esquiva = bool(fila["rolsuper"] or fila["rolbypassrls"])
        return {
            "rol": fila["rol"],
            "politicas": int(politicas["total"]),
            "esquiva": esquiva,
            "activa": not esquiva and int(politicas["total"]) > 0,
        }

    def crear_rol_de_aplicacion(
        self, nombre: str, password: Optional[str] = None
    ) -> bool:
        """Crea (o repasa) el rol restringido con el que corre el servicio.

        Es un rol sin DDL y sin ``BYPASSRLS``: puede leer y escribir los datos
        de la empresa que declare en su sesión, y nada más. El esquema lo
        sigue aplicando el rol administrador desde ``migrate.py``.

        Sobre un rol que ya existe, ``password`` en ``None`` **no toca la
        contraseña**. Rotarla sola dejaría sin base al servicio en marcha, y
        volver a correr una orden de instalación tiene que poder hacerse sin
        miedo.

        Returns:
            ``True`` si se fijó una contraseña nueva; ``False`` si solo se
            repasaron los permisos del rol que ya estaba.
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (nombre,))
        existe = cursor.fetchone() is not None
        atributos = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        if existe and password is None:
            cursor.execute(f'ALTER ROLE "{nombre}" {atributos}')
        else:
            verbo = "ALTER" if existe else "CREATE"
            cursor.execute(
                f'{verbo} ROLE "{nombre}" {atributos} PASSWORD %s', (password,)
            )
        base = self.config["dbname"]
        cursor.execute(f'GRANT CONNECT ON DATABASE "{base}" TO "{nombre}"')
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{nombre}"')
        cursor.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            f'IN SCHEMA public TO "{nombre}"'
        )
        cursor.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{nombre}"'
        )
        # Las dos lecturas que cruzan empresas, no una: sin la del kiosco,
        # revocar el EXECUTE de PUBLIC dejaría el login en pie y la marcación
        # caída, que es la peor mitad para descubrir en producción.
        cursor.execute(
            f'GRANT EXECUTE ON FUNCTION credenciales_por_usuario(text) TO "{nombre}"'
        )
        cursor.execute(
            f'GRANT EXECUTE ON FUNCTION dispositivo_por_token(text) TO "{nombre}"'
        )
        # Las tablas que se creen después también quedan alcanzadas, para que
        # agregar una no obligue a acordarse de repetir los permisos.
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{nombre}"'
        )
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{nombre}"'
        )
        self.connection.commit()
        return password is not None

    # --- Empresas ----------------------------------------------------------

    def usar_empresa(self, referencia: Any) -> Dict[str, Any]:
        """Ata la conexión a una empresa, por identificador o por slug.

        Es el único punto donde una sesión elige de qué cliente son los datos
        que va a ver. Todo lo demás lee ese valor.
        """
        if isinstance(referencia, int):
            empresa = self.get_empresa(referencia)
        else:
            empresa = self.get_empresa_por_slug(str(referencia))
        if not empresa:
            raise SinEmpresa(f"No existe la empresa '{referencia}'.")
        self.empresa_id = empresa["id"]
        return empresa

    def get_empresa(self, empresa_id: int) -> Optional[Dict[str, Any]]:
        """Devuelve una empresa por identificador."""
        return self._execute(
            "SELECT * FROM empresas WHERE id = %s", (empresa_id,), fetch="one"
        )

    def get_empresa_por_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Devuelve una empresa por su nombre corto."""
        return self._execute(
            "SELECT * FROM empresas WHERE slug = %s", (slug.strip().lower(),),
            fetch="one",
        )

    def listar_empresas(self, incluir_inactivas: bool = False) -> List[Dict[str, Any]]:
        """Lista las empresas alojadas en esta instalación, con su dotación."""
        filtro = "" if incluir_inactivas else "WHERE e.activa"
        return self._execute(
            f"""
            SELECT e.*,
                   (SELECT COUNT(*) FROM users u
                     WHERE u.empresa_id = e.id AND u.activo) AS empleados
            FROM empresas e
            {filtro}
            ORDER BY e.razon_social
            """,
            fetch="all",
        )

    def crear_empresa(
        self, slug: str, razon_social: str, ruc: str = ""
    ) -> Dict[str, Any]:
        """Da de alta un cliente nuevo y le siembra su jornada inicial.

        El turno se siembra acá y no en la migración: el esquema se aplica
        una vez y las empresas se alojan cuando se venden, así que la segunda
        nacería sin ningún horario contra el cual medir una tardanza.
        """
        cursor = self._execute(
            """
            INSERT INTO empresas (slug, razon_social, ruc)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (slug.strip().lower(), razon_social.strip(), ruc.strip()),
        )
        empresa = cursor.fetchone()
        respaldo = turnos_dominio.de_respaldo()
        tramo = respaldo.tramos[0]
        cursor = self._execute(
            """
            INSERT INTO turnos (nombre, dias, predeterminado, empresa_id)
            VALUES (%s, %s, TRUE, %s)
            RETURNING id
            """,
            (respaldo.nombre, respaldo.dias, empresa["id"]),
        )
        turno_id = cursor.fetchone()["id"]
        version = self._execute(
            """
            INSERT INTO turno_versiones
                (turno_id, vigente_desde, dias, tolerancia_min, empresa_id)
            VALUES (%s, CURRENT_DATE, %s, NULL, %s)
            RETURNING id
            """,
            (turno_id, respaldo.dias, empresa["id"]),
        ).fetchone()["id"]
        self._execute(
            """
            INSERT INTO turno_tramos
                (turno_id, version_id, orden, hora_entrada, hora_salida, empresa_id)
            VALUES (%s, %s, 1, %s, %s, %s)
            """,
            (turno_id, version, tramo.entrada, tramo.salida, empresa["id"]),
        )
        self.connection.commit()
        return empresa

    def contar_empleados(self) -> int:
        """Empleados activos de la empresa, para medir contra su plan."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM users WHERE empresa_id = %s AND activo",
            (self.empresa,),
            fetch="one",
        )
        return int(fila["total"])

    def fijar_cupo_empresa(
        self, empresa_id: int, max_empleados: Optional[int]
    ) -> bool:
        """Fija (o levanta) el tope de empleados de un cliente."""
        cursor = self._execute(
            "UPDATE empresas SET max_empleados = %s WHERE id = %s",
            (max_empleados, empresa_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def cambiar_estado_empresa(self, empresa_id: int, activa: bool) -> bool:
        """Suspende o reactiva una empresa sin borrar un solo dato suyo."""
        cursor = self._execute(
            "UPDATE empresas SET activa = %s WHERE id = %s", (activa, empresa_id)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def buscar_credenciales(self, username: str) -> List[Dict[str, Any]]:
        """Candidatos de login para una cédula, en todas las empresas.

        Es la única lectura que cruza empresas a propósito, y no puede ser de
        otra forma: la pantalla de acceso todavía no sabe de qué cliente es
        quien escribe. Por eso vive en ``credenciales_por_usuario``, una
        función con los privilegios de su dueño: es la excepción acotada que
        PostgreSQL ofrece para que las políticas por fila no la bloqueen, y
        deja la excepción escrita en el esquema en lugar de repartida por el
        código.

        Devuelve lo mínimo para decidir un login —ni el nombre, ni el rol, ni
        el salario— y no decide nada: quién entra lo resuelve
        ``auth.authenticate`` comparando la contraseña, de modo que nadie
        pueda averiguar en qué empresas existe una cédula sin tener su clave.
        """
        return self._execute(
            "SELECT * FROM credenciales_por_usuario(%s)",
            (username,),
            fetch="all",
        )

    def _adoptar_empresa_base(self) -> bool:
        """Deja la conexión apuntando a la empresa que vino con la instalación.

        Las herramientas que aplican el esquema —migraciones, siembra de
        pruebas, la consola de administración— trabajan sobre un solo cliente
        y no tendrían de dónde sacar cuál. El camino de las peticiones web no
        pasa por acá: ahí la empresa sale de la sesión.
        """
        fila = self._execute(
            "SELECT id FROM empresas WHERE slug = %s", (SLUG_EMPRESA_BASE,),
            fetch="one",
        )
        if fila:
            self.empresa_id = fila["id"]
        return fila is not None

    def _existe_tabla(self, nombre: str) -> bool:
        """``to_regclass`` devuelve NULL en vez de fallar si la tabla no está."""
        fila = self._execute(
            "SELECT to_regclass(%s) AS referencia", (f"public.{nombre}",), fetch="one"
        )
        return bool(fila and fila["referencia"])

    def version_esquema(self) -> Optional[int]:
        """Versión del esquema de esta base, o ``None`` si nunca se migró.

        Es la respuesta a *¿en qué versión está el cliente que acaba de
        llamar?*, que sin un sello en la propia base solo se podía contestar
        mirando el código que alguien cree que le instaló.
        """
        if not self._existe_tabla(TABLA_MIGRACIONES):
            return None
        filas = self._execute(
            f"SELECT nombre FROM {TABLA_MIGRACIONES} WHERE nombre LIKE 'base:%%'",
            fetch="all",
        ) or []
        versiones = [
            int(fila["nombre"].split(":", 1)[-1])
            for fila in filas
            if fila["nombre"].split(":", 1)[-1].isdigit()
        ]
        return max(versiones) if versiones else None

    def migraciones_pendientes(self) -> List[str]:
        """Lo que le falta a esta base para atender tráfico con este código.

        Devuelve los nombres y no un booleano a propósito: el mensaje que lee
        quien despliega dice **qué** falta, no solo que algo falta.
        """
        sello = f"base:{ESQUEMA_VERSION}"
        if not self._existe_tabla(TABLA_MIGRACIONES):
            return [sello] + [nombre for nombre, _ in PASOS_UNICOS]
        filas = self._execute(
            f"SELECT nombre FROM {TABLA_MIGRACIONES}", fetch="all"
        ) or []
        hechos = {fila["nombre"] for fila in filas}
        pendientes = [n for n, _ in PASOS_UNICOS if n not in hechos]
        if sello not in hechos:
            pendientes.insert(0, sello)
        return pendientes

    def historial_esquema(self) -> List[Dict[str, Any]]:
        """Qué se aplicó sobre esta base y cuándo, en orden."""
        if not self._existe_tabla(TABLA_MIGRACIONES):
            return []
        return self._execute(
            f"SELECT nombre, aplicada_en FROM {TABLA_MIGRACIONES} "
            f"ORDER BY aplicada_en, nombre",
            fetch="all",
        ) or []

    def esquema_listo(self) -> bool:
        """Indica si esta base puede atender tráfico con el código actual.

        Antes se contaban tablas contra una lista escrita a mano, y la lista
        envejeció: cuando el esquema sumó ``dispositivos``, una base migrada de
        antes seguía dando el recuento por bueno y el servidor arrancaba sin la
        tabla que ``/api/marcar`` necesita. El fallo no aparecía al arrancar,
        que es cuando se puede corregir, sino en la primera marcación.

        El sello dice lo mismo sin depender de que alguien se acuerde de
        actualizar una lista cada vez que agrega una tabla.
        """
        return not self.migraciones_pendientes()

    def latido(self) -> None:
        """Consulta mínima para confirmar que la conexión sirve de verdad.

        Tenerla abierta no prueba nada: el pool puede estar entregando un
        socket que la base cerró del otro lado hace horas.
        """
        self._execute("SELECT 1 AS vivo", fetch="one")

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
                (usuario_id, accion, tabla, registro_id, valores_anteriores,
                 valores_nuevos, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usuario_id,
                accion,
                tabla,
                registro_id,
                Json(anterior) if anterior is not None else None,
                Json(nuevos) if nuevos is not None else None,
                self.empresa,
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
            WHERE a.empresa_id = %s
            ORDER BY a.creado_en DESC
            LIMIT %s
            """,
            (self.empresa, limite),
            fetch="all",
        )

    def actualizar_hash_justificacion(
        self, justificacion_id: int, hash_legal: str
    ) -> None:
        """Persiste el hash SHA-256 del permiso para su validación legal."""
        self._execute(
            "UPDATE justificaciones SET hash_legal = %s "
            "WHERE empresa_id = %s AND id = %s",
            (hash_legal, self.empresa, justificacion_id),
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
        turno_id: Optional[int] = None,
    ) -> int:
        """Inserta un usuario y retorna su identificador.

        ``fecha_ingreso`` es la del contrato, no la del alta en el sistema:
        de ella dependen los días de vacaciones y los meses de aguinaldo. Si
        no se indica, se asume que el empleado ingresa hoy.
        """
        cursor = self._execute(
            """
            INSERT INTO users (username, password_hash, full_name, role_id,
                               salario_mensual, tipo_vinculo, fecha_ingreso,
                               turno_id, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s, %s)
            RETURNING id
            """,
            (username, password_hash, full_name, role_id, salario_mensual,
             tipo_vinculo, fecha_ingreso, turno_id, self.empresa),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Busca un usuario por nombre de acceso, incluyendo su rol."""
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.empresa_id = %s AND lower(u.username) = lower(%s)
            """,
            (self.empresa, username),
            fetch="one",
        )

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Busca un usuario por identificador, incluyendo su rol."""
        return self._execute(
            """
            SELECT u.*, r.nombre AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.empresa_id = %s AND u.id = %s
            """,
            (self.empresa, user_id),
            fetch="one",
        )

    def list_users(self, incluir_bajas: bool = False) -> List[Dict[str, Any]]:
        """Lista el personal con su rol, salario, vínculo y fecha de ingreso.

        Por defecto devuelve solo la plantilla activa: una baja conserva sus
        marcajes para el archivo laboral pero no forma parte de la nómina.
        """
        filtro = "" if incluir_bajas else "AND u.activo"
        return self._execute(
            f"""
            SELECT u.id, u.username, u.full_name, u.salario_mensual,
                   u.tipo_vinculo, r.nombre AS role_name, u.created_at,
                   u.fecha_ingreso, u.activo, u.fecha_baja, u.turno_id,
                   t.nombre AS turno_nombre, u.ciclo_id, u.ciclo_posicion
            FROM users u
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN turnos t ON t.id = u.turno_id
            WHERE u.empresa_id = %s {filtro}
            ORDER BY u.id
            """,
            (self.empresa,),
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
            WHERE empresa_id = %s AND id = %s
            """,
            (activo, fecha_baja if not activo else None, self.empresa, user_id),
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
        turno_id: Optional[int] = None,
        limpiar_turno: bool = False,
    ) -> bool:
        """Actualiza los campos provistos de un usuario y retorna si hubo cambios.

        ``limpiar_turno`` devuelve el legajo al turno predeterminado de la
        empresa: sin él, un ``turno_id`` nulo sería indistinguible de no
        haber mandado el campo y nadie podría deshacer una asignación.
        """
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
        if limpiar_turno:
            updates.append("turno_id = NULL")
        elif turno_id is not None:
            updates.append("turno_id = %s")
            params.append(turno_id)
        if not updates:
            return False
        params.extend([self.empresa, user_id])
        self._execute(
            f"UPDATE users SET {', '.join(updates)} "
            f"WHERE empresa_id = %s AND id = %s",
            tuple(params),
        )
        self.connection.commit()
        return True

    def delete_user(self, user_id: int) -> None:
        """Elimina un usuario; sus marcajes se borran en cascada."""
        self._execute(
            "DELETE FROM users WHERE empresa_id = %s AND id = %s",
            (self.empresa, user_id),
        )
        self.connection.commit()

    def asignar_biometrico_id(self, user_id: int, biometrico_id: int) -> None:
        """Asocia el identificador biométrico del reloj al usuario."""
        self._execute(
            "UPDATE users SET biometrico_id = %s "
            "WHERE empresa_id = %s AND id = %s",
            (biometrico_id, self.empresa, user_id),
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

    def listar_turnos(self, incluir_inactivos: bool = False) -> List[Dict[str, Any]]:
        """Lista los turnos con sus tramos y la dotación asignada a cada uno.

        Los tramos llegan en una sola consulta y se agrupan en memoria: un
        ``SELECT`` por turno para pintar una tabla de seis filas es la forma
        más fácil de convertir una pantalla en una tormenta de consultas.
        """
        filtro = "" if incluir_inactivos else "AND t.activo"
        turnos = self._execute(
            f"""
            SELECT t.id, t.nombre, t.sucursal, t.activo, t.predeterminado,
                   t.creado_en, t.empresa_id,
                   v.id AS version_id, v.vigente_desde, v.dias, v.tolerancia_min,
                   (SELECT COUNT(*) FROM turno_versiones x
                     WHERE x.turno_id = t.id) AS versiones,
                   (SELECT COUNT(*) FROM users u
                     WHERE u.turno_id = t.id AND u.activo) AS dotacion,
                   (SELECT COUNT(*) FROM asignaciones_turno a
                     WHERE a.turno_id = t.id
                       AND (a.hasta IS NULL OR a.hasta >= CURRENT_DATE)
                       AND a.desde <= CURRENT_DATE) AS asignados
            FROM turnos t
            LEFT JOIN LATERAL (
                SELECT vv.id, vv.vigente_desde, vv.dias, vv.tolerancia_min
                FROM turno_versiones vv
                WHERE vv.turno_id = t.id AND vv.empresa_id = t.empresa_id
                ORDER BY (vv.vigente_desde <= CURRENT_DATE) DESC,
                         ABS(CURRENT_DATE - vv.vigente_desde) ASC
                LIMIT 1
            ) v ON TRUE
            WHERE t.empresa_id = %s {filtro}
            ORDER BY t.predeterminado DESC, t.nombre
            """,
            (self.empresa,),
            fetch="all",
        )
        if not turnos:
            return []
        tramos = self._execute(
            """
            SELECT version_id, orden, hora_entrada, hora_salida
            FROM turno_tramos
            WHERE empresa_id = %s AND version_id = ANY(%s)
            ORDER BY version_id, orden
            """,
            (self.empresa, [t["version_id"] for t in turnos if t["version_id"]]),
            fetch="all",
        )
        por_version: Dict[int, List[Dict[str, Any]]] = {}
        for tramo in tramos:
            por_version.setdefault(tramo["version_id"], []).append(tramo)
        for turno in turnos:
            turno["tramos"] = por_version.get(turno["version_id"], [])
        return turnos

    def version_de_turno(
        self, turno_id: int, dia: Any = None
    ) -> Optional[Dict[str, Any]]:
        """Definición del turno que regía en esa fecha (hoy por omisión).

        Una fecha anterior a la primera versión conocida devuelve esa primera
        versión. Es deliberado: la alternativa —no resolver ningún horario—
        deja la marca sin poder liquidarse, y medir contra la definición más
        vieja que existe es la aproximación menos mala a un horario que nadie
        llegó a anotar.

        El orden hace las dos cosas de una vez: primero las versiones que ya
        estaban vigentes, y entre ellas la más cercana; si no hay ninguna, la
        más cercana hacia adelante.
        """
        dia = dia or date.today()
        return self._execute(
            """
            SELECT id, turno_id, vigente_desde, dias, tolerancia_min,
                   creado_por, creado_en
            FROM turno_versiones
            WHERE empresa_id = %s AND turno_id = %s
            ORDER BY (vigente_desde <= %s::date) DESC,
                     ABS(%s::date - vigente_desde) ASC
            LIMIT 1
            """,
            (self.empresa, turno_id, dia, dia),
            fetch="one",
        )

    def _tramos_de_version(self, version_id: int) -> List[Dict[str, Any]]:
        """Tramos de una versión concreta, en orden."""
        return self._execute(
            """
            SELECT orden, hora_entrada, hora_salida
            FROM turno_tramos
            WHERE empresa_id = %s AND version_id = %s ORDER BY orden
            """,
            (self.empresa, version_id),
            fetch="all",
        ) or []

    def get_turno(self, turno_id: int, dia: Any = None) -> Optional[Dict[str, Any]]:
        """Turno con la definición que regía en ``dia`` (hoy por omisión).

        El nombre y la sucursal salen de ``turnos`` porque son identidad y no
        cambian de sentido con el tiempo; los días, la tolerancia y los tramos
        salen de la versión, porque son exactamente lo que sí cambia.
        """
        turno = self._execute(
            "SELECT * FROM turnos WHERE empresa_id = %s AND id = %s",
            (self.empresa, turno_id),
            fetch="one",
        )
        if not turno:
            return None
        version = self.version_de_turno(turno_id, dia)
        turno["version_id"] = version["id"] if version else None
        turno["vigente_desde"] = version["vigente_desde"] if version else None
        if version:
            turno["dias"] = version["dias"]
            turno["tolerancia_min"] = version["tolerancia_min"]
        turno["tramos"] = (
            self._tramos_de_version(version["id"]) if version else []
        )
        return turno

    def historial_turno(self, turno_id: int) -> List[Dict[str, Any]]:
        """Todas las versiones del turno, de la más reciente a la más vieja.

        Es lo que contesta *"¿contra qué horario se midió esta marca de
        marzo?"* sin tener que reconstruirlo de memoria.
        """
        versiones = self._execute(
            """
            SELECT v.id, v.vigente_desde, v.dias, v.tolerancia_min,
                   v.creado_en, u.full_name AS creado_por_nombre
            FROM turno_versiones v
            LEFT JOIN users u ON u.id = v.creado_por
            WHERE v.empresa_id = %s AND v.turno_id = %s
            ORDER BY v.vigente_desde DESC
            """,
            (self.empresa, turno_id),
            fetch="all",
        ) or []
        for version in versiones:
            version["tramos"] = self._tramos_de_version(version["id"])
        return [dict(v) for v in versiones]

    def get_turno_por_nombre(self, nombre: str) -> Optional[Dict[str, Any]]:
        """Busca un turno por su nombre, que es único."""
        fila = self._execute(
            "SELECT id FROM turnos "
            "WHERE empresa_id = %s AND lower(nombre) = lower(%s)",
            (self.empresa, nombre),
            fetch="one",
        )
        return self.get_turno(fila["id"]) if fila else None

    def crear_turno(
        self,
        nombre: str,
        tramos: List[Tuple[Any, Any]],
        dias: str,
        sucursal: str,
        tolerancia_min: Optional[int] = None,
        vigente_desde: Any = None,
        creado_por: Optional[int] = None,
    ) -> int:
        """Inserta un turno con su primera versión, en una sola transacción."""
        cursor = self._execute(
            """
            INSERT INTO turnos (nombre, sucursal, dias, tolerancia_min, empresa_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (nombre, sucursal, dias, tolerancia_min, self.empresa),
        )
        turno_id = cursor.fetchone()["id"]
        self.fijar_version_turno(
            turno_id, vigente_desde or date.today(), dias, tolerancia_min,
            tramos, creado_por,
        )
        return turno_id

    # ------------------------------------------------ Dispositivos de marcación

    def crear_dispositivo(
        self, nombre: str, ubicacion: str, token_hash: str, creado_por: int
    ) -> int:
        """Da de alta un puesto de marcación guardando solo el hash del token."""
        cursor = self._execute(
            """
            INSERT INTO dispositivos
                (nombre, ubicacion, token_hash, creado_por, empresa_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (nombre, ubicacion, token_hash, creado_por, self.empresa),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def listar_dispositivos(self, incluir_inactivos: bool = False) -> List[Dict[str, Any]]:
        """Puestos de marcación de la empresa, con cuántas marcas lleva cada uno."""
        condicion = "" if incluir_inactivos else "AND d.activo = TRUE"
        cursor = self._execute(
            f"""
            SELECT d.id, d.nombre, d.ubicacion, d.activo, d.creado_en,
                   d.ultimo_visto, COUNT(m.id) AS marcas
            FROM dispositivos d
            LEFT JOIN marcajes m
                   ON m.dispositivo_id = d.id AND m.empresa_id = d.empresa_id
            WHERE d.empresa_id = %s {condicion}
            GROUP BY d.id
            ORDER BY d.activo DESC, d.nombre
            """,
            (self.empresa,),
            fetch="all",
        )
        return [dict(fila) for fila in cursor or []]

    def dispositivo_por_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """Resuelve un token a su puesto, sin exigir empresa en la sesión.

        El kiosco se identifica antes de que nadie sepa de qué cliente es: el
        token es justamente lo que lo dice. Por eso la búsqueda es global y la
        empresa sale del dispositivo, no al revés.
        """
        filas = self._execute(
            "SELECT * FROM dispositivo_por_token(%s)",
            (token_hash,),
            fetch="all",
        )
        return dict(filas[0]) if filas else None

    def marcar_dispositivo_visto(self, dispositivo_id: int) -> None:
        """Deja constancia de que el puesto sigue en pie."""
        self._execute(
            "UPDATE dispositivos SET ultimo_visto = NOW() "
            "WHERE empresa_id = %s AND id = %s",
            (self.empresa, dispositivo_id),
        )
        self.connection.commit()

    def revocar_dispositivo(self, dispositivo_id: int) -> None:
        """Desactiva un puesto sin borrarlo.

        Borrarlo dejaría sin origen a las marcas que ya registró, que es
        justamente el dato que este módulo existe para conservar.
        """
        self._execute(
            "UPDATE dispositivos SET activo = FALSE "
            "WHERE empresa_id = %s AND id = %s",
            (self.empresa, dispositivo_id),
        )
        self.connection.commit()

    def asignar_dispositivo_a_marcaje(self, marcaje_id: int, dispositivo_id: int) -> None:
        """Ata una marcación al puesto donde se hizo."""
        self._execute(
            "UPDATE marcajes SET dispositivo_id = %s WHERE empresa_id = %s AND id = %s",
            (dispositivo_id, self.empresa, marcaje_id),
        )
        self.connection.commit()

    def actualizar_turno(
        self,
        turno_id: int,
        nombre: Optional[str] = None,
        tramos: Optional[List[Tuple[Any, Any]]] = None,
        dias: Optional[str] = None,
        sucursal: Optional[str] = None,
        tolerancia_min: Optional[int] = None,
        limpiar_tolerancia: bool = False,
        vigente_desde: Any = None,
        creado_por: Optional[int] = None,
    ) -> bool:
        """Cambia un turno: la identidad en el acto, el horario desde una fecha.

        El nombre y la sucursal se corrigen en el lugar porque no cambian de
        significado con el tiempo: un turno renombrado sigue siendo el mismo
        turno, y forkear su historia por un typo la volvería ilegible.

        Los días, la tolerancia y los tramos abren una versión con vigencia
        desde ``vigente_desde`` —hoy, si no se dice otra cosa—. Lo que ya se
        liquidó se sigue midiendo contra el horario que regía entonces.
        """
        vigente_desde = vigente_desde or date.today()
        identidad: List[str] = []
        params: List[Any] = []
        if nombre is not None:
            identidad.append("nombre = %s")
            params.append(nombre)
        if sucursal is not None:
            identidad.append("sucursal = %s")
            params.append(sucursal)
        if identidad:
            self._execute(
                f"UPDATE turnos SET {', '.join(identidad)} "
                f"WHERE empresa_id = %s AND id = %s",
                tuple(params + [self.empresa, turno_id]),
            )

        base = self.get_turno(turno_id, vigente_desde)
        if base is None:
            if identidad:
                self.connection.commit()
            return bool(identidad)

        nuevos_dias = dias if dias is not None else base["dias"]
        if limpiar_tolerancia:
            nueva_tolerancia = None
        elif tolerancia_min is not None:
            nueva_tolerancia = tolerancia_min
        else:
            nueva_tolerancia = base["tolerancia_min"]
        nuevos_tramos = (
            list(tramos) if tramos is not None
            else [(x["hora_entrada"], x["hora_salida"]) for x in base["tramos"]]
        )

        if self._misma_definicion(base, nuevos_dias, nueva_tolerancia, nuevos_tramos):
            if identidad:
                self.connection.commit()
            return bool(identidad)

        self.fijar_version_turno(
            turno_id, vigente_desde, nuevos_dias, nueva_tolerancia,
            nuevos_tramos, creado_por,
        )
        return True

    @staticmethod
    def _misma_definicion(
        base: Dict[str, Any],
        dias: Optional[str],
        tolerancia_min: Optional[int],
        tramos: List[Tuple[Any, Any]],
    ) -> bool:
        """Dice si lo que se quiere guardar es lo que ya está guardado.

        El panel manda el formulario entero en cada guardado, así que renombrar
        un turno llega hasta acá con el horario repetido. Sin esta comparación,
        corregir un typo abriría una versión idéntica a la anterior y el
        historial dejaría de contar una historia.
        """
        if (base["dias"] or "") != (dias or ""):
            return False
        if base["tolerancia_min"] != tolerancia_min:
            return False
        actuales = [(x["hora_entrada"], x["hora_salida"]) for x in base["tramos"]]
        propuestos = [(_como_hora(e), _como_hora(s)) for e, s in tramos]
        return actuales == propuestos

    def fijar_version_turno(
        self,
        turno_id: int,
        vigente_desde: Any,
        dias: Optional[str],
        tolerancia_min: Optional[int],
        tramos: List[Tuple[Any, Any]],
        creado_por: Optional[int] = None,
    ) -> int:
        """Deja la definición del turno vigente a partir de esa fecha.

        Si ya hay una versión con esa misma fecha se reescribe en lugar de
        agregar otra: dos correcciones el mismo día son una sola decisión, y
        una versión que duró cero días no es historia, es ruido.
        """
        existente = self._execute(
            "SELECT id FROM turno_versiones "
            "WHERE empresa_id = %s AND turno_id = %s AND vigente_desde = %s",
            (self.empresa, turno_id, vigente_desde),
            fetch="one",
        )
        if existente:
            version_id = int(existente["id"])
            self._execute(
                "UPDATE turno_versiones "
                "SET dias = %s, tolerancia_min = %s, creado_por = %s, creado_en = NOW() "
                "WHERE empresa_id = %s AND id = %s",
                (dias, tolerancia_min, creado_por, self.empresa, version_id),
            )
        else:
            cursor = self._execute(
                """
                INSERT INTO turno_versiones
                    (turno_id, vigente_desde, dias, tolerancia_min,
                     creado_por, empresa_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (turno_id, vigente_desde, dias, tolerancia_min,
                 creado_por, self.empresa),
            )
            version_id = int(cursor.fetchone()["id"])

        self._reemplazar_tramos(turno_id, version_id, tramos)
        self._refrescar_definicion_actual(turno_id)
        self.connection.commit()
        return version_id

    def _reemplazar_tramos(
        self, turno_id: int, version_id: int, tramos: List[Tuple[Any, Any]]
    ) -> None:
        """Deja los tramos de **esa versión** como los describe la lista."""
        self._execute(
            "DELETE FROM turno_tramos WHERE empresa_id = %s AND version_id = %s",
            (self.empresa, version_id),
        )
        for orden, (entrada, salida) in enumerate(tramos, start=1):
            self._execute(
                """
                INSERT INTO turno_tramos
                    (turno_id, version_id, orden, hora_entrada, hora_salida,
                     empresa_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (turno_id, version_id, orden, entrada, salida, self.empresa),
            )

    def _refrescar_definicion_actual(self, turno_id: int) -> None:
        """Copia la versión vigente hoy a las columnas de ``turnos``.

        Son una caché, no la fuente: quedan ahí para que una consulta suelta
        contra ``turnos`` —un informe a mano, una revisión en psql— lea el
        horario de hoy y no uno de hace tres versiones.
        """
        version = self.version_de_turno(turno_id)
        if not version:
            return
        self._execute(
            "UPDATE turnos SET dias = %s, tolerancia_min = %s "
            "WHERE empresa_id = %s AND id = %s",
            (version["dias"], version["tolerancia_min"], self.empresa, turno_id),
        )

    def cambiar_estado_turno(self, turno_id: int, activo: bool) -> bool:
        """Activa o retira de circulación un turno sin borrar su historia."""
        cursor = self._execute(
            "UPDATE turnos SET activo = %s WHERE empresa_id = %s AND id = %s",
            (activo, self.empresa, turno_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def marcar_turno_predeterminado(self, turno_id: int) -> bool:
        """Traslada la marca de predeterminado a otro turno.

        El índice único parcial admite un solo predeterminado, así que hay
        que bajar el anterior antes de levantar el nuevo.
        """
        self._execute(
            "UPDATE turnos SET predeterminado = FALSE "
            "WHERE empresa_id = %s AND predeterminado AND id <> %s",
            (self.empresa, turno_id),
        )
        cursor = self._execute(
            "UPDATE turnos SET predeterminado = TRUE "
            "WHERE empresa_id = %s AND id = %s",
            (self.empresa, turno_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def eliminar_turno(self, turno_id: int) -> None:
        """Elimina un turno; sus tramos y asignaciones caen en cascada."""
        self._execute(
            "DELETE FROM turnos WHERE empresa_id = %s AND id = %s",
            (self.empresa, turno_id),
        )
        self.connection.commit()

    def contar_personal_en_turno(self, turno_id: int) -> int:
        """Empleados activos que hoy dependen del turno, por legajo o asignación."""
        fila = self._execute(
            """
            SELECT COUNT(DISTINCT u.id) AS total
            FROM users u
            LEFT JOIN asignaciones_turno a
                   ON a.usuario_id = u.id
                  AND a.turno_id = %s
                  AND a.desde <= CURRENT_DATE
                  AND (a.hasta IS NULL OR a.hasta >= CURRENT_DATE)
            WHERE u.empresa_id = %s
              AND u.activo AND (u.turno_id = %s OR a.id IS NOT NULL)
            """,
            (turno_id, self.empresa, turno_id),
            fetch="one",
        )
        return int(fila["total"])

    def asignar_turno_base(self, user_id: int, turno_id: Optional[int]) -> bool:
        """Fija el turno de contrato del legajo (``None`` lo devuelve al predeterminado)."""
        cursor = self._execute(
            "UPDATE users SET turno_id = %s WHERE empresa_id = %s AND id = %s",
            (turno_id, self.empresa, user_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def crear_asignacion_turno(
        self,
        usuario_id: int,
        turno_id: int,
        desde: Any,
        hasta: Optional[Any],
        motivo: str,
        asignado_por: Optional[int],
    ) -> int:
        """Registra una rotación con vigencia sobre el turno de contrato."""
        cursor = self._execute(
            """
            INSERT INTO asignaciones_turno
                (usuario_id, turno_id, desde, hasta, motivo, asignado_por,
                 empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, turno_id, desde, hasta, motivo, asignado_por,
             self.empresa),
        )
        self.connection.commit()
        return cursor.fetchone()["id"]

    def listar_asignaciones_turno(
        self, usuario_id: Optional[int] = None, solo_vigentes: bool = False
    ) -> List[Dict[str, Any]]:
        """Lista rotaciones con el nombre del turno y del empleado."""
        condiciones: List[str] = ["a.empresa_id = %s"]
        params: List[Any] = [self.empresa]
        if usuario_id is not None:
            condiciones.append("a.usuario_id = %s")
            params.append(usuario_id)
        if solo_vigentes:
            condiciones.append("(a.hasta IS NULL OR a.hasta >= CURRENT_DATE)")
        filtro = f"WHERE {' AND '.join(condiciones)}"
        return self._execute(
            f"""
            SELECT a.*, t.nombre AS turno_nombre, u.full_name
            FROM asignaciones_turno a
            JOIN turnos t ON t.id = a.turno_id
            JOIN users u ON u.id = a.usuario_id
            {filtro}
            ORDER BY a.desde DESC, a.id DESC
            """,
            tuple(params),
            fetch="all",
        )

    def eliminar_asignacion_turno(self, asignacion_id: int) -> bool:
        """Revoca una rotación; el empleado vuelve a su turno de contrato."""
        cursor = self._execute(
            "DELETE FROM asignaciones_turno WHERE empresa_id = %s AND id = %s",
            (self.empresa, asignacion_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def resolver_turno(self, usuario_id: int, dia: Any) -> Optional[Dict[str, Any]]:
        """Turno que rige para un empleado en una fecha, con su procedencia.

        Prioridad: asignación vigente, turno del legajo, turno predeterminado
        de la empresa. La consulta trae las tres candidatas y se queda con la
        de mayor prioridad, de modo que la resolución no dependa de en qué
        orden las pregunte el código que llama.
        """
        fila = self._execute(
            """
            SELECT elegido.turno_id, elegido.origen
            FROM (
                (SELECT turno_id, 'asignacion' AS origen, 1 AS prioridad
                   FROM asignaciones_turno
                  WHERE empresa_id = %s AND usuario_id = %s AND desde <= %s
                    AND (hasta IS NULL OR hasta >= %s)
                  ORDER BY desde DESC, id DESC
                  LIMIT 1)
                UNION ALL
                (SELECT ct.turno_id, 'ciclo', 2
                   FROM users u
                   JOIN ciclos_rotacion c
                     ON c.id = u.ciclo_id AND c.activo
                    -- Un ciclo no describe lo que pasó antes de empezar. Sin
                    -- este corte, una fecha anterior al ancla daba un tramo
                    -- distinto para cada posición y el registro del mes
                    -- quedaba incoherente entre compañeros del mismo ciclo.
                    AND %s::date >= c.ancla
                   JOIN ciclo_turnos ct
                     ON ct.ciclo_id = c.id
                    -- FLOOR y no la división entera de PostgreSQL: con una
                    -- fecha anterior al ancla, la entera trunca hacia cero y
                    -- devolvería el tramo equivocado.
                    AND ct.orden = 1 + (
                        (u.ciclo_posicion + FLOOR(
                            (%s::date - c.ancla)::numeric / c.dias_por_tramo
                        )::int)
                        %% (SELECT COUNT(*) FROM ciclo_turnos x
                             WHERE x.ciclo_id = c.id)::int
                    )
                  WHERE u.empresa_id = %s AND u.id = %s)
                UNION ALL
                (SELECT turno_id, 'legajo', 3
                   FROM users
                  WHERE empresa_id = %s AND id = %s AND turno_id IS NOT NULL)
                UNION ALL
                (SELECT id, 'predeterminado', 4
                   FROM turnos
                  WHERE empresa_id = %s AND predeterminado AND activo)
            ) elegido
            ORDER BY elegido.prioridad
            LIMIT 1
            """,
            (self.empresa, usuario_id, dia, dia,
             dia, dia, self.empresa, usuario_id,
             self.empresa, usuario_id, self.empresa),
            fetch="one",
        )
        if not fila:
            return None
        # Con la fecha, no sin ella: acá es donde una corrección de marzo
        # dejaba de medirse contra el horario de marzo.
        turno = self.get_turno(fila["turno_id"], dia)
        if turno:
            turno["origen"] = fila["origen"]
        return turno

    def turnos_vigentes_de_la_plantilla(self, dia: Any) -> Dict[int, Dict[str, Any]]:
        """Turno que rige hoy para cada empleado activo, en una sola consulta.

        El panel necesita responder "quién está en qué turno hoy", que no es
        lo mismo que el turno de contrato: con una rotación en curso, mirar
        el legajo muestra a la persona donde ya no está. Resolverlo empleado
        por empleado serían tantas consultas como filas tenga la pantalla.
        """
        filas = self._execute(
            """
            SELECT u.id AS usuario_id,
                   COALESCE(ta.id, tc.id, tu.id, td.id) AS turno_id,
                   COALESCE(ta.nombre, tc.nombre, tu.nombre, td.nombre)
                       AS turno_nombre,
                   CASE WHEN ta.id IS NOT NULL THEN 'asignacion'
                        WHEN tc.id IS NOT NULL THEN 'ciclo'
                        WHEN tu.id IS NOT NULL THEN 'legajo'
                        ELSE 'predeterminado' END AS origen
            FROM users u
            LEFT JOIN LATERAL (
                SELECT turno_id FROM asignaciones_turno
                WHERE usuario_id = u.id AND desde <= %s
                  AND (hasta IS NULL OR hasta >= %s)
                ORDER BY desde DESC, id DESC
                LIMIT 1
            ) vigente ON TRUE
            LEFT JOIN LATERAL (
                SELECT ct.turno_id
                FROM ciclos_rotacion c
                JOIN ciclo_turnos ct
                  ON ct.ciclo_id = c.id
                 AND ct.orden = 1 + (
                     (u.ciclo_posicion + FLOOR(
                         (%s::date - c.ancla)::numeric / c.dias_por_tramo
                     )::int)
                     %% (SELECT COUNT(*) FROM ciclo_turnos x
                          WHERE x.ciclo_id = c.id)::int
                 )
                WHERE c.id = u.ciclo_id AND c.activo AND %s::date >= c.ancla
                LIMIT 1
            ) rotado ON TRUE
            LEFT JOIN turnos ta ON ta.id = vigente.turno_id
            LEFT JOIN turnos tc ON tc.id = rotado.turno_id
            LEFT JOIN turnos tu ON tu.id = u.turno_id
            LEFT JOIN turnos td
                   ON td.empresa_id = u.empresa_id
                  AND td.predeterminado AND td.activo
            WHERE u.empresa_id = %s
            """,
            (dia, dia, dia, dia, self.empresa),
            fetch="all",
        )
        return {fila["usuario_id"]: fila for fila in filas}

    # --- Ciclos de rotación -------------------------------------------------

    def listar_ciclos(self, incluir_inactivos: bool = False) -> List[Dict[str, Any]]:
        """Ciclos de rotación con sus turnos en orden y su dotación."""
        filtro = "" if incluir_inactivos else "AND c.activo"
        ciclos = self._execute(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM users u
                     WHERE u.ciclo_id = c.id AND u.activo) AS dotacion
            FROM ciclos_rotacion c
            WHERE c.empresa_id = %s {filtro}
            ORDER BY c.nombre
            """,
            (self.empresa,),
            fetch="all",
        )
        if not ciclos:
            return []
        tramos = self._execute(
            """
            SELECT ct.ciclo_id, ct.orden, ct.turno_id, t.nombre AS turno_nombre
            FROM ciclo_turnos ct
            JOIN turnos t ON t.id = ct.turno_id
            WHERE ct.empresa_id = %s AND ct.ciclo_id = ANY(%s)
            ORDER BY ct.ciclo_id, ct.orden
            """,
            (self.empresa, [c["id"] for c in ciclos]),
            fetch="all",
        )
        por_ciclo: Dict[int, List[Dict[str, Any]]] = {}
        for tramo in tramos:
            por_ciclo.setdefault(tramo["ciclo_id"], []).append(tramo)
        for ciclo in ciclos:
            ciclo["turnos"] = por_ciclo.get(ciclo["id"], [])
        return ciclos

    def get_ciclo(self, ciclo_id: int) -> Optional[Dict[str, Any]]:
        """Devuelve un ciclo con sus turnos ordenados, o ``None``."""
        ciclo = self._execute(
            "SELECT * FROM ciclos_rotacion WHERE empresa_id = %s AND id = %s",
            (self.empresa, ciclo_id),
            fetch="one",
        )
        if not ciclo:
            return None
        ciclo["turnos"] = self._execute(
            """
            SELECT ct.orden, ct.turno_id, t.nombre AS turno_nombre
            FROM ciclo_turnos ct
            JOIN turnos t ON t.id = ct.turno_id
            WHERE ct.empresa_id = %s AND ct.ciclo_id = %s
            ORDER BY ct.orden
            """,
            (self.empresa, ciclo_id),
            fetch="all",
        )
        return ciclo

    def crear_ciclo(
        self, nombre: str, turnos_ids: List[int], dias_por_tramo: int, ancla: Any
    ) -> int:
        """Da de alta un ciclo con su secuencia de turnos."""
        cursor = self._execute(
            """
            INSERT INTO ciclos_rotacion
                (nombre, dias_por_tramo, ancla, empresa_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (nombre, dias_por_tramo, ancla, self.empresa),
        )
        ciclo_id = cursor.fetchone()["id"]
        self._reemplazar_turnos_de_ciclo(ciclo_id, turnos_ids)
        self.connection.commit()
        return ciclo_id

    def _reemplazar_turnos_de_ciclo(self, ciclo_id: int, turnos_ids: List[int]) -> None:
        """Deja la secuencia del ciclo exactamente como la describe la lista."""
        self._execute(
            "DELETE FROM ciclo_turnos WHERE empresa_id = %s AND ciclo_id = %s",
            (self.empresa, ciclo_id),
        )
        for orden, turno_id in enumerate(turnos_ids, start=1):
            self._execute(
                """
                INSERT INTO ciclo_turnos (ciclo_id, orden, turno_id, empresa_id)
                VALUES (%s, %s, %s, %s)
                """,
                (ciclo_id, orden, turno_id, self.empresa),
            )

    def eliminar_ciclo(self, ciclo_id: int) -> None:
        """Elimina un ciclo; su secuencia cae en cascada."""
        self._execute(
            "DELETE FROM ciclos_rotacion WHERE empresa_id = %s AND id = %s",
            (self.empresa, ciclo_id),
        )
        self.connection.commit()

    def contar_personal_en_ciclo(self, ciclo_id: int) -> int:
        """Empleados activos que rotan con ese ciclo."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM users "
            "WHERE empresa_id = %s AND ciclo_id = %s AND activo",
            (self.empresa, ciclo_id),
            fetch="one",
        )
        return int(fila["total"])

    def asignar_ciclo(
        self, user_id: int, ciclo_id: Optional[int], posicion: int = 0
    ) -> bool:
        """Pone al empleado en un ciclo, en la posición indicada.

        La posición es lo que hace que dos personas del mismo ciclo estén
        siempre en turnos distintos y roten juntas.
        """
        cursor = self._execute(
            "UPDATE users SET ciclo_id = %s, ciclo_posicion = %s "
            "WHERE empresa_id = %s AND id = %s",
            (ciclo_id, max(0, int(posicion)), self.empresa, user_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def get_ciclo_de(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Ciclo del empleado con su posición, o ``None`` si no rota."""
        fila = self._execute(
            "SELECT ciclo_id, ciclo_posicion FROM users "
            "WHERE empresa_id = %s AND id = %s",
            (self.empresa, user_id),
            fetch="one",
        )
        if not fila or fila["ciclo_id"] is None:
            return None
        ciclo = self.get_ciclo(fila["ciclo_id"])
        if ciclo:
            ciclo["posicion"] = int(fila["ciclo_posicion"] or 0)
        return ciclo

    def listar_marcajes_desde(
        self, user_id: int, desde: datetime
    ) -> List[Dict[str, Any]]:
        """Marcajes cuya entrada cae dentro de la ventana de jornada en curso.

        Se mira la ventana y no la fecha porque un turno nocturno reparte una
        sola jornada entre dos días calendario.
        """
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE empresa_id = %s AND user_id = %s
              AND hora_entrada >= %s AND NOT abandonado
            ORDER BY hora_entrada
            """,
            (self.empresa, user_id, desde),
            fetch="all",
        )

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
                                      sync_id, empresa_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sync_id) WHERE sync_id IS NOT NULL DO NOTHING
                RETURNING id
                """,
                (user_id, hora_entrada, es_tardanza, tipo_incidencia,
                 tolerancia_aplicada, condicion_climatica, verificacion_facial,
                 sync_id, self.empresa),
            )
        else:
            cursor = self._execute(
                """
                INSERT INTO marcajes (user_id, hora_entrada, es_tardanza,
                                      tipo_incidencia, tolerancia_aplicada,
                                      condicion_climatica, verificacion_facial,
                                      empresa_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, hora_entrada, es_tardanza, tipo_incidencia,
                 tolerancia_aplicada, condicion_climatica, verificacion_facial,
                 self.empresa),
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
            WHERE empresa_id = %s AND user_id = %s AND es_tardanza = TRUE
              AND hora_entrada >= %s AND hora_entrada < %s
            """,
            (self.empresa, user_id, datetime.combine(primer_dia, time.min),
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
            WHERE empresa_id = %s AND id = %s
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
                self.empresa,
                entry_id,
            ),
        )
        self.connection.commit()

    def get_open_entry(
        self, user_id: int, antes_de: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """Retorna el marcaje abierto más reciente del usuario, si existe.

        "Más reciente" es por hora de entrada y no por orden de inserción:
        al reponer una cola offline las marcas se insertan en el orden en que
        se encolaron, que no es el orden en que ocurrieron, y una salida
        terminaba cerrando la entrada equivocada.

        ``antes_de`` acota a las jornadas que ya habían empezado en ese
        instante, que es lo que necesita esa misma reposición.

        Los abandonados no cuentan como abiertos: ya salieron del circuito de
        marcación y esperan la corrección de Recursos Humanos.
        """
        filtro = "AND hora_entrada <= %s" if antes_de is not None else ""
        parametros = (
            (self.empresa, user_id, antes_de)
            if antes_de is not None
            else (self.empresa, user_id)
        )
        return self._execute(
            f"""
            SELECT * FROM marcajes
            WHERE empresa_id = %s AND user_id = %s
              AND hora_salida IS NULL AND NOT abandonado
            {filtro}
            ORDER BY hora_entrada DESC, id DESC
            LIMIT 1
            """,
            parametros,
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
            WHERE empresa_id = %s
              AND user_id = %s
              AND hora_salida IS NULL
              AND NOT abandonado
              AND hora_entrada < %s
            RETURNING id, hora_entrada
            """,
            (incidencia, self.empresa, user_id, limite),
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
            WHERE m.empresa_id = %s AND m.abandonado AND m.hora_salida IS NULL
            ORDER BY m.hora_entrada DESC
            LIMIT %s
            """,
            (self.empresa, limite),
            fetch="all",
        )

    def get_entries_by_date(self, user_id: int, date) -> List[Dict[str, Any]]:
        """Lista los marcajes de un usuario para una fecha concreta."""
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE empresa_id = %s AND user_id = %s AND hora_entrada::date = %s
            ORDER BY hora_entrada
            """,
            (self.empresa, user_id, date),
            fetch="all",
        )

    def get_all_entries(self, user_id: int) -> List[Dict[str, Any]]:
        """Lista todos los marcajes de un usuario, del más reciente al más antiguo."""
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE empresa_id = %s AND user_id = %s
            ORDER BY hora_entrada DESC
            """,
            (self.empresa, user_id),
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
            WHERE m.empresa_id = %s
              AND m.hora_entrada >= %s AND m.hora_entrada < %s
            ORDER BY u.username, m.hora_entrada
            """,
            (self.empresa, inicio, fin),
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
                 aprobado_por, horas_usadas, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usuario_id,
                tipo_permiso,
                fecha_inicio,
                fecha_fin,
                aprobado_por,
                horas_usadas,
                self.empresa,
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
            WHERE empresa_id = %s AND usuario_id = %s
              AND aprobado_por IS NOT NULL
              AND %s BETWEEN fecha_inicio AND fecha_fin
            ORDER BY id DESC
            LIMIT 1
            """,
            (self.empresa, usuario_id, fecha),
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
        filtro = "AND j.usuario_id = %s" if usuario_id is not None else ""
        return self._execute(
            f"""
            SELECT j.*, u.username, u.full_name, a.username AS aprobador
            FROM justificaciones j
            JOIN users u ON u.id = j.usuario_id
            JOIN users a ON a.id = j.aprobado_por
            WHERE j.empresa_id = %s {filtro}
            ORDER BY j.fecha_inicio
            """,
            (self.empresa, usuario_id) if usuario_id is not None else (self.empresa,),
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
            WHERE j.empresa_id = %s AND j.id = %s
            """,
            (self.empresa, justificacion_id),
            fetch="one",
        )

    def contar_justificaciones(self) -> int:
        """Total de justificaciones emitidas, sin traerlas todas."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM justificaciones WHERE empresa_id = %s",
            (self.empresa,),
            fetch="one",
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
            INSERT INTO alertas
                (tipo, severidad, mensaje, detalle, usuario_id, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, creado_en
            """,
            (tipo, severidad, mensaje, detalle, usuario_id, self.empresa),
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
        pendientes = "AND NOT leida" if no_leidas else ""
        return self._execute(
            f"SELECT * FROM alertas WHERE empresa_id = %s {pendientes} "
            f"ORDER BY creado_en DESC LIMIT %s",
            (self.empresa, limite),
            fetch="all",
        )

    def list_solicitudes_correccion(self) -> List[Dict[str, Any]]:
        """Lista las solicitudes de corrección con datos del solicitante."""
        return self._execute(
            """
            SELECT s.*, u.username, u.full_name, r.username AS revisor
            FROM solicitudes_correccion s
            JOIN users u ON u.id = s.usuario_id
            LEFT JOIN users r ON r.id = s.revisado_por
            WHERE s.empresa_id = %s
            ORDER BY s.id DESC
            """,
            (self.empresa,),
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
            WHERE empresa_id = %s
              AND verificacion_facial <> 'Verificada'
              AND hora_entrada >= NOW() - make_interval(days => %s)
            """,
            (self.empresa, dias),
            fetch="one",
        )
        return int(fila["total"])

    def count_marcajes_hoy(self) -> int:
        """Cantidad de marcajes con entrada registrada en la fecha actual."""
        fila = self._execute(
            "SELECT COUNT(*) AS total FROM marcajes "
            "WHERE empresa_id = %s AND hora_entrada::date = CURRENT_DATE",
            (self.empresa,),
            fetch="one",
        )
        return int(fila["total"]) if fila else 0

    def get_alerta(self, alerta_id: int) -> Optional[Dict[str, Any]]:
        """Recupera una alerta puntual, para repetirla entre procesos."""
        return self._execute(
            "SELECT * FROM alertas WHERE empresa_id = %s AND id = %s",
            (self.empresa, alerta_id),
            fetch="one",
        )

    def marcar_alertas_leidas(self) -> int:
        """Marca todas las alertas como leídas y devuelve la cantidad."""
        cursor = self._execute(
            "UPDATE alertas SET leida = TRUE "
            "WHERE empresa_id = %s AND NOT leida RETURNING id",
            (self.empresa,),
        )
        self.connection.commit()
        return len(cursor.fetchall())

    def limpiar_marcajes_prueba(self, user_id: int, desde: Any, hasta: Any) -> None:
        """Helper de tests: elimina marcajes de un rango de fechas."""
        self._execute(
            "DELETE FROM marcajes WHERE empresa_id = %s AND user_id = %s "
            "AND hora_entrada::date BETWEEN %s AND %s",
            (self.empresa, user_id, desde, hasta),
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
            INSERT INTO fotos (user_id, imagen, empresa_id) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET imagen = EXCLUDED.imagen,
                                                actualizado_en = NOW()
            """,
            (user_id, psycopg2.Binary(biometria.cifrar(imagen_jpg, user_id)),
             self.empresa),
        )
        self.connection.commit()

    def get_foto(self, user_id: int) -> Optional[bytes]:
        """Retorna los bytes JPEG de la foto del usuario, si existe."""
        fila = self._execute(
            "SELECT imagen FROM fotos WHERE empresa_id = %s AND user_id = %s",
            (self.empresa, user_id),
            fetch="one",
        )
        if not fila or fila["imagen"] is None:
            return None
        return biometria.descifrar(bytes(fila["imagen"]), user_id)

    def tiene_foto(self, user_id: int) -> bool:
        """Indica si el usuario tiene una foto biométrica registrada."""
        fila = self._execute(
            "SELECT 1 AS existe FROM fotos WHERE empresa_id = %s AND user_id = %s",
            (self.empresa, user_id),
            fetch="one",
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
            "SELECT user_id, imagen FROM fotos WHERE empresa_id = %s "
            "ORDER BY user_id",
            (self.empresa,),
            fetch="all",
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
        self._execute(
            "DELETE FROM fotos WHERE empresa_id = %s AND user_id = %s",
            (self.empresa, user_id),
        )
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
            WHERE empresa_id = %s AND hora_entrada >= %s AND hora_entrada < %s
            GROUP BY user_id
            """,
            (self.empresa, inicio, fin),
            fetch="all",
        )

    def get_marcajes_rango(self, user_id: int, desde: Any, hasta: Any) -> List[Dict[str, Any]]:
        """Lista los marcajes de un empleado dentro de un rango de fechas."""
        inicio = datetime.combine(desde, time.min)
        fin = datetime.combine(hasta, time.max)
        return self._execute(
            """
            SELECT * FROM marcajes
            WHERE empresa_id = %s AND user_id = %s
              AND hora_entrada BETWEEN %s AND %s
            ORDER BY hora_entrada
            """,
            (self.empresa, user_id, inicio, fin),
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
                (usuario_id, fecha_registro, tipo_marca, hora_propuesta, motivo,
                 empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, fecha_registro, tipo_marca, hora_propuesta, motivo,
             self.empresa),
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
            WHERE s.empresa_id = %s AND s.id = %s
            """,
            (self.empresa, solicitud_id),
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
            WHERE s.empresa_id = %s
            ORDER BY (s.estado = 'Pendiente') DESC, s.fecha_registro, s.id
            """,
            (self.empresa,),
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
            WHERE empresa_id = %s AND id = %s
            """,
            (estado, revisado_por, self.empresa, solicitud_id),
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
                (fecha, condicion, tolerancia_min, nota, declarado_por, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (empresa_id, fecha) DO UPDATE SET
                condicion = EXCLUDED.condicion,
                tolerancia_min = EXCLUDED.tolerancia_min,
                nota = EXCLUDED.nota,
                declarado_por = EXCLUDED.declarado_por,
                creado_en = NOW()
            RETURNING *
            """,
            (fecha, condicion, tolerancia_min, nota, declarado_por, self.empresa),
        )
        self.connection.commit()
        return cursor.fetchone()

    def get_condicion_dia(self, fecha: Any) -> Optional[Dict[str, Any]]:
        """Condición declarada para una fecha, o ``None`` si el día es normal."""
        return self._execute(
            "SELECT * FROM condiciones_dia WHERE empresa_id = %s AND fecha = %s",
            (self.empresa, fecha),
            fetch="one",
        )

    def listar_condiciones_dia(self, limite: int = 30) -> List[Dict[str, Any]]:
        """Últimas condiciones declaradas, con el nombre de quien las firmó."""
        return self._execute(
            """
            SELECT c.*, u.full_name AS declarante
            FROM condiciones_dia c
            JOIN users u ON u.id = c.declarado_por
            WHERE c.empresa_id = %s
            ORDER BY c.fecha DESC
            LIMIT %s
            """,
            (self.empresa, limite),
            fetch="all",
        )

    def borrar_condicion_dia(self, fecha: Any) -> bool:
        """Revoca la condición de un día; el día vuelve a ser normal."""
        cursor = self._execute(
            "DELETE FROM condiciones_dia WHERE empresa_id = %s AND fecha = %s",
            (self.empresa, fecha),
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
                 horas_solicitadas, motivo, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (usuario_id, tipo_permiso, fecha_inicio, fecha_fin,
             horas_solicitadas, motivo, self.empresa),
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
            WHERE s.empresa_id = %s AND s.id = %s
            """,
            (self.empresa, solicitud_id),
            fetch="one",
        )

    def listar_solicitudes_permiso(
        self, usuario_id: Optional[int] = None, solo_pendientes: bool = False
    ) -> List[Dict[str, Any]]:
        """Bandeja de solicitudes: del empleado indicado o de toda la plantilla.

        Las pendientes encabezan la lista porque son las accionables; el
        resto queda por fecha de pedido descendente.
        """
        condiciones: List[str] = ["s.empresa_id = %s"]
        parametros: List[Any] = [self.empresa]
        if usuario_id is not None:
            condiciones.append("s.usuario_id = %s")
            parametros.append(usuario_id)
        if solo_pendientes:
            condiciones.append("s.estado = 'Pendiente'")
        filtro = f"WHERE {' AND '.join(condiciones)}"
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
            tuple(parametros),
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
            WHERE empresa_id = %s AND id = %s AND estado = 'Pendiente'
            """,
            (estado, resuelto_por, observacion, justificacion_id,
             self.empresa, solicitud_id),
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
                (user_id, hora_entrada, es_tardanza, es_feriado, empresa_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, hora_entrada, es_tardanza, es_feriado, self.empresa),
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
            WHERE empresa_id = %s AND id = %s
            """,
            (hora_entrada, es_tardanza, tipo_incidencia, self.empresa, entry_id),
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
            WHERE empresa_id = %s
              AND es_tardanza = TRUE
              AND hora_entrada >= %s AND hora_entrada < %s
            GROUP BY DATE(hora_entrada AT TIME ZONE 'America/Asuncion')
            ORDER BY fecha
            """,
            (
                self.empresa,
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
            WHERE m.empresa_id = %s
            GROUP BY u.departamento
            ORDER BY (EXTRACT(EPOCH FROM SUM(m.horas_extra_50)) +
                      EXTRACT(EPOCH FROM SUM(m.horas_extra_100))) DESC
            """,
            (self.empresa,),
            fetch="all",
        )
        return [dict(fila) for fila in cursor]

    def get_proyeccion_aguinaldos(self) -> List[Dict[str, Any]]:
        """Salarios y departamento de cada empleado para proyectar aguinaldos."""
        cursor = self._execute(
            """
            SELECT id, full_name, departamento, salario_mensual
            FROM users
            WHERE empresa_id = %s AND salario_mensual > 0
            ORDER BY departamento, full_name
            """,
            (self.empresa,),
            fetch="all",
        )
        return [dict(fila) for fila in cursor]