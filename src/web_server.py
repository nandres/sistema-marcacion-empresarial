"""Servidor web de consulta histórica y reclamos con autenticación JWT.

FastAPI con Modo Oscuro Premium (#121214) y diseño responsivo: el empleado
inicia sesión con su cédula y contraseña (bcrypt), recibe un token JWT con
vigencia de 8 horas y a partir de allí consulta su historial completo de
marcas, horas extra 50%/100% y aguinaldo devengado en el período elegido
(Ley N.º 6380/2019). También puede enviar Solicitudes de Corrección a
Recursos Humanos si olvidó marcar o el sensor biométrico falló.

La identidad del empleado se resuelve exclusivamente desde el token; la
cédula nunca viaja en el formulario ni en la URL. Las consultas son de solo
lectura y cada petición abre y cierra su propia conexión a PostgreSQL.

Ejecución:
    python src/web_server.py          # http://127.0.0.1:8000
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import auth
import database
import reports

app = FastAPI(
    title="Consulta de Marcas · Sistema de Marcación",
    description="Historial de marcas, horas extra y reclamos de corrección.",
    version="3.0.0",
)

BG = "#121214"
CARD = "#1B1B1F"
PRIMARY = "#1A56DB"
TEXT = "#F2F2EE"
MUTED = "#8E8E96"

PAGINA_HTML: str = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Consulta de Marcas · Sistema de Marcación</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    background:{BG}; color:{TEXT};
    font-family:'Segoe UI', system-ui, sans-serif;
    display:flex; flex-direction:column; align-items:center;
    padding:24px 16px; min-height:100vh;
  }}
  .tarjeta {{
    background:{CARD}; border:1px solid #26262C; border-radius:12px;
    padding:28px 24px; width:100%; max-width:560px; margin-bottom:20px;
  }}
  h1 {{ font-size:1.35rem; text-align:center; }}
  .subtitulo {{ color:{MUTED}; font-size:.85rem; text-align:center; margin-top:6px; }}
  .cabecera {{
    width:100%; max-width:560px; display:flex; justify-content:space-between;
    align-items:center; margin-bottom:12px;
  }}
  .cabecera b {{ font-size:.95rem; }}
  label {{ display:block; font-size:.85rem; color:{MUTED}; margin:16px 0 6px; }}
  input, select, textarea {{
    width:100%; padding:12px 14px; border-radius:10px; border:1px solid #34343B;
    background:#232329; color:{TEXT}; font-size:1rem; outline:none;
    font-family:'Segoe UI', system-ui, sans-serif;
  }}
  input:focus, select:focus, textarea:focus {{ border-color:{PRIMARY}; }}
  textarea {{ min-height:90px; resize:vertical; }}
  .fila {{ display:flex; gap:8px; margin-top:8px; }}
  .fila > div {{ flex:1; }}
  .fila input {{ width:100%; }}
  button {{
    background:{PRIMARY}; color:#fff; border:none; border-radius:12px;
    padding:14px 22px; font-size:1rem; font-weight:700; cursor:pointer;
    width:100%; margin-top:18px; transition:background .15s;
  }}
  button:hover {{ background:#2E66E8; }}
  .btn-secundario {{
    background:transparent; border:1px solid {MUTED}; color:{MUTED};
    width:auto; padding:10px 16px; margin:0; font-size:.85rem;
  }}
  .btn-hoy {{ width:auto; margin:0; padding:13px 18px; white-space:nowrap; }}
  .oculto {{ display:none; }}
  #resultado {{ margin-top:22px; }}
  .bloque {{ background:#232329; border-radius:10px; padding:16px; margin-top:12px; }}
  .bloque h3 {{ font-size:.9rem; color:{MUTED}; margin-bottom:10px; font-weight:600; }}
  .fila-marca {{
    display:flex; justify-content:space-between; align-items:center;
    padding:7px 0; border-bottom:1px solid #26262C; font-size:.9rem; gap:8px;
  }}
  .fila-marca:last-child {{ border-bottom:none; }}
  .fila-marca small {{ color:{MUTED}; }}
  .etiqueta {{
    font-size:.7rem; padding:3px 8px; border-radius:999px; font-weight:600;
    white-space:nowrap;
  }}
  .tardanza {{ background:#3A1F1E; color:#F0544F; }}
  .feriado {{ background:#3A2F1E; color:#F5C26B; }}
  .clima {{ background:#1E2A3A; color:#7FB4F0; }}
  .ok {{ background:#12301F; color:#4ADE80; }}
  .total {{ font-size:1.15rem; font-weight:700; margin-top:8px; }}
  .error {{ color:#F0544F; text-align:center; margin-top:16px; }}
  .exito {{ color:#4ADE80; text-align:center; margin-top:14px; }}
  .pie {{ color:{MUTED}; font-size:.75rem; text-align:center; margin-top:4px; }}
  @media (max-width: 480px) {{
    .tarjeta {{ padding:20px 16px; }}
    .fila {{ flex-direction:column; }}
    .btn-hoy {{ width:100%; }}
  }}
</style>
</head>
<body>
  <div class="cabecera" id="cabecera" style="display:none">
    <b id="usuario_nombre">Sesión activa</b>
    <button class="btn-secundario" onclick="cerrarSesion()">Cerrar sesión</button>
  </div>

  <div class="tarjeta" id="vista_login">
    <h1>Sistema de Marcación</h1>
    <p class="subtitulo">Acceso seguro de empleados · tokens con vigencia de 8 horas</p>
    <label for="cedula_login">Tu cédula o usuario</label>
    <input id="cedula_login" placeholder="Ej. 1234567 o juan" autocomplete="username">
    <label for="password_login">Contraseña</label>
    <input id="password_login" type="password" placeholder="••••••••" autocomplete="current-password">
    <button onclick="iniciarSesion()">INGRESAR</button>
    <p id="login_error" class="error"></p>
  </div>

  <div class="tarjeta oculto" id="vista_consulta">
    <h1>Consulta de Historial</h1>
    <p class="subtitulo">Rango de fechas · el botón Hoy captura la fecha actual</p>
    <label for="desde">Fecha desde</label>
    <div class="fila">
      <div><input id="desde" type="date"></div>
      <div><input id="hasta" type="date"></div>
    </div>
    <div class="fila">
      <button class="btn-hoy" onclick="capturarHoy()">Hoy</button>
      <button style="margin-top:0" onclick="consultar()">VER HISTORIAL</button>
    </div>
    <div id="resultado" class="oculto"></div>
    <p id="error" class="error"></p>
  </div>

  <div class="tarjeta oculto" id="vista_reclamo">
    <h1 style="font-size:1.1rem">Solicitud de Corrección</h1>
    <p class="subtitulo">¿Olvidaste marcar o falló el sensor biométrico?</p>
    <label for="tipo_marca">Marca a corregir</label>
    <select id="tipo_marca">
      <option value="Entrada">Entrada</option>
      <option value="Salida">Salida</option>
    </select>
    <label for="fecha_reclamo">Fecha del incidente</label>
    <input id="fecha_reclamo" type="date">
    <label for="hora_propuesta">Hora propuesta</label>
    <input id="hora_propuesta" type="time">
    <label for="motivo">Motivo</label>
    <textarea id="motivo" placeholder="Ej. El lector de huella no reconoció mi dedo y no pude marcar la entrada."></textarea>
    <button onclick="enviarReclamo()">ENVIAR A RECURSOS HUMANOS</button>
    <p id="reclamo_respuesta" class="exito"></p>
  </div>

  <p class="pie">Las correcciones quedan sujetas a aprobación de Recursos Humanos</p>
<script>
  function isoHoy() {{
    const hoy = new Date();
    return hoy.getFullYear() + '-' +
      String(hoy.getMonth() + 1).padStart(2, '0') + '-' +
      String(hoy.getDate()).padStart(2, '0');
  }}
  document.getElementById('fecha_reclamo').value = isoHoy();
  document.getElementById('desde').value = new Date().getFullYear() + '-01-01';
  document.getElementById('hasta').value = isoHoy();

  function obtenerToken() {{ return localStorage.getItem('marcacion_jwt'); }}
  function mostrarLogin(mensaje) {{
    document.getElementById('vista_login').classList.remove('oculto');
    document.getElementById('vista_consulta').classList.add('oculto');
    document.getElementById('vista_reclamo').classList.add('oculto');
    document.getElementById('cabecera').style.display = 'none';
    if (mensaje) document.getElementById('login_error').textContent = mensaje;
  }}
  function mostrarApp() {{
    document.getElementById('vista_login').classList.add('oculto');
    document.getElementById('vista_consulta').classList.remove('oculto');
    document.getElementById('vista_reclamo').classList.remove('oculto');
    document.getElementById('cabecera').style.display = 'flex';
  }}
  function cerrarSesion() {{
    localStorage.removeItem('marcacion_jwt');
    mostrarLogin('');
  }}
  async function iniciarSesion() {{
    const cedula = document.getElementById('cedula_login').value.trim();
    const password = document.getElementById('password_login').value;
    document.getElementById('login_error').textContent = '';
    const resp = await fetch('/api/login', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ cedula: cedula, password: password }})
    }});
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('login_error').textContent = dato.detail || 'Error de autenticación.';
      return;
    }}
    localStorage.setItem('marcacion_jwt', dato.token);
    document.getElementById('usuario_nombre').textContent = dato.nombre + ' · ' + dato.rol;
    mostrarApp();
  }}
  async function peticionAutenticada(ruta, cuerpo) {{
    const resp = await fetch(ruta, {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + obtenerToken()
      }},
      body: JSON.stringify(cuerpo)
    }});
    if (resp.status === 401) {{
      cerrarSesion();
      document.getElementById('login_error').textContent = 'Sesión expirada. Ingrese nuevamente.';
      return null;
    }}
    return resp;
  }}
  function gs(n) {{ return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.'); }}
  function capturarHoy() {{
    const hoy = isoHoy();
    document.getElementById('desde').value = hoy;
    document.getElementById('hasta').value = hoy;
    consultar();
  }}
  function renderHistorico(d) {{
    const r = document.getElementById('resultado');
    let html = '<div class="bloque"><h3>' + d.nombre + ' · ' + d.desde + ' → ' + d.hasta + '</h3>' +
      '<div class="fila-marca"><small>Vínculo: ' + d.vinculo + '</small></div>';
    if (!d.marcas.length) {{
      html += '<p>Sin marcas registradas en el período.</p>';
    }} else {{
      d.marcas.forEach(function (m) {{
        let badges = '';
        if (m.incidencia) badges += ' <span class="etiqueta tardanza">' + m.incidencia + '</span>';
        if (m.feriado) badges += ' <span class="etiqueta feriado">Feriado</span>';
        if (m.condicion_climatica) badges += ' <span class="etiqueta clima">' + m.condicion_climatica + '</span>';
        html += '<div class="fila-marca"><span><b>' + m.fecha + '</b> ' +
          m.entrada + ' → ' + (m.salida || 'en curso') + badges + '</span></div>' +
          '<div class="fila-marca"><small>Ordinarias ' + m.ordinarias +
          ' · Extra 50% ' + m.extra_50 + ' · Extra 100% ' + m.extra_100 + '</small></div>';
      }});
    }}
    html += '</div>';
    html += '<div class="bloque"><h3>Horas extra del período</h3>' +
      '<div class="fila-marca"><span>Recargo 50%</span><span>' +
      d.extras_periodo.texto_50 + ' (' + d.extras_periodo.horas_50.toFixed(2) + ' h)</span></div>' +
      '<div class="fila-marca"><span>Recargo 100%</span><span>' +
      d.extras_periodo.texto_100 + ' (' + d.extras_periodo.horas_100.toFixed(2) + ' h)</span></div></div>';
    html += '<div class="bloque"><h3>Aguinaldo devengado · Ley 6380/2019</h3>' +
      '<div class="fila-marca"><span>Meses del período</span><span>' +
      d.aguinaldo_periodo.meses_periodo + '</span></div>' +
      '<div class="fila-marca"><span>Valor horas extra</span><span>Gs. ' +
      gs(d.aguinaldo_periodo.valor_extras) + '</span></div>' +
      '<div class="total">Aguinaldo: Gs. ' + gs(d.aguinaldo_periodo.aguinaldo) + '</div></div>';
    r.innerHTML = html;
    r.classList.remove('oculto');
  }}
  async function consultar() {{
    const desde = document.getElementById('desde').value;
    const hasta = document.getElementById('hasta').value;
    document.getElementById('error').textContent = '';
    const resp = await peticionAutenticada('/api/consulta', {{ desde: desde, hasta: hasta }});
    if (!resp) return;
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error de consulta.';
      return;
    }}
    renderHistorico(dato);
  }}
  async function enviarReclamo() {{
    const respuesta = document.getElementById('reclamo_respuesta');
    respuesta.textContent = '';
    const resp = await peticionAutenticada('/api/reclamo', {{
      tipo_marca: document.getElementById('tipo_marca').value,
      fecha: document.getElementById('fecha_reclamo').value,
      hora_propuesta: document.getElementById('hora_propuesta').value,
      motivo: document.getElementById('motivo').value
    }});
    if (!resp) return;
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error al enviar el reclamo.';
      return;
    }}
    respuesta.textContent = dato.mensaje;
    document.getElementById('motivo').value = '';
  }}
  if (obtenerToken()) {{ mostrarApp(); }} else {{ mostrarLogin(''); }}
</script>
</body>
</html>"""


