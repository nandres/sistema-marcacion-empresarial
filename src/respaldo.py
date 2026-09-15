"""Respaldo y restauración de la base, con la advertencia que importa.

Un volcado de PostgreSQL **no alcanza** para volver a poner el sistema en pie.
Las plantillas faciales están cifradas con ``BIOMETRIA_CLAVE``, que vive fuera
de la base a propósito: quien roba un backup no se lleva con qué abrirlo. La
contracara es que restaurar con otra clave deja las fotos ilegibles, y eso no
se descubre al restaurar sino semanas después, cuando alguien no puede marcar.

Por eso cada respaldo guarda junto al volcado la **huella** de la clave con la
que se hizo —no la clave: su SHA-256 truncado— y la restauración compara. Si
no coinciden avisa antes de tocar nada, en lugar de dejar el problema latente.

Uso:
    python src/respaldo.py crear [carpeta]
    python src/respaldo.py verificar <archivo.dump>
    python src/respaldo.py restaurar <archivo.dump> [--forzar] [--clave-distinta]

``--forzar`` evita la confirmación por teclado. ``--clave-distinta`` es otra
cosa y por eso es otra bandera: acepta restaurar con una clave biométrica que
no es la del respaldo, sabiendo que las fotos quedarán ilegibles.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import Database, load_config, load_dotenv

CARPETA_PREDETERMINADA = "respaldos"

# Tablas cuyo recuento se guarda en el manifiesto. No es una suma de control
# criptográfica: es lo mínimo para notar que se restauró un respaldo truncado.
TABLAS_TESTIGO = ("empresas", "users", "marcajes", "justificaciones",
                  "fotos", "turnos", "solicitudes_permiso")


class RespaldoInvalido(RuntimeError):
    """El archivo no existe, está truncado o no es un volcado de esta base."""


def _detalle(error: BaseException) -> str:
    """Primera línea legible de un error, sin que la codificación la tape.

    PostgreSQL devuelve sus mensajes en la codificación del servidor, que en
    una instalación en español no es UTF-8. Al convertirlo a texto se pierde
    el mensaje real y queda un error de códec que no le dice nada a nadie.
    """
    try:
        texto = str(error)
    except Exception:
        texto = ""
    if not texto or "codec can't decode" in texto:
        argumento = next((a for a in getattr(error, "args", ()) if a), None)
        if isinstance(argumento, bytes):
            texto = argumento.decode("latin-1", "replace")
        else:
            texto = type(error).__name__
    return texto.splitlines()[0][:200]


def _binario(nombre: str) -> str:
    """Ubica ``pg_dump``/``pg_restore``, que en Windows no suelen estar en PATH.

    El instalador de PostgreSQL los deja en ``bin`` bajo la carpeta de la
    versión, así que se busca la más alta si no aparecen en el entorno.
    """
    forzado = os.getenv(f"PG_{nombre.upper()}")
    if forzado:
        return forzado

    from shutil import which
    encontrado = which(nombre)
    if encontrado:
        return encontrado

    raices = [Path(r"C:\Program Files\PostgreSQL"),
              Path("/usr/lib/postgresql")]
    candidatos: List[Path] = []
    for raiz in raices:
        if not raiz.exists():
            continue
        for version in raiz.iterdir():
            binario = version / "bin" / (nombre + (".exe" if os.name == "nt" else ""))
            if binario.exists():
                candidatos.append(binario)
    if candidatos:
        return str(sorted(candidatos)[-1])

    raise RespaldoInvalido(
        f"No encontré {nombre}. Instalá las herramientas de cliente de "
        f"PostgreSQL, o indicá la ruta en PG_{nombre.upper()}."
    )


def huella_de_clave() -> str:
    """Identifica la clave biométrica sin revelarla.

    Se deriva de la clave ya normalizada —la misma que usa el cifrado— para
    que dos formas de escribir el mismo secreto den la misma huella.
    """
    try:
        import biometria
        material = biometria._clave()
    except Exception:
        return ""
    return hashlib.sha256(b"huella-respaldo" + material).hexdigest()[:16]


def _recuentos(db: Database) -> Dict[str, int]:
    conteos: Dict[str, int] = {}
    for tabla in TABLAS_TESTIGO:
        try:
            fila = db._execute(f"SELECT COUNT(*) AS total FROM {tabla}", fetch="one")
            conteos[tabla] = int(fila["total"]) if fila else 0
        except Exception:
            conteos[tabla] = -1
    return conteos


def _manifiesto_de(destino: Path) -> Path:
    return destino.with_suffix(destino.suffix + ".json")


def crear(carpeta: Path) -> Path:
    """Vuelca la base entera y escribe el manifiesto al lado."""
    load_dotenv()
    config = load_config()
    carpeta.mkdir(parents=True, exist_ok=True)

    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = carpeta / f"{config['dbname']}-{sello}.dump"

    entorno = dict(os.environ, PGPASSWORD=config["password"])
    orden = [
        _binario("pg_dump"),
        "--host", config["host"], "--port", str(config["port"]),
        "--username", config["user"], "--dbname", config["dbname"],
        # Formato propio: permite restaurar tablas sueltas y comprime solo.
        "--format=custom", "--no-owner", "--no-privileges",
        "--file", str(destino),
    ]
    print(f"Volcando {config['dbname']} desde {config['host']}…")
    resultado = subprocess.run(orden, env=entorno, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RespaldoInvalido(
            f"pg_dump falló: {(resultado.stderr or '').strip()[:300]}"
        )

    # `connect` y no `initialize`: sacar un respaldo no debe aplicar
    # migraciones ni sembrar nada sobre la base que se está volcando.
    db = Database()
    db.connect()
    manifiesto = {
        "creado": datetime.now().isoformat(timespec="seconds"),
        "base": config["dbname"],
        "host": config["host"],
        "archivo": destino.name,
        "bytes": destino.stat().st_size,
        "huella_biometrica": huella_de_clave(),
        "recuentos": _recuentos(db),
    }
    db.cerrar()

    _manifiesto_de(destino).write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nRespaldo: {destino}")
    print(f"  {manifiesto['bytes'] / 1_048_576:.1f} MB")
    for tabla, total in manifiesto["recuentos"].items():
        print(f"  {total:>8}  {tabla}")

    if not manifiesto["huella_biometrica"]:
        print("\n  Aviso: no hay BIOMETRIA_CLAVE en este entorno, así que el")
        print("  respaldo no registra con qué clave se cifraron las fotos.")
    else:
        print(f"\n  Huella de BIOMETRIA_CLAVE: {manifiesto['huella_biometrica']}")
        print("  Guardá la clave en otro lugar que este archivo. Juntas, un")
        print("  respaldo robado entrega los rostros de toda la plantilla.")
    return destino


def leer_manifiesto(archivo: Path) -> Optional[Dict[str, Any]]:
    ruta = _manifiesto_de(archivo)
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def verificar(archivo: Path) -> Dict[str, Any]:
    """Comprueba que el volcado se puede leer y con qué clave se hizo."""
    if not archivo.exists():
        raise RespaldoInvalido(f"No existe {archivo}")

    orden = [_binario("pg_restore"), "--list", str(archivo)]
    resultado = subprocess.run(orden, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RespaldoInvalido(
            f"El archivo no es un volcado legible: "
            f"{(resultado.stderr or '').strip()[:200]}"
        )
    objetos = [l for l in resultado.stdout.splitlines()
               if l.strip() and not l.startswith(";")]
    print(f"{archivo.name}: volcado legible, {len(objetos)} objetos")

    manifiesto = leer_manifiesto(archivo)
    if manifiesto is None:
        print("  Sin manifiesto al lado: no puedo decir de cuándo es ni con")
        print("  qué clave biométrica se hizo.")
        return {"objetos": len(objetos)}

    print(f"  creado {manifiesto['creado']} desde {manifiesto['base']}")
    for tabla, total in manifiesto.get("recuentos", {}).items():
        print(f"    {total:>8}  {tabla}")

    esperada = manifiesto.get("huella_biometrica") or ""
    actual = huella_de_clave()
    if not esperada:
        print("  El respaldo no registra huella biométrica.")
    elif not actual:
        print("  Este entorno no tiene BIOMETRIA_CLAVE: las fotos del respaldo")
        print("  quedarían ilegibles.")
    elif esperada == actual:
        print("  BIOMETRIA_CLAVE coincide: las fotos se van a poder leer.")
    else:
        print("  ATENCIÓN: la BIOMETRIA_CLAVE de este entorno NO es la del")
        print("  respaldo. Las fotos restauradas no se van a poder descifrar y")
        print("  habrá que volver a tomarlas.")
    return {"objetos": len(objetos), "manifiesto": manifiesto}


def restaurar(
    archivo: Path,
    forzar: bool = False,
    aceptar_clave_distinta: bool = False,
) -> None:
    """Restaura sobre la base configurada, avisando antes de lo irreversible.

    Args:
        forzar: No pide confirmación por teclado. Es comodidad para guiones.
        aceptar_clave_distinta: Sigue adelante aunque la clave biométrica no
            sea la del respaldo, asumiendo que las fotos quedarán ilegibles.

    Las dos banderas están separadas a propósito. Juntas, quien automatiza una
    restauración para no quedarse esperando un ``input`` desactivaría sin
    querer la única comprobación que evita restaurar fotos que nadie va a
    poder descifrar.
    """
    load_dotenv()
    config = load_config()
    estado = verificar(archivo)

    manifiesto = estado.get("manifiesto") or {}
    esperada = manifiesto.get("huella_biometrica") or ""
    actual = huella_de_clave()
    if esperada and actual and esperada != actual and not aceptar_clave_distinta:
        raise RespaldoInvalido(
            "La clave biométrica no coincide con la del respaldo. Restaurar "
            "así deja las fotos ilegibles. Corregí BIOMETRIA_CLAVE, o repetí "
            "con --clave-distinta si ya sabés que vas a volver a tomarlas."
        )

    # La base tiene que existir de antes. Comprobarlo acá evita el caso feo:
    # pg_restore falla por no encontrarla, el recuento posterior la crea al
    # conectarse, y el informe termina describiendo un esquema recién sembrado
    # como si fuera una restauración a medias.
    try:
        sonda = Database()
        sonda.connect()
        sonda.cerrar()
    except Exception as error:
        raise RespaldoInvalido(
            f"No puedo conectarme a «{config['dbname']}» en {config['host']}.\n"
            f"La base de destino tiene que existir antes de restaurar; creala "
            f"con «python src/migrate.py» apuntando a ella.\n"
            f"Detalle: {_detalle(error)}"
        )

    print(f"\nRestaurando sobre {config['dbname']} en {config['host']}.")
    print("Esto reemplaza el contenido actual de esa base.")
    if not forzar:
        respuesta = input("Escribí el nombre de la base para confirmar: ").strip()
        if respuesta != config["dbname"]:
            print("Cancelado.")
            return

    entorno = dict(os.environ, PGPASSWORD=config["password"])
    orden = [
        _binario("pg_restore"),
        "--host", config["host"], "--port", str(config["port"]),
        "--username", config["user"], "--dbname", config["dbname"],
        "--clean", "--if-exists", "--no-owner", "--no-privileges",
        str(archivo),
    ]
    resultado = subprocess.run(orden, env=entorno, capture_output=True, text=True)
    # pg_restore devuelve != 0 por avisos que no son fallas (objetos que no
    # existían y el --clean intentó borrar), así que se informa y se comprueba
    # el resultado contra los recuentos en lugar de confiar en el código.
    if resultado.returncode != 0:
        print(f"  pg_restore terminó con avisos:\n"
              f"{(resultado.stderr or '').strip()[:400]}")

    # `connect`, no `initialize`: esta última crea la base y siembra el turno
    # predeterminado, así que contar con ella inventaría las filas que viene a
    # comprobar.
    db = Database()
    db.connect()
    obtenidos = _recuentos(db)
    db.cerrar()

    print("\nRecuentos tras restaurar:")
    diferencias = 0
    for tabla, esperado in (manifiesto.get("recuentos") or {}).items():
        real = obtenidos.get(tabla, -1)
        marca = "OK " if real == esperado else "!= "
        if real != esperado:
            diferencias += 1
        print(f"  {marca} {tabla:<22} esperado {esperado:>7} · hay {real:>7}")

    if diferencias:
        print(f"\n{diferencias} tabla(s) no coinciden con el manifiesto.")
    else:
        print("\nRestauración completa y verificada.")


def _uso() -> None:
    print(__doc__.strip())


def main(argumentos: List[str]) -> int:
    if not argumentos:
        _uso()
        return 1

    accion = argumentos[0]
    resto = [a for a in argumentos[1:] if not a.startswith("--")]
    forzar = "--forzar" in argumentos

    try:
        if accion == "crear":
            carpeta = Path(resto[0]) if resto else Path(CARPETA_PREDETERMINADA)
            crear(carpeta)
        elif accion == "verificar":
            if not resto:
                raise RespaldoInvalido("Falta el archivo a verificar.")
            verificar(Path(resto[0]))
        elif accion == "restaurar":
            if not resto:
                raise RespaldoInvalido("Falta el archivo a restaurar.")
            restaurar(Path(resto[0]), forzar,
                      aceptar_clave_distinta="--clave-distinta" in argumentos)
        else:
            _uso()
            return 1
    except RespaldoInvalido as error:
        print(f"\n{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
