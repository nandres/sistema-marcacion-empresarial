# Manual de Diseño UI-UX Simplificado y Reportes PDF

> Rediseño integral del [[Ecosistema Sistema de Marcación|Sistema de Marcación]]: interfaz simplificada sin ventanas emergentes, temas dinámicos Claro/Oscuro aplicados al instante y generación automatizada de reportes PDF oficiales con validación SHA-256.

## Filosofía de diseño

- **Un solo clic a todo**: el Panel de Gestión usa dos columnas (botones grandes a la izquierda, paneles a la derecha). Nada de modales: el login, la consulta de marcas y la edición de personal ocurren en línea.
- **Login unificado**: el kiosco de marcación es público; el Portal del Empleado y la Gestión piden usuario + contraseña (bcrypt) y avisan "El usuario no existe." o "Contraseña incorrecta." El rol decide el destino (Empleado → resumen; RRHH/Admin → Panel de Gestión).
- **Temas dinámicos**: paletas declaradas como tokens (`t("CARD")`) con aplicación inmediata a todos los widgets y a los gráficos matplotlib; la web los replica con variables CSS y `localStorage`.

## Paleta de tokens

| Token | Oscuro | Claro |
| --- | --- | --- |
| `BG` | `#0B0B0C` | `#F8F9FA` |
| `CARD` | `#1E1E24` | `#FFFFFF` |
| `CARD_BORDER` | `#2A2A32` | `#E4E7EB` |
| `INPUT_BG` | `#191920` | `#FFFFFF` |
| `PRIMARY` | `#1A56DB` | `#1A56DB` |
| `TEXT` | `#F2F2EE` | `#1A1A1E` |
| `MUTED` | `#8E8E96` | `#6B7280` |
| `SUCCESS` | `#4ADE80` | `#16A34A` |
| `DANGER` | `#F0544F` | `#DC2626` |
| `ACCENTO` | `#F5C26B` | `#B45309` |

## Arquitectura del tema (escritorio)

- `src/gui.py`: diccionarios `TEMA_OSCURO`/`TEMA_CLARO`, registro `TEMAS`, función `t(clave)`.
- `_recolorear(widget, anterior, nuevo)`: recorrido recursivo del árbol; los widgets construidos con los helpers llevan `_rol` ("tarjeta", "primario", "secundario", "entrada", "switch_tema") y el resto se clasifica comparando colores contra el tema anterior (p. ej. un `CTkLabel` cuyo `text_color` era `MUTED` sigue siendo `MUTED`).
- `interruptor_tema(master, app)` en la cabecera pública y en el sidebar del Panel de Gestión.
- `MarcacionApp.registrar_refresco_tema(refresco)`: los paneles se suscriben (DashboardTab y AuditoríaTab) para redibujar datos y gráficos al alternar el tema; `_cambiar_tema()` sincroniza además las etiquetas de todos los interruptores.

## Portal del Empleado

- Kiosco público: reloj, marcación con interruptor de lluvia y ticket de salida; a la derecha, el **login** (usuario + contraseña) con enlace a **Cambiar contraseña** (verifica la clave actual, exige 6+ caracteres y audita el cambio).
- `EmployeeDashboard` en `src/gui.py`: tarjetas de **Vacaciones Art. 23** (disponibles/usadas/devengadas), **Permisos del mes Art. 25** (total + detalle por tipo) y **Horas extra del mes** (50%/100% Ley 213), gráfico matplotlib de horas ordinarias por día y lista de permisos con botón **Descargar PDF**.
- Datos provistos por `reports.resumen_empleado(db, user, fecha=None)`.

## Reportes PDF oficiales

- `reports.generar_pdf_permiso(solicitud_id: int) -> str` (firma exacta de negocio): abre su propia conexión (sin DDL, evita bloqueos de locks), localiza la justificación y devuelve la ruta del PDF en `reportes/permiso_{id:04d}.pdf`.
- Documento A4 con membrete simulado de la empresa (Dirección de Talento Humano), tabla de datos del empleado (cédula, vínculo, dependencia, período, días hábiles), firmas electrónicas del tutor y el sello institucional.
- **Integridad legal**: serie canónica `EMPRESA|3028/2024|id|username|tipo|inicio|fin|aprobador` → SHA-256 persistido en `justificaciones.hash_legal` (columna `VARCHAR(64)`).
- La web lo sirve por `GET /api/permiso/{solicitud_id}/pdf` (FileResponse) validando que la justificación pertenezca al usuario autenticado; la GUI lo abre con `os.startfile`.

## Administración simplificada

- `PanelGestion`: 6 secciones con botones grandes (Personal, Justificaciones, Reportes, Correcciones, Analítica, **Auditoría**); sección por defecto Personal.
- `AuditoriaTab`: bitácora JSONB (`logs_auditoria`) con valores anterior/nuevo por evento, alimentada por `database.listar_auditoria(limite=60)`.
- `PersonalTab`: edición **inline** debajo de la fila del empleado (salario, rol, vínculo, contraseña) — se eliminaron `EditEmployeeModal`, `LoginModal` y `ConsultaLocalModal`.
- `JustificacionesTab`: listado de permisos emitidos con hash visible y botón **Descargar PDF**.

## Web (portal responsivo)

- `src/web_server.py` v3.1: variables CSS + `data-theme` con alternador **◐ Claro/Oscuro** persistido en `localStorage`.
- Tras el login JWT (cédula + contraseña, 8 h) se muestra el tablero personal: tarjetas de vacaciones/permisos/extra, gráfico SVG de horas por día y botones PDF por permiso.
- Endpoints nuevos: `GET /api/resumen` y `GET /api/permiso/{id}/pdf`; conserva `/api/login`, `/api/consulta` y `/api/reclamo`.

## Decisiones de negocio

- El kiosco de escritorio es público solo para **marcar**; el resumen personal y la gestión exigen usuario + contraseña (el web conserva cédula + contraseña con JWT).
- `generar_pdf_permiso` usa `ensure_database()` + `connect()` en lugar de `initialize()` para no ejecutar DDL mientras otra conexión del sistema mantiene transacciones abiertas (evita esperas de locks).
- Los tests de humo deben setear `app.variable_tema.set(True)` antes de llamar `_cambiar_tema()` (el flujo real lo dispara el switch).

## Enlaces

- [[Ecosistema Sistema de Marcación]]
- [[Reglamento de Asistencia y Disciplina]] (Art. 23 y 25, Res. 3028/2024)
- [[Diseño de Interfaz Premium UI-UX]] (evolución del tema)
- [[Panel de Reportes y Auditoría]] (log JSONB)
- [[Panel de Analítica Visual y UX Premium]] (gráficos matplotlib)
- [[Estructura Web y Conexión Biométrica]] (servidor FastAPI)