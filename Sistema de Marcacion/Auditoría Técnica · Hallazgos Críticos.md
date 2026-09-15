# Auditoría Técnica · Hallazgos Críticos

> Revisión completa del repositorio (~9.500 líneas) desde tres frentes: arquitectura/backend, producto/UX y seguridad/QA. Fecha de corte: **2026-09-13**, commit `d0559f3`.
> Cada hallazgo lleva archivo, línea, impacto real y corrección propuesta. Los marcados con ✅ fueron **reproducidos ejecutando el código**, no inferidos por lectura.

> [!success] Estado de remediación · 2026-09-14
> Corregidos los **cinco P0** (el quinto apareció al abrir el portal en un navegador durante la verificación) y los **cuatro errores de liquidación del motor horario**, más dos defectos que cayeron de arrastre.
>
> | # | Estado | Verificación |
> | --- | --- | --- |
> | P0-1 | Corregido | El servidor responde con otra sesión reteniendo un lock de `users`; antes toda petición se encolaba detrás |
> | P0-2 | Corregido | `initialize()` sobre base virgen crea las 8 tablas, columnas e índices |
> | P0-3 | Corregido | Un Empleado recibe 403 al publicar; el payload se renderiza como texto y no se ejecuta |
> | P0-4 | Corregido | Secreto ausente o corto aborta el proceso; firma migrada a HMAC-SHA256 |
> | P0-5 | Corregido | `node --check` sobre el script renderizado; portal operativo en navegador |
> | P1-3 | Corregido | Tope único por turno: 06:00–21:00 liquida 7 h 30 ordinarias, no 9 |
> | P1-4 | Corregido | `horas_nocturnas` en columna propia, con el +30 % propagado al aguinaldo |
> | P1-5 | Corregido | Sábado 22:00 → domingo 06:00 manda las 6 h de domingo al 100 % |
> | P2-3 | Corregido | `feriados_de(anio)` compone cualquier año: fijos + Pascua + traslados |
> | P2-1 | Corregido de arrastre | Aprobar una corrección de salida ya no lanza `TypeError` |
> | P2-4 | Corregido | Una sola regla de tardanza: la corrección de RRHH y la marca en vivo dan el mismo veredicto |
> | P2-5 | Corregido | Turnos con tramos, días y rotación con vigencia; la hora de entrada dejó de ser una constante del proceso |
>
> Cobertura nueva: `tests/test_motor_horario.py` (14 turnos frontera) y `tests/smoke_portal_js.py`.

## Cómo leer este registro

| Severidad | Criterio |
| --- | --- |
| **P0** | Impide operar en producción o permite tomar el control del sistema |
| **P1** | Produce pérdida económica, fraude o liquidación legal incorrecta |
| **P2** | Rompe un flujo funcional para el usuario final |
| **P3** | Deuda de cumplimiento, datos o escalabilidad a plazo corto |

---

## P0 · Bloqueantes

### P0-1 · Migraciones DDL en cada petición HTTP ✅ corregido

`src/web_server.py:862` → `_cliente()` llama a `db.initialize()` en **cada request**, y cada endpoint abre dos clientes (uno en `_usuario_autenticado`, otro en el handler).

`initialize()` ejecuta ~40 sentencias DDL, entre ellas:

```sql
ALTER TABLE justificaciones DROP CONSTRAINT IF EXISTS justificaciones_tipo_permiso_check;
ALTER TABLE justificaciones ADD CONSTRAINT justificaciones_tipo_permiso_check CHECK (...);
```

`DROP/ADD CONSTRAINT` toma **ACCESS EXCLUSIVE LOCK** sobre `justificaciones`. Con `gunicorn -w 4` (Dockerfile) y 200 empleados marcando a las 08:00, cada petición compite por un lock exclusivo contra las otras tres: las peticiones se serializan, la latencia se dispara y aparecen *lock timeouts*.

Hay además una ventana real de corrupción: entre el `DROP` y el `ADD` de un worker, otro worker puede insertar un `tipo_permiso` fuera del catálogo.

La bitácora **ya registró esta lección** para el worker de sincronización ("no debe correr `initialize()` (DDL) en cada ciclo"), pero la corrección nunca se trasladó al servidor web.

**Corrección**: mover las migraciones a un paso de arranque explícito (`alembic upgrade head` o un `migrate.py` invocado por el entrypoint) y que la petición solo abra conexión desde un pool.

### P0-2 · El esquema no se puede crear desde cero ✅ corregido

`src/database.py:219-238` ejecuta sentencias contra `marcajes` **antes** de crear la tabla, que recién aparece en la línea 241:

```python
cursor.execute("CREATE INDEX IF NOT EXISTS idx_marcajes_analitica ON marcajes (es_tardanza, hora_entrada)")  # línea 220
cursor.execute("ALTER TABLE marcajes ADD COLUMN IF NOT EXISTS tolerancia_aplicada ...")                       # línea 228
...
cursor.execute("CREATE TABLE IF NOT EXISTS marcajes (...)")                                                    # línea 241
```

Sobre una base virgen, la línea 220 levanta `UndefinedTable (42P01)`. Como la conexión es `autocommit = False` y no hay `rollback`, la transacción queda abortada y el resto del `initialize()` falla en cascada. **El sistema solo arranca sobre bases que ya tenían el esquema**; un despliegue limpio no levanta.

**Corrección**: ordenar el DDL por dependencias, o directamente migraciones versionadas.

### P0-3 · XSS almacenado en el panel de alertas → toma de cuenta de Administrador ✅ corregido

Dos defectos que se encadenan:

1. `src/web_server.py:977` — `POST /api/alertas` exige token pero **no valida rol**. Cualquier empleado autenticado publica una alerta con `tipo`, `mensaje`, `detalle` y `usuario_id` arbitrarios.
2. `src/web_server.py:309-311` — el toast en vivo renderiza con `innerHTML` **sin escapar**:

```js
aviso.innerHTML = '<b>' + icono + alerta.mensaje + '</b>' +
  (alerta.detalle ? '<span>' + alerta.detalle + '</span>' : '');
```

Un empleado publica una alerta con `usuario_id: null` (llega a todos los conectados) cuyo mensaje sea `<img src=x onerror="...">`. El siguiente usuario de RRHH que abra el portal ejecuta ese código con su sesión. Como el JWT vive en `localStorage` (`marcacion_jwt`), el payload lo exfiltra y el atacante queda con una sesión de Administrador durante 8 horas.

