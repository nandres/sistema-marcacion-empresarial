"""Servidor web local de consulta histórica y reclamos para empleados.

FastAPI con Modo Oscuro Premium (#121214) y diseño responsivo: el empleado
ingresa su cédula y elige un rango de fechas (el botón "Hoy" captura la
fecha actual de su equipo) para revisar de golpe su historial completo de
marcas, las horas extra 50%/100% acumuladas y el aguinaldo devengado en el
período (Ley N.º 6380/2019). También puede enviar una Solicitud de
Corrección a Recursos Humanos si olvidó marcar o el sensor biométrico falló.

Las consultas son de solo lectura; los reclamos se crean en estado
Pendiente y solo RRHH/Administrador pueden resolverlos desde el panel de
gestión del escritorio.

Ejecución:
    python src/web_server.py          # http://127.0.0.1:8000
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import database
import reports

app = FastAPI(
    title="Consulta de Marcas · Sistema de Marcación",
    description="Historial de marcas, horas extra y reclamos de corrección.",
    version="2.0.0",
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
  <div class="tarjeta">
    <h1>Sistema de Marcación</h1>
    <p class="subtitulo">Consulta transparente de tu historial · Paraguay</p>
    <label for="cedula">Tu cédula o usuario</label>
    <input id="cedula" placeholder="Ej. 1234567 o juan" autocomplete="off">
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
  <div class="tarjeta">
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
  function capturarHoy() {{
    const hoy = isoHoy();
    document.getElementById('desde').value = hoy;
    document.getElementById('hasta').value = hoy;
    consultar();
  }}
  document.getElementById('fecha_reclamo').value = isoHoy();
  document.getElementById('desde').value = new Date().getFullYear() + '-01-01';
  document.getElementById('hasta').value = isoHoy();
  function gs(n) {{ return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.'); }}
  function renderHistorico(d) {{
    const r = document.getElementById('resultado');
    let html = '<div class="bloque"><h3>' + d.nombre + ' · ' + d.desde + ' → ' + d.hasta + '</h3>';
    if (!d.marcas.length) {{
      html += '<p>Sin marcas registradas en el período.</p>';
    }} else {{
      d.marcas.forEach(function (m) {{
        let badges = '';
        if (m.incidencia) badges += ' <span class="etiqueta tardanza">' + m.incidencia + '</span>';
        if (m.feriado) badges += ' <span class="etiqueta feriado">Feriado</span>';
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
    const cedula = document.getElementById('cedula').value.trim();
    const desde = document.getElementById('desde').value;
    const hasta = document.getElementById('hasta').value;
    document.getElementById('error').textContent = '';
    if (!cedula || !desde || !hasta) {{
      document.getElementById('error').textContent = 'Ingrese su cédula y el rango de fechas.';
      return;
    }}
    const resp = await fetch('/api/consulta', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ cedula: cedula, desde: desde, hasta: hasta }})
    }});
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error de consulta.';
      return;
    }}
    renderHistorico(dato);
  }}
  async function enviarReclamo() {{
    const cedula = document.getElementById('cedula').value.trim();
    const respuesta = document.getElementById('reclamo_respuesta');
    respuesta.textContent = '';
    if (!cedula) {{
      document.getElementById('error').textContent = 'Ingrese su cédula antes de reclamar.';
      return;
    }}
    const resp = await fetch('/api/reclamo', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        cedula: cedula,
        tipo_marca: document.getElementById('tipo_marca').value,
        fecha: document.getElementById('fecha_reclamo').value,
        hora_propuesta: document.getElementById('hora_propuesta').value,
        motivo: document.getElementById('motivo').value
      }})
    }});
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error al enviar el reclamo.';
      return;
    }}
    respuesta.textContent = dato.mensaje;
    document.getElementById('motivo').value = '';
  }}
</script>
</body>
</html>"""


class ConsultaRequest(BaseModel):
    """Cuerpo de la consulta: cédula y rango de fechas en formato ISO."""

    cedula: str
    desde: str = ""
    hasta: str = ""
    fecha: str = ""


class ReclamoRequest(BaseModel):
    """Cuerpo del reclamo: cédula, marca a corregir y datos propuestos."""

    cedula: str
    tipo_marca: str
    fecha: str
    hora_propuesta: str
    motivo: str


def _cliente() -> database.Database:
    """Abre una conexión fresca por petición para evitar sesiones cruzadas."""
    db = database.Database()
    db.initialize()
    return db


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Página principal del autoservicio del empleado."""
    return PAGINA_HTML


@app.post("/api/consulta")
def api_consulta(payload: ConsultaRequest) -> Dict[str, Any]:
    """Historial del empleado: rango completo o consulta puntual de un día."""
    db = _cliente()
    try:
        user = db.get_user_by_username(payload.cedula.strip())
        if not user:
            raise HTTPException(status_code=404, detail="Empleado no encontrado.")
        if payload.fecha:
            try:
                puntual = datetime.date.fromisoformat(payload.fecha.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
            return reports.resumen_consulta(db, user, puntual)
        try:
            desde = datetime.date.fromisoformat(payload.desde.strip())
            hasta = datetime.date.fromisoformat(payload.hasta.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail="Rango inválido. Use AAAA-MM-DD.")
        try:
            return reports.resumen_historico(db, user, desde, hasta)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
    finally:
        db.cerrar()


@app.post("/api/reclamo")
def api_reclamo(payload: ReclamoRequest) -> Dict[str, Any]:
    """Registra una solicitud de corrección en estado Pendiente."""
    db = _cliente()
    try:
        user = db.get_user_by_username(payload.cedula.strip())
        if not user:
            raise HTTPException(status_code=404, detail="Empleado no encontrado.")
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
            user["id"], fecha, payload.tipo_marca, hora, motivo
        )
        return {
            "id": solicitud_id,
            "estado": "Pendiente",
            "mensaje": f"Solicitud #{solicitud_id} enviada a Recursos Humanos.",
        }
    finally:
        db.cerrar()


def main() -> None:
    """Levanta el servidor web local de consulta y reclamos."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()