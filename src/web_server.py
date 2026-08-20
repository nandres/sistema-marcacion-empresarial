"""Servidor web del Sistema de Marcación con UX simplificada y PDFs legales.

FastAPI con autenticación JWT (cédula + contraseña bcrypt, vigencia 8 horas),
temas dinámicos Claro/Oscuro persistidos en ``localStorage`` y un tablero
personal que resume vacaciones (Art. 23), permisos del mes (Art. 25) y horas
extra. Cada permiso aprobado puede descargarse como PDF oficial con
hash SHA-256 (Res. 3028/2024).

La identidad del empleado se resuelve exclusivamente desde el token; la
cédula nunca viaja en la URL. Las consultas son de solo lectura y cada
petición abre y cierra su propia conexión a PostgreSQL.

Ejecución:
    python src/web_server.py          # http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import auth
import clock_engine
import database
import notifications
import reports

app = FastAPI(
    title="Sistema de Marcación · Portal, Kiosco y Gestión",
    description="Kiosco de marcación, tablero del empleado y panel de Recursos Humanos.",
    version="3.2.0",
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
  #caja_toast {{ position:fixed; right:16px; bottom:16px; z-index:99; display:flex;
    flex-direction:column; gap:8px; max-width:340px; }}
  .toast {{ background:var(--card); border:1px solid var(--peligro); border-left:5px solid
    var(--peligro); border-radius:10px; box-shadow:var(--sombra); padding:12px 14px;
    font-size:.85rem; color:var(--texto); animation:aparecer .25s ease; }}
  .toast b {{ display:block; margin-bottom:2px; }}
  @keyframes aparecer {{ from {{ opacity:0; transform:translateY(8px); }}
    to {{ opacity:1; transform:translateY(0); }} }}
  svg text {{ font-family:'Segoe UI', system-ui, sans-serif; }}
  @media (max-width: 480px) {{
    .tarjeta {{ padding:20px 16px; }}
    .fila {{ flex-direction:column; }}
    .btn-hoy {{ width:100%; }}
  }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }}
  .tab {{ background:var(--card); border:1px solid var(--borde); color:var(--mutado);
    padding:8px 14px; border-radius:20px; cursor:pointer; font-size:.9rem; }}
  .tab.activo {{ background:var(--primario); border-color:var(--primario); color:#fff; }}
  .tabla {{ width:100%; border-collapse:collapse; font-size:.88rem; margin-top:8px; }}
  .tabla th, .tabla td {{ padding:8px 10px; border-bottom:1px solid var(--borde); text-align:left; vertical-align:top; }}
  .tabla th {{ color:var(--mutado); font-weight:600; font-size:.8rem; text-transform:uppercase; }}
</style>
</head>
<body>
  <div id="caja_toast"></div>
  <div class="cabecera" id="cabecera" style="display:none">
    <b id="usuario_nombre">Sesión activa</b>
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
      <button class="btn-secundario" onclick="mostrarPortal()">Portal</button>
      <button class="btn-secundario" id="btn_gestion" onclick="mostrarGestion()" style="display:none">Gestión</button>
      <button class="btn-secundario" onclick="mostrarKiosco()">Kiosco</button>
      <span class="conmutador" onclick="alternarTema()">◐ <span id="texto_tema">Claro</span></span>
      <button class="btn-secundario" onclick="cerrarSesion()">Cerrar sesión</button>
    </div>
  </div>

  <div class="tarjeta" id="vista_kiosco">
    <h1 style="font-size:1.3rem">Kiosco de Marcación</h1>
    <p class="subtitulo">Registra tu entrada o salida desde el navegador</p>
    <label for="cedula_kiosco">Cédula o usuario</label>
    <input id="cedula_kiosco" placeholder="Ej. 1234567 o juan" autocomplete="username">
    <label for="password_kiosco">Contraseña</label>
    <input id="password_kiosco" type="password" placeholder="••••••••" autocomplete="current-password">
    <label style="display:flex; align-items:center; gap:8px; font-size:.9rem">
      <input id="lluvia_kiosco" type="checkbox" style="width:auto">
      Día lluvioso (tolerancia ampliada)
    </label>
    <button onclick="marcarKiosco()">REGISTRAR ASISTENCIA</button>
    <p id="kiosco_respuesta" class="exito-texto"></p>
    <p id="kiosco_error" class="error"></p>
    <button class="btn-secundario" style="margin-top:10px" onclick="mostrarLogin('')">Portal del Empleado →</button>
  </div>

  <div class="tarjeta" id="vista_login">
    <h1>Sistema de Marcación</h1>
    <p class="subtitulo">Portal del Empleado · tokens con vigencia de 8 horas</p>
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

  <div class="tarjeta oculto" id="vista_gestion">
    <h1 style="font-size:1.2rem">Panel de Gestión · Recursos Humanos</h1>
    <div class="tabs" id="tabs_gestion">
      <button class="tab activo" data-seccion="resumen" onclick="mostrarSeccionGestion('resumen')">Resumen</button>
      <button class="tab" data-seccion="personal" onclick="mostrarSeccionGestion('personal')">Personal</button>
      <button class="tab" data-seccion="justificaciones" onclick="mostrarSeccionGestion('justificaciones')">Justificaciones</button>
      <button class="tab" data-seccion="correcciones" onclick="mostrarSeccionGestion('correcciones')">Correcciones</button>
      <button class="tab" data-seccion="alertas" onclick="mostrarSeccionGestion('alertas')">Alertas</button>
      <button class="tab" data-seccion="auditoria" onclick="mostrarSeccionGestion('auditoria')">Auditoría</button>
    </div>
    <div id="seccion_resumen"></div>
    <div id="seccion_personal" class="oculto"></div>
    <div id="seccion_justificaciones" class="oculto"></div>
    <div id="seccion_correcciones" class="oculto"></div>
    <div id="seccion_alertas" class="oculto"></div>
    <div id="seccion_auditoria" class="oculto"></div>
  </div>
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
  var socketAlertas = null;
  function conectarAlertas() {{
    var token = obtenerToken();
    if (!token) return;
    if (socketAlertas) socketAlertas.close();
    var protocolo = location.protocol === 'https:' ? 'wss://' : 'ws://';
    socketAlertas = new WebSocket(protocolo + location.host + '/ws/alertas?token=' + encodeURIComponent(token));
    socketAlertas.onmessage = function (evento) {{
      var alerta = JSON.parse(evento.data);
      var caja = document.getElementById('caja_toast');
      var aviso = document.createElement('div');
      aviso.className = 'toast';
      var icono = alerta.severidad === 'alta' ? '🔔 ' : (alerta.severidad === 'media' ? '⚠ ' : 'ℹ ');
      aviso.innerHTML = '<b>' + icono + alerta.mensaje + '</b>' +
        (alerta.detalle ? '<span>' + alerta.detalle + '</span>' : '');
      caja.appendChild(aviso);
      setTimeout(function () {{ aviso.remove(); }}, 8000);
    }};
    socketAlertas.onclose = function () {{
      if (obtenerToken()) setTimeout(conectarAlertas, 5000);
    }};
  }}
  function mostrarLogin(mensaje) {{
    ocultarVistas();
    document.getElementById('vista_login').classList.remove('oculto');
    document.getElementById('cabecera').style.display = 'none';
    if (mensaje) document.getElementById('login_error').textContent = mensaje;
  }}
  function ocultarVistas() {{
    ['vista_login','vista_kiosco','vista_tablero','vista_consulta','vista_reclamo','vista_gestion']
      .forEach(function (v) {{ document.getElementById(v).classList.add('oculto'); }});
  }}
  function mostrarKiosco() {{
    ocultarVistas();
    document.getElementById('vista_kiosco').classList.remove('oculto');
    document.getElementById('cabecera').style.display = obtenerToken() ? 'flex' : 'none';
  }}
  function mostrarPortal() {{
    ocultarVistas();
    document.getElementById('vista_tablero').classList.remove('oculto');
    document.getElementById('vista_consulta').classList.remove('oculto');
    document.getElementById('vista_reclamo').classList.remove('oculto');
    document.getElementById('cabecera').style.display = 'flex';
    cargarResumen();
  }}
  function mostrarGestion() {{
    ocultarVistas();
    document.getElementById('vista_gestion').classList.remove('oculto');
    document.getElementById('cabecera').style.display = 'flex';
    mostrarSeccionGestion('resumen');
  }}
  function actualizarNav(rol) {{
    document.getElementById('btn_gestion').style.display =
      (rol === 'Administrador' || rol === 'Recursos Humanos') ? 'inline-block' : 'none';
  }}
  function cerrarSesion() {{
    localStorage.removeItem('marcacion_jwt');
    localStorage.removeItem('marcacion_rol');
    if (socketAlertas) {{ socketAlertas.close(); socketAlertas = null; }}
    mostrarKiosco();
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
    localStorage.setItem('marcacion_rol', dato.rol);
    document.getElementById('usuario_nombre').textContent = dato.nombre + ' · ' + dato.rol;
    actualizarNav(dato.rol);
    mostrarPortal();
    conectarAlertas();
  }}
  async function peticionAutenticada(ruta, cuerpo, metodo) {{
    const opciones = {{
      headers: {{ 'Authorization': 'Bearer ' + obtenerToken() }}
    }};
    if (cuerpo !== undefined || metodo) {{
      opciones.method = metodo || 'POST';
      opciones.headers['Content-Type'] = 'application/json';
      if (cuerpo !== undefined) opciones.body = JSON.stringify(cuerpo);
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
  async function marcarKiosco() {{
    var ce = document.getElementById('cedula_kiosco').value.trim();
    var pa = document.getElementById('password_kiosco').value;
    var ll = document.getElementById('lluvia_kiosco').checked;
    var respuesta = document.getElementById('kiosco_respuesta');
    var error = document.getElementById('kiosco_error');
    respuesta.textContent = ''; error.textContent = '';
    if (!ce || !pa) {{ error.textContent = 'Ingresa tu cédula y contraseña.'; return; }}
    var resp = await fetch('/api/marcar', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ cedula: ce, password: pa, es_dia_lluvioso: ll }})
    }});
    var dato = await resp.json();
    if (!resp.ok) {{ error.textContent = dato.detail || 'Error al marcar.'; return; }}
    respuesta.innerHTML = '<b>' + dato.nombre + '</b> · ' + dato.tipo + ' a las ' + dato.hora +
      '<br><br><pre style="text-align:left;background:var(--card);padding:12px;border-radius:8px;' +
      'border:1px solid var(--borde);overflow-x:auto;font-size:.75rem">' + dato.ticket + '</pre>';
    document.getElementById('password_kiosco').value = '';
  }}
  function esc(s) {{
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}
  function mostrarSeccionGestion(nombre) {{
    document.querySelectorAll('#tabs_gestion .tab').forEach(function (b) {{
      b.classList.toggle('activo', b.dataset.seccion === nombre);
    }});
    ['resumen','personal','justificaciones','correcciones','alertas','auditoria'].forEach(function (s) {{
      document.getElementById('seccion_' + s).classList.toggle('oculto', s !== nombre);
    }});
    if (nombre === 'resumen') cargarResumenPanel();
    if (nombre === 'personal') cargarPersonal();
    if (nombre === 'justificaciones') cargarJustificaciones();
    if (nombre === 'correcciones') cargarCorrecciones();
    if (nombre === 'alertas') cargarAlertasPanel();
    if (nombre === 'auditoria') cargarAuditoria();
  }}
  async function cargarResumenPanel() {{
    var cont = document.getElementById('seccion_resumen');
    var resp = await peticionAutenticada('/api/panel/resumen');
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (d.detail || 'Error.') + '</p>'; return; }}
    cont.innerHTML =
      '<div class="rejilla">' +
      tarjetaDato(d.personal, 'Empleados registrados', 'Todos los vínculos', '') +
      tarjetaDato(d.marcas_hoy, 'Marcajes de hoy', 'Entradas registradas', 'exito') +
      tarjetaDato(d.justificaciones, 'Justificaciones emitidas', 'PDF oficial disponible', 'acento') +
      tarjetaDato(d.correcciones_pendientes, 'Correcciones pendientes', 'Solicitudes sin resolver', 'acento') +
      tarjetaDato(d.alertas_no_leidas, 'Alertas sin leer', 'Notificaciones activas', 'peligro') +
      '</div>' +
      '<div class="bloque"><h3>Acciones rápidas</h3>' +
      '<button class="btn-secundario" onclick="mostrarSeccionGestion(\'personal\')">Gestionar personal</button> ' +
      '<button class="btn-secundario" onclick="mostrarSeccionGestion(\'justificaciones\')">Emitir justificación</button> ' +
      '<button class="btn-secundario" onclick="mostrarSeccionGestion(\'correcciones\')">Revisar correcciones</button></div>';
  }}
  var personalActual = [];
  var rolesActual = [];
  function renderTablaPersonal(lista, editandoId) {{
    var html = '<table class="tabla"><tr><th>Nombre</th><th>Usuario</th><th>Rol</th>' +
      '<th>Vínculo</th><th>Salario</th><th>Acciones</th></tr>';
    lista.forEach(function (p) {{
      if (p.id === editandoId) {{
        var opRoles = rolesActual.map(function (r) {{
          return '<option' + (r === p.role_name ? ' selected' : '') + '>' + esc(r) + '</option>';
        }}).join('');
        var opVinculos = ['Funcionario','Pasante'].map(function (v) {{
          return '<option' + (v === p.tipo_vinculo ? ' selected' : '') + '>' + v + '</option>';
        }}).join('');
        html += '<tr><td colspan="6"><div class="rejilla">' +
          '<div><label>Nombre</label><input id="e_nombre" value="' + esc(p.full_name) + '"></div>' +
          '<div><label>Rol</label><select id="e_rol">' + opRoles + '</select></div>' +
          '<div><label>Vínculo</label><select id="e_vinculo">' + opVinculos + '</select></div>' +
          '<div><label>Salario (Gs.)</label><input id="e_salario" type="number" min="0" step="100000" value="' + (p.salario_mensual || 0) + '"></div>' +
          '<div><label>Nueva contraseña (vacío = no cambia)</label><input id="e_password" type="password"></div>' +
          '</div><button onclick="guardarPersonal(' + p.id + ')">Guardar</button> ' +
          '<button class="btn-secundario" onclick="cargarPersonal()">Cancelar</button></td></tr>';
        return;
      }}
      html += '<tr><td>' + esc(p.full_name) + '</td><td>' + esc(p.username) + '</td>' +
        '<td>' + esc(p.role_name) + '</td><td>' + esc(p.tipo_vinculo || '') + '</td>' +
        '<td>Gs. ' + gs(p.salario_mensual || 0) + '</td>' +
        '<td><button class="btn-pdf" onclick="editarPersonal(' + p.id + ')">Editar</button> ' +
        '<button class="btn-secundario" onclick="eliminarPersonal(' + p.id + ')">Eliminar</button></td></tr>';
    }});
    html += '</table>';
    return html;
  }}
  async function cargarPersonal() {{
    var cont = document.getElementById('seccion_personal');
    var resp = await peticionAutenticada('/api/panel/personal');
    if (!resp) return;
    var dato = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (dato.detail || 'Error.') + '</p>'; return; }}
    personalActual = dato.personal;
    rolesActual = dato.roles;
    var roles = dato.roles.map(function (r) {{ return '<option>' + esc(r) + '</option>'; }}).join('');
    var html = '<div class="bloque"><h3>Nuevo empleado</h3><div class="rejilla">' +
      '<div><label>Usuario</label><input id="n_username" placeholder="p. ej. maria"></div>' +
      '<div><label>Contraseña</label><input id="n_password" type="password"></div>' +
      '<div><label>Nombre y apellido</label><input id="n_nombre" placeholder="Nombre completo"></div>' +
      '<div><label>Rol</label><select id="n_rol">' + roles + '</select></div>' +
      '<div><label>Salario mensual (Gs.)</label><input id="n_salario" type="number" min="0" step="100000" value="0"></div>' +
      '<div><label>Vínculo</label><select id="n_vinculo"><option>Funcionario</option><option>Pasante</option></select></div>' +
      '</div><button onclick="crearPersonal()">CREAR EMPLEADO</button><p id="personal_respuesta" class="exito-texto"></p></div>';
    html += '<div class="bloque"><h3>Personal registrado</h3>' + renderTablaPersonal(personalActual, null) + '</div>';
    cont.innerHTML = html;
  }}
  async function crearPersonal() {{
    var respuesta = document.getElementById('personal_respuesta');
    respuesta.textContent = '';
    var body = {{
      username: document.getElementById('n_username').value.trim(),
      password: document.getElementById('n_password').value,
      full_name: document.getElementById('n_nombre').value.trim(),
      role_name: document.getElementById('n_rol').value,
      salario_mensual: parseFloat(document.getElementById('n_salario').value) || 0,
      tipo_vinculo: document.getElementById('n_vinculo').value
    }};
    if (!body.username || !body.password || !body.full_name) {{
      respuesta.textContent = 'Completa usuario, contraseña y nombre.';
      return;
    }}
    var resp = await peticionAutenticada('/api/panel/personal', body);
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ respuesta.textContent = d.detail || 'Error al crear.'; return; }}
    cargarPersonal();
  }}
  function editarPersonal(id) {{
    document.getElementById('seccion_personal').innerHTML =
      renderTablaPersonal(personalActual, id);
  }}
  async function guardarPersonal(id) {{
    var body = {{
      full_name: document.getElementById('e_nombre').value.trim(),
      role_name: document.getElementById('e_rol').value,
      salario_mensual: parseFloat(document.getElementById('e_salario').value) || 0,
      tipo_vinculo: document.getElementById('e_vinculo').value
    }};
    var pass = document.getElementById('e_password').value;
    if (pass) body.password = pass;
    var resp = await peticionAutenticada('/api/panel/personal/' + id, body, 'PUT');
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ alert(d.detail || 'Error al actualizar.'); return; }}
    cargarPersonal();
  }}
  async function eliminarPersonal(id) {{
    if (!confirm('¿Eliminar a este empleado? La acción queda auditada.')) return;
    var resp = await peticionAutenticada('/api/panel/personal/' + id, undefined, 'DELETE');
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ alert(d.detail || 'Error al eliminar.'); return; }}
    cargarPersonal();
  }}
  async function cargarJustificaciones() {{
    var cont = document.getElementById('seccion_justificaciones');
    var resp = await peticionAutenticada('/api/panel/justificaciones');
    if (!resp) return;
    var dato = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (dato.detail || 'Error.') + '</p>'; return; }}
    var opcionesEmp = (dato.personal || []).map(function (p) {{
      return '<option value="' + p.id + '">' + esc(p.full_name) + ' (' + esc(p.username) + ')</option>';
    }}).join('');
    var opcionesTipo = (dato.tipos || []).map(function (t) {{
      return '<option>' + esc(t) + '</option>';
    }}).join('');
    var html = '<div class="bloque"><h3>Emitir justificación oficial</h3><div class="rejilla">' +
      '<div><label>Empleado</label><select id="j_empleado">' + opcionesEmp + '</select></div>' +
      '<div><label>Tipo de permiso</label><select id="j_tipo">' + opcionesTipo + '</select></div>' +
      '<div><label>Desde</label><input id="j_desde" type="date"></div>' +
      '<div><label>Hasta</label><input id="j_hasta" type="date"></div>' +
      '<div><label>Horas usadas (solo permisos por horas)</label><input id="j_horas" type="number" step="0.5" min="0" value="0"></div>' +
      '</div><button onclick="crearJustificacion()">EMITIR JUSTIFICACIÓN</button><p id="j_respuesta" class="exito-texto"></p></div>';
    var tabla = '<div class="bloque"><h3>Justificaciones emitidas</h3><table class="tabla">' +
      '<tr><th>Empleado</th><th>Tipo</th><th>Desde</th><th>Hasta</th><th>Horas</th><th>PDF</th></tr>';
    (dato.justificaciones || []).forEach(function (j) {{
      tabla += '<tr><td>' + esc(j.full_name) + '</td><td>' + esc(j.tipo_permiso) + '</td>' +
        '<td>' + j.fecha_inicio + '</td><td>' + j.fecha_fin + '</td><td>' + (j.horas_usadas || 0) + '</td>' +
        '<td><button class="btn-pdf" onclick="descargarPDFPanel(' + j.id + ')">PDF</button></td></tr>';
    }});
    tabla += '</table></div>';
    cont.innerHTML = html + tabla;
    document.getElementById('j_desde').value = isoHoy();
    document.getElementById('j_hasta').value = isoHoy();
  }}
  function descargarPDFPanel(id) {{
    window.open('/api/panel/justificaciones/' + id + '/pdf?token=' + encodeURIComponent(obtenerToken()), '_blank');
  }}
  async function crearJustificacion() {{
    var respuesta = document.getElementById('j_respuesta');
    respuesta.textContent = '';
    var body = {{
      empleado_id: parseInt(document.getElementById('j_empleado').value, 10),
      tipo_permiso: document.getElementById('j_tipo').value,
      fecha_inicio: document.getElementById('j_desde').value,
      fecha_fin: document.getElementById('j_hasta').value,
      horas_usadas: parseFloat(document.getElementById('j_horas').value) || 0
    }};
    var resp = await peticionAutenticada('/api/panel/justificaciones', body);
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ respuesta.textContent = d.detail || 'Error al emitir.'; return; }}
    respuesta.textContent = d.mensaje;
    cargarJustificaciones();
  }}
  async function cargarCorrecciones() {{
    var cont = document.getElementById('seccion_correcciones');
    var resp = await peticionAutenticada('/api/panel/correcciones');
    if (!resp) return;
    var lista = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (lista.detail || 'Error.') + '</p>'; return; }}
    var html = '<div class="bloque"><h3>Solicitudes de corrección</h3>';
    if (!lista.length) html += '<p>Sin solicitudes.</p>';
    else {{
      html += '<table class="tabla"><tr><th>#</th><th>Empleado</th><th>Fecha</th><th>Marca</th>' +
        '<th>Hora propuesta</th><th>Motivo</th><th>Estado</th><th>Acciones</th></tr>';
      lista.forEach(function (s) {{
        html += '<tr><td>' + s.id + '</td><td>' + esc(s.full_name) + '</td><td>' + s.fecha_registro + '</td>' +
          '<td>' + s.tipo_marca + '</td><td>' + s.hora_propuesta + '</td><td>' + esc(s.motivo) + '</td>' +
          '<td>' + (s.estado || 'Pendiente') + '</td>';
        if (s.estado === 'Pendiente') {{
          html += '<td><button class="btn-pdf" onclick="resolverCorreccion(' + s.id + ', true)">Aprobar</button> ' +
            '<button class="btn-secundario" onclick="resolverCorreccion(' + s.id + ', false)">Rechazar</button></td>';
        }} else {{
          html += '<td></td>';
        }}
        html += '</tr>';
      }});
      html += '</table>';
    }}
    html += '</div>';
    cont.innerHTML = html;
  }}
  async function resolverCorreccion(id, aprobar) {{
    var resp = await peticionAutenticada('/api/panel/correcciones/' + id + (aprobar ? '/aprobar' : '/rechazar'));
    if (!resp) return;
    var d = await resp.json();
    if (!resp.ok) {{ alert(d.detail || 'Error.'); return; }}
    cargarCorrecciones();
  }}
  async function cargarAlertasPanel() {{
    var cont = document.getElementById('seccion_alertas');
    var resp = await peticionAutenticada('/api/alertas');
    if (!resp) return;
    var dato = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (dato.detail || 'Error.') + '</p>'; return; }}
    var html = '<div class="bloque"><h3>Alertas del sistema</h3>';
    if (!dato.alertas.length) html += '<p>Sin alertas.</p>';
    else {{
      html += '<table class="tabla"><tr><th>Severidad</th><th>Mensaje</th><th>Detalle</th><th>Fecha</th></tr>';
      dato.alertas.forEach(function (a) {{
        var icono = a.severidad === 'alta' ? '🔔' : (a.severidad === 'media' ? '⚠️' : 'ℹ️');
        html += '<tr><td>' + icono + ' ' + esc(a.severidad) + '</td><td>' + esc(a.mensaje) + '</td>' +
          '<td>' + esc(a.detalle || '') + '</td><td>' + (a.creado_en || '') + '</td></tr>';
      }});
      html += '</table>';
      html += '<br><button class="btn-secundario" onclick="marcarAlertasLeidas()">Marcar todas como leídas</button>';
    }}
    html += '</div>';
    cont.innerHTML = html;
  }}
  async function marcarAlertasLeidas() {{
    var resp = await peticionAutenticada('/api/alertas/leidas');
    if (!resp) return;
    cargarAlertasPanel();
  }}
  async function cargarAuditoria() {{
    var cont = document.getElementById('seccion_auditoria');
    var resp = await peticionAutenticada('/api/panel/auditoria');
    if (!resp) return;
    var lista = await resp.json();
    if (!resp.ok) {{ cont.innerHTML = '<p class="error">' + (lista.detail || 'Error.') + '</p>'; return; }}
    var html = '<div class="bloque"><h3>Bitácora de auditoría</h3>';
    if (!lista.length) html += '<p>Sin eventos registrados.</p>';
    else {{
      html += '<table class="tabla"><tr><th>Fecha</th><th>Usuario</th><th>Acción</th><th>Registro</th></tr>';
      lista.forEach(function (e) {{
        var nuevo = e.valores_nuevos ? JSON.stringify(e.valores_nuevos) : '';
        html += '<tr><td>' + (e.creado_en || '') + '</td><td>' + esc(e.full_name || e.username) + '</td>' +
          '<td>' + esc(e.accion) + '</td><td>' + esc(nuevo) + '</td></tr>';
      }});
      html += '</table>';
    }}
    html += '</div>';
    cont.innerHTML = html;
  }}
  if (obtenerToken()) {{
    actualizarNav(localStorage.getItem('marcacion_rol') || 'Empleado');
    mostrarPortal();
    conectarAlertas();
  }} else {{
    mostrarKiosco();
  }}
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


def _alerta_json(alerta: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte a JSON puro (send_json de WebSocket usa json.dumps plano)."""
    salida = dict(alerta)
    if isinstance(salida.get("creado_en"), datetime.datetime):
        salida["creado_en"] = salida["creado_en"].isoformat()
    return salida


