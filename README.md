# Sistema de Marcación

Control de asistencia laboral bajo normativa paraguaya. Multiempresa, y sigue registrando cuando se corta la conexión.

[![CI](https://img.shields.io/github/actions/workflow/status/nandres/sistema-marcacion-empresarial/deploy.yml?branch=main&label=CI&style=flat-square)](https://github.com/nandres/sistema-marcacion-empresarial/actions/workflows/deploy.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)](https://www.python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-336791?style=flat-square)](https://www.postgresql.org)
[![Contenedor](https://img.shields.io/badge/Contenedor-ghcr.io-2496ED?style=flat-square)](https://github.com/nandres/sistema-marcacion-empresarial/pkgs/container/sistema-marcacion-empresarial)

Un kiosco de escritorio, un portal web con kiosco de navegador y panel de Recursos Humanos, lectura biométrica por hardware, y un motor que liquida la jornada —diurna, nocturna, mixta, feriados y horas extraordinarias— según el Código del Trabajo, en lugar de dejarla para la planilla de Excel del cierre de mes.

Una misma instalación puede alojar a varias empresas sin que ninguna vea los datos de otra, y el aislamiento no depende de que nadie se olvide un `WHERE`: lo impone PostgreSQL.

| | |
| --- | --- |
| **Normativa** | Ley 213/1993 · Ley 6380/2019 · Ley 6534/2020 · Res. 3028/2024 · Res. 1307/2010 |
| **Interfaces** | Kiosco de escritorio · Kiosco web · Portal del empleado · Panel de RRHH · CLI |
| **Resiliencia** | Cola local firmada, reposición idempotente y alertas entre procesos |
| **Pruebas** | 25 conjuntos en integración continua, sobre base virgen y pantalla virtual |

---

## Cumplimiento Normativo Integrado

El núcleo de cálculo (`clock_engine.py`) procesa las marcas abstrayendo la complejidad de la legislación laboral paraguaya:

*   **Código del Trabajo (Ley 213/1993):** El turno se parte en tramos homogéneos por naturaleza (diurno/nocturno) y por día calendario, de modo que un turno que cruza hacia un domingo liquida al 100 % **solo esa porción**. El tope de jornada ordinaria se decide una vez para todo el turno (8 h diurna, 7 h nocturna, 7 h 30 mixta; nocturno ≥ 5 h reputa la jornada nocturna) y el excedente es extraordinario: +50 % diurno, +100 % nocturno. Las horas ordinarias nocturnas llevan el recargo del 30 % del Art. 232 y se liquidan en columna propia.
*   **Reforma Tributaria (Ley 6380/2019):** Aguinaldo proporcional y acumulado, calculado sobre la antigüedad real del contrato.
*   **Res. Directorio 3028/2024:** Tolerancia climática y reglas distintas para pasantes y funcionarios. La condición excepcional del día —lluvia intensa, corte de rutas, paro de transporte— la **declara Recursos Humanos** para toda la plantilla, con firma y auditoría: no es una casilla que marque quien llega tarde.
*   **Reglamento Interno (Res. 1307/2010):** Catálogo de 32 artículos de permisos y licencias, con cuotas por horas o por usos y bloqueo automático al agotarlas. Los artículos que el reglamento cuenta en días hábiles se cuentan en días hábiles, y un permiso que cruza el año imputa a cada período los días que le tocan.
*   **Ley 6534/2020 · datos personales:** La plantilla facial es dato de categoría especial. Se guarda cifrada y se destruye con la baja del empleado.

---

## Cómo se decide a qué hora entra cada persona

La hora contra la que se mide cada llegada sale del **turno** del empleado: su horario (una franja, o dos si la jornada es partida), los días de la semana que cubre y, si hace falta, su propia tolerancia.

Una empresa que rota turnos define un **ciclo** —qué turnos, en qué orden y cada cuántos días— y el turno de cada día se calcula. No hay que cargar semana por semana, y el calendario sigue siendo correcto en cualquier fecha futura. La posición dentro del ciclo es lo que mantiene a dos compañeros en turnos distintos rotando juntos.

La rotación puntual se programa con vigencia y le gana al ciclo, así que al vencer la persona vuelve sola a donde estaba: nadie tiene que acordarse de deshacer el cambio. Los días que su turno no cubre figuran como **franco**, no como ausencia — es una diferencia que se paga.

Decidir si una marca abre una jornada o cierra la anterior no se hace por fecha (el turno nocturno reparte una jornada entre dos días) sino por el **descanso**: se camina hacia atrás y se corta en el primer hueco que constituya descanso entre jornadas.

---

## Aislamiento entre empresas alojadas

Cuatro capas lo sostienen, y cada una tapa lo que la anterior deja pasar:

| Capa | Qué hace |
| --- | --- |
| Esquema | Catorce tablas de datos llevan `empresa_id` |
| Tiempo de ejecución | Una conexión sin empresa activa **falla**, no devuelve todo |
| Verificador estático | Una consulta nueva sin acotar rompe la build |
| PostgreSQL | Políticas de seguridad por fila: al `WHERE` olvidado no le devuelve las filas de todos, le devuelve ninguna |

Ninguna sesión puede ver dos clientes a la vez. El cliente se resuelve por subdominio (`acme.midominio.com.py`) y cada empresa tiene su cupo de empleados activos, que se comprueba al dar de alta.

> Las políticas por fila **no alcanzan a un superusuario**: PostgreSQL lo exceptúa siempre. Ver [Rol del servicio](#rol-del-servicio-aislamiento-en-vigor).

---

## Stack Tecnológico y Módulos Core

### Frontend & Interfaces

Ambas interfaces comparten un lenguaje visual llamado **Planilla**, tomado del objeto que este software reemplaza: la hoja rayada de asistencia. La jerarquía la dan filetes tipográficos y espacio en blanco —ni una esquina redondeada ni una sombra proyectada en toda la hoja de estilos—, toda cifra va monoespaciada y tabular, y el color aparece solo cuando significa algo. Se define una vez por tema y `tests/test_paleta.py` lo verifica en tres frentes: contraste WCAG AA, paridad entre web y escritorio, y deriva del lenguaje.

*   **Kiosco de Escritorio:** `CustomTkinter` con gráficos en vivo por `matplotlib`, temas claro/oscuro y paleta tokenizada.
*   **Web (Kiosco + Portal + Gestión):** `FastAPI` + `WebSockets` sirviendo una interfaz en archivos propios (`src/static`). El portal del empleado se ordena por lo que la persona realmente pregunta: **si marcó hoy**, cómo viene el mes día por día, cuántos días le quedan, cómo rota su turno, y un pedido de corrección que nace desde el día que se toca. El panel de RRHH abre por **excepción** —lo que está sin resolver— y no por contadores de plantilla.
*   **Turnos reales:** un turno que entra un día y sale al siguiente se cierra normalmente —la acción se decide por el estado del empleado, no por el calendario— y una entrada que nadie cerró no lo deja trabado: pasadas 18 horas se marca como sin cierre, se avisa a RRHH y la hora se repone por el circuito de correcciones.
*   **Legajo con historia:** la antigüedad sale de la **fecha de ingreso del contrato**, de la que dependen los días de vacaciones y los meses de aguinaldo. La baja es lógica: el empleado pierde el acceso y sale de la nómina, pero sus marcajes y comprobantes se conservan para el archivo laboral.
*   **Autoservicio real:** el empleado pide cualquiera de los 32 artículos desde el portal, con su saldo y sus condiciones a la vista; el pedido se valida contra el artículo invocado antes de guardarse y una solicitud pendiente ya compromete la cuota. Aprobar emite la justificación y su PDF sin ningún paso extra. La **planilla de horas extraordinarias** (Art. 232/233/234) y la **constancia de asistencia** se componen solas desde los marcajes liquidados: ningún formulario se llena a mano.

### Backend, Datos y Resiliencia

*   **Base de datos:** `PostgreSQL 14+`, con auditoría en `JSONB`. El esquema se aplica en un **paso explícito de despliegue**, nunca dentro del ciclo de una petición: el DDL toma locks exclusivos de tabla.
*   **Sin conexión no se pierde una marca:** si PostgreSQL no responde, `offline_queue.py` guarda la marca en `SQLite` local. Cada marca se **firma con HMAC-SHA256** sobre todo lo que el servidor central va a creer, con la clave fuera del archivo: quien edite la base local a mano no puede fabricar una marca válida. Lo que no verifica se aparta con su motivo y avisa a RRHH, en vez de desaparecer en silencio.
*   **Reposición idempotente:** `sync_worker.py` procesa la cola en segundo plano preservando el *timestamp* original, sin duplicar registros (`sync_id` único) y con techo de reintentos — una marca que nunca va a entrar deja de consumir el ciclo cada quince segundos para siempre.
*   **Alertas en vivo entre procesos:** el aviso se difunde por `LISTEN`/`NOTIFY` de PostgreSQL, así que llega a los clientes conectados a **cualquier** worker y no solo al que la originó. Se eligió la base y no una pieza nueva porque la base ya es dependencia.
*   **Pool de conexiones** en el proceso que atiende tráfico, con la empresa borrada al devolver la conexión: una conexión reutilizada no hereda el cliente anterior.

### Hardware & Visión Artificial

*   **Relojes biométricos:** comunicación directa por `TCP/IP` (puerto 4370) con equipos `ZKTeco` para sincronizar el personal.
*   **Validación facial:** `OpenCV` (LBPH) con **tres veredictos**, no dos. Un rostro que no coincide bloquea siempre y dispara alerta de **fraude**. Lo que el motor no puede verificar —sin foto de referencia, sin cámara— nunca se da por verificado: según `BIOMETRIA_OBLIGATORIA` se bloquea o se registra como *No verificada* con aviso a RRHH.
*   **Prueba de vida opcional:** con `BIOMETRIA_PRUEBA_VIDA` encendida el kiosco sortea un gesto —acercarse, girar— y lo exige antes de capturar. Una foto quieta no pasa. Es un **disuasivo, no una prueba**: ver [Límites conocidos](#límites-conocidos).
*   **Plantillas cifradas:** `AES-256-GCM` con el `user_id` como dato asociado, y la clave fuera de la base. Mover una foto de una fila a otra la vuelve ilegible.

---

## Estructura del Proyecto

```text
src/
├── app.py            # CLI administrativa
├── gui.py            # Kiosco y Panel de Gestión de escritorio (CustomTkinter)
├── web_server.py     # API del kiosco, el portal y el panel (FastAPI + WebSockets)
├── static/           # Interfaz web: index.html, estilos.css y portal.js
├── migrate.py        # Paso de despliegue: aplica el esquema antes de servir tráfico
├── database.py       # Capa de datos PostgreSQL, esquema, aislamiento y auditoría JSONB
├── auth.py           # Autenticación, control de acceso por roles (RBAC) y JWT
├── clock_engine.py   # Motor de evaluación horaria y desglose legal paraguayo
├── turnos.py         # Horarios por empleado: tramos, días, rotación y ciclos
├── reglamento.py     # Catálogo de permisos, cuotas y conteo en días hábiles
├── reports.py        # Reportes y formularios (PDF, XLSX, CSV, aguinaldos)
├── biometria.py      # Cifrado en reposo de las plantillas faciales (AES-256-GCM)
├── facial.py         # Reconocimiento facial, veredictos y prueba de vida
├── biometric_sync.py # Driver TCP con relojes ZKTeco
├── offline_queue.py  # Cola local firmada (SQLite + HMAC)
├── sync_worker.py    # Reposición idempotente en segundo plano
├── rate_limit.py     # Freno a la fuerza bruta en autenticación
└── notifications.py  # Bus de alertas en vivo (LISTEN/NOTIFY) y correo SMTP

tests/                # Suite de regresión y automatización (CI) + setup_ci.py
data/                 # Modelos Haar Cascade para detección facial
.github/workflows/    # Pipeline de integración y despliegue continuo
```

---

## Instalación y Ejecución Local

### Requisitos Previos
*   Python 3.11+
*   PostgreSQL 14+
*   Cámara web (opcional, solo para el reconocimiento facial)

### Configuración del Entorno
```bash
# 1. Clonar el repositorio y acceder al directorio
git clone https://github.com/nandres/sistema-marcacion-empresarial.git
cd sistema-marcacion-empresarial

# 2. Configurar el entorno virtual de Python
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt
```

### Configuración de Variables de Entorno
Cree un archivo `.env` en la raíz del proyecto:
```ini
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marcacion
DB_USER=tu_usuario
DB_PASSWORD=tu_contraseña
JWT_SECRET_KEY=usa_un_token_seguro_hex
COMPROBANTE_CLAVE=clave_firma_comprobantes

# Hora con la que se siembra el turno inicial de la instalación. A partir de
# ahí los horarios se administran desde Gestión -> Turnos, uno por cada
# horario real de la empresa.
JORNADA_INICIO=08:00

# Empresa que atiende esta instalación cuando la base aloja a varias. Vacío
# con un solo cliente. Se usa en el escritorio y en la consola; la web la
# resuelve por sesión.
EMPRESA_ACTIVA=

# Razón social con la que se crea la empresa inicial en la primera migración.
EMPRESA_NOMBRE=Empresa

# Dominio propio para resolver el cliente por subdominio (acme.miapp.com.py).
# Vacío con un solo cliente: contar etiquetas del host no sirve, porque
# miapp.com.py ya tiene tres sin tener ningún subdominio.
DOMINIO_BASE=

# Bloquea toda marca que el motor biométrico no pueda verificar. Viene
# apagada: una plantilla recién migrada no tiene fotos cargadas.
BIOMETRIA_OBLIGATORIA=0

# Pide un gesto al azar (acercarse, girar) antes de aceptar la marca, para
# que una foto sostenida frente a la cámara no pase. Viene apagada porque
# cambia lo que la persona tiene que hacer para marcar.
BIOMETRIA_PRUEBA_VIDA=0

# Cifra las plantillas faciales en reposo (Ley 6534/2020). Si se pierde,
# las fotos registradas quedan ilegibles y hay que volver a tomarlas.
# Generar con:
#   python -c "import sys; sys.path.insert(0,'src'); import biometria; print(biometria.generar_clave())"
BIOMETRIA_CLAVE=clave_de_32_bytes_en_base64

# Opcionales
SMTP_HOST=smtp.tuproveedor.com   # Notificaciones por correo
SMTP_PORT=587
SMTP_USER=****
SMTP_PASSWORD=****
SMTP_FROM=no-reply@sistema-marcacion.com
HOST=127.0.0.1                   # Servidor web
PORT=8000
DB_POOL_MIN=1                    # Pool de conexiones del proceso que atiende
DB_POOL_MAX=10
```

> **`JWT_SECRET_KEY` y `COMPROBANTE_CLAVE` son obligatorias y no tienen valor por defecto.** Si falta alguna, o mide menos de 32 caracteres, el proceso aborta al arrancar en lugar de firmar con una clave conocida. Generá cada una con:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(48))"
> ```
> `COMPROBANTE_CLAVE` firma además las marcas de la cola sin conexión. Cambiarla invalida lo que haya quedado encolado sin reponer.

> **`BIOMETRIA_CLAVE` vive fuera de la base a propósito**, para que un backup robado no traiga con qué abrirlo. La contracara es que **restaurar la base sin la clave no recupera las fotos**: guardarla junto al backup anula la protección, no guardarla en ningún lado pierde el dato.

### Rol del servicio (aislamiento en vigor)

Las políticas de aislamiento por fila **no alcanzan a un superusuario**: PostgreSQL lo exceptúa siempre. El proceso que atiende tráfico debe correr con un rol restringido, que se crea una sola vez:

```bash
python src/migrate.py rol-app
```

Imprime el `DB_USER` y el `DB_PASSWORD` que van en el `.env` **del servicio**; el rol administrador se sigue usando solo para migrar, porque el rol restringido no puede —ni debe— alterar tablas. El nombre y la contraseña se pueden fijar con `DB_APP_USER` y `DB_APP_PASSWORD`. El comando es idempotente: sobre un rol que ya existe refresca los permisos sin rotar la contraseña, para no cortarle el acceso a un servicio en marcha.

`python src/migrate.py` informa en cada corrida si el aislamiento está activo o inerte.

### Inicialización de Componentes
```bash
# 1. Aplicar el esquema (crea la base si no existe)
python src/migrate.py

# 2. Ejecutar la CLI administrativa
python src/app.py

# Interfaz gráfica de escritorio (kiosco + panel)
python src/gui.py

# Servidor web (http://127.0.0.1:8000)
python src/web_server.py
```
*Credenciales de demostración:*
*   **Administrador / RRHH:** `admin` / `admin123`
*   **Empleado funcionario:** `juan` / `clave123`

---

## Pruebas Automatizadas (CI)

Veinticinco conjuntos, entre pruebas de regresión funcional y simulación de interfaces gráficas sin pantalla real (*headless*):

```bash
python tests/setup_ci.py             # Siembra de datos iniciales en base limpia
python tests/smoke_portal_js.py      # Interfaz estática: sintaxis, CSP y sin scripts embebidos
python tests/test_motor_horario.py   # Turnos frontera: jornadas, recargos y feriados
python tests/test_paleta.py          # Contraste WCAG, paridad web/escritorio y deriva visual
python tests/test_condicion_dia.py   # Antifraude: la tolerancia la declara RRHH, no el empleado
python tests/smoke_permisos_autoservicio.py  # Pedido, cuota reservada, aprobación y PDF
python tests/test_planilla_extras.py # Planilla de horas extra y constancia de asistencia
python tests/test_turno_nocturno.py  # Turnos que cruzan la medianoche y jornadas sin cierre
python tests/test_turnos.py          # Horarios por empleado, jornada partida y francos
python tests/test_rotacion.py        # Ciclos: semana A / semana B calculada, no cargada a mano
python tests/guardia_arrendamiento.py  # Ninguna consulta de datos de cliente sin acotar
python tests/test_multiempresa.py    # Dos clientes alojados: ningún dato cruzado
python tests/test_cola_firmada.py    # La cola offline rechaza marcas fabricadas a mano
python tests/test_sesion_cookie.py   # Sesión en cookie HttpOnly: ni en la URL ni en localStorage
python tests/test_bus_alertas.py     # Las alertas en vivo cruzan de un worker a otro
python tests/test_antiguedad_y_bajas.py  # Antigüedad desde el contrato y baja lógica
python tests/test_seguridad_datos.py # Biometría cifrada y freno de intentos fallidos
python tests/validar_art14.py        # Límites de cuota y usos del Art. 14
python tests/validar_reglamento.py   # Catálogo y cuotas de permisos por reglamento
python tests/smoke_sync.py           # Motor offline/online transaccional
python tests/smoke_facial.py         # Validación del motor de reconocimiento OpenCV
python tests/smoke_alertas.py        # API + WebSocket de alertas en vivo
python tests/smoke_login.py          # GUI: login, cambio de clave y temas
python tests/smoke_reglamento_gui.py # GUI: justificaciones y dashboard
python tests/smoke_web_panel.py      # Web: kiosco de navegador + panel RRHH
```

El aislamiento entre empresas se verifica **atacándolo**: `test_multiempresa.py` crea un rol restringido, se conecta con él y lanza consultas deliberadamente sin acotar, además de un `INSERT` contra la empresa ajena. Comprobar que la política existe en `pg_policies` no dice nada; esto sí.

---

## Despliegue y Pipeline DevOps

Con cada `push` a `main`, **GitHub Actions** (`.github/workflows/deploy.yml`) ejecuta:

1.  **Pruebas en aislamiento.** Levanta un PostgreSQL 16 transitorio, aplica el esquema sobre una base virgen y corre la suite completa. Las pruebas de la GUI de escritorio usan una pantalla virtual (`xvfb-run`).
2.  **Contenedor.** Construye la imagen sobre `python:3.11-slim`, con un usuario sin privilegios y las migraciones fuera de los workers.
3.  **Distribución.** Publica el artefacto en GitHub Container Registry (`ghcr.io`).
4.  **Despliegue (opcional).** Si existe el secreto `RENDER_DEPLOY_HOOK`, gatilla la actualización en Render. Si no, el paso saltea.

Para despliegues manuales en local:
```bash
docker build -t marcacion .
docker run -p 8000:8000 --env-file .env marcacion
```

---

## Límites conocidos

Lo que el sistema todavía no hace, dicho de frente:

*   **El kiosco no tiene identidad propia.** Una marca no registra desde dónde se hizo, así que *"marqué desde casa"* no es hoy un hecho detectable. Cerrarlo pide un certificado o token por dispositivo.
*   **La prueba de vida es un disuasivo, no una prueba.** Deja afuera el ataque habitual —una foto sostenida frente a la cámara— pero quien mueva el teléfono siguiendo la consigna pasa igual. Cerrarlo de verdad pide una cámara con infrarrojo o profundidad, o un modelo de anti-suplantación entrenado.
*   **Nadie probó la carga.** El perfil de uso de un control de asistencia concentra todo el tráfico del día en dos ventanas de quince minutos, y no hay ninguna prueba de doscientas personas marcando en el mismo minuto.
*   **Multi-sede sin husos propios.** La sucursal es hoy un campo del turno: alcanza para horarios por sede, no para sedes en husos horarios distintos.
*   **El cambio de horario rige hacia adelante y no versiona el pasado.** Si se edita un turno y después se corrige una marca vieja, la corrección usa el horario nuevo.
*   **Facturación**, fuera de alcance a propósito: el cupo por empresa existe para hacer cumplible un plan, no para cobrarlo.

---

## Documentación

La bóveda vive en [`Sistema de Marcacion/`](Sistema%20de%20Marcacion/) (Obsidian). La **Bitácora de Implementación** resume las fases construidas con sus commits; la **Auditoría Técnica** lista los defectos encontrados, cómo se cerró cada uno y qué sigue abierto; y las notas temáticas profundizan cada módulo: motor de horas extra, turnos y rotación, reglamento, multiempresa, seguridad y despliegue.

---
*Desarrollado bajo normativa laboral paraguaya. Los datos sensibles de empleados están protegidos: el archivo `.env` y los reportes generados están ignorados en el control de versiones.*
