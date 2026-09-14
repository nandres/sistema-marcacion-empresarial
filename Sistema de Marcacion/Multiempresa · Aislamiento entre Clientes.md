# Multiempresa · Aislamiento entre Clientes

> Cómo una sola instalación aloja a varios clientes sin que ninguno vea los datos del otro. Implementado el **2026-09-14**.

## Lo que había antes

Una instalación por cliente. Funcionaba, pero diez clientes eran diez despliegues, diez bases, diez migraciones y diez ventanas de mantenimiento. El problema no era técnico sino de costo de operación, y crecía en línea recta con las ventas.

## El modelo

La **empresa** es el arrendatario. Cada fila de datos de cliente le pertenece a una y solo a una.

```
empresas           slug · razón social · RUC · activa
<tabla>.empresa_id NOT NULL, FK con ON DELETE CASCADE, índice propio
```

Doce tablas la llevan: `users`, `marcajes`, `justificaciones`, `alertas`, `fotos`, `solicitudes_correccion`, `condiciones_dia`, `solicitudes_permiso`, `turnos`, `turno_tramos`, `asignaciones_turno` y `logs_auditoria`. Los **roles** no: son el mismo catálogo para todos.

Lo que era único en toda la base pasó a serlo dentro de la empresa: el nombre de usuario, el nombre del turno, el turno predeterminado y la condición declarada de un día. Dos clientes pueden tener su turno "Mañana" y la misma persona puede trabajar en los dos.

## Las tres capas que lo sostienen

Una sola consulta sin acotar basta para mostrarle a un cliente la planilla de otro. Son más de setenta consultas, así que el aislamiento no puede depender de que nadie se olvide.

### 1. La conexión falla cerrado

`Database` lleva la empresa activa. Toda consulta de datos de cliente la interpola; si nadie la fijó, **el acceso falla**:

```python
@property
def empresa(self) -> int:
    if self.empresa_id is None:
        raise SinEmpresa(...)
    return self.empresa_id
```

La alternativa —devolver las filas de todos los clientes— es exactamente la falla que este error existe para impedir. Un `SinEmpresa` en producción es un defecto; una consulta sin acotar que devuelve datos es una fuga.

> [!note] Lo atajó apenas se encendió
> `reports.generar_pdf_permiso` abría su propia conexión para componer el PDF. Con una sola empresa eso no se notaba; con el guardián puesto, falló al primer intento. Ahora recibe la conexión del llamador, que es la que sabe de qué cliente es el permiso.

### 2. El verificador estático

`tests/guardia_arrendamiento.py` lee `database.py` con el AST y falla si algún método toca una tabla de empresa sin nombrar `empresa_id`. Las excepciones están declaradas una por una con su motivo.

La primera versión aceptaba un atajo: filtrar por la clave del padre (`user_id`, `turno_id`) parecía suficiente, porque un marcaje pertenece a la empresa de su empleado. **No lo es**: el identificador llega por URL en buena parte de la API, así que Recursos Humanos de un cliente podría editar el legajo de otro probando números. Se quitó el atajo y se acotaron las 56 consultas que lo usaban.

### 3. La prueba de aislamiento

`tests/test_multiempresa.py` aloja dos empresas con los datos **deliberadamente superpuestos** —la misma cédula, el mismo nombre de turno, la misma fecha con condición declarada— e intenta cruzar por todos los caminos que existen: listados, búsquedas por id, ediciones, borrados, login, token forjado, bus de alertas y los informes.

No comprueba que el aislamiento esté implementado; comprueba que **no se pueda cruzar**.

## El acceso

La pantalla de login no sabe de qué cliente es quien escribe, así que la cédula se busca en todas las empresas y **la contraseña decide**: se compara contra cada candidato y entra el que coincide.

El orden importa. Preguntar primero "¿en qué empresa estás?" le diría a cualquiera en qué clientes existe una cédula sin necesidad de saber su clave. Con este orden, no se aprende nada sin las credenciales completas.

El campo de empresa existe pero va plegado bajo *"Trabajo en más de una empresa"*: solo hace falta cuando la misma persona trabaja en dos de las alojadas **y** usa la misma contraseña en las dos.

Hecho el login, la empresa viaja **firmada dentro del token** (claim `emp`) y no se vuelve a preguntar. Un token con el usuario de una empresa y la empresa de otra no resuelve a nadie: la búsqueda del propio usuario ya está acotada.

## El bus de alertas

Es un objeto en memoria compartido por todo el proceso, así que no lo protege ninguna consulta. Cada alerta viaja con su `empresa_id` y tanto el WebSocket como la campana del escritorio comprueban la frontera antes que el destinatario. Una alerta sin empresa —la base estaba caída al registrarla— no se entrega a nadie: mostrarla a todos sería peor que perderla.

## Alojar un cliente

Es un acto de instalación, no una pantalla del producto:

```bash
python src/app.py alta-empresa
```

Pide nombre corto, razón social y RUC, y enseguida crea el primer administrador de esa empresa. El turno inicial se siembra en el alta y no en la migración: el esquema se aplica una vez y las empresas se alojan cuando se venden, así que la segunda nacería sin ningún horario contra el cual medir una tardanza.

**No existe ninguna sesión que pueda ver dos clientes a la vez**, y ese es justamente el aislamiento que se ofrece. No hay un rol de plataforma, ni una pantalla que liste los clientes de otros, ni una forma de cambiar de empresa sin volver a autenticarse.

## Operación

| Situación | Cómo se resuelve |
| --- | --- |
| Alojar un cliente nuevo | `python src/app.py alta-empresa` |
| Administrar por consola con varios alojados | El menú pregunta cuál, o se fija con `EMPRESA_ACTIVA` |
| Kiosco de escritorio en la sede de un cliente | `EMPRESA_ACTIVA=<slug>` en su `.env` |
| Suspender un cliente que dejó de pagar | `cambiar_estado_empresa`: no entra nadie y no se borra un solo dato |
| Dar de baja un cliente | Borrar la empresa arrastra todo en cascada, auditoría incluida |

En la web, la empresa activa va sellada en el membrete junto al nombre. Con varios clientes alojados no es un adorno: es lo que evita dar de baja al empleado del cliente equivocado.

## Lo que falta

- **Aislamiento en la base misma (RLS)**. Hoy lo garantiza la aplicación, con el verificador estático y la prueba de dos empresas como red. PostgreSQL puede imponerlo con *row-level security* y una política por tabla, que resiste incluso a una consulta mal escrita. El obstáculo concreto: la búsqueda de credenciales cruza empresas a propósito, así que necesitaría una función `SECURITY DEFINER` propia; y las políticas son inertes si el proceso conecta como superusuario, que es como conecta hoy. **Una política que no se aplica es peor que no tenerla**, porque parece protección, así que se documenta el paso en lugar de dejarlo a medias.
- **Subdominio por cliente** (`acme.miapp.com`). Hoy la empresa la resuelve la contraseña; un subdominio la resolvería antes y dejaría el campo plegado sin razón de existir.
- **Cuotas por cliente** (cantidad de empleados, retención de datos). La tabla `empresas` tiene dónde ponerlas, pero nada las mira.
- **Facturación**. Fuera del alcance del producto.

## Enlaces

[[Puesta en Marcha en un Cliente]] · [[Arquitectura Objetivo · Plataforma y Portal del Empleado]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Control de Roles y Permisos RBAC]] · [[Turnos y Rotación de Horarios]] · [[Despliegue en la Nube e Infraestructura SaaS]]
