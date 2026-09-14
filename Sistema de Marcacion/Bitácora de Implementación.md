# Bitácora de Implementación

> Historial cronológico de todo lo construido en el proyecto **Sistema de Marcación Empresarial**, con los commits de referencia y las validaciones ejecutadas. Las notas temáticas profundizan cada módulo (ver [[Ecosistema Sistema de Marcación]]).

## Resumen general

Sistema completo de control de asistencia para la empresa: **PostgreSQL** como base, **CustomTkinter** como interfaz de escritorio (kiosco + gestión), **FastAPI** como autoservicio web, **ZKTeco** como fuente biométrica y **Docker** para despliegue. Cumple la **Ley 213** (horas extra), la **Ley 6380/2019** (aguinaldo), la **Res. Directorio 3028/2024** (tolerancias y reglas para pasantes/funcionarios) y el **Reglamento Interno de Personal Res. 1307/2010** (catálogo de permisos y licencias).

## Fases construidas

### 1. Fundación: base de datos y autenticación
*Commits: `1f231a1` → `1be2fa4`, `339a2fe`, `d215df5`*

- Esquema PostgreSQL con **creación automática de la base** (detección de SQLSTATE 3D000, UTF-8, diagnóstico claro si faltan permisos).
- Tablas: `roles`, `users`, `marcajes`, `logs_auditoria` (JSONB), `justificaciones`, `solicitudes_correccion`.
- **bcrypt** para contraseñas, **bootstrap del primer administrador**, auditoría automática de CRUD.
- CLI (`app.py`) con tipado completo, docstrings y menú adaptado al rol.

### 2. Motor de marcación · Ley 213
*Commits: `a89ec4a`, `df4d76e`*

- Registro de **entrada/salida con auto-detección** (`REGISTRAR ASISTENCIA`), tolerancia de 10 min en la entrada con **tardanza auditada**.
- **Horas extra 50%/100%** (Ley 213) con algoritmo documentado: [[Motor de Reglas de Horas Extra]].
- **Justificaciones aprobadas** evitan faltas y reconocen las 8 h ordinarias legales en días laborables; retornos con instante exacto.

### 3. Reportes, comprobantes y aguinaldo
*Commits: `170dfaf`, `ca6d490`, `388b02c`, `6286b8c`*

- Exportación **mensual xlsx/csv** con tardanzas para contabilidad.
- **Comprobante digital** de marcación con firma **SHA-256 verificable**.
- **Aguinaldo proporcional** (Ley 6380/2019) y menú de exportación.

### 4. Interfaz de escritorio premium
*Commits: `de5c977`, `388b02c`, `fcd6b03`, `3e750ab`*

- Frontend **CustomTkinter** con control administrativo completo, ticket de comprobante en cada marcación, menú de justificaciones y aguinaldo, salario en CRUD.

### 5. Autoservicio web
*Commits: `df393cc`, `73a0a86`, `199355a`*

- **FastAPI** con consulta transparente: horas extra del mes, aguinaldo proporcional, histórico **enero → hoy**.
- **Autenticación JWT** (HS256, 8 h) y protección de endpoints.
- **Reclamos web** de marcación fallida (Pendiente/Aprobado/Rechazado) e incidencias.

### 6. Analítica y UX premium
*Commits: `ede7796`, `0c89326`*

- **Panel de analítica visual** con gráficos interactivos: [[Panel de Analítica Visual y UX Premium]].
- Rediseño premium: tarjetas flotantes, micro-interacciones, accesos rápidos históricos: [[Diseño de Interfaz Premium UI-UX]].

### 7. Biometría y despliegue
*Commits: `721a341`, `821a66c`*

- **Sincronización TCP/IP con reloj ZKTeco** (puerto 4370) y columna `biometrico_id`: [[Estructura Web y Conexión Biométrica]].
- **Dockerfile** (gunicorn + uvicorn) y variables de entorno para producción 24/7: [[Despliegue en la Nube e Infraestructura SaaS]].

### 8. Cumplimiento · Res. 3028/2024
*Commit: `a575d99`*

- Adaptación para **pasantes y funcionarios**: columna `tipo_vinculo`, reglas de tolerancia, **día de lluvia intensa** (30 min) y ausencias injustificadas: [[Reglamento de Asistencia y Disciplina]].

### 9. UX simplificada + PDFs oficiales
*Commit: `56c7501`*

- **Tema claro/oscuro** al instante (tokens `TEMAS`, `_recolorear`, refresco de gráficos matplotlib).
- Panel de Gestión de **dos columnas** (accesos a la izquierda, paneles en línea), **edición inline** de personal, consulta local con botón **Hoy**.
- **PDFs oficiales** de permisos (firma de comprobante, `generar_pdf_permiso` sin DDL para evitar locks): [[Manual de Diseño UI-UX Simplificado y Reportes PDF]].

