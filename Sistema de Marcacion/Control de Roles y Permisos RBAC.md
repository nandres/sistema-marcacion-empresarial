# Control de Roles y Permisos RBAC

> Diseño del control de accesos basado en roles (RBAC) del [[Ecosistema Sistema de Marcación]]. Se une al tejido junto a [[Módulo de Gestión de Usuarios]].

## Modelo de roles

La tabla `roles` define tres roles semilla que se insertan automáticamente al inicializar la base de datos:

| id | nombre | Alcance |
| --- | --- | --- |
| 1 | Administrador | Control total: crear, editar y eliminar todo |
| 2 | Recursos Humanos | Gestiona usuarios (crear/editar) pero **no** puede eliminarlos |
| 3 | Empleado | Solo puede registrar marcas (entrada/salida) |

```mermaid
flowchart TD
    A[Administrador] -->|CRUD total| U[Usuarios]
    A -->|CRUD| M[Marcajes]
    R[Recursos Humanos] -->|Crear / Editar| U
    R -.->|X Eliminar| U
    E[Empleado] -->|Marcar entrada/salida| M
```

## Matriz de permisos

| Acción | Administrador | Recursos Humanos | Empleado |
| --- | :-: | :-: | :-: |
| Crear usuario | ✔ | ✔ | ✖ |
| Editar usuario | ✔ | ✔ | ✖ |
| Eliminar usuario | ✔ | ✖ | ✖ |
| Asignar rol Administrador | ✔ | ✖ | ✖ |
| Registrar marcas | ✔ | ✔ | ✔ |
| Consultar registros propios | ✔ | ✔ | ✔ |

## Implementación

- `src/auth.py` expone `require_role(db, user, roles_permitidos)` que valida el rol **antes** de tocar la base de datos y lanza `PermissionError` si no está autorizado.
- Constantes: `ROLE_ADMIN`, `ROLE_RRHH`, `ROLE_EMPLEADO`.
- Endurecimiento: solo un Administrador puede crear/editar a otro Administrador (evita escalada de privilegios desde RRHH).
- `src/app.py` oculta las opciones del menú según el rol del usuario conectado; la validación real ocurre en `auth.py`.

## Relación con el esquema

`users.role_id` → `roles.id` (llave foránea). El rol viaja con el usuario autenticado vía `role_name` en las consultas `JOIN` de `src/database.py`.