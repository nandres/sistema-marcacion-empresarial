"""P3-2: el token de sesión viajaba en la URL y vivía en `localStorage`.

En la URL del WebSocket quedaba escrito en los logs de acceso del servidor, en
el historial del navegador y en la cabecera `Referer` hacia cualquier recurso
externo. En `localStorage` lo alcanzaba cualquier script que llegara a correr
en la página.

Ahora la sesión del navegador vive en una cookie `HttpOnly` y `SameSite=Strict`.
Acá se comprueba que la cookie tenga las marcas que la hacen valer —sin ellas
es una cookie común, o sea el mismo problema con otro nombre—, que sirva para
autenticar, que el cierre de sesión la anule y que el token haya desaparecido
de la interfaz.
"""

import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8000")
COOKIE = "marcacion_sesion"

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(
        f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
        + (f" | {detalle}" if detalle else "")
    )


print("SESIÓN EN COOKIE · P3-2")

# ------------------------------------------------- 1. La cookie y sus marcas
print("\n1) La cookie llega con las marcas que la hacen valer")

with httpx.Client(base_url=BASE, timeout=30) as cliente:
    entrada = cliente.post(
        "/api/login", json={"cedula": "admin", "password": "admin123"}
    )
    verificar("el login responde", entrada.status_code == 200,
              str(entrada.status_code))
    cabecera = entrada.headers.get("set-cookie", "")
    verificar("deja la cookie de sesión", COOKIE in cabecera)
    verificar("marcada HttpOnly: ningún script la lee",
              "httponly" in cabecera.lower())
    verificar("marcada SameSite=Strict: no viaja desde otro sitio",
              "samesite=strict" in cabecera.lower().replace(" ", ""),
              cabecera.split(";")[-1].strip())
    verificar("con vencimiento propio", "max-age" in cabecera.lower())

    # ------------------------------------------- 2. Autentica sin encabezado
    print("\n2) La cookie sola alcanza para autenticar")

    sesion = cliente.get("/api/sesion")
    verificar("responde quién es la sesión en curso",
              sesion.status_code == 200, str(sesion.status_code))
    datos = sesion.json() if sesion.status_code == 200 else {}
    verificar("con el nombre, el rol y la empresa",
              {"nombre", "rol", "empresa"} <= set(datos),
              ", ".join(sorted(datos)))

    resumen = cliente.get("/api/resumen")
    verificar("y sirve para el resto de la API sin cabecera Authorization",
              resumen.status_code == 200, str(resumen.status_code))

    # --------------------------------------------- 3. El cierre la anula
    print("\n3) El cierre de sesión la anula del lado del servidor")

    salida = cliente.post("/api/logout")
    verificar("el cierre responde", salida.status_code == 200)
    verificar("y después la sesión ya no vale",
              cliente.get("/api/sesion").status_code == 401)

# ------------------------------------------- 4. El encabezado sigue sirviendo
print("\n4) El encabezado sigue sirviendo para lo que no es un navegador")

with httpx.Client(base_url=BASE, timeout=30) as cliente:
    token = cliente.post(
        "/api/login", json={"cedula": "admin", "password": "admin123"}
    ).json()["token"]
    cliente.cookies.clear()
    con_cabecera = cliente.get(
        "/api/sesion", headers={"Authorization": f"Bearer {token}"}
    )
    verificar("un cliente sin cookies entra con Bearer",
              con_cabecera.status_code == 200, str(con_cabecera.status_code))
    verificar("y sin nada no entra",
              cliente.get("/api/sesion").status_code == 401)

# --------------------------------------- 5. El token salió de la interfaz
print("\n5) El token desapareció de la interfaz")

portal = (RAIZ / "src" / "static" / "portal.js").read_text(encoding="utf-8")
verificar("el WebSocket ya no lleva el token en la URL",
          "ws/alertas?token=" not in portal)
verificar("no queda ninguna referencia a un token de sesión",
          "sesion.token" not in portal)
verificar("no se guarda el token en el almacenamiento del navegador",
          "marcacion_jwt" not in portal)
guardados = set(re.findall(r'almacenar\("([a-z_]+)"', portal))
verificar("lo único que el navegador guarda es la preferencia de tema",
          guardados == {"marcacion_tema"}, ", ".join(sorted(guardados)) or "nada")

print()
if fallos:
    print(f"SESIÓN EN COOKIE: {fallos} verificación(es) fallidas")
    sys.exit(1)
print("SESIÓN EN COOKIE OK · P3-2 cerrado: ni en la URL ni en localStorage")