### 10. Catálogo reglamentario de permisos
*Commit: `ea33463`*

- `src/reglamento.py`: catálogo de **15 artículos para funcionarios y 18 para pasantes** (Res. 1307/2010 + 3028/2024) con cuotas por período (horas, días o veces), `usos_max` y `disponibilidad_permisos`.
- `auth.crear_justificacion` valida artículo por vínculo, fechas ≤ hoy y cuota; las justificaciones admiten `horas_usadas`.
- GUI: `JustificacionesTab` con artículos según vínculo, panel de disponibilidad y campo de horas; `EmployeeDashboard` con histórico **enero-cualquier-año → hoy**; `reports.py` con `resumen_empleado` y `resumen_historico`.
- Nota: [[Catálogo de Permisos y Licencias]].

### 11. Acceso, calendarios y tema (última fase)
*Commit: `e60bc94`*

- **Login unificado** en el escritorio: el kiosco solo marca; Portal y Gestión piden usuario + contraseña (bcrypt) con mensajes **"El usuario no existe."** y **"Contraseña incorrecta."**, y ruteo por rol (Empleado → resumen; RRHH/Admin → Panel de Gestión).
- **Cambio de contraseña** autoservicio: `auth.cambiar_clave` verifica la clave actual, exige 6+ caracteres y **audita el cambio** sin exponer hashes.
- **Corrección del tema claro/oscuro**: botones `_rol = "plano"` conservaban el hover del otro tema; el gráfico matplotlib del tablero del empleado no se redibujaba. Ambos resueltos (`_recolorear` + suscripción de `EmployeeDashboard._refrescar` al cambio de tema).
- **Calendario popup** con botón **Hoy** en los 4 campos de fecha (inicio/fin de justificación e histórico del empleado).
- **Art. 14 · Salidas por motivos personales** para pasantes: 4 h/mes con **máximo 3 usos/mes** (el primer intento de un 4.º uso se bloquea con mensaje de cuota).

### 12. Motor offline/online, notificaciones y facial
*Commit: `cbe0a08`*

- **M1 · Robustez offline/online**: cola local SQLite (`src/offline_queue.py`) + worker de fondo (`src/sync_worker.py`) que reinserta entrada/salida con el **timestamp original** y `sync_id` único (`ON CONFLICT ... DO NOTHING`); sin duplicados ni pérdida de hora. El kiosco guarda localmente si PostgreSQL no responde y muestra cuántas marcas esperan sincronizar.
- **M2 · Notificaciones en tiempo real**: `src/notifications.py` con bus pub/sub en memoria, persistencia en tabla `alertas` y **SMTP opcional** (`SMTP_HOST/PORT/USER/PASSWORD/FROM`). Hooks de alerta en cuota agotada (Art. 14), llegada tardía injustificada y fraude facial. **WebSocket** `/ws/alertas?token=` en `web_server.py` + endpoints `POST/GET /api/alertas` y `/api/alertas/leidas`; el panel de gestión **parpadea la campana** con la sección 🔔 **Alertas**.
- **M3 · Reconocimiento facial**: `src/facial.py` (OpenCV, Haar cascade en `data/`, LBPH con aumento sintético para entrenar con 1 foto). El kiosco valida el rostro contra la foto registrada en `PersonalTab`; si no coincide, bloquea la marcación y **audita FRAUDE** + alerta de alta severidad.
- **M4 · CI/CD**: `.github/workflows/deploy.yml` corre py_compile + smokes (headless y GUI bajo `xvfb-run`) contra un **PostgreSQL 16** de servicio, construye la imagen Docker y la publica en **GHCR** con disparo opcional a **Render**. Los smokes viven ahora en `tests/` con ruta relativa + `tests/setup_ci.py` que siembra admin/juan.

### 13. Kiosco web y Panel de Gestión en el navegador (Render = escritorio)
*Commit: pendiente (rama local)*

