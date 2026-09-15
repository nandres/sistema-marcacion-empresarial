"""Punto de entrada del kiosco empaquetado.

No es `src/gui.py` directamente por una sola razón: una aplicación de ventana
sin consola que revienta al arrancar no muestra nada. El usuario hace doble
clic, no pasa nada, y no hay forma de saber por qué. Acá cualquier fallo de
arranque termina en un archivo junto al ejecutable y en un cuadro de diálogo
que dice dónde mirar.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _carpeta_del_programa() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _registrar(error: BaseException) -> Path:
    destino = _carpeta_del_programa() / "error-al-arrancar.txt"
    momento = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detalle = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    with destino.open("a", encoding="utf-8") as archivo:
        archivo.write(f"\n===== {momento} =====\n{detalle}")
    return destino


def _avisar(mensaje: str) -> None:
    """Un cuadro de diálogo, porque no hay consola donde escribir."""
    try:
        import tkinter
        from tkinter import messagebox

        raiz = tkinter.Tk()
        raiz.withdraw()
        messagebox.showerror("Sistema de Marcación", mensaje)
        raiz.destroy()
    except Exception:
        print(mensaje, file=sys.stderr)


def main() -> int:
    if getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(sys._MEIPASS) / "src"))

    try:
        import gui
    except Exception as error:  # dependencia que no entró en el paquete
        registro = _registrar(error)
        _avisar(
            "No se pudo iniciar el kiosco: falta un componente del programa.\n\n"
            f"El detalle quedó en:\n{registro}"
        )
        return 1

    try:
        gui.main()
    except Exception as error:
        registro = _registrar(error)
        _avisar(
            "El kiosco se cerró por un error.\n\n"
            f"El detalle quedó en:\n{registro}\n\n"
            "Si menciona una variable de entorno, revisá el archivo .env que "
            "está junto a este programa."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
