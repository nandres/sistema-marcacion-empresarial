# Autoservicio de Permisos y Formularios

> Implementado el **2026-09-14**. El empleado pide sus permisos desde el portal y los documentos se emiten solos desde los datos que el sistema ya tiene. Ningún formulario se llena a mano y ninguno se transcribe.

## El problema que resolvía el papel

Hasta acá el circuito era: el empleado consultaba el reglamento (un PDF aparte), llenaba una nota, la llevaba a Recursos Humanos, alguien verificaba a mano si le quedaba cuota, y después la cargaba en el sistema. Cuatro pasos, tres de los cuales son transcripción, y uno —la verificación de cuota— es aritmética que la base ya sabe hacer.

El dato duro: `reglamento.py` codifica **32 artículos** con su cuota, su unidad, su período de cómputo y sus condiciones. Todo eso estaba disponible para RRHH y era invisible para el empleado.

## Lo que el empleado ve ahora

**El catálogo es su catálogo.** `GET /api/permisos/catalogo` devuelve solo los artículos que aplican a su vínculo —15 para Funcionario bajo la Res. 1307/2010, 17 para Pasante bajo la Res. 3028/2024— cada uno con el saldo real y las condiciones que exige. Un pasante no ve el Art. 34 y un funcionario no ve el Art. 25.

**El formulario se adapta al artículo.** Elegir *Salidas por motivos personales* (Art. 18) descubre el campo de horas y bloquea la fecha de fin, porque un permiso por horas se toma en un solo día. Elegir *Vacaciones* (Art. 29) lo esconde. Las condiciones del artículo se muestran ahí mismo, que es el momento en que el empleado puede hacer algo con ellas.

**El pedido se valida antes de enviarse.** Artículo aplicable al vínculo, coherencia de fechas, unidad de medida correcta, motivo de al menos diez caracteres y cuota disponible. A la bandeja de RRHH solo llegan pedidos que cumplen el artículo invocado: aprobar deja de ser control aritmético y pasa a ser una decisión.

**El futuro es el caso normal.** `crear_justificacion` prohibía fechas posteriores a hoy, porque nació para registrar ausencias ya ocurridas. Un permiso se pide antes de tomarlo, así que la aprobación de una solicitud levanta ese tope con `permitir_futuro=True`. La carga directa de RRHH lo conserva.

### La cuota reservada

El detalle que hace que el circuito no se pueda explotar: **una solicitud pendiente ya compromete la cuota**. Sin eso, diez pedidos del mismo artículo pasaban todos, porque ninguno había llegado a consumir nada todavía.

`disponibilidad_permisos` distingue ahora tres cantidades:

| Campo | Significa |
| --- | --- |
| `usados` | Consumido por justificaciones ya emitidas |
| `pendientes` | Comprometido por solicitudes sin resolver |
| `restantes_efectivos` | Lo que queda de verdad para pedir |

Un pedido rechazado libera lo reservado sin haber consumido nada.

## Lo que ve Recursos Humanos

La bandeja abre con los **pedidos de permiso** primero, después las correcciones de marcaje, y al pie el estado del día. Aprobar emite la justificación oficial y su PDF sin ningún paso adicional; rechazar exige un motivo de al menos diez caracteres, que viaja con el pedido y queda visible para el empleado.

La cuota se vuelve a verificar en el momento de aprobar y no en el del pedido, porque entre uno y otro pudo aprobarse otra solicitud. Una solicitud ya resuelta no se puede volver a resolver: el `UPDATE` filtra por estado `Pendiente`, así que dos revisores simultáneos no aprueban dos veces el mismo pedido.

El mismo circuito existe en el escritorio, en la sección **02 · Pedidos de permiso** del panel de gestión.

## Formularios que se emiten solos

| Documento | De dónde sale | Quién lo baja |
| --- | --- | --- |
| **Planilla de horas extraordinarias** | Marcajes liquidados del mes | Empleado y RRHH |
| **Constancia de asistencia** | Marcajes del rango consultado | Empleado |
| **Permiso aprobado** | Justificación emitida | Empleado y RRHH |
| **Comprobante de marcación** | La marca, firmado con HMAC-SHA256 | Kiosco |
| **Planilla mensual de asistencia** (xlsx/csv) | Marcajes del mes | RRHH |

### Planilla de horas extraordinarias

Cita los artículos **232** (recargo nocturno del 30 %), **233** (domingo y feriado al 100 %) y **234** (extras diurnas al 50 %), lista día por día solo los turnos con recargo —una planilla de extras que muestra los veinte días normales esconde los cuatro que importan—, cierra con el total y liquida en guaraníes sobre el valor hora del legajo.

Del nocturno liquida **solo el recargo**, no la hora: esa hora ya está pagada como ordinaria o como extra en su propia línea. Sumarla dos veces es el error clásico de la planilla hecha a mano, y el de la primera versión de esta —`RECARGO_NOCTURNO` es la tasa `0.30`, no el multiplicador `1.30`, y restarle uno daba un importe negativo. Lo detectó `tests/test_planilla_extras.py` antes de que saliera de la máquina.

Un mes sin extras emite igual su documento: es lo que hay que archivar para dejar constancia de que no hubo horas extraordinarias.

### Constancia de asistencia

El papel que el empleado terminaba pidiendo en ventanilla para un banco o un trámite. Sale del rango que está mirando en el historial, con días registrados, jornadas completas, tardanzas, horas efectivas y permisos del período, y lleva su propio sello SHA-256 para que quien la recibe pueda contrastarla.

## Superficie nueva de API

```
GET    /api/permisos/catalogo              artículos aplicables con saldo real
POST   /api/permisos/solicitar             presenta un pedido validado
GET    /api/permisos/solicitudes           los pedidos propios y su estado
GET    /api/horas-extra/pdf                planilla propia del mes
GET    /api/constancia/pdf                 constancia del rango indicado
GET    /api/condicion-hoy                  condición declarada (sin datos personales)

GET    /api/panel/solicitudes-permiso      bandeja de RRHH
POST   /api/panel/solicitudes-permiso/{id}/resolver
GET    /api/panel/condiciones              condiciones declaradas
POST   /api/panel/condiciones              declarar una condición
DELETE /api/panel/condiciones/{fecha}      revocarla
GET    /api/panel/horas-extra/{id}/pdf     planilla de cualquier empleado
```

## Modelo de datos agregado

**`solicitudes_permiso`** — pedido, artículo invocado, período, horas, motivo, estado, observación del revisor y el `justificacion_id` que lo materializa. Atar la justificación a la solicitud es lo que permite que el PDF del permiso aparezca en el portal del empleado sin ningún paso extra.

**`condiciones_dia`** — ver [[Antifraude y Resiliencia en Picos de Marcación]] y el hallazgo P1-1 en [[Auditoría Técnica · Hallazgos Críticos]].

**`marcajes.verificacion_facial`** — el veredicto biométrico se guarda con la marca en lugar de descartarse. Una marca verificada no es lo mismo que una que el motor no pudo comprobar, y RRHH necesita distinguirlas: las ve contadas en su bandeja.

## Enlaces

[[Catálogo de Permisos y Licencias]] · [[Reglamento de Asistencia y Disciplina]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Antifraude y Resiliencia en Picos de Marcación]] · [[Sistema de Diseño · Planilla]] · [[Módulo de Justificaciones y Aguinaldos]]
