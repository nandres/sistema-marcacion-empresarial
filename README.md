# Sistema de Marcación · Paraguay 🇵🇾

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![PostgreSQL](https://img.shields.io/badge/postgresql-14%2B-blue)](https://www.postgresql.org)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-2496ED)](https://docs.github.com/es/packages)
[![CI/CD](https://github.com/nandres/sistema-marcacion-empresarial/actions/workflows/deploy.yml/badge.svg)](https://github.com/nandres/sistema-marcacion-empresarial/actions/workflows/deploy.yml)

Sistema integral y resiliente para el control de asistencia laboral adaptado al marco legal paraguayo. Combina un kiosco de escritorio híbrido (Online/Offline), un portal web con kiosco de navegador y panel de gestión para Recursos Humanos, procesamiento biométrico por hardware e inteligencia normativa automatizada.

---

## Cumplimiento Normativo Integrado

El núcleo de cálculo del sistema (`clock_engine.py`) procesa las marcas abstrayendo la complejidad de la legislación laboral de Paraguay:

*   **Código del Trabajo (Ley 213/1993):** Cómputo automatizado de horas extraordinarias (recargos 50%/100%), turnos nocturnos que cruzan la medianoche y validación de jornadas máximas (8 h diurnas / 7 h nocturnas).
*   **Reforma Tributaria (Ley 6380/2019):** Cálculo automatizado del aguinaldo proporcional y acumulado.
*   **Res. Directorio 3028/2024:** Motor de tolerancia climática (+30 min en días de lluvia intensa) y diferenciación estricta de reglas de negocio entre pasantes y funcionarios.
*   **Reglamento Interno (Res. 1307/2010):** Catálogo automatizado de permisos, licencias y control estricto de cuotas mensuales por horas o usos (bloqueo automático al 4.° uso del Art. 14).

---

## Stack Tecnológico y Módulos Core

### Frontend & Interfaces
*   **Kiosco de Escritorio:** Desarrollado con `CustomTkinter` y gráficos en tiempo real mediante `matplotlib`. Diseñado para despliegues locales con soporte nativo de temas claro/oscuro.
*   **Web (Kiosco + Portal + Gestión):** Construido sobre `FastAPI` + `WebSockets` para alertas en vivo, autenticación mediante `JWT` (sesiones de 8 horas), marcación desde el navegador con ticket oficial y **Panel de Gestión RRHH completo** (personal, justificaciones con PDF oficial, correcciones, alertas y auditoría).

### Backend, Datos y Resiliencia
*   **Base de Datos Principal:** `PostgreSQL 14+` con esquemas de auto-migración y pistas de auditoría mediante tipos de datos nativos `JSONB`.
*   **Arquitectura Tolerante a Fallos:** Si el servidor central PostgreSQL no responde, `offline_queue.py` captura localmente las marcas en `SQLite`. El componente `sync_worker.py` procesa la cola de fondo de manera **idempotente** (vía `sync_id` único), preservando el *timestamp* original de la marca sin duplicar registros.

### Hardware & Visión Artificial
*   **Validación Biométrica:** Integración directa por protocolo `TCP/IP` (Puerto 4370) con relojes biométricos `ZKTeco` para sincronización de personal.
*   **Seguridad y Auditoría:** Validación facial integrada con `OpenCV` (algoritmo LBPH). Los descalces biométricos bloquean la operación y disparan alertas inmediatas de **FRAUDE** en el panel de RRHH.

---

## Estructura del Proyecto

```text
src/
├── app.py            # Interfaz de Línea de Comandos (CLI) administrativa
├── gui.py            # Kiosco y Panel de Gestión de Escritorio (CustomTkinter)
├── web_server.py     # Kiosco de navegador, Portal Web y API (FastAPI + WebSockets)
├── database.py       # Capa de datos PostgreSQL, migraciones y auditorías JSONB
├── auth.py           # Autenticación unificada, Control de Acceso Basado en Roles (RBAC) y JWT
├── clock_engine.py   # Motor de evaluación horaria y desglose legal paraguayo
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
JORNADA_INICIO=08:00
COMPROBANTE_CLAVE=clave_firma_comprobantes

# Opcionales
SMTP_HOST=smtp.tuproveedor.com   # Notificaciones por correo
SMTP_PORT=587
SMTP_USER=****
SMTP_PASSWORD=****
SMTP_FROM=no-reply@sistema-marcacion.com
HOST=127.0.0.1                   # Servidor web
PORT=8000
```

### Inicialización de Componentes
La base de datos se migrará automáticamente en el primer arranque:
```bash
# Ejecutar la CLI Administrativa
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

## Pruebas Automatizadas (CI)

La suite de pruebas incluye tests de humo de regresión funcional y simulación de interfaces gráficas sin entorno de visualización real (*headless*):

```bash
python tests/setup_ci.py             # Siembra de datos iniciales en DB limpia
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

## Despliegue y Pipeline DevOps

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

## Documentación

La bóveda de documentación vive en [`Sistema de Marcacion/`](Sistema%20de%20Marcacion/) (Obsidian): la **Bitácora de Implementación** resume las fases construidas con sus commits, y las notas temáticas profundizan cada módulo (motor de horas extra, reglamento, despliegue, seguridad, etc.).

---
*Desarrollado bajo estrictos estándares normativos paraguayos. Los datos sensibles de empleados están protegidos; el archivo `.env` y los reportes generados están explícitamente ignorados en el control de versiones `.gitignore`.*