El resto del panel sí usa `esc()` (`cargarAlertasPanel`, `cargarAuditoria`), lo que confirma que el escape del toast es un olvido, no una decisión.

**Corrección**: `_exigir_rrhh` en el POST, `textContent` en lugar de `innerHTML` en el toast, y CSP `default-src 'self'` en las cabeceras de respuesta.

### P0-4 · Clave de firma JWT con valor por defecto público ✅ corregido

`src/auth.py:44`

```python
return os.getenv("JWT_SECRET_KEY", "clave-de-desarrollo-no-usar-en-produccion")
```

Si la variable no está definida —un `.env` mal montado, una variable olvidada en Render— el sistema **no falla: firma con una clave que está en el código fuente público**. Cualquiera fabrica un token con `rol: Administrador` y entra.

Lo mismo ocurre en `src/reports.py:155` con `COMPROBANTE_CLAVE`: los comprobantes "de fidelidad legal" se firman con una clave conocida y quedan falsificables.

**Corrección**: fallar al arrancar si falta el secreto. Sin valor por defecto, nunca.

### P0-5 · El portal web estaba caído por completo ✅

Hallazgo descubierto **al abrir la página en un navegador** para verificar la corrección de P0-3, no durante la lectura del código.

`src/web_server.py:643-645` escribía comillas escapadas para JavaScript dentro de un f-string de Python:

```python
'<button ... onclick="mostrarSeccionGestion(\'personal\')">Gestionar personal</button> ' +
```

Python consume el `\'` y entrega al navegador una comilla suelta que cierra la cadena de JavaScript antes de tiempo:

```javascript
'<button ... onclick="mostrarSeccionGestion('personal')">…'
//                                            ^ la cadena termina acá
```

El resultado es `SyntaxError: Unexpected identifier 'personal'` **en tiempo de parseo**, lo que anula el bloque `<script>` entero. No fallaba una función: no se definía ninguna de las 42. Sin login, sin kiosco, sin tablero, sin panel. El sitio servía el HTML y nada más.

Confirmado renderizando la plantilla de `HEAD` y pasándola por `node --check`:

```
VERSION HEAD renderizada de verdad -> node --check exit: 1
    '<button class="btn-secundario" onclick="mostrarSeccionGestion('personal')">…' +
    SyntaxError: Unexpected identifier 'personal'
```

**Por qué nadie lo notó**: toda la suite ejerce la API por HTTP y ninguna prueba renderiza el script. Un fallo total del cliente convive con una suite en verde.

**Corrección**: duplicar la barra (`\\'`) para que Python emita un escape válido de JavaScript, y `tests/smoke_portal_js.py` como guarda de regresión.

---

## P1 · Fraude y liquidación

### P1-1 · El empleado se auto-otorga la tolerancia climática ✅ corregido

> Cerrado el 2026-09-14. La condición vive en la tabla `condiciones_dia`, la firma Recursos Humanos y alcanza a toda la plantilla. `tests/test_condicion_dia.py` verifica además que no haya reaparecido ninguna vía para que el dato lo aporte quien marca: ni en `evaluar_asistencia`, ni en `registrar_asistencia`, ni en `MarcarRequest`, ni en las dos interfaces.

`src/web_server.py:1150` → `MarcarRequest.es_dia_lluvioso` viaja **desde el cliente**, marcado por un checkbox del propio kiosco (`lluvia_kiosco`).

El sujeto de la regla controla el input de la regla: llego 25 minutos tarde, tildo "día lluvioso" y los 30 minutos del Art. 3028/2024 me cubren. No queda registro de la mentira: `condicion_climatica` guarda "Lluvia intensa" como hecho.

**Corrección aplicada**: la condición climática es un **estado del día declarado por RRHH**, nunca un campo del request de marcación. La declaración lleva fecha, condición, tolerancia en minutos (con techo de 120), nota interna y firmante, y se audita en `logs_auditoria`. El kiosco la muestra pero no la puede declarar. La cola offline ya no la transporta: se resuelve al sincronizar, porque la declaración puede firmarse después de que el kiosco perdiera la conexión.

### P1-2 · La validación facial falla en abierto ✅ corregido

> Cerrado el 2026-09-14. `facial.validar` devuelve tres estados en vez de un booleano y la política vive aparte, en `facial.decidir`.

`src/facial.py:174-176`

```python
objetivo_tiene_foto = any(int(foto["user_id"]) == user_id for foto in fotos)
if not objetivo_tiene_foto:
    return True, "El usuario no tiene foto registrada; validación omitida."
```

Un control antifraude que devuelve `True` cuando no puede verificar. Como todo empleado nuevo nace sin foto, el bypass es el estado por defecto: basta usar la cédula de alguien sin foto registrada.

Se suma que **LBPH no tiene detección de vida**: una foto en la pantalla de un celular pasa la verificación. Y `UMBRAL_CONFIANZA = 80.0` es permisivo para este algoritmo (por debajo de 50 es donde LBPH discrimina de verdad).

**Corrección aplicada**: `validar` devuelve `Verificada`, `Rechazada` o `No verificable`, y `decidir` aplica la política. Un rostro que no coincide bloquea **siempre**: ahí hay identidad comprobable y no es la que dice ser. La falta de datos para comparar sigue a `BIOMETRIA_OBLIGATORIA`: apagada (por defecto, porque una plantilla recién migrada no tiene fotos) la marca pasa pero queda grabada en `marcajes.verificacion_facial` como *No verificada* y dispara una alerta a RRHH, que además la ve contada en su bandeja; encendida, se bloquea y el kiosco web queda cerrado por no tener cámara.

Sigue pendiente lo de fondo: LBPH no tiene detección de vida y `UMBRAL_CONFIANZA = 80.0` es permisivo. Sustituirlo por *embeddings* faciales con prueba de vida pasiva es trabajo aparte.

### P1-3 · La jornada mixta sobredeclara horas ordinarias ✅

`src/clock_engine.py:210-216` suma los topes de ambas jornadas de forma independiente:

```python
ordinarias_diurnas = min(diurno, JORNADA_DIURNA)      # 8 h
ordinarias_nocturnas = min(nocturno, JORNADA_NOCTURNA) # 7 h
```