@app.websocket("/ws/alertas")
async def ws_alertas(websocket: WebSocket, token: str = "") -> None:
    """Push en tiempo real: cada alerta publicada en el bus llega al cliente.

    Un empleado recibe solo sus propias alertas (marcación con incidencia);
    las alertas globales (cuota bloqueada, fraude) llegan a todos los
    conectados autenticados. Se entrega el historial no leído al conectar.
    """
    try:
        usuario = _usuario_por_token_query(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    db = _cliente()
    try:
        pendientes = db.listar_alertas(no_leidas=True, limite=20)
        for alerta in pendientes:
            if (
                alerta.get("usuario_id") is None
                or int(alerta.get("usuario_id") or 0) == int(usuario["id"])
            ):
                await websocket.send_json(_alerta_json(alerta))
    finally:
        db.cerrar()

    def remitente(alerta: Dict[str, Any]) -> None:
        if (
            alerta.get("usuario_id") is None
            or int(alerta.get("usuario_id") or 0) == int(usuario["id"])
        ):
            try:
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json(_alerta_json(alerta)), loop
                )
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    notifications.BUS.suscribir(remitente)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        notifications.BUS.desuscribir(remitente)


class AlertaRequest(BaseModel):
    tipo: str
    severidad: str = "media"
    mensaje: str
    detalle: str = ""
    usuario_id: Optional[int] = None


