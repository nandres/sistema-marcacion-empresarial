"""Servidor web local de consulta transparente para empleados (FastAPI).

Expone una interfaz responsiva (celular/PC) con el Modo Oscuro Premium del
sistema (#121214): el empleado ingresa su cédula, pulsa el botón inteligente
"Hoy" y ve al instante sus marcas del día, las horas extra acumuladas del
mes y su aguinaldo proporcional (Ley N.º 6380/2019).

Solo realiza consultas de lectura sobre PostgreSQL; no registra marcajes,
no expone credenciales ni información de otros empleados.

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
    description="Autoservicio del empleado: marcas del día, horas extra del mes y aguinaldo.",
    version="1.0.0",
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
    padding:28px 24px; width:100%; max-width:520px;
  }}
  h1 {{ font-size:1.35rem; text-align:center; }}
  .subtitulo {{ color:{MUTED}; font-size:.85rem; text-align:center; margin-top:6px; }}
  label {{ display:block; font-size:.85rem; color:{MUTED}; margin:18px 0 6px; }}
  input {{
    width:100%; padding:13px 14px; border-radius:10px; border:1px solid #34343B;
    background:#232329; color:{TEXT}; font-size:1rem; outline:none;
  }}
  input:focus {{ border-color:{PRIMARY}; }}
  .fila {{ display:flex; gap:8px; margin-top:8px; }}
  .fila input {{ flex:1; }}
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
    padding:8px 0; border-bottom:1px solid #26262C; font-size:.92rem;
  }}
  .fila-marca:last-child {{ border-bottom:none; }}
  .etiqueta {{
    font-size:.72rem; padding:3px 8px; border-radius:999px; font-weight:600;
  }}
  .tardanza {{ background:#3A1F1E; color:#F0544F; }}
  .feriado {{ background:#3A2F1E; color:#F5C26B; }}
  .ok {{ background:#12301F; color:#4ADE80; }}
  .total {{ font-size:1.15rem; font-weight:700; margin-top:8px; }}
  .error {{ color:#F0544F; text-align:center; margin-top:16px; }}
  .pie {{ color:{MUTED}; font-size:.75rem; text-align:center; margin-top:18px; }}
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
    <label for="fecha">Fecha a consultar</label>
    <div class="fila">
      <input id="fecha" type="date">
      <button class="btn-hoy" onclick="capturarHoy()">Hoy</button>
    </div>
    <button onclick="consultar()">VER MIS MARCAS</button>
    <div id="resultado" class="oculto"></div>
    <p id="error" class="error"></p>
  </div>
  <p class="pie">Modo consulta · solo lectura · no se registran marcajes desde la web</p>
<script>
  function capturarHoy() {{
    const hoy = new Date();
    const iso = hoy.getFullYear() + '-' +
      String(hoy.getMonth() + 1).padStart(2, '0') + '-' +
      String(hoy.getDate()).padStart(2, '0');
    document.getElementById('fecha').value = iso;
  }}
  capturarHoy();
  function gs(n) {{ return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.'); }}
  function render(d) {{
    const r = document.getElementById('resultado');
    let html = '<div class="bloque"><h3>' + d.nombre + ' · ' + d.fecha + '</h3>';
    if (!d.marcas_dia.length) {{
      html += '<p>Sin marcas registradas este día.</p>';
    }} else {{
      d.marcas_dia.forEach(function (m) {{
        let badges = '';
        if (m.tardanza) badges += ' <span class="etiqueta tardanza">Tardanza</span>';
        if (m.feriado) badges += ' <span class="etiqueta feriado">Feriado</span>';
        html += '<div class="fila-marca"><span>Entrada ' + m.entrada +
          ' · Salida ' + (m.salida || 'en curso') + badges + '</span></div>' +
          '<div class="fila-marca"><span>Ordinarias ' + m.ordinarias +
          ' · Extra 50% ' + m.extra_50 + ' · Extra 100% ' + m.extra_100 + '</span></div>';
      }});
    }}
    html += '</div>';
    html += '<div class="bloque"><h3>Horas extra del mes</h3>' +
      '<div class="fila-marca"><span>Recargo 50%</span><span>' +
      d.extras_mes.texto_50 + ' (' + d.extras_mes.horas_50.toFixed(2) + ' h)</span></div>' +
      '<div class="fila-marca"><span>Recargo 100%</span><span>' +
      d.extras_mes.texto_100 + ' (' + d.extras_mes.horas_100.toFixed(2) + ' h)</span></div></div>';
    if (d.aguinaldo) {{
      html += '<div class="bloque"><h3>Aguinaldo proporcional · Ley 6380/2019</h3>' +
        '<div class="fila-marca"><span>Salario mensual</span><span>Gs. ' +
        gs(d.aguinaldo.salario_mensual) + '</span></div>' +
        '<div class="fila-marca"><span>Meses trabajados</span><span>' +
        d.aguinaldo.meses_trabajados + '</span></div>' +
        '<div class="fila-marca"><span>Valor horas extra</span><span>Gs. ' +
        gs(d.aguinaldo.valor_extras) + '</span></div>' +
        '<div class="total">Aguinaldo: Gs. ' + gs(d.aguinaldo.aguinaldo) + '</div></div>';
    }}
    r.innerHTML = html;
    r.classList.remove('oculto');
  }}
  async function consultar() {{
    const cedula = document.getElementById('cedula').value.trim();
    const fecha = document.getElementById('fecha').value;
    document.getElementById('error').textContent = '';
    if (!cedula || !fecha) {{
      document.getElementById('error').textContent = 'Ingrese su cédula y fecha.';
      return;
    }}
    const resp = await fetch('/api/consulta', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ cedula: cedula, fecha: fecha }})
    }});
    const dato = await resp.json();
    if (!resp.ok) {{
      document.getElementById('error').textContent = dato.detail || 'Error de consulta.';
      return;
    }}
    render(dato);
  }}
</script>
</body>
</html>"""


class ConsultaRequest(BaseModel):
    """Cuerpo de la consulta: cédula/usuario y fecha en formato ISO."""

    cedula: str
    fecha: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Página principal del autoservicio del empleado."""
    return PAGINA_HTML


@app.post("/api/consulta")
def api_consulta(payload: ConsultaRequest) -> Dict[str, Any]:
    """Devuelve marcas del día, horas extra del mes y aguinaldo del empleado."""
    db = database.Database()
    db.initialize()
    user = db.get_user_by_username(payload.cedula.strip())
    if not user:
        raise HTTPException(status_code=404, detail="Empleado no encontrado.")
    try:
        fecha = datetime.date.fromisoformat(payload.fecha.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha inválida. Use AAAA-MM-DD.")
    return reports.resumen_consulta(db, user, fecha)


def main() -> None:
    """Levanta el servidor web local de consulta."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()