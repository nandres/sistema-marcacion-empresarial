"""El esquema de PostgreSQL: cómo se crea, cómo se pone al día y en qué va.

Vive aparte de las consultas por un motivo operativo antes que estético. El
DDL toma locks exclusivos de tabla y se aplica una sola vez, en el despliegue,
con el rol administrador; las consultas corren miles de veces por hora con un
rol que no puede —ni debe— alterar nada. Son dos cosas con vidas distintas y
permisos distintos, y estaban en el mismo archivo solo porque crecieron juntas.

Nada de acá pasa por ``Database``: estas funciones reciben una conexión suelta,
porque el esquema se consulta y se aplica **antes** de que haya una empresa
activa, que es lo primero que aquel objeto exige.

    python src/migrate.py            # aplica
    python src/migrate.py estado     # informa
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

import registro
import reglamento
import turnos as turnos_dominio

_log = registro.obtener("esquema")

_TIPOS_SQL: str = ", ".join(f"'{tipo}'" for tipo in reglamento.TIPOS_PERMISO_CHECK)
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


def _consultar(conexion: Any, sql: str, params: Tuple[Any, ...] = (),
               varias: bool = False) -> Any:
    """Lectura suelta del catálogo, sin pasar por el objeto de datos.

    El esquema se consulta antes de que haya empresa activa —de hecho, antes
    de que haya empresa— así que no puede usar el camino normal, que exige una.
    """
    cursor = conexion.cursor(cursor_factory=RealDictCursor)
    cursor.execute(sql, params)
    return cursor.fetchall() if varias else cursor.fetchone()


def aplicar(conexion: Any) -> None:
    """Crea o pone al día el esquema completo sobre una conexión abierta.

    Es un paso de despliegue, no de arranque: el DDL toma locks exclusivos de
    tabla, así que cuatro procesos aplicándolo a la vez se bloquean entre sí.
    Quien llama se ocupa de que la base exista y la conexión esté abierta.
    """
    cursor = conexion.cursor()
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
    empresa_base = _aplicar_arrendamiento(cursor)
    _sembrar_turno_predeterminado(cursor, empresa_base)
    _aplicar_politicas_rls(cursor)
    anotar_migraciones(cursor)
    conexion.commit()


def anotar_migraciones(cursor: Any) -> List[str]:
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


def _aplicar_arrendamiento(cursor: Any) -> int:
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


def _sembrar_turno_predeterminado(cursor: Any, empresa_id: int) -> None:
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


def _aplicar_politicas_rls(cursor: Any) -> None:
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


def crear_rol_de_aplicacion(
    conexion: Any, base: str, nombre: str,
    password: Optional[str] = None,
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
    cursor = conexion.cursor()
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
    conexion.commit()
    return password is not None


def rls_efectiva(conexion: Any) -> Dict[str, Any]:
    """Informa si las políticas de la base realmente alcanzan a esta conexión.

    Un superusuario las esquiva por diseño de PostgreSQL. Decirlo es
    parte del control: una política instalada pero inerte se parece
    demasiado a una que protege.
    """
    fila = _consultar(
        conexion,
        """
        SELECT rolsuper, rolbypassrls, current_user AS rol
        FROM pg_roles WHERE rolname = current_user
        """,
    )
    politicas = _consultar(
        conexion,
        "SELECT COUNT(*) AS total FROM pg_policies WHERE schemaname = 'public'",
    )
    esquiva = bool(fila["rolsuper"] or fila["rolbypassrls"])
    return {
        "rol": fila["rol"],
        "politicas": int(politicas["total"]),
        "esquiva": esquiva,
        "activa": not esquiva and int(politicas["total"]) > 0,
    }


def existe_tabla(conexion: Any, nombre: str) -> bool:
    """``to_regclass`` devuelve NULL en vez de fallar si la tabla no está."""
    fila = _consultar(
        conexion, "SELECT to_regclass(%s) AS referencia", (f"public.{nombre}",)
    )
    return bool(fila and fila["referencia"])


def version(conexion: Any) -> Optional[int]:
    """Versión del esquema de esta base, o ``None`` si nunca se migró.

    Es la respuesta a *¿en qué versión está el cliente que acaba de
    llamar?*, que sin un sello en la propia base solo se podía contestar
    mirando el código que alguien cree que le instaló.
    """
    if not existe_tabla(conexion, TABLA_MIGRACIONES):
        return None
    filas = _consultar(
        conexion,
        f"SELECT nombre FROM {TABLA_MIGRACIONES} WHERE nombre LIKE 'base:%%'",
        varias=True,
    ) or []
    versiones = [
        int(fila["nombre"].split(":", 1)[-1])
        for fila in filas
        if fila["nombre"].split(":", 1)[-1].isdigit()
    ]
    return max(versiones) if versiones else None


def pendientes(conexion: Any) -> List[str]:
    """Lo que le falta a esta base para atender tráfico con este código.

    Devuelve los nombres y no un booleano a propósito: el mensaje que lee
    quien despliega dice **qué** falta, no solo que algo falta.
    """
    sello = f"base:{ESQUEMA_VERSION}"
    if not existe_tabla(conexion, TABLA_MIGRACIONES):
        return [sello] + [nombre for nombre, _ in PASOS_UNICOS]
    filas = _consultar(
        conexion, f"SELECT nombre FROM {TABLA_MIGRACIONES}", varias=True
    ) or []
    hechos = {fila["nombre"] for fila in filas}
    pendientes = [n for n, _ in PASOS_UNICOS if n not in hechos]
    if sello not in hechos:
        pendientes.insert(0, sello)
    return pendientes


def historial(conexion: Any) -> List[Dict[str, Any]]:
    """Qué se aplicó sobre esta base y cuándo, en orden."""
    if not existe_tabla(conexion, TABLA_MIGRACIONES):
        return []
    return _consultar(
        conexion,
        f"SELECT nombre, aplicada_en FROM {TABLA_MIGRACIONES} "
        f"ORDER BY aplicada_en, nombre",
        varias=True,
    ) or []


def esta_al_dia(conexion: Any) -> bool:
    """Indica si esta base puede atender tráfico con el código actual.

    Antes se contaban tablas contra una lista escrita a mano, y la lista
    envejeció: cuando el esquema sumó ``dispositivos``, una base migrada de
    antes seguía dando el recuento por bueno y el servidor arrancaba sin la
    tabla que ``/api/marcar`` necesita. El fallo no aparecía al arrancar,
    que es cuando se puede corregir, sino en la primera marcación.

    El sello dice lo mismo sin depender de que alguien se acuerde de
    actualizar una lista cada vez que agrega una tabla.
    """
    return not pendientes(conexion)