Ejecutado sobre un turno 06:00 → 21:00 (15 horas continuas):

```
ordinarias: 9.00h | extra50: 6.00h | extra100: 0.00h
```

Nueve horas ordinarias en un solo día. El Código del Trabajo topea la **jornada mixta en 7 h 30**, y el techo absoluto es la jornada, no la suma de dos jornadas distintas. Cada hora mal clasificada como ordinaria es una hora extra que no se paga.

La propia nota [[Motor de Reglas de Horas Extra]] documenta el error como ejemplo válido (fila "07:00–22:00 mixto → 10 h ordinarias").

**Corrección**: el tope de jornada se decide **una vez** según la naturaleza del turno (diurno 8 h / nocturno 7 h / mixto 7 h 30), y todo excedente es extraordinario.

### P1-4 · El recargo nocturno del 30 % no existe ✅ corregido

El motor **clasifica** horas nocturnas pero nunca las recarga. El Art. 232 del Código del Trabajo ordena un **+30 % sobre el valor ordinario** para el trabajo nocturno. El sistema paga la hora nocturna ordinaria igual que la diurna.

Ni `calcular_horas_paraguay` ni `calcular_aguinaldo` (`src/reports.py:226`) contemplan el recargo, así que el error se propaga también al aguinaldo.

### P1-5 · El feriado se decide por la hora de entrada ✅

`src/clock_engine.py:290` → `es_feriado_o_domingo(open_entry["hora_entrada"])` clasifica **todo el turno** por su instante inicial.

Verificado sobre un turno sábado 22:00 → domingo 06:00:

```
ordinarias: 7.00h | extra100: 1.00h
es_feriado_o_domingo(entrada sábado) = False
es_feriado_o_domingo(salida domingo) = True
```

Las seis horas trabajadas en domingo (00:00–06:00) se liquidan sin el recargo del 100 %. El error es simétrico: un turno que arranca el domingo a las 22:00 paga el lunes entero al 100 %.

**Corrección**: segmentar el turno por día calendario y aplicar el recargo por tramo, igual que ya se hace con la frontera diurno/nocturno.

### P1-6 · La cuota por días no valida el rango solicitado ✅ corregido

> Cerrado el 2026-09-14 junto con P3-7 y P3-8: los tres vivían en el mismo cálculo.

`src/auth.py:354` solo compara `horas_usadas` contra `restantes`, y ese camino existe únicamente para permisos medidos en horas. Para los permisos en **días** basta con que `disponible` sea `True`.

"Motivos Particulares" tiene cuota de 5 días al año. Con cero usos, `disponible = True`, y nada impide emitir una justificación del 1 de enero al 31 de diciembre: 365 días de una sola vez. La cuota recién bloquea el **siguiente** intento.

**Corrección aplicada**: `reglamento.dias_de_cuota` calcula lo que consume un rango y `crear_justificacion` lo valida contra lo que queda, igual que ya hacía con las horas. El camino de autoservicio ya lo validaba; el que faltaba era la carga directa de RRHH, donde alcanzaba con tipear mal una fecha de fin.

---

## P2 · Flujos rotos

### P2-1 · Aprobar una corrección de salida rompe el servidor ✅

`src/auth.py:458` construye un instante **naive**:

```python
instante = datetime.combine(solicitud["fecha_registro"], solicitud["hora_propuesta"])
```

y lo compara contra `marcaje["hora_entrada"]`, que viene de un `TIMESTAMPTZ` y es **aware**. Reproducido:

```
TypeError: can't compare offset-naive and offset-aware datetimes
```

Todo reclamo de tipo "Salida" que RRHH intente aprobar responde 500. El flujo de corrección —la red de seguridad del sistema cuando el biométrico falla— está caído para la mitad de los casos.

### P2-2 · El turno nocturno no se puede cerrar ✅ corregido

> Cerrado el 2026-09-14. `detectar_accion_hoy` decide por `get_open_entry()` —el estado real— y no por el calendario. Verificado en `tests/test_turno_nocturno.py` con un turno que entra un día y sale el siguiente.

`src/clock_engine.py:332-339` → `detectar_accion_hoy()` consulta `get_entries_by_date(user, hoy)`.

Un empleado entra el lunes 22:00 y quiere salir el martes 06:00. El martes no hay marcajes con fecha de martes, así que la función decide `ENTRADA`; `clock_in` encuentra la entrada abierta y aborta con "Ya hay una entrada abierta sin salida registrada".

**El empleado queda atrapado: no puede marcar salida ni entrada.** Es exactamente el caso de uso que el README promociona como soportado.

**Corrección aplicada**: la decisión se basa en `get_open_entry()` (estado real del empleado) y no en el calendario. El guardia de "ya marcó entrada y salida hoy" se conserva, pero después de resolver la jornada abierta.

### P2-3 · El sistema queda ciego a partir de enero de 2027 ✅

`src/clock_engine.py:39` → `FERIADOS_PARAGUAY_2026` es un `frozenset` de 12 fechas de 2026. Verificado: `es_feriado_o_domingo(2027-01-01) = False`.

En menos de cuatro meses todos los feriados se liquidan como días comunes. El recargo del 100 % desaparece del cálculo.

**Corrección**: tabla `feriados` en base, administrable por RRHH, con año y tipo (fijo / trasladable).

### P2-4 · Dos definiciones distintas de "llegada tardía" ✅ corregido

> Cerrado el 2026-09-14 junto con P2-5. Detalle en [[Turnos y Rotación de Horarios]].

- `es_tardanza()` → gracia de **10 minutos**.
- `evaluar_asistencia()` → gracia de **15 minutos** para funcionarios.

El flujo normal de marcación usaba la segunda; la corrección aprobada por RRHH, la primera. Un funcionario que marcaba 08:12 era "Normal" al marcar, pero si RRHH corregía su marca **a esa misma hora** quedaba como "Llegada Tardía". La corrección castigaba por corregir.

**Corrección aplicada**: `es_tardanza` delega en `evaluar_asistencia`. Una sola regla y un solo veredicto, medidos ambos contra el turno del empleado.

### P2-5 · No existía la entidad `turnos` ✅ corregido

> Cerrado el 2026-09-14. Diseño completo en [[Turnos y Rotación de Horarios]].

