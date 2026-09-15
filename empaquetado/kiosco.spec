# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado del kiosco de escritorio en un ejecutable de Windows.

Se construye con:
    pyinstaller empaquetado/kiosco.spec --noconfirm

El resultado queda en `dist/Sistema de Marcacion/`. Es una carpeta y no un
archivo único a propósito: OpenCV y matplotlib pesan, y un ejecutable de un
solo archivo los descomprime en un temporal en cada arranque, lo que en una
PC de mostrador se nota. La carpeta arranca al instante.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

RAIZ = Path(SPECPATH).parent

datos = [
    # Los modelos de detección facial se leen en tiempo de ejecución.
    (str(RAIZ / "data"), "data"),
]
# customtkinter reparte temas y assets en .json que no son importables: si no
# viajan, la ventana abre sin estilos o directamente falla al construirse.
datos += collect_data_files("customtkinter")

ocultos = [
    # psycopg2 carga su extensión binaria por nombre.
    "psycopg2._psycopg",
    # matplotlib elige el backend en tiempo de ejecución.
    "matplotlib.backends.backend_tkagg",
]
ocultos += collect_submodules("customtkinter")

analisis = Analysis(
    [str(RAIZ / "empaquetado" / "kiosco.py")],
    pathex=[str(RAIZ / "src")],
    binaries=[],
    datas=datos,
    hiddenimports=ocultos,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # El servidor web y sus dependencias no forman parte del kiosco de
    # escritorio; incluirlas sumaría decenas de megabytes sin uso.
    excludes=["fastapi", "uvicorn", "gunicorn", "httpx", "starlette",
              "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analisis.pure)

exe = EXE(
    pyz,
    analisis.scripts,
    [],
    exclude_binaries=True,
    name="Sistema de Marcacion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Sin consola: es un kiosco. Los fallos de arranque van a
    # `error-al-arrancar.txt`, que para eso existe `kiosco.py`.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coleccion = COLLECT(
    exe,
    analisis.binaries,
    analisis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Sistema de Marcacion",
)
