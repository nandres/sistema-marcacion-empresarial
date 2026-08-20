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

## Enlaces

- [[Ecosistema Sistema de Marcación]] · [[Catálogo de Permisos y Licencias]] · [[Reglamento de Asistencia y Disciplina]]
- [[Manual de Diseño UI-UX Simplificado y Reportes PDF]] · [[Módulo de Justificaciones y Aguinaldos]] · [[Motor de Reglas de Horas Extra]]
- [[Módulo de Gestión de Usuarios]] · [[Control de Roles y Permisos RBAC]] · [[Panel de Reportes y Auditoría]] · [[Seguridad y Cifrado de Comunicaciones]]