"""Qué pasa cuando toda la empresa marca en el mismo minuto.

Un control de asistencia no tiene tráfico parejo: concentra el día entero en
dos ventanas de quince minutos, a la entrada y a la salida. Es el único
momento que importa y era el único que no se había probado nunca.

Lo que se **afirma** es la corrección bajo concurrencia: que cada marca quede
registrada una sola vez y que ninguna se pierda. Los tiempos se informan pero
no se afirman: un umbral en milisegundos sobre una máquina de desarrollo es
una prueba que falla por lo que haya estado corriendo al lado.

    WEB_BASE=http://127.0.0.1:8000 python tests/test_carga.py
"""

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

import auth
from database import Database

BASE = os.getenv("WEB_BASE", "http://127.0.0.1:8000")
CUANTOS = int(os.getenv("CARGA_EMPLEADOS", "40"))
CLAVE = "carga123456"
PREFIJO = "carga_"

fallos = 0


def verificar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK  ' if condicion else 'FALLA'} {descripcion}"
          + (f" | {detalle}" if detalle else ""))


def limpiar(db: Database, admin) -> None:
    for usuario in db.listar_usuarios() if hasattr(db, "listar_usuarios") else []:
        if str(usuario.get("username", "")).startswith(PREFIJO):
            try:
                auth.delete_user(db, admin, usuario["id"])
            except Exception:
                pass


async def marcar(cliente: httpx.AsyncClient, cedula: str) -> dict:
    inicio = time.perf_counter()
    try:
        respuesta = await cliente.post(
            f"{BASE}/api/marcar",
            json={"cedula": cedula, "password": CLAVE},
            timeout=60.0,
        )
        return {
            "cedula": cedula,
            "codigo": respuesta.status_code,
            "ms": (time.perf_counter() - inicio) * 1000,
            "cuerpo": respuesta.text[:120],
        }
    except Exception as error:
        return {
            "cedula": cedula,
            "codigo": 0,
            "ms": (time.perf_counter() - inicio) * 1000,
            "cuerpo": f"{type(error).__name__}: {error}"[:120],
        }


async def avalancha(cedulas: list) -> list:
    limites = httpx.Limits(max_connections=len(cedulas) + 10,
                           max_keepalive_connections=len(cedulas) + 10)
    async with httpx.AsyncClient(limits=limites) as cliente:
        # Sin semáforo a propósito: el punto es que salgan todas juntas, que
        # es lo que hace la gente a las ocho de la mañana.
        return await asyncio.gather(*(marcar(cliente, c) for c in cedulas))


def main() -> int:
    global fallos

    try:
        httpx.get(f"{BASE}/", timeout=10)
    except Exception as error:
        print(f"No hay servidor en {BASE}: {error}")
        print("Levantalo con: python src/web_server.py")
        return 1

    db = Database()
    db.initialize()
    admin = db.get_user_by_username("admin")

    # El cupo del plan se comprueba al dar de alta: sin levantarlo, la prueba
    # se quedaría sin poder crear su propia plantilla.
    empresa = db.get_empresa(db.empresa)
    cupo_previo = (empresa or {}).get("max_empleados")
    db.fijar_cupo_empresa(db.empresa, None)

    print(f"CARGA · {CUANTOS} personas marcando a la vez contra {BASE}")

    print(f"\n1) Plantilla de prueba")
    cedulas = []
    for indice in range(CUANTOS):
        usuario = f"{PREFIJO}{indice:03d}"
        existente = db.get_user_by_username(usuario)
        if existente:
            auth.delete_user(db, admin, existente["id"])
        auth.create_user(db, admin, usuario, CLAVE, f"Carga {indice:03d}",
                         "Empleado", 2500000)
        cedulas.append(usuario)
    verificar("se dieron de alta todos", len(cedulas) == CUANTOS, str(len(cedulas)))

    antes = db.count_marcajes_hoy()

    print(f"\n2) Todos marcan en el mismo instante")
    arranque = time.perf_counter()
    resultados = asyncio.run(avalancha(cedulas))
    total_s = time.perf_counter() - arranque

    exitos = [r for r in resultados if r["codigo"] == 200]
    errores = [r for r in resultados if r["codigo"] != 200]
    tiempos = sorted(r["ms"] for r in resultados)

    verificar("ninguna marca fue rechazada", not errores,
              errores[0]["cuerpo"] if errores else "")
    verificar("respondieron todas", len(resultados) == CUANTOS)

    print(f"\n  {CUANTOS} marcas en {total_s:.2f} s "
          f"({CUANTOS / total_s:.0f} por segundo)")
    print(f"  mediana {statistics.median(tiempos):.0f} ms · "
          f"p95 {tiempos[int(len(tiempos) * 0.95) - 1]:.0f} ms · "
          f"máxima {tiempos[-1]:.0f} ms")

    if errores:
        reparto = {}
        for e in errores:
            reparto[e["codigo"]] = reparto.get(e["codigo"], 0) + 1
        print(f"  errores por código: {reparto}")

    print(f"\n3) Cada marca quedó registrada una sola vez")
    despues = db.count_marcajes_hoy()
    verificar("se registraron tantas marcas como éxitos hubo",
              despues - antes == len(exitos),
              f"esperaba {len(exitos)}, hay {despues - antes}")

    abiertas = 0
    duplicadas = 0
    for cedula in cedulas:
        usuario = db.get_user_by_username(cedula)
        if not usuario:
            continue
        abierta = db.get_open_entry(usuario["id"])
        if abierta:
            abiertas += 1
        filas = db._execute(
            "SELECT COUNT(*) AS total FROM marcajes WHERE empresa_id = %s "
            "AND user_id = %s AND hora_salida IS NULL",
            (db.empresa, usuario["id"]), fetch="one",
        )
        if filas and int(filas["total"]) > 1:
            duplicadas += 1

    verificar("cada quien tiene su entrada abierta", abiertas == len(exitos),
              f"{abiertas} de {len(exitos)}")
    verificar("nadie quedó con dos entradas abiertas", duplicadas == 0,
              f"{duplicadas} con más de una")

    for cedula in cedulas:
        registro = db.get_user_by_username(cedula)
        if registro:
            auth.delete_user(db, admin, registro["id"])
    db.fijar_cupo_empresa(db.empresa, cupo_previo)
    db.cerrar()

    print()
    if fallos:
        print(f"CARGA: {fallos} verificación(es) fallidas")
        return 1
    print("CARGA OK · nada se pierde ni se duplica con todos marcando juntos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