- **Objetivo**: que lo desplegado en **Render** ofrezca lo mismo que el ejecutable de escritorio, no solo el autoservicio del empleado.
- **Kiosco web** (`POST /api/marcar`): entrada/salida con cédula + contraseña (evita marcación por terceros), tolerancia de día lluvioso y **ticket oficial** `EMPRESA|3028/2024|...` con hash SHA-256. Vista pública de la página principal (primera pantalla, como el kiosco de escritorio).
- **Panel de Gestión web** (rol RRHH/Admin; `_exigir_rrhh`): pestañas **Resumen** (empleados, marcas de hoy, justificaciones, correcciones pendientes, alertas sin leer), **Personal** (alta/edición/eliminación con RBAC y salario), **Justificaciones** (catálogo real de permisos del reglamento, validación de cuotas y **PDF oficial**), **Correcciones** (aprobar/rechazar con materialización de la marca), **Alertas** (leer) y **Auditoría** (JSONB de trazabilidad).
- **Seguridad**: `_personal_publico` filtra credenciales (nunca se expone `password_hash`); un empleado recibe 403 en todo `/api/panel/*`; los PDFs del panel exigen rol RRHH.
- **Infra**: endpoints con modelos Pydantic, `List[Dict]` donde se devuelven listas (FastAPI valida la respuesta), nuevos métodos `db.list_solicitudes_correccion()` y `db.count_marcajes_hoy()`, `smoke_web_panel.py` en CI (`WEB_BASE` configurable) y README actualizado.

### 13. Auditoría técnica integral
*Corte: 2026-09-13 · commit `d0559f3` · sin cambios de código*

Revisión completa de las ~9.500 líneas del repositorio desde tres frentes —arquitectura/backend, producto/UX y seguridad/QA— con los hallazgos reproducidos ejecutando el código, no solo por lectura.

- **27 hallazgos** clasificados por severidad en [[Auditoría Técnica · Hallazgos Críticos]]: 4 bloqueantes (P0), 6 de fraude o liquidación (P1), 6 de flujos rotos (P2) y 11 de datos, cumplimiento y escala (P3).
- **Lo más grave**: XSS almacenado en el toast de alertas encadenado con `POST /api/alertas` sin control de rol, que permite a un empleado tomar la sesión de un Administrador; y `initialize()` ejecutándose en cada petición HTTP, que autobloquea el servidor en el pico de marcación.
- **Verificado ejecutando**: sobredeclaración de horas ordinarias en jornada mixta, feriado mal atribuido en turnos que cruzan la medianoche, feriados ciegos desde 2027, `TypeError` al aprobar correcciones de salida y `JORNADA_INICIO` ignorado.
- **Rediseño propuesto** en [[Arquitectura Objetivo · Plataforma y Portal del Empleado]] y [[Antifraude y Resiliencia en Picos de Marcación]].
- Notas corregidas por contradecir al código: [[Motor de Reglas de Horas Extra]] (tabla de ejemplos y tolerancias) y [[Ecosistema Sistema de Marcación]].

### 14. Remediación P0, motor horario y rediseño
*2026-09-14 · sobre el commit `d0559f3`*

**Bloqueantes cerrados.** Migraciones fuera del ciclo de petición (`migrate.py`, con `lock_timeout` de 10 s), orden del DDL corregido, cadena de XSS cortada (rol en `POST /api/alertas` + `textContent` + CSP) y secretos sin valor por defecto. Apareció un quinto bloqueante al abrir el portal en un navegador: un escape mal resuelto en el `f-string` anulaba el script entero y **el sitio estaba caído**.

**Motor horario reescrito.** El turno se parte en tramos por naturaleza y por día calendario; el tope de jornada se decide una vez (8 h / 7 h / 7 h 30, con el corte de 5 h nocturnas); se agregó el recargo nocturno del 30 % en columna propia, propagado al aguinaldo; y el calendario de feriados se compone para cualquier año. Detalle en [[Motor de Reglas de Horas Extra]].

**Rediseño de ambas interfaces.** La web salió del `f-string` a `src/static` (el servidor bajó de 1.565 a 774 líneas) y el portal se reordenó por lo que el empleado pregunta. La GUI adoptó la misma paleta a través de su sistema de tokens. Ver [[Sistema de Diseño · Planilla]].

**Cobertura nueva**: `test_motor_horario.py` (14 turnos frontera), `test_paleta.py` (contraste WCAG y paridad web/escritorio) y `smoke_portal_js.py` (sintaxis del portal y CSP).

### 15. Lenguaje visual «Planilla»
*2026-09-14 · mismo día, después de revisar la fase anterior con ojo de diseño*

El rediseño de la fase 14 arregló la **jerarquía** pero conservó la **forma**: tarjeta redondeada con sombra, barra con desenfoque, píldoras de estado, cuatro métricas de igual peso, grilla de cuadraditos para el mes y `Inter` como primera tipografía. Ninguno de esos patrones sale del problema que el software resuelve; juntos son la huella de una interfaz generada.

**Se reemplazó el objeto de referencia.** El antecesor físico de este sistema es la planilla de asistencia: hoja rayada, columnas, marca al margen, sello al pie. De ahí salen las cinco reglas del lenguaje —filetes en vez de cajas, cifras monoespaciadas tabulares, tres tipografías con oficio distinto, la tinta como acento y color sólo cuando significa. Detalle en [[Sistema de Diseño · Planilla]].

