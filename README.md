# Sistema de Marcación · Paraguay 🇵🇾

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![PostgreSQL](https://img.shields.io/badge/postgresql-14%2B-blue)](https://www.postgresql.org)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-2496ED)](https://docs.github.com/es/packages)
[![CI/CD](https://github.com/nandres/sistema-marcacion-empresarial/actions/workflows/deploy.yml/badge.svg)](https://github.com/nandres/sistema-marcacion-empresarial/actions/workflows/deploy.yml)

Sistema integral y resiliente para el control de asistencia laboral adaptado al marco legal paraguayo. Combina un kiosco de escritorio híbrido (Online/Offline), un portal web con kiosco de navegador y panel de gestión para Recursos Humanos, procesamiento biométrico por hardware e inteligencia normativa automatizada.

---

## ⚖️ Cumplimiento Normativo Integrado

El núcleo de cálculo del sistema (`clock_engine.py`) procesa las marcas abstrayendo la complejidad de la legislación laboral de Paraguay:

*   **Código del Trabajo (Ley 213/1993):** El turno se parte en tramos homogéneos por naturaleza (diurno/nocturno) y por día calendario, de modo que un turno que cruza hacia un domingo liquida al 100 % **solo esa porción**. El tope de jornada ordinaria se decide una vez para todo el turno (8 h diurna, 7 h nocturna, 7 h 30 mixta; nocturno ≥ 5 h reputa la jornada nocturna) y el excedente es extraordinario: +50 % diurno, +100 % nocturno. Las horas ordinarias nocturnas llevan el recargo del 30 % del Art. 232 y se liquidan en columna propia.
*   **Reforma Tributaria (Ley 6380/2019):** Cálculo automatizado del aguinaldo proporcional y acumulado.
*   **Res. Directorio 3028/2024:** Tolerancia climática y diferenciación estricta de reglas entre pasantes y funcionarios. La condición excepcional del día (lluvia intensa, corte de rutas, paro de transporte) la **declara Recursos Humanos** para toda la plantilla, con firma y auditoría: no es una casilla que marque quien llega tarde.
*   **Reglamento Interno (Res. 1307/2010):** Catálogo automatizado de permisos, licencias y control estricto de cuotas mensuales por horas o usos (bloqueo automático al 4.° uso del Art. 14).

La hora contra la que se mide cada llegada sale del **turno** del empleado: cada turno tiene su horario (una franja, o dos si la jornada es partida), los días de la semana que cubre y, si hace falta, su propia tolerancia. La rotación se programa con vigencia, así que al vencer la persona vuelve sola a su horario de contrato, y los días que su turno no cubre figuran como **franco** en lugar de contarse como ausencia.

---

## 🛠️ Stack Tecnológico y Módulos Core

### Frontend & Interfaces
Ambas interfaces comparten un lenguaje visual llamado **Planilla**, tomado del objeto que este software reemplaza: la hoja rayada de asistencia. La jerarquía la dan filetes tipográficos y espacio en blanco —ni una esquina redondeada ni una sombra proyectada en toda la hoja de estilos—, toda cifra va monoespaciada y tabular, y el color aparece sólo cuando significa algo. Se define una sola vez por tema y `tests/test_paleta.py` lo verifica en tres frentes: contraste WCAG AA, paridad entre web y escritorio, y deriva del lenguaje.

*   **Kiosco de Escritorio:** `CustomTkinter` con gráficos en vivo por `matplotlib`, temas claro/oscuro y paleta tokenizada.
*   **Web (Kiosco + Portal + Gestión):** `FastAPI` + `WebSockets` sirviendo una interfaz en archivos propios (`src/static`). El portal del empleado se ordena por lo que la persona realmente pregunta: **si marcó hoy**, cómo viene el mes en un registro día por día, cuántos días le quedan, y un pedido de corrección que nace desde el día que se toca. El panel de RRHH abre por **excepción** —lo que está sin resolver— y no por contadores de plantilla.
*   **Turnos reales:** un turno que entra un día y sale al siguiente se cierra normalmente —la acción se decide por el estado del empleado, no por el calendario— y una entrada que nadie cerró no lo deja trabado: pasadas 18 horas se marca como sin cierre, se avisa a Recursos Humanos y la hora se repone por el circuito de correcciones.
*   **Legajo con historia:** la antigüedad sale de la **fecha de ingreso del contrato**, de la que dependen los días de vacaciones y los meses de aguinaldo. La baja es lógica: el empleado pierde el acceso y sale de la nómina, pero sus marcajes y comprobantes se conservan para el archivo laboral.
*   **Autoservicio real:** el empleado pide cualquiera de los **32 artículos** del reglamento desde el portal, con su saldo y sus condiciones a la vista; el pedido se valida contra el artículo invocado antes de guardarse y una solicitud pendiente ya compromete la cuota. Aprobar emite la justificación y su PDF sin ningún paso extra. La **planilla de horas extraordinarias** (Art. 232/233/234) y la **constancia de asistencia** se componen solas desde los marcajes liquidados: ningún formulario se llena a mano.

### Backend, Datos y Resiliencia
*   **Base de Datos Principal:** `PostgreSQL 14+` con esquemas de auto-migración y pistas de auditoría mediante tipos de datos nativos `JSONB`.
*   **Arquitectura Tolerante a Fallos:** Si el servidor central PostgreSQL no responde, `offline_queue.py` captura localmente las marcas en `SQLite`. El componente `sync_worker.py` procesa la cola de fondo de manera **idempotente** (vía `sync_id` único), preservando el *timestamp* original de la marca sin duplicar registros.

### Hardware & Visión Artificial
*   **Validación Biométrica:** Integración directa por protocolo `TCP/IP` (Puerto 4370) con relojes biométricos `ZKTeco` para sincronización de personal. Las plantillas faciales se guardan **cifradas con AES-256-GCM** y se destruyen con la baja del empleado, conforme a la Ley N.º 6534/2020 de protección de datos personales.
*   **Seguridad y Auditoría:** Validación facial integrada con `OpenCV` (algoritmo LBPH) con **tres veredictos**, no dos. Un rostro que no coincide bloquea siempre y dispara una alerta de **FRAUDE**. Lo que el motor no puede verificar —sin foto de referencia, sin cámara— nunca se da por verificado: según `BIOMETRIA_OBLIGATORIA`, se bloquea o se registra en `marcajes.verificacion_facial` como *No verificada* con aviso a RRHH.

---

## 📂 Estructura del Proyecto

```text
src/
├── app.py            # Interfaz de Línea de Comandos (CLI) administrativa
├── gui.py            # Kiosco y Panel de Gestión de Escritorio (CustomTkinter)
├── web_server.py     # API del kiosco, el portal y el panel (FastAPI + WebSockets)
├── static/           # Interfaz web: index.html, estilos.css y portal.js
├── migrate.py        # Paso de despliegue: aplica el esquema antes de servir tráfico
├── database.py       # Capa de datos PostgreSQL, esquema y auditorías JSONB
├── auth.py           # Autenticación unificada, Control de Acceso Basado en Roles (RBAC) y JWT
├── clock_engine.py   # Motor de evaluación horaria y desglose legal paraguayo
├── turnos.py         # Horarios por empleado: tramos, días, rotación con vigencia
├── reglamento.py     # Lógica e interpretación de cuotas del catálogo de permisos
├── reports.py        # Módulo generador de reportes (PDF, XLSX, CSV, Aguinaldos)
├── offline_queue.py  # Gestor de cola transaccional local (SQLite)
├── sync_worker.py    # Trabajador en segundo plano para sincronización idempotente
├── notifications.py  # Bus de eventos en tiempo real y alertas SMTP
├── facial.py         # Módulo de reconocimiento facial y visión artificial
└── biometric_sync.py # Driver de comunicación TCP con relojes ZKTeco

tests/                # Suite de regresión y automatización (CI) + setup_ci.py
data/                 # Modelos Haar Cascade para detección facial
.github/workflows/    # Pipeline de Integración y Despliegue Continuo (CI/CD)
```

---

## 🚀 Instalación y Ejecución Local

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
source .venv/bin/activate  # En Windows use: .venv\Scripts\activate

# 3. Instalar dependencias del sistema
pip install -r requirements.txt
```

### Configuración de Variables de Entorno
Cree un archivo `.env` en la raíz del proyecto basándose en las siguientes variables obligatorias:
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

# Bloquea toda marca que el motor biométrico no pueda verificar. Viene
# apagada: una plantilla recién migrada no tiene fotos cargadas.
BIOMETRIA_OBLIGATORIA=0

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
```

> **`JWT_SECRET_KEY` y `COMPROBANTE_CLAVE` son obligatorias y no tienen valor por defecto.** Si falta alguna, o mide menos de 32 caracteres, el proceso aborta al arrancar en lugar de firmar con una clave conocida. Generá cada una con:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(48))"
> ```

### Inicialización de Componentes
El esquema se aplica en un paso explícito, previo a levantar cualquier proceso que atienda tráfico. Las migraciones toman locks exclusivos de tabla, así que no pueden correr dentro del ciclo de una petición:
```bash
# 1. Aplicar el esquema (crea la base si no existe)
python src/migrate.py

# 2. Ejecutar la CLI Administrativa
python src/app.py

# Lanzar la Interfaz Gráfica de Escritorio (Kiosco + Panel)
python src/gui.py

# Iniciar el Servidor Web (Acceso en http://127.0.0.1:8000)
python src/web_server.py
```
*Credenciales de demostración predeterminadas:*
*   **Administrador / RRHH:** `admin` / `admin123`
*   **Empleado Funcionario:** `juan` / `clave123`

---

## 🧪 Pruebas Automatizadas (CI)

La suite de pruebas incluye tests de humo de regresión funcional y simulación de interfaces gráficas sin entorno de visualización real (*headless*):

```bash
python tests/setup_ci.py             # Siembra de datos iniciales en DB limpia
python tests/smoke_portal_js.py      # Interfaz estática: sintaxis, CSP y sin scripts embebidos
python tests/test_motor_horario.py   # Turnos frontera: jornadas, recargos y feriados
python tests/test_paleta.py          # Contraste WCAG, paridad web/escritorio y deriva visual
python tests/test_condicion_dia.py   # Antifraude: la tolerancia la declara RRHH, no el empleado
python tests/smoke_permisos_autoservicio.py  # Pedido, cuota reservada, aprobación y PDF
python tests/test_planilla_extras.py # Planilla de horas extra y constancia de asistencia
python tests/test_turno_nocturno.py  # Turnos que cruzan la medianoche y jornadas sin cierre
python tests/test_turnos.py          # Horarios por empleado, rotación, jornada partida y francos
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

---

## 🐳 Despliegue y Pipeline DevOps

El repositorio cuenta con automatización total a través de **GitHub Actions** (`.github/workflows/deploy.yml`). Con cada `push` a la rama `main`, el pipeline ejecuta de forma asíncrona:

1.  **Testing en Aislamiento:** Levanta un servicio transitorio de PostgreSQL 16 y corre la suite completa de pruebas. Las pruebas de la GUI de escritorio se ejecutan utilizando un servidor virtual de pantalla mediante `xvfb-run`.
2.  **Containerización:** Construye la imagen Docker basada en `python:3.11-slim` garantizando un entorno optimizado y seguro.
3.  **Distribución (GHCR):** Publica de forma automática el artefacto en GitHub Container Registry (`ghcr.io`).
4.  **Continuous Deployment (Opcional):** Si se detecta el webhook secreto `RENDER_DEPLOY_HOOK`, gatilla de forma automática la actualización del entorno de producción en Render.

Para despliegues manuales en local:
```bash
docker build -t marcacion .
docker run -p 8000:8000 --env-file .env marcacion
```

---

## 📚 Documentación

La bóveda de documentación vive en [`Sistema de Marcacion/`](Sistema%20de%20Marcacion/) (Obsidian): la **Bitácora de Implementación** resume las fases construidas con sus commits, y las notas temáticas profundizan cada módulo (motor de horas extra, reglamento, despliegue, seguridad, etc.).

---
*Desarrollado bajo estrictos estándares normativos paraguayos. Los datos sensibles de empleados están protegidos; el archivo `.env` y los reportes generados están explícitamente ignorados en el control de versiones `.gitignore`.*