`clock_engine.py` congelaba `INICIO_JORNADA` en tiempo de importación, antes de que `load_dotenv()` llegara a correr (vive dentro de `load_config()`, en `Database.__init__`). Reproducido:

```
os.getenv JORNADA_INICIO (antes de load_dotenv): None
os.getenv JORNADA_INICIO (después de load_dotenv): '08:00'
INICIO_JORNADA sigue siendo: 08:00:00  <-- congelado en import-time
```

El defecto estaba latente porque el `.env` coincidía con el valor por defecto. Pero el fondo era mayor: **la hora de entrada era una constante global del proceso**. Sin la entidad "turno" no había horarios rotativos, ni turnos por sucursal, ni jornadas partidas. Una empresa con dos turnos no podía usar el sistema.

**Corrección aplicada**: tablas `turnos`, `turno_tramos` y `asignaciones_turno`, con resolución por prioridad (rotación vigente → turno del legajo → predeterminado de la empresa). La constante desapareció; `JORNADA_INICIO` sobrevive solo como valor con que el esquema siembra el turno inicial. De yapa cayeron tres defectos que la entidad dejó a la vista: la salida anticipada medida contra el tope legal en lugar de contra el tramo pactado, los días de franco contados como ausencia, y la cola offline decidiendo entrada o salida por fecha calendario.

### P2-6 · Un marcaje abierto bloquea al empleado para siempre ✅ corregido

> Cerrado el 2026-09-14 junto con P2-2.

`get_open_entry` no filtraba por antigüedad. Si alguien olvidaba marcar la salida, la entrada quedaba abierta indefinidamente y **todas** sus marcaciones futuras fallaban con "Ya hay una entrada abierta". No había auto-cierre ni escalamiento a RRHH.

**Corrección aplicada**: pasadas 18 horas (`MAX_JORNADA_ABIERTA`, que cubre con holgura el turno nocturno de 7 h más extras) la entrada se marca como **abandonada** con la incidencia *Salida no registrada*, se avisa a Recursos Humanos y el empleado vuelve a marcar. No se inventa la hora de salida: la repone el circuito de correcciones, que ya existía.

Se descartan **todas** las vencidas de una sola vez y no la última. Liberar solo una dejaba al empleado igual de trabado en la marcación siguiente; lo detectó `smoke_web_panel` contra un usuario con una cola acumulada.

---

## P3 · Datos, cumplimiento y escala

### P3-1 · Datos biométricos sin cifrar ✅ corregido

> Cerrado el 2026-09-14. Cifrado AES-256-GCM en `src/biometria.py`, clave fuera de la base y destrucción del dato con la baja.

La tabla `fotos` (`database.py:328`) guarda el rostro en `BYTEA` plano. Bajo la **Ley 6534/2020** de protección de datos personales, un dato biométrico es de categoría especial y exige medidas reforzadas. Un `SELECT` sobre un backup expone la plantilla facial de toda la plantilla.

Tampoco hay política de retención: la foto sobrevive a la baja del empleado.

**Corrección aplicada**: cifrado a nivel de columna con **AES-256-GCM**, clave en `BIOMETRIA_CLAVE` fuera de la base. El cifrado es autenticado y liga la foto al empleado como dato asociado: una plantilla movida de una fila a otra deja de descifrar, así que no se puede reasignar un rostro editando la base. Las fotos de instalaciones anteriores se detectan por prefijo y las cifra `migrate.py`.

**Retención**: la baja del empleado destruye la plantilla facial, porque el fin que legitimaba el tratamiento desaparece con la relación laboral. El resto del legajo se conserva, que es lo que exige el archivo laboral.

Sigue pendiente lo de fondo del reconocimiento: LBPH no tiene prueba de vida y una foto en la pantalla de un celular pasa la verificación.

### P1-2b · Sin prueba de vida, una foto pasa el control facial ⚠️ mitigado

> Mitigado el 2026-09-14. No cerrado: ver el límite al final.

El reconocimiento comparaba un rostro contra la foto de referencia y nada más. Una foto impresa, o la pantalla de un teléfono con la cara del compañero, pasaba el control: es exactamente lo que el control existe para impedir.

**Mitigación aplicada**: con `BIOMETRIA_PRUEBA_VIDA` encendida, el kiosco **sortea un gesto** —acercarse, girar a un lado— y lo pide antes de capturar. La secuencia tiene que mostrar ese gesto: que el rostro se desplace o cambie de tamaño, y que la imagen varíe entre cuadros. Una foto quieta no hace ninguna de las dos cosas, y el veredicto es `Suplantada`, que se trata como fraude y bloquea.

El gesto se sortea en cada marcación a propósito: uno fijo se graba una vez en video y se reproduce siempre.

El orden también importa. Si la prueba de vida no pasa, la identidad **no se evalúa**: que la cara sea la correcta es justamente lo que una foto garantiza.

> [!warning] Es un disuasivo, no una prueba
> Quien mueva el teléfono siguiendo la consigna pasa igual. Cerrar el hueco de verdad pide una cámara con infrarrojo o profundidad, o un modelo de anti-suplantación entrenado: hardware y datos que este sistema no tiene. La cascada de ojos, que permitiría exigir un parpadeo, dejó de venir con OpenCV 5 y traerla al repositorio es una decisión de dependencias que corresponde tomar aparte.
>
> Lo que sí hace es dejar afuera el ataque habitual —una foto sostenida quieta frente a la cámara— y obligar a que el intento sea deliberado en lugar de trivial.

### P3-2 · El token viaja en la query string ✅ corregido

`src/web_server.py:465` y `:726` — la descarga de PDF pasa el JWT como parámetro de URL:

```js
window.open('/api/permiso/' + id + '/pdf?token=' + encodeURIComponent(obtenerToken()));
```

El token queda en los logs de acceso del servidor, en el historial del navegador y en la cabecera `Referer` hacia cualquier recurso externo. Mismo problema en el WebSocket (`/ws/alertas?token=`).

**Corrección aplicada**: la descarga de PDF ya usaba `fetch` con la cabecera `Authorization` y un blob. Lo que quedaba era el WebSocket, y ahí el navegador no puede poner cabeceras en el saludo: la sesión pasó a una cookie `HttpOnly`, `SameSite=Strict`, que el navegador manda sola en el handshake.