**Cambios de fondo, no cosméticos.** La tira de cuadraditos del mes pasó a ser un registro de seis columnas que también responde "a qué hora salí el martes"; las cuatro tarjetas de horas, a cuentas con línea de puntos y regla doble para el total; los saldos, a una línea por permiso con la regla de consumo sólo si algo se consumió. En el escritorio, `RADIO = 2` reemplazó a 27 `corner_radius`, las secciones del panel se numeran en vez de llevar glifos, y se agregaron los helpers `titulo()` y `cifra()`.

**Lo que atajó la verificación**: seis defectos que ninguna prueba veía —el borde de campo por debajo del mínimo de WCAG 1.4.11, las cabeceras numéricas tocándose en teléfono, la tabla comiéndose el margen lateral, las citas de artículo desapareciendo en pantalla angosta, el domingo futuro con rayas de fila cargada, y el reloj en vivo compitiendo con la hora del comprobante.

**Cobertura nueva**: `test_paleta.py` creció de 10 a 13 pares de contraste, de 4 a 8 tokens de paridad, y suma una guarda de **deriva del lenguaje** que falla si reaparecen los radios, las sombras, el desenfoque o la tipografía de plantilla.

### 16. Autoservicio de permisos y cierre de P1-1 / P1-2
*2026-09-14 · fraude y formularios*

**P1-1 cerrado.** La tolerancia climática la declaraba el propio empleado con una casilla del kiosco: el sujeto de la regla controlaba el dato que la disparaba. Ahora vive en `condiciones_dia`, la firma Recursos Humanos con techo de 120 minutos y alcanza a toda la plantilla. La cola offline dejó de transportarla: se resuelve al sincronizar, porque la declaración puede firmarse después de que el kiosco perdiera la conexión.

**P1-2 cerrado.** `facial.validar` devolvía `True` cuando el empleado no tenía foto de referencia —el estado por defecto de todo empleado nuevo—, así que bastaba con no registrarla para quedar exento. Ahora devuelve tres estados y la política vive aparte en `facial.decidir`: un rostro que no coincide bloquea siempre; la falta de datos para comparar sigue a `BIOMETRIA_OBLIGATORIA` y, con la política permisiva, la marca pasa pero queda grabada como *No verificada* y avisa a RRHH.

**Autoservicio completo.** El empleado pide cualquiera de los 32 artículos del reglamento desde el portal, con su saldo real y las condiciones a la vista; el pedido se valida antes de guardarse y una solicitud pendiente ya compromete la cuota. Detalle en [[Autoservicio de Permisos y Formularios]].

**Formularios automáticos.** Planilla de horas extraordinarias (Art. 232/233/234) y constancia de asistencia, compuestas desde los marcajes liquidados, sin ningún campo que completar.

**Lo que atajó la verificación**: el importe del recargo nocturno salía **negativo** —`RECARGO_NOCTURNO` es la tasa `0.30` y le restaba uno como si fuera el multiplicador `1.30`—, y el cartel de condición del kiosco no se releía al entrar a la vista.

**Cobertura nueva**: `test_condicion_dia.py` (P1-1 en motor, API e interfaces), `smoke_permisos_autoservicio.py` (ciclo completo y cuota reservada) y `test_planilla_extras.py` (detalle, totales y liquidación). `smoke_facial.py` reescrito para los tres estados. La suite pasó de 11 a 14 conjuntos.

### 17. Endurecimiento para operación y venta
*2026-09-14 · cierre de siete hallazgos*

**Lo que trababa la operación.** `detectar_accion_hoy` decidía por calendario, así que un turno que entraba el lunes 22:00 y salía el martes 06:00 dejaba al empleado sin poder marcar nada (P2-2); y una entrada sin cierre lo bloqueaba para siempre (P2-6). Ahora la decisión mira el estado real y las entradas vencidas pasan a *abandonadas* con aviso a RRHH, sin inventar la hora de salida. Además `_execute` deshace la transacción ante un error (P3-5): antes una consulta fallida dejaba la conexión inservible para todo lo que viniera después.

**Lo que costaba dinero.** La antigüedad se contaba desde el alta en el sistema (P3-6), así que migrar una plantilla dejaba a todos con cero años de servicio: 12 días de vacaciones en lugar de 20 o 30. Ahora hay `users.fecha_ingreso` y un único resolutor. Se sumó la **baja lógica**, que faltaba: antes solo existía el borrado, y borrar a quien se va destruye el respaldo de las liquidaciones que ya se le pagaron.

