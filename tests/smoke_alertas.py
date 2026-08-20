import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient
import web_server
import notifications

c = TestClient(web_server.app)

r = c.post("/api/login", json={"cedula": "admin", "password": "admin123"})
print("login", r.status_code)
token = r.json()["token"]

r2 = c.get("/api/alertas", headers={"Authorization": f"Bearer {token}"})
print("listar alertas", r2.status_code, "total", len(r2.json()["alertas"]), "no_leidas", r2.json()["no_leidas"])

r4 = c.post("/api/alertas", json={"tipo": "test", "severidad": "alta", "mensaje": "alerta publicada", "detalle": "desde api"})
print("publicar sin token", r4.status_code)
r5 = c.post("/api/alertas", json={"tipo": "test", "severidad": "alta", "mensaje": "alerta publicada", "detalle": "desde api"}, headers={"Authorization": f"Bearer {token}"})
print("publicar con token", r5.status_code, r5.json()["id"])

with c.websocket_connect("/ws/alertas?token=" + token) as ws:
    m = ws.receive_json()
    print("ws primer mensaje:", m["mensaje"])
    ws.send_text("ping")

r3 = c.post("/api/alertas/leidas", headers={"Authorization": f"Bearer {token}"})
print("marcar leidas", r3.status_code, r3.json())

r6 = c.post("/api/alertas", json={"tipo": "fraude_facial", "severidad": "alta", "mensaje": "Suplantacion detectada", "detalle": "cara distinta", "usuario_id": 2}, headers={"Authorization": f"Bearer {token}"})
print("publicar con usuario_id", r6.status_code)

# WS del empleado 2: debe recibir solo la alerta con su usuario_id (no la global pendiente)
import json
rlogin = c.post("/api/login", json={"cedula": "juan", "password": "clave123"})
print("login juan", rlogin.status_code)
token_juan = rlogin.json()["token"]
with c.websocket_connect("/ws/alertas?token=" + token_juan) as ws:
    m = ws.receive_json()
    print("ws juan recibió:", m["mensaje"], "usuario_id", m.get("usuario_id"))

print("WS+API OK")