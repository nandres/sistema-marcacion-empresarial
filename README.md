# Sistema de Marcación · Paraguay

Sistema integral de control de asistencia: **kiosco de marcación de escritorio**, **panel de Recursos Humanos**, **autoservicio web** para el empleado y **despliegue en contenedor** con integración continua.

Cumple la **Ley 213** (horas extra), la **Ley 6380/2019** (aguinaldo proporcional), la **Res. Directorio 3028/2024** (tolerancias y reglas para pasantes/funcionarios) y el **Reglamento Interno de Personal Res. 1307/2010** (catálogo de permisos y licencias).

---

## Características

| Área | Descripción |
| --- | --- |
| **Marcación** | Kiosco con auto-detección de entrada/salida, ticket criptográfico SHA-256, tolerancia climática (lluvia intensa +30 min) y día feriado/domingo. |
| **Offline/Online** | Si PostgreSQL no responde, la marca se guarda en una **cola local SQLite** y un worker de fondo la sincroniza con el **timestamp original**, sin duplicados (`sync_id` único). |
| **Reconocimiento facial** | Validación del rostro contra la foto registrada del empleado (OpenCV LBPH); si no coincide, la marcación se bloquea y se **audita FRAUDE**. |
| **Notificaciones** | Bus de alertas en tiempo real: cuota agotada (Art. 14), llegada tardía injustificada y fraude → campana parpadeante en el Panel de Gestión, **WebSocket** hacia el portal web y **correo SMTP** opcional con el comprobante. |
| **Reglamento** | 15 artículos para funcionarios y 18 para pasantes (Res. 1307/2010 + 3028/2024) con cuotas mensuales por horas, días o usos, y bloqueo del 4.º uso del Art. 14. |
| **RRHH** | Alta/edición de personal con roles RBAC, justificaciones con PDF oficial CONATEL, correcciones de marcación, reportes xlsx/csv y aguinaldo. |
| **Analítica** | Dashboard con gráficos (CustomTkinter + matplotlib) y tema claro/oscuro al instante. |
| **Autoservicio web** | FastAPI + JWT (8 h): resumen personal, histórico, permisos con PDF y reclamos de marcación. |
| **Biometría ZKTeco** | Sincronización TCP/IP (puerto 4370) de empleados con el reloj biométrico. |
| **Despliegue** | Docker (gunicorn + uvicorn) y **GitHub Actions** que corre los tests contra PostgreSQL y publica la imagen en GHCR con disparo opcional a Render. |

## Estructura

```
src/
├── app.py            # CLI administrativa
├── gui.py            # Escritorio CustomTkinter (kiosco + Panel de Gestión)
├── web_server.py     # Portal del empleado (FastAPI + WebSockets)
├── database.py       # PostgreSQL (esquema auto-migrado, auditoría JSONB)
├── auth.py           # Login unificado, JWT, cambio de clave, justificaciones
├── clock_engine.py   # Evaluación de asistencia y desglose legal de horas
├── reglamento.py     # Catálogo de permisos y cuotas reglamentarias
├── reports.py        # Comprobantes, PDFs, xlsx/csv, aguinaldo
├── offline_queue.py  # Cola local SQLite para marcaciones sin red
├── sync_worker.py    # Sincronización offline → PostgreSQL (idempotente)
├── notifications.py  # Bus de alertas en vivo + SMTP opcional
├── facial.py         # Reconocimiento facial del kiosco
└── biometric_sync.py # Reloj ZKTeco (TCP 4370)

tests/                # Smokes de regresión (CI) + setup_ci.py
data/                 # Haar cascade del detector facial
.github/workflows/    # CI/CD (deploy.yml)
```

## Requisitos

- **Python 3.11+** (desarrollo local; el contenedor usa 3.11-slim)
- **PostgreSQL 14+** (o `DATABASE_URL`)
- Cámara web (opcional, solo para el reconocimiento facial)
- Windows/Linux/macOS; en CI la GUI corre bajo `xvfb`

## Instalación y ejecución

```bash
# 1) Entorno virtual e instalación
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt

# 2) Configurar el acceso a PostgreSQL (.env, ver más abajo)
#    La primera ejecución crea la base automáticamente.

# 3) Ejecutar el componente deseado
python src/app.py                 # CLI administrativa
python src/gui.py                 # Escritorio (kiosco + gestión)
python src/web_server.py          # Portal web → http://127.0.0.1:8000
```

**Usuarios de demostración:** `admin/admin123` (Administrador · Funcionario) y `juan/clave123` (Empleado · Funcionario).

## Variables de entorno (`.env`)

```ini
# PostgreSQL (o usa DATABASE_URL completa)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marcacion
DB_USER=marcacion
DB_PASSWORD=****

# Seguridad (generar con: python -c "import secrets; print(secrets.token_hex(32))")
JWT_SECRET_KEY=****

# Reglas (opcional)
JORNADA_INICIO=08:00
COMPROBANTE_CLAVE=****

# Correo de notificaciones (opcional: sin esto el ticket no se envía)
SMTP_HOST=smtp.tuproveedor.com
SMTP_PORT=587
SMTP_USER=****
SMTP_PASSWORD=****
SMTP_FROM=sistema@conatel.gob.py

# Servidor web (opcional)
HOST=127.0.0.1
PORT=8000
```

## Despliegue

```bash
docker build -t marcacion .
docker run -p 8000:8000 --env-file .env marcacion
```

El pipeline de **GitHub Actions** (`.github/workflows/deploy.yml`) ejecuta en cada push a `main`:

1. Compilación de todos los módulos y smokes headless contra un **PostgreSQL 16** de servicio.
2. Smokes de GUI bajo `xvfb-run` (login, temas, justificaciones).
3. Smoke del servidor web (login + alertas reales por HTTP).
4. Build de la imagen Docker → **GHCR** (`ghcr.io/<repo>:latest`).
5. Si se define el secreto `RENDER_DEPLOY_HOOK`, dispara el despliegue en Render.

## Pruebas

```bash
python tests/setup_ci.py            # siembra admin/juan (requiere DB vacía)
python tests/validar_art14.py       # cuota y usos del Art. 14
python tests/validar_reglamento.py  # catálogo de permisos
python tests/smoke_sync.py          # motor offline/online
python tests/smoke_facial.py        # reconocimiento facial
python tests/smoke_alertas.py       # API + WebSocket de alertas
python tests/smoke_login.py         # GUI: login, clave, temas
python tests/smoke_reglamento_gui.py# GUI: justificaciones y dashboard
```

## Documentación

La bóveda de documentación vive en [`Sistema de Marcacion/`](Sistema%20de%20Marcacion/) (Obsidian): la **Bitácora de Implementación** resume las 12 fases construidas con sus commits, y las notas temáticas profundizan cada módulo (motor de horas extra, reglamento, despliegue, seguridad, etc.).

---

*Sistema desarrollado para cumplir la normativa laboral y reglamentaria envase a la CONATEL Paraguay. Los datos de empleados son confidenciales: no subir `.env` ni reportes generados (ambos ignorados por git).*