**Lo legal para vender.** La plantilla facial se guardaba en `BYTEA` plano; bajo la Ley 6534/2020 es dato de categoría especial (P3-1). Ahora va cifrada con AES-256-GCM, con la clave fuera de la base, ligada al empleado como dato autenticado y destruida con la baja. El login no tenía límite de intentos (P3-3) y el contenedor corría como root (P3-11).

**Lo que atajó la verificación**: `smoke_web_panel` falló contra un usuario con varias entradas trabadas y destapó que el descarte liberaba solo la última. También reveló que ese bloque de la prueba nunca se había ejecutado: esperaba `"Entrada"` capitalizado cuando la API siempre devolvió `"ENTRADA"`.

**Cobertura nueva**: `test_turno_nocturno.py`, `test_antiguedad_y_bajas.py` y `test_seguridad_datos.py`. La suite pasó de 14 a 17 conjuntos.

### 18. Entidad `turnos`: horarios, rotación y jornada partida
*2026-09-14 · cierre de P2-5 y P2-4*

**Lo que faltaba.** La hora de entrada era una constante del proceso, congelada al importar el módulo. Una empresa con dos turnos no podía usar el sistema, y el `JORNADA_INICIO` del `.env` se ignoraba en silencio. Ahora hay tres tablas —`turnos`, `turno_tramos`, `asignaciones_turno`— y una resolución por prioridad: rotación vigente, turno del legajo, predeterminado de la empresa. La rotación tiene vigencia, así que al vencer el empleado vuelve **solo** a su horario de contrato.

**Lo que la entidad dejó a la vista.** Tres defectos que la constante tapaba:

- La **salida anticipada** se medía contra el tope legal de la jornada, así que cerrar el tramo de la mañana de una jornada partida a las cuatro horas —lo pactado— se reprochaba por no llegar a ocho.
- Los **días de franco** se contaban como ausencia: quien tiene turno de martes a sábado figuraba ausente todos los lunes, y una ausencia injustificada descuenta.
- La **cola offline** decidía entrada o salida por fecha calendario, así que descartaba el segundo tramo de una jornada partida; y como `get_open_entry` ordenaba por inserción y no por hora, una salida repuesta podía cerrar la entrada equivocada.

**P2-4 de arrastre.** Con una sola hora de referencia ya no tenía sentido sostener dos definiciones de tardanza: `es_tardanza` (10 min) y `evaluar_asistencia` (15 min para funcionarios) daban veredictos distintos para la misma marca, de modo que corregir una marca **a su hora exacta** podía convertir un día normal en una llegada tardía. `es_tardanza` ahora delega.

**Lo que atajó la verificación.** Tres cosas, todas en pruebas que escribí junto con el código:

- La primera regla de "dónde empieza la jornada" —una ventana fija de 18 horas— bloqueaba al turno nocturno: la entrada de anoche a las 22:00 seguía contando al fichar hoy a la misma hora. La segunda —anclar a la hora prevista del turno— fallaba con quien trabaja lejos de su horario. La que quedó mira el **descanso** entre marcas, que no depende de horarios teóricos.
- `smoke_sync` destapó que `evaluar_asistencia` hacía `replace(tzinfo=None)` sin convertir: una marca guardada en UTC se evaluaba como si esa hora fuera local. La prueba lo daba por bueno porque estaba escrita con la misma confusión.
- El replay de una cola ya subida duplicaba marcas. La defensa vieja era "¿marcó ese día?", que la jornada partida invalida; la nueva pregunta si el instante cae **dentro** de una jornada ya registrada.

**Cobertura nueva**: `test_turnos.py`, 48 verificaciones sobre definición, resolución, tardanza, francos, jornada partida, turno nocturno, permisos y reposición offline. La suite pasó de 17 a 18 conjuntos.

### 19. Multiempresa: una instalación, varios clientes
*2026-09-14 · aislamiento verificado*

**Lo que había.** Una instalación por cliente. Funcionaba, pero diez clientes eran diez despliegues, diez bases y diez ventanas de mantenimiento. El costo de operación crecía en línea recta con las ventas.

**El modelo.** Doce tablas de datos llevan `empresa_id` con `ON DELETE CASCADE`; los roles no, porque son el mismo catálogo para todos. Lo que era único en toda la base pasó a serlo dentro de la empresa: el usuario, el nombre del turno, el turno predeterminado y la condición declarada de un día.

**Tres capas, porque una no alcanza.** Son más de setenta consultas y basta una sin acotar para mostrarle a un cliente la planilla de otro:

