"""Smoke de login unificado, cambio de contraseña y tema con dashboard abierto."""
import sys
from datetime import date

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth
import gui
from database import Database

gui.ctk.set_appearance_mode("light")
gui.TEMA_ACTIVO = "claro"

db = Database()
db.initialize()
app = gui.MarcacionApp()
app.update()
db = app.db
assert hasattr(app, "entrada_usuario"), "debe mostrarse la tarjeta de login"
print("OK login card inicial")

app.entrada_usuario.insert(0, "nadie")
app.entrada_clave.insert(0, "x")
app._ingresar()
app.update()
assert app.lbl_login.cget("text") == "El usuario no existe. Verifique el nombre.", app.lbl_login.cget("text")
print("OK usuario inexistente avisado")

app.entrada_usuario.delete(0, "end")
app.entrada_clave.delete(0, "end")
app.entrada_usuario.insert(0, "juan")
app.entrada_clave.insert(0, "incorrecta")
app._ingresar()
app.update()
assert app.lbl_login.cget("text") == "Contraseña incorrecta.", app.lbl_login.cget("text")
print("OK contrasena incorrecta avisada")

app.entrada_clave.delete(0, "end")
app.entrada_clave.insert(0, "clave123")
app._ingresar()
app.update()
assert isinstance(app.dashboard_empleado, gui.EmployeeDashboard), "debe abrir el tablero del empleado"
print("OK juan entra al EmployeeDashboard")

def buscar_canvas(raiz):
    resultado = []
    def caminar(nodo):
        for hijo in nodo.winfo_children():
            if type(hijo).__name__ == "Canvas" and type(hijo.master).__name__ == "CTkFrame":
                if type(hijo.master.master).__name__ != "CTkScrollableFrame":
                    resultado.append(hijo)
            caminar(hijo)
    caminar(raiz)
    return resultado[-1] if resultado else None

canvas_antes = buscar_canvas(app.dashboard_empleado)
assert canvas_antes is not None, "dashboard debe tener su grafico"
canvas_antes.mi_marca = "viejo"
app.variable_tema.set(True)
app._cambiar_tema()
app.update()
assert gui.TEMA_ACTIVO == "claro"
canvas_despues = buscar_canvas(app.dashboard_empleado)
assert canvas_despues is not None and not hasattr(canvas_despues, "mi_marca"), "el grafico debe reconstruirse al cambiar tema"
print("OK tema claro + grafico del dashboard reconstruido")

canvas_despues.mi_marca = "medio"
app.variable_tema.set(False)
app._cambiar_tema()
app.update()
assert gui.TEMA_ACTIVO == "oscuro"
canvas_otra = buscar_canvas(app.dashboard_empleado)
assert canvas_otra is not None and not hasattr(canvas_otra, "mi_marca"), "debe reconstruirse al volver a oscuro"
print("OK tema oscuro + grafico del dashboard reconstruido de nuevo")

hijos = app.dashboard_empleado.area.winfo_children()
assert any(w.winfo_children() for w in hijos), "tarjetas del resumen reconstruidas"
print("OK dashboard reconstruido tras el cambio de tema")

clase_plano = gui.ctk.CTkButton(app.dashboard_empleado, text="X", fg_color="transparent")
clase_plano._rol = "plano"
clase_plano.configure(hover_color=gui.TEMAS["oscuro"]["INPUT_BG"])
clase_plano_seleccionado = gui.ctk.CTkButton(app.dashboard_empleado, text="X", fg_color=gui.TEMAS["oscuro"]["PRIMARY"], text_color="#FFFFFF")
clase_plano_seleccionado._rol = "plano"
app.variable_tema.set(True)
app._cambiar_tema()
assert clase_plano.cget("hover_color") == gui.TEMAS["claro"]["INPUT_BG"], "hover plano debe seguir al tema nuevo"
assert clase_plano.cget("text_color") == gui.TEMAS["claro"]["MUTED"]
assert clase_plano_seleccionado.cget("fg_color") == gui.TEMAS["claro"]["PRIMARY"], "plano seleccionado conserva PRIMARY"
print("OK rama plano de _recolorear")

app.dashboard_empleado.on_volver()
app.update()
assert hasattr(app, "entrada_usuario") and app.lbl_login.cget("text") == "", "volver al login"
print("OK volver al login")

app._mostrar_cambio_clave()
app.update()
app.entrada_usuario.insert(0, "juan")
app.entrada_nueva.insert(0, "claveNueva1")
app.entrada_repetir.insert(0, "otraCosa")
app._ejecutar_cambio_clave()
assert app.lbl_cambio.cget("text") == "Las contraseñas nuevas no coinciden."
print("OK claves nuevas no coinciden avisadas")

app.entrada_repetir.delete(0, "end")
app.entrada_repetir.insert(0, "claveNueva1")
app._ejecutar_cambio_clave()
assert app.lbl_cambio.cget("text") == "La contraseña actual no es correcta."
print("OK clave actual incorrecta avisada")

app.entrada_actual.insert(0, "clave123")
app.entrada_nueva.delete(0, "end")
app.entrada_repetir.delete(0, "end")
app.entrada_nueva.insert(0, "corta")
app.entrada_repetir.insert(0, "corta")
app._ejecutar_cambio_clave()
assert app.lbl_cambio.cget("text") == "La contraseña nueva debe tener al menos 6 caracteres."
print("OK clave nueva corta avisada")

app.entrada_nueva.delete(0, "end")
app.entrada_repetir.delete(0, "end")
app.entrada_nueva.insert(0, "clave456")
app.entrada_repetir.insert(0, "clave456")
app._ejecutar_cambio_clave()
assert app.lbl_cambio.cget("text").startswith("Contraseña actualizada"), app.lbl_cambio.cget("text")
juan = db.get_user_by_username("juan")
assert auth.authenticate(db, "juan", "clave456"), "la nueva clave debe funcionar"
assert not auth.authenticate(db, "juan", "clave123"), "la anterior clave no debe funcionar"
print("OK cambio de contrasena con clave456")

auth.cambiar_clave(db, juan, "clave456", "clave123")
assert auth.authenticate(db, "juan", "clave123"), "clave original restaurada"
print("OK clave original restaurada a clave123")

app._mostrar_portal()
app.update()
app.entrada_usuario.insert(0, "admin")
app.entrada_clave.insert(0, "admin123")
app._ingresar()
app.update()
assert hasattr(app, "panel_gestion") and app.panel_gestion is not None, "admin debe entrar a PanelGestion"
assert not app.frame_publico.winfo_ismapped(), "frame publico oculto durante gestion"
print("OK admin entra a PanelGestion")

app._volver_publico()
app.update()
assert app.frame_publico.winfo_ismapped() and hasattr(app, "entrada_usuario"), "volver al login tras gestion"
print("OK volver al login tras gestion")

eventos = db.listar_auditoria(200)
cambios = [e for e in eventos if e.get("tabla") == "users" and e.get("accion") == "ACTUALIZAR" and "(cambiado)" in str(e.get("valores_nuevos", {}))]
assert cambios, "cambio de clave auditado"
print("OK cambio de clave auditado en logs_auditoria")

app.destroy()
print("SMOKE LOGIN + CAMBIO DE CLAVE + TEMA COMPLETO")