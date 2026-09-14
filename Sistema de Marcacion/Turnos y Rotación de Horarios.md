# Turnos y Rotación de Horarios

> Cómo el sistema modela **a qué hora entra cada persona**. Cierra P2-5 y P2-4 de [[Auditoría Técnica · Hallazgos Críticos]]. Implementado el **2026-09-14**.

## El problema que resolvía

La hora de entrada era una constante del proceso: `INICIO_JORNADA`, leída de `JORNADA_INICIO` **al importar el módulo**, antes de que `load_dotenv()` llegara a correr. Tres consecuencias, de menor a mayor:

1. El valor del `.env` se ignoraba en silencio. Estaba latente porque coincidía con el predeterminado.
2. Toda la empresa compartía una sola hora de entrada.
3. **No existía la entidad**: sin turnos no hay turnos rotativos, ni jornada partida, ni horarios por sucursal. Una empresa con dos turnos no podía usar el sistema.

## El modelo

Un turno es una lista ordenada de **tramos**, una máscara de días y, si hace falta, una tolerancia propia.

```
turnos             nombre · sucursal · dias (7 caracteres 0/1) · tolerancia_min · predeterminado
turno_tramos       orden · hora_entrada · hora_salida
asignaciones_turno usuario · turno · desde · hasta · motivo · asignado_por
users.turno_id     el turno de contrato del legajo
```

La máscara empieza el lunes, como `date.weekday()`. `1111100` es Lun a Vie; `0111110`, Mar a Sáb.

Un tramo que cruza la medianoche tiene que ser el último del turno: si cruzara uno del medio, el siguiente empezaría antes de que termine el anterior. `construir_tramos` rechaza eso, los tramos superpuestos y los de duración nula.

### Por qué tramos y no un horario de corte

La jornada partida del comercio (07:00–11:00 y 14:00–18:00) son **cuatro marcas** al día, no dos con un descuento de almuerzo. Descontar la pausa de un único par entrada–salida da el mismo total de horas pero pierde el dato de si la persona volvió a la hora, que es justamente lo que un control de asistencia tiene que registrar.

## Quién trabaja qué turno

La resolución tiene tres niveles y siempre devuelve algo:

| Prioridad | Origen | Para qué sirve |
| --- | --- | --- |
| 1 | **Asignación vigente** en esa fecha | Rotar sin tocar el legajo |
| 2 | **Turno del legajo** | El horario de contrato |
| 3 | **Turno predeterminado** de la empresa | Quien todavía no tiene turno propio |

La rotación tiene vigencia (`desde`, `hasta`): al vencer, el empleado **vuelve solo** a su turno de contrato. Nadie tiene que acordarse de deshacer el cambio, que es donde estos sistemas suelen acumular gente en el turno equivocado.

Si la cadena se agota —base recién migrada, o un legajo que apunta a un turno borrado— cae a un turno de respaldo de 8 horas. Quedarse sin marcar por un hueco de configuración es peor que medirse contra la jornada administrativa.

## Qué cambió con la entidad

### La tardanza se mide contra el turno

`evaluar_asistencia` ya no compara contra una constante sino contra la **entrada prevista del tramo que toca cubrir**. Marcar 06:40 es tardanza en el turno de mañana y es llegar cuatro horas antes en el de tarde.

La tolerancia propia del turno, si está declarada, **desplaza** a la del vínculo: el horario de atención al público no admite la misma gracia que una oficina.

### Los días de franco dejaron de ser ausencias

Quien tiene turno de martes a sábado ya no figura ausente los lunes: el registro del mes muestra **Franco**. Es una diferencia que se paga: una ausencia injustificada descuenta.

### P2-4: una sola definición de llegada tardía

Había dos. `es_tardanza()` aplicaba una gracia de 10 minutos y `evaluar_asistencia()` de 15. El flujo de marcación usaba la segunda y la corrección aprobada por RRHH, la primera: un funcionario que marcaba 08:12 era *Normal* al marcar y *Llegada Tardía* si le corregían la marca **a esa misma hora**. La corrección castigaba por corregir.

Ahora `es_tardanza` delega en `evaluar_asistencia`. Una regla, un veredicto.