1. La conexión **falla cerrado**: sin empresa activa, toda consulta de datos de cliente lanza `SinEmpresa` en lugar de recorrer la tabla entera.
2. Un **verificador estático** lee `database.py` con el AST y falla si un método toca una tabla de empresa sin nombrar `empresa_id`.
3. Una **prueba de aislamiento** aloja dos empresas con los datos superpuestos a propósito —la misma cédula, el mismo nombre de turno, la misma fecha con condición— e intenta cruzar por listados, ids de URL, ediciones, borrados, login, token forjado, bus de alertas e informes.

**El acceso.** La pantalla de login no sabe de qué cliente es quien escribe, así que la cédula se busca en todas las empresas y **la contraseña decide**. El orden importa: preguntar primero por la empresa diría en qué clientes existe una cédula sin necesidad de saber su clave. Hecho el login, la empresa viaja firmada en el token y no se vuelve a preguntar.

**Lo que atajó cada capa.** La primera versión del verificador aceptaba un atajo —filtrar por `user_id` parecía suficiente, porque un marcaje pertenece a la empresa de su empleado— hasta que quedó claro que ese id llega por URL: RRHH de un cliente podía editar el legajo de otro probando números. Se quitó el atajo y se acotaron las 56 consultas que dependían de él. El fallo cerrado, apenas encendido, destapó que `generar_pdf_permiso` abría su propia conexión sin empresa. Y el DDL de arrendamiento, que corre en cada arranque, tomaba locks exclusivos incluso sin nada que migrar: ahora pregunta primero al catálogo.

**La cuarta capa: la base lo impone.** Las tres anteriores viven en el código. Cada tabla de datos de cliente quedó además con una política de seguridad por fila acotada a `app.empresa_id`, que la conexión publica al fijar la empresa. Una consulta sin `WHERE` deja de devolver las filas de todos y pasa a devolver ninguna; sin empresa declarada, tampoco.

Dos obstáculos concretos, los dos resueltos: el login cruza empresas a propósito, así que vive en `credenciales_por_usuario`, una función `SECURITY DEFINER` que devuelve lo mínimo para decidir un acceso; y las políticas son inertes bajo un superusuario, así que `migrate.py rol-app` crea el rol restringido con el que corre el servicio y `migrate.py` dice en cada corrida si el aislamiento está en vigor o no.

Se verificó con el rol restringido y consultas **deliberadamente sin acotar** —es la única forma de saber si la política protege o si el código se protege a sí mismo—, y después corriendo el servidor web completo bajo ese rol con la suite entera en verde.

**Cobertura nueva**: `test_multiempresa.py` (40 verificaciones, seis de ellas contra la base con el rol restringido) y `guardia_arrendamiento.py`, que además corre solo en CI. La suite pasó de 19 a 20 conjuntos.

## Cómo ejecutar

