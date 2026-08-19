# Módulo de Gestión de Usuarios

> Diseño del módulo de usuarios sobre PostgreSQL, parte del [[Ecosistema Sistema de Marcación]]. Complementa a [[Control de Roles y Permisos RBAC]] y alimenta [[Panel de Reportes y Auditoría]].

## Esquema relacional en PostgreSQL

```mermaid
erDiagram
    ROLES ||--o{ USERS : "asigna"
    USERS ||--o{ MARCAJES : "registra"
    USERS ||--o{ LOGS : "audita"

    ROLES {
        serial id PK
        varchar nombre "UNIQUE"
    }

    USERS {
        serial id PK
        varchar username "UNIQUE"
        text password_hash "bcrypt"
        varchar full_name
        int role_id FK
        timestamptz created_at "DEFAULT NOW()"
    }

    MARCAJES {
        serial id PK
        int user_id FK "ON DELETE CASCADE"
        timestamptz hora_entrada
        timestamptz hora_salida
        boolean es_feriado "DEFAULT FALSE"
        boolean es_tardanza "DEFAULT FALSE"
        interval horas_ordinarias
        interval horas_extra_50 "diurno 50%"
        interval horas_extra_100 "nocturno/feriados 100%"
        timestamptz created_at "DEFAULT NOW()"
    }

    LOGS_AUDITORIA {
        serial id PK
        int usuario_id FK
        varchar accion "CREAR/ACTUALIZAR/ELIMINAR"
        varchar tabla
        int registro_id
        jsonb valores_anteriores
        jsonb valores_nuevos
        timestamptz creado_en "DEFAULT NOW()"
    }
```

## Seguridad de contraseñas (bcrypt)

```python
def hash_password(password: str) -> str:
    """Encripta la contraseña con bcrypt y retorna el hash en texto seguro."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    """Verifica la contraseña contra un hash bcrypt almacenado."""
    return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
```

- bcrypt embebe la **sal aleatoria** en el propio hash; no hay que guardarla aparte.
- Los hashes **nunca** se almacenan en texto plano en PostgreSQL.
- Los valores sensibles se excluyen de los snapshots de auditoría.

## Auditoría de operaciones

Cada operación de RRHH/Administrador queda registrada en `logs_auditoria`:

| Operación | Acción registrada | Valores |
| --- | --- | --- |
| Crear usuario | `CREAR` | nuevos |
| Editar usuario | `ACTUALIZAR` | anterior + nuevos |
| Eliminar usuario | `ELIMINAR` | anteriores |

```python
db.registrar_auditoria(
    actor["id"],              # quién lo hizo
    "ACTUALIZAR",             # qué cambió
    "users",
    user_id,                  # registro afectado
    anterior=_valores_auditoria(target),   # valor previo
    nuevos=_valores_auditoria(actualizado) # valor posterior
)
```

## Operaciones por rol (RBAC)

| Operación | Función (`src/auth.py`) | Roles permitidos |
| --- | --- | --- |
| Crear usuario | `create_user` | Administrador, RRHH |
| Editar usuario | `update_user` | Administrador, RRHH |
| Eliminar usuario | `delete_user` | Administrador |
| Asignar rol Administrador | `create_user` / `update_user` | Solo Administrador |

## Conexión

Las credenciales se leen del archivo `.env` (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`), cargado por `src/database.py` y bloqueado por `.gitignore`. Si la base `marcacion` no existe y el usuario tiene permisos, el script la crea automáticamente.

## Notas relacionadas

- [[Motor de Reglas de Horas Extra]] — desglose legal que se persiste en `marcajes`.
- [[Panel de Reportes y Auditoría]] — exportación mensual y trazabilidad.