Eso cierra de paso el otro punto de la lista: el token ya no vive en `localStorage`, donde lo alcanzaba cualquier script que llegara a correr. `SameSite=Strict` es lo que reemplaza a la inmunidad natural del Bearer frente a CSRF: el navegador no manda la cookie en peticiones que nacen de otro sitio. La cabecera `Authorization` se sigue aceptando para los clientes que no son un navegador.

Al recargar la página el script ya no sabe quién es —no puede leer la cookie—, así que lo pregunta: `GET /api/sesion`. Y el cierre necesita un endpoint propio (`POST /api/logout`), porque una cookie `HttpOnly` la borra el servidor y no el navegador.

**Corrección**: cookie `HttpOnly` + `Secure` + `SameSite=Strict`, que además cierra el vector de robo por XSS del P0-3.

### P3-3 · Login sin límite de intentos ✅ corregido

> Cerrado el 2026-09-14. `src/rate_limit.py`: 8 intentos por ventana de 5 minutos y bloqueo de 15, contados por cédula **y** por origen. La ventana es en memoria del proceso, así que con varios workers el umbral efectivo se multiplica: alcanza contra un diccionario y no reemplaza a un límite en el proxy de entrada.

`POST /api/login` no tiene *rate limiting* ni bloqueo progresivo. Doble consecuencia: fuerza bruta contra contraseñas de empleados (que en la práctica serán débiles), y **vector de DoS** — bcrypt es caro por diseño, así que un atacante satura la CPU con peticiones de login inválidas.

### P3-4 · Las alertas en vivo solo llegan al 25 % de los clientes ✅ corregido

`src/notifications.py:54` → `BUS = BusAlertas()` es un pub/sub **en memoria del proceso**. El Dockerfile arranca `gunicorn -w 4`.

Una alerta publicada en el worker 1 no alcanza a los WebSockets conectados a los workers 2, 3 y 4. El panel de RRHH pierde silenciosamente tres de cada cuatro alertas de fraude.

**Corrección aplicada**: `LISTEN`/`NOTIFY` de PostgreSQL, no Redis: la base ya es una dependencia del sistema, y una pieza de infraestructura más es una pieza más que instalar, monitorear y explicar en la puesta en marcha de cada cliente.

Cada proceso levanta un hilo que escucha el canal `alertas_marcacion` y repite en su bus local lo que publicaron los demás. El aviso lleva solo el identificador y la empresa: el payload de `NOTIFY` tiene un tope de 8 kB y el detalle de una alerta no tiene ninguno, así que mandarlo entero funcionaría hasta el día en que alguien escriba una nota larga.

El que publica también recibe su propio aviso, así que el bus recuerda los últimos identificadores que sacó y no los repite. Y la empresa viaja con la alerta: un canal compartido por toda la instalación es justamente donde se cruzarían dos clientes.

**Verificado con un proceso de verdad.** La primera versión de la prueba publicaba la alerta desde el mismo proceso que escuchaba: pasaba en verde aunque el canal no existiera, porque el bus local ya la entregaba por su cuenta. Ahora el emisor es un subproceso.

### P3-5 · Una excepción envenena toda la petición ✅ corregido

> Cerrado el 2026-09-14. `Database._execute` deshace la transacción ante un error de PostgreSQL antes de propagarlo, de modo que la conexión queda utilizable.

`database._execute` (línea 396) nunca cierra cursores y **no hace `rollback`** ante un error. Con `autocommit = False`, la primera excepción deja la transacción en estado abortado y toda consulta posterior de esa conexión falla con *"current transaction is aborted"*.

`notifications.registrar_alerta` agrava el patrón: captura `except Exception` y sigue adelante como si nada, dejando la conexión rota para el resto del request y **perdiendo la evidencia de la alerta de fraude** que intentaba guardar.

### P3-6 · La antigüedad se calcula desde el alta en el sistema ✅ corregido

> Cerrado el 2026-09-14. Columna `users.fecha_ingreso` con relleno desde `created_at`, y un único resolutor (`reglamento.fecha_ingreso`) que reemplaza a los tres cálculos que había sueltos. Verificado en `tests/test_antiguedad_y_bajas.py`: un legajo migrado hoy con contrato de hace 12 años recibe 30 días de vacaciones y no 12.

`reglamento.py:522` y `reports.py:215-220` usan `user["created_at"]` como fecha de ingreso.

El día que se migre la plantilla real, **todos** los empleados nacen con antigüedad cero: alguien con 15 años de servicio recibe 12 días de vacaciones en lugar de 30, y su aguinaldo se calcula sobre los meses equivocados.

Falta el campo `fecha_ingreso`, que es un dato de negocio independiente de cuándo se creó la fila.

### P3-7 · Días corridos donde el reglamento dice hábiles ✅ corregido

`reglamento._usados` computaba `(fecha_fin - fecha_inicio).days + 1` para todos los permisos por días. Pero el catálogo define varios artículos en **días hábiles**: Art. 23 de pasantes ("diez (10) días hábiles") y Fuerza Mayor ("5 días hábiles al año"). Una licencia que cruzaba un fin de semana consumía dos días de cuota que el reglamento no consume.

**Corrección aplicada**: esos dos artículos llevan la marca `habiles` en el catálogo y `dias_de_cuota` descuenta sábados, domingos y feriados. El calendario sale de `clock_engine`, importado dentro de la función porque el módulo depende de `database`, que depende del catálogo.

### P3-8 · Imputación de cuota por fecha de inicio ✅ corregido

`reglamento._en_periodo` imputaba el permiso completo al período de su `fecha_inicio`. Una licencia del 28 de diciembre al 10 de enero descontaba trece días del año que terminaba y cero del que empezaba.

**Corrección aplicada**: el permiso pertenece a todo período que **solape**, y cada uno cuenta solo los días que le tocan. El mismo ejemplo reparte ahora cuatro días a un año y diez al otro.

De paso quedó a la vista que `resumen_empleado` tenía su **propia copia** del conteo —corridos, imputados por fecha de inicio—, así que el saldo que veía el empleado en su portal no era el que aplicaba la cuota al aprobar. Ahora los dos llaman al catálogo.

### P3-9 · Escaneo completo de justificaciones por consulta ✅ corregido