| Componente | Comando |
| --- | --- |
| CLI | `python src/app.py` |
| Escritorio (kiosco + gestión) | `python src/gui.py` |
| Web (kiosco + portal + panel RRHH) | `python src/web_server.py` (http://localhost:8000) |
| Contenedor | `docker build -t marcacion .` + `docker run -p 8000:8000 --env-file .env marcacion` |

Usuarios de demostración: **admin/admin123** (Administrador · Funcionario), **juan/clave123** (Empleado · Funcionario), pasante de prueba **5642815**.

## Validaciones ejecutadas

Los scripts de humo viven en `tests/` del repositorio (antes en la carpeta temporal):

- `validar_art14.py` — cuota y usos del Art. 14 (pasante) con limpieza. **OK**
- `smoke_login.py` — login (usuario inexistente/contraseña/roles), cambio de clave (4 validaciones + auditoría) y tema con dashboard abierto (gráfico reconstruido). **OK**
- `validar_reglamento.py` — catálogo, cuotas por horas, fechas futuras, vínculo, períodos anuales, resúmenes. **OK**
- `smoke_reglamento_gui.py` — JustificacionesTab + EmployeeDashboard + bloqueo por cuota en la GUI. **OK**
- `smoke_sync.py` — cola offline → PostgreSQL: timestamps preservados, sin duplicados (reinserción descartada), alerta de tardanza con `usuario_id`. **OK**
- `smoke_alertas.py` — API de alertas (401/200/leídas) + WebSocket con filtro por usuario. **OK**
- `smoke_facial.py` — sin rostro rechazado, foto registrada, verificación OK (confianza < 80) y bloqueo ante rostro distinto (217). **OK**
- `smoke_web_panel.py` — kiosco web (ticket con serie EMPRESA, contraseña incorrecta/usuario inexistente → 401), panel RRHH (resumen, CRUD personal, justificación + PDF, correcciones, auditoría, alertas) y 403 para roles sin permiso. **OK**
- `smoke_panel.py`, `prueba_dashboard.py`, `prueba_conatel_gui.py`, `diag_tema*.py`, `pdf_e2e.py`, `web_reglamento.py` — regresiones de UX, analítica, PDF y web. **OK**

### Lo que la suite no cubre

La auditoría del 2026-09-13 encontró defectos reproducibles **con la suite completa en verde**. El patrón es consistente: las pruebas son de humo sobre el camino feliz y no ejercitan fronteras ni rutas de error.

| Defecto no detectado | Qué prueba falta |
| --- | --- |
| Jornada mixta con 9 h ordinarias | Tabla de turnos frontera contra `calcular_horas_paraguay` (hoy no hay ninguna prueba unitaria del motor) |
| Feriado mal atribuido al cruzar medianoche | Turno sábado → domingo y domingo → lunes |
| Feriados ciegos desde 2027 | Aserción sobre una fecha del año siguiente |
| `TypeError` al aprobar corrección de salida | Caso de aprobación de reclamo tipo "Salida" de punta a punta |
| Turno nocturno que no se puede cerrar | `detectar_accion_hoy` con entrada abierta del día anterior |
| Esquema que no se crea desde cero | CI sobre base virgen, sin `setup_ci.py` previo |

Las dos primeras filas son la brecha de mayor valor: el motor horario es lógica pura, sin dependencias, y admite pruebas de tabla exhaustivas a costo casi nulo. Es el módulo más crítico del sistema y el único sin pruebas propias.

## Lecciones registradas

- **Locks de DDL**: `Database.initialize()` puede quedarse esperando por conexiones "idle in transaction" de scripts colgados; matar los procesos python y reintentar.
- **PowerShell cp1252**: no usar heredocs ni reescribir archivos UTF-8 vía PowerShell (corrompe acentos); los scripts de prueba van en la carpeta temporal.
- **`except ... as e` en Python 3**: la variable `e` se borra al salir del bloque; capturarla con `mensaje = str(e)` dentro.
- **Tema**: los widgets con `_rol = "plano"` deben actualizar `hover_color`; los gráficos matplotlib requieren refresco registrado (`registrar_refresco_tema`).
- **LBPH no entrena con 1 muestra**: la validación facial requiere ≥2 muestras; se resuelve con aumento sintético (desplazamientos de ±6 px) para entrenar con una sola foto por usuario.
- **OpenCV 5.0** ya no empaqueta el cascade Haar en `cv2.data`; el modelo vive en `data/haarcascade_frontalface_default.xml` (commit de 930 KB) y `facial.py` lo carga desde ahí.
- **Worker de sincronización**: no debe correr `initialize()` (DDL) en cada ciclo; una conexión larga con transacciones de lectura bloquea los `CREATE INDEX`. El hilo reutiliza la conexión ya inicializada de la app (`iniciar_hilo(..., db=...)`).
- **`ON CONFLICT (sync_id)`** con índice parcial requiere el predicado: `ON CONFLICT (sync_id) WHERE sync_id IS NOT NULL DO NOTHING`.
- **FastAPI valida la respuesta**: si el endpoint anota `-> Dict` pero devuelve una lista (o filas de psycopg2 con `date/time`), responde 500 con `ResponseValidationError`; anotar `List[Dict]` o devolver JSON-serializable.
- **`smoke_web_panel.py`**: los prints de respuestas binarias (PDF) rompen en consolas cp1252; imprimir el tamaño en bytes en lugar del texto.

### Agregadas por la auditoría (2026-09-13)

- **La lección del DDL se aprendió a medias**: ya estaba registrado que el worker de sincronización no debe correr `initialize()` en cada ciclo, pero `web_server._cliente()` lo sigue haciendo **en cada petición HTTP**. Cuando una lección se registra, hay que buscar todos los llamadores, no solo el que falló.
- **Un valor por defecto en un secreto es una vulnerabilidad, no una comodidad**: `JWT_SECRET_KEY` y `COMPROBANTE_CLAVE` tienen respaldo hardcodeado, así que una variable de entorno ausente no rompe nada — solo deja el sistema firmando con una clave pública. Los secretos se validan al arrancar y el proceso no levanta sin ellos.
- **Escapar en un lugar y no en otro es peor que no escapar**: el panel de alertas usa `esc()` y el toast en vivo no. La inconsistencia crea la falsa sensación de que el tema está resuelto.
- **Un control de seguridad que no puede verificar debe negar o marcar, nunca aprobar**: `facial.validar` devuelve `True` cuando el usuario no tiene foto, que es el estado por defecto de todo empleado nuevo.
- **Naive y aware no se mezclan**: `datetime.combine()` produce un instante sin zona que revienta al compararse con un `TIMESTAMPTZ` de PostgreSQL. La frontera de la aplicación debe normalizar a *aware* una sola vez.
- **Si el sujeto de una regla controla el dato que la activa, la regla no existe**: el checkbox de "día lluvioso" lo declara el propio empleado que se beneficia de la tolerancia.

### Agregadas por la entidad `turnos` (2026-09-14)

- **Una constante global no es una configuración con un solo valor; es la ausencia de la entidad.** `JORNADA_INICIO` parecía "la hora de entrada, configurable". No lo era: era la prueba de que el concepto *turno* no existía. Arreglar la lectura del `.env` habría cerrado el síntoma y dejado el sistema igual de invendible.
- **Una regla temporal no se define con una ventana fija.** "Las últimas N horas" falla en los dos extremos: corta jornadas partidas si N es chico y encadena jornadas distintas si es grande. La pregunta correcta era dónde está el **descanso**, que es un dato observable y no un parámetro a calibrar.
- **`replace(tzinfo=None)` no convierte, descarta.** Un `TIMESTAMPTZ` en UTC evaluado así se lee como si su hora fuera local. La prueba que lo cubría estaba escrita con la misma confusión, así que confirmaba el error en vez de encontrarlo: una prueba escrita desde la misma cabeza que el código hereda sus supuestos.
- **Elegir "el más cercano" premia faltar.** Al decidir contra qué tramo medir una llegada, la cercanía decía que presentarse a las 11:00 en un turno 07:00–11:00 / 14:00–18:00 era llegar tres horas temprano al segundo tramo. Es llegar cuatro tarde al primero. El orden de los tramos pendientes es el dato, no la distancia.
- **Una asignación sin vencimiento es una que alguien va a olvidar deshacer.** La rotación se modeló con `desde`/`hasta` desde el principio para que el caso normal —volver al turno de contrato— no dependa de que nadie se olvide.

### Agregadas por el multiempresa (2026-09-14)

- **Un invariante que depende de la disciplina no es un invariante.** "Acordate de filtrar por empresa" en setenta consultas es una fuga esperando el método setenta y cuatro. Lo que sostiene la regla es que el código la comprueba: el fallo cerrado en tiempo de ejecución y el verificador estático en CI.
- **La ausencia de datos tiene que ser un error, no una consulta sin filtro.** El diseño fácil era tratar "sin empresa" como "todas". Es el mismo error que un `WHERE` olvidado, solo que escrito a propósito.
- **Un atajo de seguridad se evalúa contra el atacante, no contra el flujo feliz.** Filtrar por `user_id` acota correctamente cuando el id viene de una consulta propia; no acota nada cuando viene de una URL que el atacante escribe.
- **El orden de las preguntas en un login es parte del diseño.** Pedir la empresa antes de la contraseña convierte la pantalla de acceso en un directorio de qué cédulas trabajan en qué cliente. Se busca primero y decide la clave.
- **Un bus en memoria no lo protege ninguna consulta.** El aislamiento de la base no alcanza para lo que nunca pasa por la base: las alertas en vivo se filtran en el proceso, y una alerta sin empresa no se entrega a nadie.
- **Una política que no se aplica es peor que no tenerla.** Las políticas RLS de PostgreSQL son inertes bajo un superusuario. Instalarlas no alcanza: hay que crear el rol restringido, correr el servicio con él, y que la herramienta de migración diga en voz alta cuál de los dos casos es el actual.
- **Una defensa se prueba atacándola, no leyéndola.** Comprobar que la política existe en `pg_policies` no dice nada. Lo que lo dice es conectar con el rol restringido y lanzar un `SELECT` sin `WHERE`.
- **Una capa de seguridad que rompe la aplicación no se va a activar.** Por eso el servidor completo se corrió bajo el rol restringido con la suite entera: hizo falta separar `initialize()` de `migrar()`, porque el rol que atiende tráfico no puede —ni debe— alterar tablas.

## Enlaces

- [[Auditoría Técnica · Hallazgos Críticos]] · [[Arquitectura Objetivo · Plataforma y Portal del Empleado]] · [[Antifraude y Resiliencia en Picos de Marcación]]
- [[Ecosistema Sistema de Marcación]] · [[Catálogo de Permisos y Licencias]] · [[Reglamento de Asistencia y Disciplina]]
- [[Manual de Diseño UI-UX Simplificado y Reportes PDF]] · [[Módulo de Justificaciones y Aguinaldos]] · [[Motor de Reglas de Horas Extra]] · [[Autoservicio de Permisos y Formularios]] · [[Turnos y Rotación de Horarios]]
- [[Módulo de Gestión de Usuarios]] · [[Control de Roles y Permisos RBAC]] · [[Panel de Reportes y Auditoría]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Multiempresa · Aislamiento entre Clientes]]