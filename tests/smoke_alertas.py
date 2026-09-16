import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

import web_server

c = TestClient(web_server.app)

r = c.post("/api/login", json={"cedula": "admin", "password": "admin123"})
print("login", r.status_code)
token = r.json()["token"]

r2 = c.get("/api/alertas", headers={"Authorization": f"Bearer {token}"})
print("listar alertas", r2.status_code,
      "total", len(r2.json()["alertas"]),
      "no_leidas", r2.json()["no_leidas"])

ALERTA = {"tipo": "test", "severidad": "alta",
          "mensaje": "alerta publicada", "detalle": "desde api"}

r4 = c.post("/api/alertas", json=ALERTA)
print("publicar sin token", r4.status_code)
r5 = c.post("/api/alertas", json=ALERTA,
            headers={"Authorization": f"Bearer {token}"})
print("publicar con token", r5.status_code, r5.json()["id"])

with c.websocket_connect("/ws/alertas", cookies={"marcacion_sesion": token}) as ws:
    m = ws.receive_json()
    print("ws primer mensaje:", m["mensaje"])
    ws.send_text("ping")

r3 = c.post("/api/alertas/leidas", headers={"Authorization": f"Bearer {token}"})
print("marcar leidas", r3.status_code, r3.json())

r6 = c.post(
    "/api/alertas",
    json={"tipo": "fraude_facial", "severidad": "alta",
          "mensaje": "Suplantacion detectada", "detalle": "cara distinta",
          "usuario_id": 2},
    headers={"Authorization": f"Bearer {token}"},
)
print("publicar con usuario_id", r6.status_code)

# WS del empleado 2: debe recibir solo la alerta con su usuario_id (no la global pendiente)
rlogin = c.post("/api/login", json={"cedula": "juan", "password": "clave123"})
print("login juan", rlogin.status_code)
token_juan = rlogin.json()["token"]

# Un Empleado no puede publicar alertas: se difunden a todos los conectados,
# asi que sin control de rol cualquiera inyecta contenido en la pantalla ajena.
r7 = c.post(
    "/api/alertas",
    json={"tipo": "fraude_facial", "severidad": "alta",
          "mensaje": "<img src=x onerror=alert(1)>", "detalle": "inyeccion"},
    headers={"Authorization": f"Bearer {token_juan}"},
)
print("publicar como Empleado (espera 403):", r7.status_code)
assert r7.status_code == 403, f"un Empleado pudo publicar una alerta: {r7.status_code}"
with c.websocket_connect("/ws/alertas",
                         cookies={"marcacion_sesion": token_juan}) as ws:
    m = ws.receive_json()
    print("ws juan recibió:", m["mensaje"], "usuario_id", m.get("usuario_id"))

print("WS+API OK")