@app.post("/api/alertas")
def api_publicar_alerta(
    payload: AlertaRequest,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Publica una alerta desde el escritorio (kiosco u otro origen)."""
    db = _cliente()
    try:
        return notifications.registrar_alerta(
            db,
            payload.tipo,
            payload.severidad,
            payload.mensaje.strip(),
            payload.detalle.strip(),
            payload.usuario_id,
        )
    finally:
        db.cerrar()


@app.get("/api/alertas")
def api_listar_alertas(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Lista de alertas para el Panel de Gestión (RRHH/Administrador)."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")
    db = _cliente()
    try:
        return {
            "alertas": db.listar_alertas(limite=60),
            "no_leidas": len(db.listar_alertas(no_leidas=True)),
        }
    finally:
        db.cerrar()


@app.post("/api/alertas/leidas")
def api_marcar_alertas_leidas(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Marca todas las alertas como leídas (Panel de Gestión)."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")
    db = _cliente()
    try:
        return {"marcadas": db.marcar_alertas_leidas()}
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


class MarcarRequest(BaseModel):
    cedula: str
    password: str
    es_dia_lluvioso: bool = False


class PersonalNuevo(BaseModel):
    username: str
    password: str
    full_name: str
    role_name: str
    salario_mensual: float = 0.0
    tipo_vinculo: str = "Funcionario"


class PersonalEditar(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    role_name: Optional[str] = None
    salario_mensual: Optional[float] = None
    tipo_vinculo: Optional[str] = None


class JustificacionRRHH(BaseModel):
    empleado_id: int
    tipo_permiso: str
    fecha_inicio: str
    fecha_fin: str
    horas_usadas: float = 0.0


def _exigir_rrhh(usuario: Dict[str, Any]) -> None:
    """Exige rol de Recursos Humanos o Administrador para el Panel de Gestión."""
    if usuario["role_name"] not in ("Administrador", "Recursos Humanos"):
        raise HTTPException(status_code=403, detail="Requiere rol de Recursos Humanos.")


def _personal_publico(fila: Dict[str, Any]) -> Dict[str, Any]:
    """Filtra los campos de un empleado sin exponer credenciales."""
    return {
        k: fila[k]
        for k in (
            "id",
            "username",
            "full_name",
            "role_name",
            "salario_mensual",
            "tipo_vinculo",
            "activo",
            "creado_en",
        )
        if k in fila
    }


@app.post("/api/marcar")
def api_marcar(payload: MarcarRequest) -> Dict[str, Any]:
    """Kiosco web: registra entrada/salida con cédula y contraseña."""
    db = _cliente()
    try:
        usuario = auth.authenticate(db, payload.cedula.strip(), payload.password)
        if not usuario:
            raise HTTPException(
                status_code=401, detail="Cédula o contraseña incorrectas."
            )
        engine = clock_engine.ClockEngine(db, usuario)
        try:
            registro_id, momento, tipo = engine.registrar_asistencia(
                es_dia_lluvioso=payload.es_dia_lluvioso
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        return {
            "nombre": usuario["full_name"],
            "tipo": tipo,
            "hora": momento.strftime("%H:%M:%S"),
            "momento": momento.isoformat(),
            "ticket": reports.comprobante_marcacion(registro_id, momento, tipo),
        }
    finally:
        db.cerrar()


@app.get("/api/panel/resumen")
def api_panel_resumen(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Resumen operativo del Panel de Gestión (RRHH/Administrador)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "personal": len(db.list_users()),
            "marcas_hoy": db.count_marcajes_hoy(),
            "justificaciones": len(db.list_justificaciones()),
            "correcciones_pendientes": sum(
                1
                for c in db.list_solicitudes_correccion()
                if c.get("estado") == "Pendiente"
            ),
            "alertas_no_leidas": len(db.listar_alertas(no_leidas=True)),
        }
    finally:
        db.cerrar()


@app.get("/api/panel/personal")
def api_panel_personal(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Lista de empleados (sin credenciales) y roles disponibles."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "roles": [r["nombre"] for r in db.list_roles()],
            "personal": [_personal_publico(u) for u in db.list_users()],
        }
    finally:
        db.cerrar()


@app.post("/api/panel/personal")
def api_panel_personal_crear(
    payload: PersonalNuevo,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Crea un empleado desde el Panel de Gestión."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            nuevo_id = auth.create_user(
                db,
                usuario,
                payload.username.strip(),
                payload.password,
                payload.full_name.strip(),
                payload.role_name,
                payload.salario_mensual,
                payload.tipo_vinculo,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"id": nuevo_id, "mensaje": "Personal creado correctamente."}
    finally:
        db.cerrar()


@app.put("/api/panel/personal/{user_id}")
def api_panel_personal_editar(
    user_id: int,
    payload: PersonalEditar,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Actualiza los datos de un empleado."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            auth.update_user(
                db,
                usuario,
                user_id,
                full_name=payload.full_name.strip() if payload.full_name else None,
                password=payload.password or None,
                role_name=payload.role_name or None,
                salario_mensual=payload.salario_mensual,
                tipo_vinculo=payload.tipo_vinculo or None,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"mensaje": "Personal actualizado correctamente."}
    finally:
        db.cerrar()


@app.delete("/api/panel/personal/{user_id}")
def api_panel_personal_eliminar(
    user_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Elimina un empleado (queda auditado en logs_auditoria)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            auth.delete_user(db, usuario, user_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"mensaje": "Personal eliminado correctamente."}
    finally:
        db.cerrar()


@app.get("/api/panel/justificaciones")
def api_panel_justificaciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Justificaciones emitidas, empleados y catálogo de permisos."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return {
            "justificaciones": db.list_justificaciones(),
            "personal": [_personal_publico(u) for u in db.list_users()],
            "tipos": list(auth.TIPOS_PERMISO),
        }
    finally:
        db.cerrar()


@app.post("/api/panel/justificaciones")
def api_panel_justificaciones_crear(
    payload: JustificacionRRHH,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Emitir una justificación oficial (valida reglamento y cuotas)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        try:
            inicio = datetime.date.fromisoformat(payload.fecha_inicio.strip())
            fin = datetime.date.fromisoformat(payload.fecha_fin.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Fechas inválidas. Use AAAA-MM-DD.")
        try:
            solicitud_id = auth.crear_justificacion(
                db,
                usuario,
                payload.empleado_id,
                payload.tipo_permiso,
                inicio,
                fin,
                payload.horas_usadas,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {
            "id": solicitud_id,
            "mensaje": f"Justificación #{solicitud_id} emitida y aprobada.",
        }
    finally:
        db.cerrar()


@app.get("/api/panel/justificaciones/{solicitud_id}/pdf")
def api_panel_justificaciones_pdf(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> FileResponse:
    """Descarga del PDF oficial de una justificación (solo RRHH/Admin)."""
    _exigir_rrhh(usuario)
    try:
        ruta = reports.generar_pdf_permiso(solicitud_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return FileResponse(
        ruta, media_type="application/pdf", filename=Path(ruta).name
    )


@app.get("/api/panel/correcciones")
def api_panel_correcciones(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Solicitudes de corrección de marcaje con su estado."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.list_solicitudes_correccion()
    finally:
        db.cerrar()


def _resolver_correccion(
    solicitud_id: int, aprobar: bool, usuario: Dict[str, Any]
) -> Dict[str, Any]:
    db = _cliente()
    try:
        try:
            estado = auth.aprobar_solicitud_correccion(db, usuario, solicitud_id, aprobar)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"estado": estado, "mensaje": f"Solicitud #{solicitud_id} {estado.lower()}."}
    finally:
        db.cerrar()


@app.post("/api/panel/correcciones/{solicitud_id}/aprobar")
def api_panel_correcciones_aprobar(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Aprueba la corrección: materializa la marca propuesta."""
    _exigir_rrhh(usuario)
    return _resolver_correccion(solicitud_id, True, usuario)


@app.post("/api/panel/correcciones/{solicitud_id}/rechazar")
def api_panel_correcciones_rechazar(
    solicitud_id: int,
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> Dict[str, Any]:
    """Rechaza la corrección sin tocar los marcajes."""
    _exigir_rrhh(usuario)
    return _resolver_correccion(solicitud_id, False, usuario)


@app.get("/api/panel/auditoria")
def api_panel_auditoria(
    usuario: Dict[str, Any] = Depends(_usuario_autenticado),
) -> List[Dict[str, Any]]:
    """Bitácora de auditoría para trazabilidad (RRHH/Administrador)."""
    _exigir_rrhh(usuario)
    db = _cliente()
    try:
        return db.listar_auditoria(limite=100)
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