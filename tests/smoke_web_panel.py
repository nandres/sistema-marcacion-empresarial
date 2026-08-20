import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8080")
B = {"Content-Type": "application/json"}


def pedir(cliente, metodo, ruta, token=None, cuerpo=None):
    h = dict(B)
    if token:
        h["Authorization"] = "Bearer " + token
    r = cliente.request(metodo, BASE + ruta, headers=h, json=cuerpo)
    try:
        d = r.json()
    except Exception:
        d = "<binario " + str(r.status_code) + " " + str(len(r.content)) + " bytes>"
    print(f"{metodo} {ruta} -> {r.status_code} | {str(d)[:110].encode('ascii', 'replace').decode()}")
    return r, d


with httpx.Client(timeout=30) as c:
    r, d = pedir(c, "POST", "/api/login", cuerpo={"cedula": "admin", "password": "admin123"})
    assert r.status_code == 200, "login admin falló"
    token = d["token"]

    r, d = pedir(c, "GET", "/api/panel/resumen", token)
    assert r.status_code == 200 and "personal" in d
    assert d["personal"] >= 2, "debe haber al menos admin y juan"

    r, d = pedir(c, "GET", "/api/panel/personal", token)
    assert r.status_code == 200 and d["roles"] and d["personal"]

    r, d = pedir(c, "POST", "/api/panel/personal", token, {
        "username": "temp_web", "password": "temp123", "full_name": "Temp Web",
        "role_name": "Empleado", "salario_mensual": 1500000, "tipo_vinculo": "Funcionario",
    })
    assert r.status_code == 200, d
    temp_id = d["id"]

    r, d = pedir(c, "PUT", f"/api/panel/personal/{temp_id}", token,
                 {"full_name": "Temp Web Editado", "salario_mensual": 1600000})
    assert r.status_code == 200, d

    r, d = pedir(c, "DELETE", f"/api/panel/personal/{temp_id}", token)
    assert r.status_code == 200, d

    hoy = datetime.date.today().isoformat()
    r, d = pedir(c, "GET", "/api/panel/justificaciones", token)
    assert r.status_code == 200 and "tipos" in d and "personal" in d
    juan_id = next(p["id"] for p in d["personal"] if p["username"] == "juan")

    just_id = None
    for tipo in ["Motivos Particulares", "Duelo", "Matrimonio", "Salud Familiar", "Permiso por Examen"]:
        r, d = pedir(c, "POST", "/api/panel/justificaciones", token, {
            "empleado_id": juan_id, "tipo_permiso": tipo,
            "fecha_inicio": hoy, "fecha_fin": hoy, "horas_usadas": 0.0,
        })
        if r.status_code == 200:
            just_id = d["id"]
            break
        assert "Cuota" in d["detail"] or "disponible" in d["detail"], d
    assert just_id, "ningún permiso con cuota disponible"

    r, d = pedir(c, "GET", f"/api/panel/justificaciones/{just_id}/pdf", token)
    assert r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf"), r.status_code

    r, d = pedir(c, "GET", "/api/panel/correcciones", token)
    assert r.status_code == 200

    r, d = pedir(c, "GET", "/api/panel/auditoria", token)
    assert r.status_code == 200 and isinstance(d, list)

    r, d = pedir(c, "GET", "/api/alertas", token)
    assert r.status_code == 200 and "alertas" in d

    r, d = pedir(c, "POST", "/api/login", cuerpo={"cedula": "juan", "password": "clave123"})
    assert r.status_code == 200
    token_juan = d["token"]

    r, d = pedir(c, "GET", "/api/panel/resumen", token_juan)
    assert r.status_code == 403, "un Empleado no debe ver el panel"

    r, d = pedir(c, "POST", "/api/marcar", cuerpo={"cedula": "juan", "password": "clave123", "es_dia_lluvioso": False})
    if r.status_code == 200:
        assert d["tipo"] in ("Entrada", "Salida") and "ticket" in d, d
        assert "EMPRESA|3028/2024" in d["ticket"], "el ticket debe llevar la serie EMPRESA"
        if d["tipo"] == "Entrada":
            r2, d2 = pedir(c, "POST", "/api/marcar", cuerpo={"cedula": "juan", "password": "clave123"})
            assert r2.status_code == 200 and d2["tipo"] == "Salida", d2
    elif r.status_code == 400 and any(
        m in d["detail"] for m in ("entrada abierta", "entrada y su salida de hoy")
    ):
        pass
    else:
        raise AssertionError(f"respuesta inesperada del kiosco: {r.status_code} {d}")

    r, d = pedir(c, "POST", "/api/marcar", cuerpo={"cedula": "juan", "password": "incorrecta"})
    assert r.status_code == 401, "contraseña mala debe fallar"

    r, d = pedir(c, "POST", "/api/marcar", cuerpo={"cedula": "noexiste", "password": "x"})
    assert r.status_code == 401, "usuario inexistente debe fallar"

print("SMOKE WEB PANEL OK: kiosco, gestión RRHH y permisos verificados")