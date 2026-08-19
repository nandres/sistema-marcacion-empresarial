"""Servidor web del Sistema de Marcación con UX simplificada y PDFs legales.

FastAPI con autenticación JWT (cédula + contraseña bcrypt, vigencia 8 horas),
temas dinámicos Claro/Oscuro persistidos en ``localStorage`` y un tablero
personal que resume vacaciones (Art. 23), permisos del mes (Art. 25) y horas
extra. Cada permiso aprobado puede descargarse como PDF oficial CONATEL con
hash SHA-256 (Res. 3028/2024).

La identidad del empleado se resuelve exclusivamente desde el token; la
cédula nunca viaja en la URL. Las consultas son de solo lectura y cada
petición abre y cierra su propia conexión a PostgreSQL.

Ejecución:
    python src/web_server.py          # http://127.0.0.1:8000
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import auth
import database
import reports

app = FastAPI(
    title="Sistema de Marcación · Portal del Empleado",
    description="Tablero personal, historial de marcas y PDFs de permisos CONATEL.",
    version="3.1.0",
)

PAGINA_HTML: str = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema de Marcación · Portal del Empleado</title>
<style>
  :root {{
    --bg:#F8F9FA; --card:#FFFFFF; --borde:#E4E7EB; --input:#FFFFFF;
    --input-borde:#D8DCE1; --texto:#1A1A1E; --mutado:#6B7280;
    --primario:#1A56DB; --primario-hover:#2E66E8; --exito:#16A34A;
    --peligro:#DC2626; --acento:#B45309; --sombra:0 6px 18px rgba(0,0,0,.05);
  }}
  [data-tema="oscuro"] {{
    --bg:#0B0B0C; --card:#1E1E24; --borde:#2A2A32; --input:#191920;
    --input-borde:#26262C; --texto:#F2F2EE; --mutado:#8E8E96;
    --primario:#1A56DB; --primario-hover:#2E66E8; --exito:#4ADE80;
    --peligro:#F0544F; --acento:#F5C26B; --sombra:0 6px 18px rgba(0,0,0,.35);
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    background:var(--bg); color:var(--texto);
    font-family:'Segoe UI', system-ui, sans-serif;
    display:flex; flex-direction:column; align-items:center;
    padding:24px 16px; min-height:100vh;
    transition:background .2s, color .2s;
  }}
  .tarjeta {{
    background:var(--card); border:1px solid var(--borde); border-radius:14px;
    box-shadow:var(--sombra);
    padding:26px 24px; width:100%; max-width:760px; margin-bottom:18px;
  }}
  h1 {{ font-size:1.4rem; text-align:center; }}
  .subtitulo {{ color:var(--mutado); font-size:.85rem; text-align:center; margin-top:6px; }}
  .cabecera {{
    width:100%; max-width:760px; display:flex; justify-content:space-between;
    align-items:center; margin-bottom:14px; gap:10px;
  }}
  .cabecera b {{ font-size:.95rem; }}
  .conmutador {{
    display:inline-flex; align-items:center; gap:8px; cursor:pointer;
    border:1px solid var(--borde); border-radius:999px; padding:7px 14px;
    font-size:.8rem; color:var(--mutado); background:var(--card);
    user-select:none; transition:background .2s;
  }}
  label {{ display:block; font-size:.85rem; color:var(--mutado); margin:16px 0 6px; }}
  input, select, textarea {{
    width:100%; padding:12px 14px; border-radius:10px; border:1px solid var(--input-borde);
    background:var(--input); color:var(--texto); font-size:1rem; outline:none;
    font-family:'Segoe UI', system-ui, sans-serif;
    transition:border-color .15s, background .2s;
  }}
  input:focus, select:focus, textarea:focus {{ border-color:var(--primario); }}
  textarea {{ min-height:90px; resize:vertical; }}
  .fila {{ display:flex; gap:8px; margin-top:8px; }}
  .fila > div {{ flex:1; }}
  .fila input {{ width:100%; }}
  button {{
    background:var(--primario); color:#fff; border:none; border-radius:12px;
    padding:14px 22px; font-size:1rem; font-weight:700; cursor:pointer;
    width:100%; margin-top:18px; transition:background .15s;
  }}
  button:hover {{ background:var(--primario-hover); }}
  .btn-secundario {{
    background:transparent; border:1px solid var(--mutado); color:var(--mutado);
    width:auto; padding:10px 16px; margin:0; font-size:.85rem;
  }}
  .btn-pdf {{
    width:auto; margin:0; padding:9px 14px; font-size:.8rem; border-radius:9px;
    white-space:nowrap;
  }}
  .btn-hoy {{ width:auto; margin:0; padding:13px 18px; white-space:nowrap; }}
  .oculto {{ display:none; }}
  .rejilla {{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:14px; margin-top:16px;
  }}
  .tarjeta-dato {{
    background:var(--bg); border:1px solid var(--borde); border-radius:12px;
    padding:16px; text-align:center;
  }}
  .tarjeta-dato .numero {{ font-size:1.7rem; font-weight:800; }}
  .tarjeta-dato .leyenda {{ font-size:.75rem; color:var(--mutado); margin-top:4px; }}
  .tarjeta-dato .dato-bajo {{ font-size:.8rem; color:var(--mutado); margin-top:6px; }}
  .exito {{ color:var(--exito); }}
  .acento {{ color:var(--acento); }}
  .bloque {{ background:var(--bg); border:1px solid var(--borde); border-radius:12px; padding:16px; margin-top:14px; }}
  .bloque h3 {{ font-size:.9rem; color:var(--mutado); margin-bottom:10px; font-weight:600; }}
  .fila-marca {{
    display:flex; justify-content:space-between; align-items:center;
    padding:7px 0; border-bottom:1px solid var(--borde); font-size:.9rem; gap:8px;
  }}
  .fila-marca:last-child {{ border-bottom:none; }}
  .fila-marca small {{ color:var(--mutado); }}
  .etiqueta {{
    font-size:.7rem; padding:3px 8px; border-radius:999px; font-weight:600;
    white-space:nowrap;
  }}
  .tardanza {{ background:#3A1F1E; color:#F0544F; }}
  .feriado {{ background:#3A2F1E; color:#F5C26B; }}
  .clima {{ background:#1E2A3A; color:#7FB4F0; }}
  .ok {{ background:#12301F; color:#4ADE80; }}
  .total {{ font-size:1.15rem; font-weight:700; margin-top:8px; }}
  .error {{ color:var(--peligro); text-align:center; margin-top:16px; }}
  .exito-texto {{ color:var(--exito); text-align:center; margin-top:14px; }}
  .pie {{ color:var(--mutado); font-size:.75rem; text-align:center; margin-top:4px; }}
  svg text {{ font-family:'Segoe UI', system-ui, sans-serif; }}
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
    <div style="display:flex; gap:8px; align-items:center">
      <span class="conmutador" onclick="alternarTema()">◐ <span id="texto_tema">Claro</span></span>
      <button class="btn-secundario" onclick="cerrarSesion()">Cerrar sesión</button>
    </div>
  </div>

  <div class="tarjeta" id="vista_login">
    <h1>Sistema de Marcación</h1>
    <p class="subtitulo">Portal del Empleado · CONATEL · tokens con vigencia de 8 horas</p>
    <label for="cedula_login">Tu cédula o usuario</label>
    <input id="cedula_login" placeholder="Ej. 1234567 o juan" autocomplete="username">
    <label for="password_login">Contraseña</label>
    <input id="password_login" type="password" placeholder="••••••••" autocomplete="current-password">
    <button onclick="iniciarSesion()">INGRESAR</button>
    <p id="login_error" class="error"></p>
  </div>

  <div class="tarjeta oculto" id="vista_tablero">
    <h1 id="tablero_titulo">Mi Resumen</h1>
    <p class="subtitulo" id="tablero_subtitulo"></p>
    <div class="rejilla" id="tarjetas_resumen"></div>
    <div id="grafico_marcas"></div>
    <div class="bloque" id="bloque_permisos"></div>
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
    <p id="reclamo_respuesta" class="exito-texto"></p>
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

  const TEMA_PRE = document.getElementById('texto_tema');
  function aplicarTema(tema) {{
    document.documentElement.setAttribute('data-tema', tema);
    TEMA_PRE.textContent = (tema === 'oscuro') ? 'Oscuro' : 'Claro';
    localStorage.setItem('marcacion_tema', tema);
  }}
  aplicarTema(localStorage.getItem('marcacion_tema') || 'claro');
  function alternarTema() {{
    const actual = document.documentElement.getAttribute('data-tema');
    aplicarTema(actual === 'oscuro' ? 'claro' : 'oscuro');
  }}

  function obtenerToken() {{ return localStorage.getItem('marcacion_jwt'); }}
  function mostrarLogin(mensaje) {{
    document.getElementById('vista_login').classList.remove('oculto');
    document.getElementById('vista_tablero').classList.add('oculto');
    document.getElementById('vista_consulta').classList.add('oculto');
    document.getElementById('vista_reclamo').classList.add('oculto');
    document.getElementById('cabecera').style.display = 'none';
    if (mensaje) document.getElementById('login_error').textContent = mensaje;
  }}
  function mostrarApp() {{
    document.getElementById('vista_login').classList.add('oculto');
    document.getElementById('vista_tablero').classList.remove('oculto');
    document.getElementById('vista_consulta').classList.remove('oculto');
    document.getElementById('vista_reclamo').classList.remove('oculto');
    document.getElementById('cabecera').style.display = 'flex';
    cargarResumen();
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
    const opciones = {{
      headers: {{ 'Authorization': 'Bearer ' + obtenerToken() }}
    }};
    if (cuerpo !== undefined) {{
      opciones.method = 'POST';
      opciones.headers['Content-Type'] = 'application/json';
      opciones.body = JSON.stringify(cuerpo);
    }}
    const resp = await fetch(ruta, opciones);
    if (resp.status === 401) {{
      cerrarSesion();
      document.getElementById('login_error').textContent = 'Sesión expirada. Ingrese nuevamente.';
      return null;
    }}
    return resp;
  }}
  function gs(n) {{ return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.'); }}
  function tarjetaDato(numero, leyenda, extra, clase) {{
    return '<div class="tarjeta-dato"><div class="numero ' + (clase || '') + '">' + numero +
      '</div><div class="leyenda">' + leyenda + '</div>' +
      (extra ? '<div class="dato-bajo">' + extra + '</div>' : '') + '</div>';
  }}
  function graficoSVG(dias, horas) {{
    const ancho = 620, alto = 180, margen = 30;
    const max = Math.max.apply(null, horas.concat([1]));
    const px = (ancho - margen * 2) / Math.max(dias.length, 1);
    const py = (alto - margen * 2) / max;
    let barras = '', ejes = '';
    dias.forEach(function (d, i) {{
      const x = margen + i * px;
      const h = horas[i] * py;
      barras += '<rect x="' + (x + px * 0.25) + '" y="' + (alto - margen - h) +
        '" width="' + (px * 0.5) + '" height="' + h +
        '" rx="4" fill="var(--primario)"></rect>';
      ejes += '<text x="' + (x + px / 2) + '" y="' + (alto - 8) +
        '" font-size="9" fill="var(--mutado)" text-anchor="middle">' + d + '</text>';
    }});
    return '<div class="bloque"><h3>Horas ordinarias por día · mes en curso</h3>' +
      '<svg viewBox="0 0 ' + ancho + ' ' + alto + '" width="100%" style="display:block">' +
      barras + ejes + '</svg></div>';
  }}
  function renderTablero(d) {{
    document.getElementById('tablero_titulo').textContent = d.nombre;
    document.getElementById('tablero_subtitulo').textContent =
      d.usuario + ' · ' + d.vinculo + ' · ' + d.antiguedad_anios + ' años de antigüedad';
    const vac = d.vacaciones, per = d.permisos_mes, ext = d.extras_mes;
    let detalle = '';
    Object.keys(per.detalle).forEach(function (t) {{
      detalle += '<div class="fila-marca"><span>' + t + '</span><span>' + per.detalle[t] + '</span></div>';
    }});
    document.getElementById('tarjetas_resumen').innerHTML =
      tarjetaDato(vac.disponibles, 'Días de vacaciones disponibles', 'Art. 23 · devengadas ' + vac.devengadas + ' · usadas ' + vac.usadas, 'exito') +
      tarjetaDato(per.total, 'Permisos en el mes', 'Art. 25 · Res. 3028/2024', 'acento') +
      tarjetaDato(ext.horas_50 + ' h', 'Horas extra 50%', 'Ley N.º 213', '') +
      tarjetaDato(ext.horas_100 + ' h', 'Horas extra 100%', 'Ley N.º 213', '');
    document.getElementById('grafico_marcas').innerHTML =
      d.marcas_mes.dias.length
        ? graficoSVG(d.marcas_mes.dias, d.marcas_mes.ordinarias)
        : '<div class="bloque"><h3>Horas ordinarias por día · mes en curso</h3>' +
          '<p>Sin marcas registradas en el mes.</p></div>';
    let lista = '<h3>Permisos aprobados · descarga el PDF oficial</h3>';
    if (!d.permisos.length) {{
      lista += '<p>Sin permisos emitidos todavía.</p>';
    }} else {{
      d.permisos.forEach(function (p) {{
        lista += '<div class="fila-marca"><span><b>' + p.tipo + '</b> · ' +
          p.inicio + ' → ' + p.fin + ' · aprobado por ' + p.aprobador + '</span>' +
          '<button class="btn-pdf" onclick="descargarPDF(' + p.id + ')">PDF</button></div>';
      }});
    }}
    if (detalle) {{
      lista += '<h3 style="margin-top:12px">Detalle de permisos del mes</h3>' + detalle;
    }}
    document.getElementById('bloque_permisos').innerHTML = lista;
  }}
  async function cargarResumen() {{
    const resp = await peticionAutenticada('/api/resumen');
    if (!resp) return;
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error al cargar el resumen.';
      return;
    }}
    renderTablero(dato);
  }}
  function descargarPDF(solicitudId) {{
    window.open('/api/permiso/' + solicitudId + '/pdf?token=' + encodeURIComponent(obtenerToken()), '_blank');
  }}
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


def _usuario_por_token_query(token: str) -> Dict[str, Any]:
    """Resuelve el usuario desde un token recibido por query string (PDFs)."""
    db = _cliente()
    try:
        claims = auth.verificar_token_acceso(token)
        usuario = db.get_user_by_id(int(claims["sub"]))
        if not usuario:
            raise ValueError("Usuario del token inexistente.")
        return usuario
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")
    finally:
        db.cerrar()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Página principal del Portal del Empleado."""
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


@app.get("/api/resumen")
def api_resumen(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Tablero personal: vacaciones, permisos del mes, marcas y horas extra."""
    db = _cliente()
    try:
        return reports.resumen_empleado(db, usuario)
    finally:
        db.cerrar()


@app.get("/api/permiso/{solicitud_id}/pdf")
def api_permiso_pdf(solicitud_id: int, token: str = "") -> FileResponse:
    """Entrega el PDF oficial del permiso si pertenece al usuario autenticado."""
    usuario = _usuario_por_token_query(token)
    db = _cliente()
    try:
        justificacion = next(
            (j for j in db.list_justificaciones() if j["id"] == solicitud_id), None
        )
        if not justificacion or justificacion["usuario_id"] != usuario["id"]:
            raise HTTPException(status_code=404, detail="Permiso no encontrado.")
    finally:
        db.cerrar()
    try:
        ruta = Path(reports.generar_pdf_permiso(solicitud_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(
        ruta,
        media_type="application/pdf",
        filename=ruta.name,
    )


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
    """Levanta el servidor web del Portal del Empleado.

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