class LoginRequest(BaseModel):
    """Credenciales del empleado para emitir el token de acceso."""

    cedula: str
    password: str


class ConsultaRequest(BaseModel):
    """Rango de fechas de la consulta; la identidad viaja en el token."""

    desde: str = ""
    hasta: str = ""
    fecha: str = ""


class ReclamoRequest(BaseModel):
    """Datos de la corrección solicitada; la identidad viaja en el token."""

    tipo_marca: str
    fecha: str
    hora_propuesta: str
    motivo: str


def _cliente() -> database.Database:
    """Abre una conexión fresca por petición para evitar sesiones cruzadas."""
    db = database.Database()
    db.initialize()
    return db


def _usuario_autenticado(
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Extrae y valida el Bearer Token, devolviendo el usuario autenticado."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Se requiere un token de acceso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    db = _cliente()
    try:
        claims = auth.verificar_token_acceso(token)
        usuario = db.get_user_by_id(int(claims["sub"]))
        if not usuario:
            raise ValueError("Usuario del token inexistente.")
        return usuario
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(
            status_code=401,
            detail="Sesión inválida o expirada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    finally:
        db.cerrar()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Página principal del autoservicio del empleado."""
    return PAGINA_HTML


@app.post("/api/login")
def api_login(payload: LoginRequest) -> Dict[str, Any]:
    """Valida credenciales con bcrypt y emite el JWT de acceso."""
    db = _cliente()
    try:
        user = auth.authenticate(db, payload.cedula.strip(), payload.password)
        if not user:
            raise HTTPException(status_code=401, detail="Cédula o contraseña incorrectas.")
        rol = auth.get_role_name(db, user)
        token = auth.crear_token_acceso(user["id"], rol)
        return {
            "token": token,
            "rol": rol,
            "nombre": user["full_name"],
            "vigencia_horas": auth.JWT_EXPIRACION_HORAS,
        }
    finally:
        db.cerrar()


@app.post("/api/consulta")
def api_consulta(
    payload: ConsultaRequest, usuario: Dict[str, Any] = Depends(_usuario_autenticado)
) -> Dict[str, Any]:
    """Historial del empleado autenticado: rango completo o un día puntual."""
    db = _cliente()
    try:
        if payload.fecha:
            try:
                puntual = datetime.date.fromisoformat(payload.fecha.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
            return reports.resumen_consulta(db, usuario, puntual)
        try:
            desde = datetime.date.fromisoformat(payload.desde.strip())
            hasta = datetime.date.fromisoformat(payload.hasta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Rango inválido. Use AAAA-MM-DD.")
        try:
            return reports.resumen_historico(db, usuario, desde, hasta)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()


@app.post("/api/reclamo")
def api_reclamo(
    payload: ReclamoRequest, usuario: Dict[str, Any] = Depends(_usuario_autenticado)
) -> Dict[str, Any]:
    """Registra una solicitud de corrección en estado Pendiente."""
    db = _cliente()
    try:
        if payload.tipo_marca not in ("Entrada", "Salida"):
            raise HTTPException(status_code=422, detail="Tipo de marca inválido.")
        try:
            fecha = datetime.date.fromisoformat(payload.fecha.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
        try:
            hora = datetime.time.fromisoformat(payload.hora_propuesta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Hora inválida. Use HH:MM.")
        motivo = payload.motivo.strip()
        if len(motivo) < 10:
            raise HTTPException(
                status_code=422, detail="Explique el motivo (mínimo 10 caracteres)."
            )
        solicitud_id = db.crear_solicitud_correccion(
            usuario["id"], fecha, payload.tipo_marca, hora, motivo
        )
        return {
            "id": solicitud_id,
            "estado": "Pendiente",
            "mensaje": f"Solicitud #{solicitud_id} enviada a Recursos Humanos.",
        }
    finally:
        db.cerrar()


def main() -> None:
    """Levanta el servidor web local de consulta y reclamos.

    Respeta ``HOST`` y ``PORT`` del entorno para adaptarse a los proxies
    de plataformas cloud (Render, Railway) sin modificar el código.
    """
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()