> Cerrado el 2026-09-14. `list_justificaciones(usuario_id)` filtra en la base; se agregaron `get_justificacion(id)` y `contar_justificaciones()` para los tres llamadores que traían la tabla entera para quedarse con una fila o con un número.

`reglamento.disponibilidad_permisos` (línea 523) trae **todas las justificaciones de la empresa** y filtra en Python:

```python
todas = [j for j in db.list_justificaciones() if j["usuario_id"] == user["id"]]
```

Con 500 empleados y tres años de histórico, cada apertura del portal arrastra la tabla entera. Lo mismo en `api_permiso_pdf` (`web_server.py:1072`), que recorre todas las justificaciones para encontrar una por id.

### P3-10 · La cola offline confía en un nombre de usuario ✅ corregido

`offline_queue.py` almacena solo `username` y `momento`. `sync_worker` reinyecta esas marcas **sin verificar credenciales**. Quien tenga acceso al archivo `marcaciones_offline.db` del kiosco puede fabricar marcaciones para cualquier empleado, y entrarán al sistema central como legítimas.

Además, una marca que falla de forma permanente (usuario inexistente) se reintenta cada 15 segundos para siempre: no hay contador de reintentos ni cola de descarte.

**Corrección aplicada**: cada fila se firma con HMAC-SHA256 al encolarla, sobre el `sync_id`, el empleado, el instante y el veredicto biométrico. La clave vive en el entorno y no en el archivo, así que copiar el SQLite no alcanza para falsificar. Lo que no verifica no se reintenta —no va a mejorar— y no se borra en silencio: se aparta en una tabla `descartadas` con su motivo y RRHH recibe una alerta. Las que fallan por otro motivo se reintentan hasta un techo de 20 lotes y después se apartan igual.

De paso, el veredicto del control biométrico ahora viaja con la marca: la cámara estaba en el kiosco y al sincronizar ya no, así que antes toda marca offline entraba como *No verificada* aunque el rostro hubiera coincidido.

Y quedó a la vista un defecto propio del sincronizador: una entrada vieja **sin cerrar** hacía que toda marca posterior pareciera ya cubierta y se descartara en silencio. Ahora solo cubren las jornadas con salida.

### P3-11 · El contenedor corre como root ✅ corregido

> Cerrado el 2026-09-14. El Dockerfile crea el usuario `marcacion` (uid 10001) y cambia a él antes del `CMD`.

El `Dockerfile` no crea usuario sin privilegios. Cualquier ejecución de código en el proceso web obtiene root dentro del contenedor.

---

### P3-12 · El pipeline de CI nunca llegó a ejecutarse ✅ corregido

