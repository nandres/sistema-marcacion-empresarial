# Puesta en Marcha en un Cliente

> Guía operativa para instalar el sistema en una empresa: qué configurar, en qué orden, qué cargar antes de la primera marcación y qué **no** está incluido. Escrita el **2026-09-14**, con el sistema en el estado que describe [[Auditoría Técnica · Hallazgos Críticos]].

## Antes de instalar: lo que hay que preguntarle al cliente

Cuatro datos condicionan todo lo demás y conviene tenerlos antes de tocar un servidor.

| Pregunta | Dónde se configura | Si se equivoca |
| --- | --- | --- |
| ¿Qué turnos tiene la empresa? | *Gestión → Turnos* | Las tardanzas se miden contra la hora equivocada |
| ¿Hay pasantes o solo funcionarios? | `tipo_vinculo` de cada legajo | Se aplica el reglamento que no corresponde (Res. 3028/2024 vs 1307/2010) |
| ¿Desde cuándo trabaja cada persona? | `fecha_ingreso` del legajo | Vacaciones y aguinaldo mal liquidados |
| ¿Va a usar reconocimiento facial? | `BIOMETRIA_OBLIGATORIA` | Ver más abajo |

El `.env` conserva `JORNADA_INICIO` para sembrar el turno inicial de la instalación. Es un valor de arranque, no la configuración: a partir de ahí los horarios se administran desde la pantalla de turnos (ver [[Turnos y Rotación de Horarios]]).

**La fecha de ingreso es el dato que más se subestima.** De ella dependen los días de vacaciones (12 / 20 / 30 según la escala de la Ley 1626/00) y los meses de aguinaldo. Si se migra la plantilla sin cargarla, el sistema asume que todos ingresaron el día de la migración y liquida 12 días a quien le corresponden 30.

## Una instalación, uno o varios clientes

Una misma instalación puede alojar a varias empresas sin que ninguna vea los datos de otra ([[Multiempresa · Aislamiento entre Clientes]]). La migración crea la empresa inicial, que es la que usa un cliente único; las demás se alojan una por una:

```bash
python src/app.py alta-empresa
```

Cada empresa tiene su propio personal, sus turnos, sus permisos y su administrador. **Ninguna sesión puede ver dos a la vez**: cambiar de cliente exige volver a entrar.

## Orden de instalación

```bash
# 1. Dependencias
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. Secretos: los tres son obligatorios y sin valor por defecto
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # COMPROBANTE_CLAVE
python -c "import sys; sys.path.insert(0,'src'); import biometria; print(biometria.generar_clave())"

# 3. Esquema (paso explícito, previo a levantar cualquier proceso)
python src/migrate.py

# 4. Primer administrador
python src/app.py        # el menú ofrece crearlo si la tabla está vacía

# 5. Servidor
python src/web_server.py
```

El `migrate.py` avisa si `BIOMETRIA_CLAVE` falta y cifra las fotos que hayan quedado en claro de una instalación anterior.

## Los tres secretos

Ninguno tiene valor por defecto: el proceso no arranca sin ellos. Es deliberado —un secreto con respaldo hardcodeado deja el sistema firmando con una clave que cualquiera puede leer en el repositorio.

| Variable | Qué protege | Si se pierde |
| --- | --- | --- |
| `JWT_SECRET_KEY` | Firma de las sesiones | Se cierran todas las sesiones abiertas; nada más |
| `COMPROBANTE_CLAVE` | Firma HMAC de los tickets de marcación | Los comprobantes viejos dejan de verificarse |
| `BIOMETRIA_CLAVE` | Cifrado de las plantillas faciales | **Las fotos registradas quedan ilegibles y hay que volver a tomarlas** |

> [!warning] El backup de la base no alcanza
> `BIOMETRIA_CLAVE` vive fuera de la base a propósito: así un backup robado no trae con qué abrirlo. La contracara es que **restaurar la base sin la clave no recupera las fotos**. Guardar el `.env` en el mismo lugar que el backup anula la protección; guardarlo en ningún lado pierde el dato.

## Carga inicial de la plantilla

El orden importa porque cada paso depende del anterior:

