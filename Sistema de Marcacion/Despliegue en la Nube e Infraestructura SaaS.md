# Despliegue en la Nube e Infraestructura SaaS

> El camino a producción 24/7: el servidor web de consulta y reclamos
> empaquetado en un contenedor Docker listo para Render o Railway, con la
> conexión a PostgreSQL desacoplada de la PC local mediante la variable
> universal `DATABASE_URL`.

## Contenedor Docker (`Dockerfile`)

- Imagen base ligera `python:3.11-slim` con pip sin caché y
  `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED` para logs fluidos.
- Instala `requirements.txt` completo (uvicorn, gunicorn, psycopg2-binary,
  pandas, matplotlib, PyJWT, bcrypt, fastapi…).
- Expone el **puerto 8000** y arranca con **gunicorn + worker Uvicorn**
  (4 workers) escuchando en `0.0.0.0`; el puerto se toma de `PORT` (lo que
  inyectan Render/Railway) con respaldo a 8000.
- `HEALTHCHECK` propio (HTTP GET a `/` cada 30 s) para que la plataforma
  detecte la salud real del servicio.
- `.dockerignore` excluye `.env`, la bóveda Obsidian, git y reportes
  locales: los secretos y documentos internos nunca entran a la imagen.

## Conexión flexible a PostgreSQL

`database.py` ahora prioriza **`DATABASE_URL`** (estándar de Render y
Railway, formato `postgresql://usuario:clave@host:puerto/base`) y la
descompone con `urllib.parse` decodificando valores escapados (`%40` en
contraseñas). Si no existe, cae a las variables clásicas (`DB_HOST`,
`DB_NAME`, …) del `.env` para desarrollo local. Las credenciales nunca se
hardcodean: ni localhost ni la nube.

El servidor web respeta `HOST`/`PORT` del entorno, por lo que el mismo
código corre en la PC y en el proxy de la nube sin cambios.

## Flujo de despliegue (Render / Railway)

1. Conectar el repositorio de GitHub.
2. La plataforma detecta el `Dockerfile`, construye y publica el puerto.
3. Proveer en el panel: `DATABASE_URL` (base PostgreSQL cloud), la
   `JWT_SECRET_KEY` de producción y, si se desea, `HOST=0.0.0.0`.
4. La migración de esquema se ejecuta sola al arrancar (`initialize()`),
   igual que en local.

```mermaid
flowchart LR
    GIT[GitHub] -->|Dockerfile detectado| RENDER[Render / Railway]
    RENDER -->|build + puerto 8000| CONT[Contenedor python:3.11-slim]
    CONT -->|gunicorn · uvicorn workers| WEB[web_server.py]
    WEB -->|DATABASE_URL| PG[(PostgreSQL cloud)]
    SEC[JWT_SECRET_KEY de producción] --> WEB
```

## Verificación realizada

- Imagen construida sin errores ni advertencias.
- Contenedor ejecutado localmente: estado `healthy` (healthcheck superado)
  y `HTTP 200` con la página de login del autoservicio.

## Vinculación

- [[Estructura Web y Conexión Biométrica]]
- [[Seguridad y Cifrado de Comunicaciones]]
- [[Ecosistema Sistema de Marcación]]
- [[Panel de Analítica Visual y UX Premium]]