> Cerrado el 2026-09-15. El hook de despliegue pasa por `env`, que sí se puede leer desde una condición. Verificado por el propio pipeline: primer run completo en verde —35 pasos de pruebas y 2 de despliegue— con la imagen publicada en el registro de contenedores. Ese verde resultó ser parcialmente accidental en los pasos que levantan servidor; ver [[#P3-14 · Las pruebas de CI corrían contra el servidor del paso anterior ✅ corregido|P3-14]].

El paso final del despliegue se salteaba con `if: ${{ secrets.RENDER_DEPLOY_HOOK != '' }}`. `secrets` no es un contexto válido dentro de un `if`: nombrarlo ahí no deja el paso en gris, **invalida el workflow completo al validarlo**. Todos los runs del repositorio, desde el commit que trajo el archivo, figuraban en rojo con cero jobs y cero anotaciones — un estado que desde la lista de Actions se parece bastante a un test que falla.

La consecuencia real no es el despliegue caído sino la red de seguridad ausente: veintitantos pasos de prueba declarados, ninguno ejecutado nunca. Todo lo verde que se reportó en este proyecto salió de correr las suites a mano en una máquina de desarrollo, con su `.env` cargado.

Al arrancar por primera vez aparecieron dos defectos que el workflow había estado escondiendo:

- **Faltaba `BIOMETRIA_CLAVE` en el entorno del job.** No tiene valor de respaldo a propósito —una clave conocida abriría las plantillas faciales de un backup robado—, así que su ausencia no degradaba nada en silencio: rompía `test_seguridad_datos` y `smoke_facial` en el primer cifrado.
- **La comprobación de cabeceras de seguridad medía un error.** Usaba `curl -I`, que manda `HEAD` sobre una ruta que solo sirve `GET`, y leía las cabeceras de un 405. Aprobaba igual porque el estado de un pipe es el del último comando: el `grep` encontraba la cabecera y tapaba al `curl` caído. Ahora va con `-D -` sobre un `GET` real, y el paso corre con `pipefail` para que eso no pueda repetirse.

- **`requirements.txt` tenía dos dependencias en un renglón.** `websockets>=12.0cryptography>=43.0.0`, pegadas por un append sin salto de línea en `1ec79a9`. Pip no parsea eso, así que un clon nuevo del repositorio nunca se pudo instalar — ni el `docker build`, que hace lo mismo. En desarrollo era invisible porque ambas bibliotecas ya estaban en el entorno y el archivo no se volvía a leer. Es el defecto que solo existe para quien llega de cero, que es exactamente para quien sirve un CI.

**Lección de método.** Un pipeline en rojo permanente enseña a ignorar el rojo. Conviene distinguir dos preguntas que la pantalla de Actions mezcla: *¿falló una prueba?* y *¿llegó a correr alguna?* La segunda se responde mirando cuántos jobs produjo el run — cero jobs no es una prueba que falla, es un archivo que no compila.

---

### P0-6 · El pico de la mañana rechazaba tres de cada cuatro marcas ✅ corregido

> Cerrado el 2026-09-15. La petición espera un lugar en el pool en vez de recibir un error.

Con cuarenta personas marcando a la vez, treinta recibían **500**. El número no era casual: pasaban exactamente `DB_POOL_MAX`. `psycopg2.pool.getconn` no espera —cuando el pool llega a su tope lanza excepción en el acto—, así que las primeras diez marcas entraban y el resto se perdía.

Es la peor forma posible de quedarse corto. Una petición dura milisegundos y la conexión se libera enseguida, de modo que la espera habría sido imperceptible; pero una marca rechazada no se recupera sola, y quien la intentó ya se fue a trabajar. El pico de un control de asistencia concentra todo el tráfico del día en dos ventanas de quince minutos: era el único momento que importaba y el único que nunca se había probado.

Ahora la petición espera hasta `DB_POOL_ESPERA` segundos antes de rendirse. El tope sigue existiendo para que una base caída se note como una caída y no como un servidor colgado.

Se resuelve por sondeo y no con un semáforo a propósito: un semáforo que se desincronice del pool —una devolución que no ocurre por una excepción en el camino— deja el proceso bloqueado para siempre, que es peor que el problema que viene a resolver.

**Medido después del arreglo**, en un solo proceso: 150 personas marcando en el mismo instante entran todas, en 4,7 s, sin perder ni duplicar ninguna. El techo de unas 32 por segundo es `bcrypt` verificando contraseñas, caro por diseño, así que escala con procesos y CPU y no con el tamaño del pool.

**Lección de método.** El defecto estaba desde que se agregó el pool y la suite entera pasaba en verde, porque ninguna prueba hacía dos cosas al mismo tiempo. Una prueba de carga no mide velocidad: mide corrección bajo concurrencia, que es una propiedad distinta y no se deduce de las otras.

---

### P2-7 · Una marcación no registraba desde dónde se hizo ✅ corregido

> Cerrado el 2026-09-15. Cada kiosco se identifica con un token propio.

*"Marqué desde casa"* quedaba en una afirmación contra otra. Para un producto cuyo trabajo es certificar presencia, el origen de la marca no es un dato accesorio: es el fraude central.

Cada puesto se da de alta y recibe un token, que se entrega una sola vez; en la base queda su SHA-256, por el mismo motivo que una contraseña. El puesto se identifica **antes** que la persona, y de él sale la empresa: un token dice de qué cliente es el kiosco con más certeza que el nombre del host, que cualquiera puede fijar.

Eso obligó a una segunda lectura que cruza empresas, por el mismo motivo que el login, y vive donde la primera: en una función con los privilegios de su dueño, para que la excepción quede escrita en el esquema y no repartida por el código.

Revocar no borra. Un puesto dado de baja deja de servir pero conserva el origen de las marcas que ya registró, que es justamente el dato que el módulo existe para guardar.

`DISPOSITIVO_OBLIGATORIO` exige que toda marca venga de un puesto habilitado. Viene apagada: una instalación existente no tiene puestos cargados, y encenderla de golpe dejaría a todos sin poder marcar.

---

### P3-13 · No había procedimiento de respaldo ✅ corregido

> Cerrado el 2026-09-15. `src/respaldo.py`, con el viaje redondo probado en CI.

El sistema produce prueba legal de sueldos y no tenía forma documentada de respaldarse ni de restaurarse. Un `pg_dump` a secas tampoco alcanzaba: las plantillas faciales se cifran con `BIOMETRIA_CLAVE`, que vive fuera de la base a propósito, así que **restaurar con otra clave deja las fotos ilegibles** — y eso no se descubre al restaurar sino semanas después, cuando alguien no puede marcar.

Cada respaldo guarda al lado la huella de la clave con que se hizo y los recuentos de siete tablas testigo. La restauración compara la huella antes de tocar nada y después contrasta lo que quedó contra el manifiesto, en vez de confiar en el código de salida de `pg_restore`, que devuelve distinto de cero por avisos que no son fallas.

Escribir la prueba encontró que `--forzar` hacía dos cosas a la vez: saltear la confirmación por teclado y saltear la comprobación de la clave. Quien automatizara una restauración para no quedarse esperando un `input` desactivaba sin querer la única protección que justifica todo el diseño. Van separadas.

---

### P3-14 · Las pruebas de CI corrían contra el servidor del paso anterior ✅ corregido

> Cerrado el 2026-09-15. `tests/servidor.sh` libera el puerto antes de arrancar y espera a que el servidor conteste de verdad.

Los tres pasos que necesitan el portal levantado hacían lo mismo: `timeout 25 python src/web_server.py &`, `sleep 6`, y a probar. Cada paso del workflow corre en su propia shell, pero **un proceso en segundo plano le sobrevive**: el servidor del paso 28 seguía ocupando el puerto 8000 durante los pasos 29 y 30. Los servidores de esos dos pasos no llegaban a ligar —`address already in use`— y morían en el acto, con el error enterrado en segundo plano donde nadie lo mira, mientras las pruebas se ejecutaban contra el servidor del paso 28.

Mientras los plazos se solaparon, todo se veía verde. Se rompió cuando el paso de carga arrancó a las 04:06:18: su propio servidor no pudo ligar, durmió seis segundos, y al despertar a las 04:06:24 el único servidor vivo se había apagado un segundo antes por su `timeout 25`. El diagnóstico desde la lista de Actions es engañoso al máximo: el paso que falla es el de carga, el paso culpable es otro, y la prueba de carga no tenía nada malo.

Lo importante no es el paso caído sino lo que estuvo tapando: **tres pasos de prueba nunca comprobaron que su servidor hubiera arrancado**. `sleep 6` es una suposición, no una verificación; un servidor que no levanta y uno que tarda siete segundos producen exactamente la misma línea de log, que es ninguna.

Ahora el arranque es explícito: se libera el puerto —el PID va a un archivo, porque la shell que lo lanzó ya no existe cuando el paso siguiente necesita matarlo—, se sondea la portada hasta que responde, y si no levanta se vuelca el registro del proceso en lugar de fallar más tarde con un error de conexión. Cada paso apaga lo suyo al terminar.

**Lección de método.** El fallo no estaba en el paso que se puso rojo, y el verde anterior tampoco significaba lo que parecía. Una prueba de integración que asume su entorno en vez de comprobarlo mide dos cosas mezcladas —el código y la suerte— y solo se separan el día que la suerte se acaba.

---

### P3-15 · Sin registro operativo, sin chequeo real de salud y sin versión de esquema ✅ corregido

> Cerrado el 2026-09-15. `src/registro.py`, `GET /salud` y un sello de versión en la propia base. Cubierto por `tests/test_operacion.py` (23 comprobaciones) y por un paso propio de CI.

Tres huecos que no cambiaban nada de lo que ve un empleado y decidían si el sistema se podía sostener una vez instalado. Ninguno venía de la auditoría original: aparecieron al revisar qué faltaba para darlo por terminado.

**No había registro operativo.** Cero uso de `logging` en `src/`. Un 500 en producción dejaba el *traceback* de uvicorn en la salida estándar y nada más: sin identificador de petición, sin empresa, sin usuario, sin duración. `logs_auditoria` existía y existe, pero es otra cosa —quién aprobó qué permiso, para mostrárselo a una inspección— y vive en la base junto a los datos del cliente. Ahora cada petición lleva un identificador que también vuelve en la cabecera `X-Peticion`, de modo que *"me dio error a las 7:42"* deja de ser una pista y pasa a ser una clave de búsqueda. Lo que nunca entra: contraseñas, tokens de sesión, tokens de kiosco y plantillas faciales — un registro operativo se le pasa a un tercero el día que hay que pedir ayuda, y ese día tiene que poder copiarse entero.

Dos eventos entraron al registro por mérito propio: el freno de intentos y la **espera del pool**. Una espera que termina bien no rompe nada, pero es el aviso de que el pool quedó chico; era exactamente lo que faltaba para ver venir [[#P0-6 · El pico de la mañana rechazaba tres de cada cuatro marcas ✅ corregido|P0-6]] antes de que se convirtiera en marcas rechazadas.

**El chequeo de salud no tocaba la base.** `docker-compose` y el `HEALTHCHECK` del Dockerfile pedían la portada, que arma el portal entero sin consultar PostgreSQL: con la base caída el contenedor se reportaba sano y nadie lo reiniciaba. `GET /salud` abre conexión y pregunta, porque tenerla abierta no prueba nada —el pool puede estar entregando un socket que la base cerró del otro lado hace horas—. Responde sin credenciales y no dice versiones ni nombres de host, porque lo consulta cualquiera.

**El esquema no tenía versión.** Se crea con DDL idempotente y `esquema_listo()` contaba tablas contra una lista escrita a mano. La lista envejeció: cuando el esquema sumó `dispositivos`, una base migrada de antes seguía dando el recuento por bueno, el servidor arrancaba sin la tabla que `/api/marcar` necesita, y el fallo aparecía en la primera marcación en lugar de al arrancar, que es cuando todavía se puede corregir. Ahora la base lleva un sello propio: `python src/migrate.py estado` contesta *¿en qué versión está este cliente?* sin depender de mirar el código que uno cree haberle instalado, y el servidor se niega a arrancar cuando el código es más nuevo que la base, diciendo qué falta y no solo que algo falta.

El DDL base sigue siendo idempotente y se repite sin consecuencias. Lo que **no** se puede repetir —rellenar una columna nueva desde las viejas, corregir filas cargadas mal, cambiar un tipo— va en `PASOS_UNICOS`, dentro de la misma transacción que el resto del DDL: si algo falla, la base no puede quedar declarando una versión que en realidad no terminó de aplicarse.

De arrastre apareció una asimetría en los permisos del rol de servicio: se otorgaba `EXECUTE` sobre `credenciales_por_usuario` pero no sobre `dispositivo_por_token`, las dos únicas lecturas que cruzan empresas a propósito. Funcionaba porque PostgreSQL concede ejecución a `PUBLIC` por defecto; el día que alguien la revoque, el login seguiría en pie y la marcación caída, que es la peor mitad para descubrir en producción.

**Lección de método.** Con un cliente los tres huecos son invisibles: el desarrollador tiene la consola delante, sabe qué versión instaló y se entera de que la base está caída porque se lo dicen por teléfono. Los tres aparecen al segundo cliente, y para entonces ya no hay a quién preguntarle qué pasó.

---

## Prioridad de remediación sugerida

| Orden | Trabajo | Cierra | Estado |
| --- | --- | --- | --- |
| 1 | Escapar el toast + RBAC en `POST /api/alertas` + CSP | P0-3 | ✅ hecho |
| 2 | Secretos sin valor por defecto (arranque fail-closed) | P0-4 | ✅ hecho |
| 3 | Migraciones fuera del ciclo de request | P0-1, P0-2 | ✅ hecho (falta el pool de conexiones) |
| 3b | Escapes de JavaScript en la plantilla + guarda de regresión | P0-5 | ✅ hecho |
| 4 | Reescritura del motor horario (jornada única, tramos por día, +30 % nocturno) | P1-3, P1-4, P1-5, P2-3 | ✅ hecho |
| 5 | Clima declarado por RRHH + biometría fail-closed | P1-1, P1-2 | ✅ hecho |
| 6 | `detectar_accion_hoy` por estado, no por calendario | P2-2, P2-6 | ✅ hecho |
| 7 | `fecha_ingreso` y baja lógica | P3-6 | ✅ hecho |
| 8 | Biometría cifrada, freno de intentos y contenedor sin privilegios | P3-1, P3-3, P3-11 | ✅ hecho |
| 9 | Entidad `turnos` (multi-turno, jornada partida, rotación) | P2-4, P2-5 | ✅ hecho |
| 10 | Aislamiento multiempresa: `empresa_id`, fallo cerrado, guardia estática y RLS en PostgreSQL | — | ✅ hecho |
| 11 | Cookie `HttpOnly` y bus de alertas fuera del proceso | P3-2, P3-4 | ✅ hecho |
| 12 | Pipeline que arranca: contexto válido en el `if`, clave biométrica en CI y cabeceras medidas sobre un GET | P3-12 | ✅ hecho |
| 13 | Entregable: instalador del kiosco, despliegue en servidor propio y respaldo verificable | P3-13 | ✅ hecho |
| 14 | El pico de marcación y el origen de cada marca | P0-6, P2-7 | ✅ hecho |
| 15 | Arranque verificado del servidor en cada paso de CI | P3-14 | ✅ hecho |
| 16 | Registro operativo, chequeo de salud contra la base y versión de esquema | P3-15 | ✅ hecho |

El detalle del rediseño está en [[Arquitectura Objetivo · Plataforma y Portal del Empleado]]; las contramedidas de fraude y carga, en [[Antifraude y Resiliencia en Picos de Marcación]].

## Enlaces

[[Ecosistema Sistema de Marcación]] · [[Motor de Reglas de Horas Extra]] · [[Turnos y Rotación de Horarios]] · [[Multiempresa · Aislamiento entre Clientes]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Catálogo de Permisos y Licencias]] · [[Bitácora de Implementación]]