1. **Roles**: ya vienen creados (`Administrador`, `Recursos Humanos`, `Empleado`).
2. **Turnos**, desde *Gestión → Turnos*: uno por cada horario real de la empresa. El esquema siembra una jornada administrativa de 8 horas que sirve de punto de partida y de red para quien todavía no tenga turno propio.
3. **Legajos**, desde *Gestión → Personal*, con **vínculo**, **fecha de ingreso** y **turno** reales.
4. **Fotos biométricas**, si se van a usar: desde la GUI de escritorio, botón *Foto* de cada legajo. Requiere cámara en el equipo.
5. **Feriados trasladados del año**: el calendario base ubica cada feriado en su fecha estatutaria, que es lo legalmente correcto a falta de decreto. Los traslados del año se cargan en `FERIADOS_TRASLADADOS` de `clock_engine.py`. Ver [[Motor de Reglas de Horas Extra]].

## La decisión sobre biometría

`BIOMETRIA_OBLIGATORIA` viene **apagada** y conviene dejarla así hasta terminar de cargar las fotos.

- **Apagada**: una marca que el motor no puede verificar pasa, pero queda registrada como *No verificada* y Recursos Humanos la ve contada en su bandeja. Es lo opuesto a darla por buena en silencio, que era el comportamiento anterior.
- **Encendida**: sin verificación no hay marca. Cierra también el kiosco web, porque el navegador no tiene cámara: solo marca el kiosco físico.

Encenderla con la plantilla a medio cargar deja gente sin poder marcar.

## Operación diaria

| Situación | Quién la resuelve | Dónde |
| --- | --- | --- |
| Alguien cambia de horario por un tiempo | RRHH programa una rotación con vigencia | *Gestión → Turnos* |
| Alguien olvidó marcar la salida | Se libera solo a las 18 h y avisa a RRHH | *Gestión → Pendientes* |
| Lluvia, paro de transporte, corte de rutas | RRHH declara la condición del día | *Gestión → Condiciones del día* |
| Pedido de permiso | El empleado desde el portal, RRHH aprueba | *Gestión → Pendientes* |
| Marca fallida | El empleado toca el día y pide corrección | *Gestión → Pendientes* |
| Planilla de horas extra | Se emite sola | Portal del empleado o *Personal → Extras* |
| Constancia para un banco | El empleado la baja del historial | Portal → *Historial → Constancia* |
| Alguien deja la empresa | RRHH lo da de baja (no lo elimina) | *Gestión → Personal* |

La diferencia entre **dar de baja** y **eliminar** es la que más hay que explicar: la baja conserva marcajes y comprobantes para el archivo laboral; eliminar los destruye y solo debería usarse para corregir un alta equivocada.

## Qué NO está incluido

Decirlo por adelantado evita una venta mal hecha.

| Falta | Consecuencia | Detalle |
| --- | --- | --- |
| **Prueba de vida en el reconocimiento facial** | Una foto en la pantalla de un celular pasa la verificación | P1-2, parte de fondo |
| **Bus de alertas fuera del proceso** | Con varios workers, una alerta en vivo llega solo a los clientes conectados a ese worker | P3-4 |
| **Cookie `HttpOnly` para la sesión** | El token vive en `localStorage` | P3-2 |
| **Aislamiento impuesto por la base (RLS)** | El aislamiento entre clientes lo garantiza la aplicación, verificada por prueba; PostgreSQL todavía no lo impone | [[Multiempresa · Aislamiento entre Clientes]] |
| **Pool de conexiones** | Cada petición abre y cierra su conexión; irrelevante para una empresa, relevante para muchas | — |
| **Calendario de rotación automática** | La rotación semana A / semana B se carga a mano, tramo por tramo | [[Turnos y Rotación de Horarios]] |

Lo primero de esa lista es lo que más se va a pedir: **prueba de vida** en el reconocimiento facial. Sin ella, la biometría disuade pero no prueba.

## Verificación post-instalación

La suite completa corre contra la base real y tarda unos minutos:

```bash
python tests/setup_ci.py
python tests/test_motor_horario.py
python tests/test_condicion_dia.py
python tests/test_turno_nocturno.py
python tests/test_turnos.py
python tests/test_multiempresa.py
python tests/test_antiguedad_y_bajas.py
python tests/test_seguridad_datos.py
python tests/test_planilla_extras.py
python tests/smoke_permisos_autoservicio.py
```

Si `test_seguridad_datos` falla en la primera comprobación, falta `BIOMETRIA_CLAVE`.

## Enlaces

[[Despliegue en la Nube e Infraestructura SaaS]] · [[Multiempresa · Aislamiento entre Clientes]] · [[Turnos y Rotación de Horarios]] · [[Auditoría Técnica · Hallazgos Críticos]] · [[Autoservicio de Permisos y Formularios]] · [[Seguridad y Cifrado de Comunicaciones]] · [[Reglamento de Asistencia y Disciplina]] · [[Motor de Reglas de Horas Extra]]