### La salida anticipada se mide contra lo pactado

Antes se comparaba contra el tope legal de la jornada. Cerrar el tramo de la mañana de una jornada partida a las cuatro horas —que es exactamente lo acordado— se reprochaba como salida anticipada por no llegar a ocho. La referencia ahora es `min(tope legal, duración del tramo)`.

### Las horas que rinde el turno

`horas_previstas_legales` no devuelve la duración del horario sino lo que de ella es jornada **ordinaria** según el Art. 194. Un turno de 22:00 a 06:00 dura ocho horas y rinde siete: el tope nocturno son siete y la octava ya es extraordinaria. Eso es lo que reconoce una justificación aprobada, de modo que justificar una ausencia no pague una hora extra.

## Dónde empieza una jornada

Decidir si una marca abre una jornada nueva o cierra la anterior no se puede hacer por fecha —el turno nocturno reparte una jornada entre dos días— ni con una ventana fija hacia atrás. Se probaron las dos y las dos fallan:

| Regla | Dónde se rompe |
| --- | --- |
| Día calendario | El nocturno que entra lunes 22:00 no tiene marcas del martes |
| Ventana fija de 18 h | La entrada de anoche a las 22:00 sigue contando al fichar hoy a la misma hora |
| Ancla en la hora prevista del turno | Falla con quien trabaja lejos de su horario |

La regla que quedó mira el **descanso**: se camina hacia atrás desde la marca y se corta en el primer hueco que constituya un descanso entre jornadas. El umbral de 8 horas vive entre los dos valores que lo rodean —la pausa más larga de una jornada partida ronda las cuatro y el descanso legal entre jornadas son doce—, así que no hay ambigüedad posible.

## La cola offline

La reposición sin conexión decidía por fecha, así que descartaba el segundo tramo de una jornada partida y podía cerrar la entrada equivocada: las marcas se insertan en el orden en que se encolaron, que no es el orden en que ocurrieron. Tres correcciones:

- `get_open_entry` ordena por **hora de entrada** y no por orden de inserción, y acepta un corte `antes_de`.
- Una entrada abierta de otra jornada (más de 18 h) no es la que la marca viene a cerrar.
- Una marca que cae **dentro** de una jornada ya registrada se descarta. No alcanza con "¿marcó ese día?": la jornada partida tiene dos entradas legítimas en la misma fecha.

## Operación

**Gestión → Turnos** en la web y sección **02 · Turnos** en el escritorio.

| Acción | Efecto |
| --- | --- |
| Crear turno | Nombre, franja (o dos si es partida), días y tolerancia opcional |
| Hacer predeterminado | Pasa a regir para quien no tenga turno propio |
| Cambiar turno | Fija el turno de contrato del legajo |
| Rotar | Asigna un turno por un período; al vencer vuelve solo |
| Retirar | Solo si nadie depende del turno; si nunca se usó, se elimina |

No se retira un turno con gente adentro: el horario de esa gente pasaría en silencio a ser otro y sus tardanzas se medirían contra una hora que nadie les comunicó.

El empleado ve su horario en el portal, debajo del parte del día, con la marca **Rotación vigente** cuando corresponde. Una rotación que la persona no puede ver es una regla que no se le comunicó.

## Lo que sigue faltando

- **El cambio de horario rige hacia adelante y no versiona el pasado.** Los marcajes ya liquidados conservan la incidencia calculada con el horario de entonces, que es lo correcto; pero si se edita un turno y después se corrige una marca vieja, la corrección usa el horario **nuevo**.
- **La línea de tiempo del mes resuelve el turno una vez**, no día por día: una rotación a mitad de mes desplaza los francos del tramo anterior. Es un detalle de presentación frente a treinta consultas por pantalla.
- **No hay calendario de rotación automática** (semana A / semana B). Cada tramo se carga a mano.

## Enlaces

[[Motor de Reglas de Horas Extra]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Reglamento de Asistencia y Disciplina]] · [[Puesta en Marcha en un Cliente]] · [[Módulo de Gestión de Usuarios]] · [[Antifraude y Resiliencia en Picos de Marcación]]
