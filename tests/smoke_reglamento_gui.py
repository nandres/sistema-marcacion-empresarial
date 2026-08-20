"""Smoke de JustificacionesTab (artículos por vínculo + disponibilidad + horas)
y EmployeeDashboard (historial enero-cualquier-año → hoy)."""
import sys
import traceback
from datetime import date

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gui
import database
import reglamento

ERRORES = []


def verificar(app):
    try:
        db = database.Database()
        db.initialize()
        admin = db.get_user_by_username("admin")
        juan = db.get_user_by_username("juan")
        db._execute("DELETE FROM justificaciones WHERE usuario_id = %s", (juan["id"],))
        db.connection.commit()
        print("OK limpieza previa de justificaciones de juan")
        panel = gui.PanelGestion(app, db, admin, on_cerrar=app._volver_publico)
        panel.grid(row=0, column=0, sticky="nsew")
        panel._seleccionar(1)
        just = panel.justificaciones_tab
        articulos = just._articulos_actuales()
        assert articulos, "no hay articulos para el empleado inicial"
        assert len(articulos) == len(
            reglamento.articulos_aplicables(admin.get("tipo_vinculo") or "Funcionario")
        )
        assert just.scroll_disp.winfo_children(), "panel de disponibilidad vacio"
        print("OK disponibilidad renderizada para", len(articulos), "articulos")
        just.menu_empleado.set(f"{juan['username']} ({juan['full_name']})")
        just._cambiar_empleado()
        assert "Art. 18 · Salidas por motivos personales" in just.menu_tipo.cget("values"), "funcionario debe ver Art. 18"
        just.menu_tipo.set("Art. 18 · Salidas por motivos personales")
        just._cambiar_tipo()
        assert just.ent_horas.winfo_manager() == "grid", "campo de horas no visible para Art. 18"
        just.ent_horas.grid_remove()
        just.menu_tipo.set("Art. 29 · Vacaciones anuales con goce de sueldo")
        just._cambiar_tipo()
        assert just.ent_horas.winfo_manager() != "grid", "campo de horas visible sin corresponder"
        print("OK artículos por vínculo y campo de horas condicional")
        just.menu_tipo.set("Art. 18 · Salidas por motivos personales")
        just._cambiar_tipo()
        just.ent_inicio.insert(0, date.today().isoformat())
        just.ent_fin.insert(0, "2099-01-01")
        just.ent_horas.insert(0, "5.5")
        just._crear()
        assert "no puede superar" in just.lbl_resultado.cget("text"), just.lbl_resultado.cget("text")
        print("OK fecha futura rechazada en la GUI")
        just.ent_fin.delete(0, "end")
        just.ent_fin.insert(0, date.today().isoformat())
        just.ent_horas.delete(0, "end")
        just.ent_horas.insert(0, "5.5")
        just._crear()
        assert "aprobada" in just.lbl_resultado.cget("text"), just.lbl_resultado.cget("text")
        print("OK justificacion creada con horas via GUI")
        just.ent_horas.delete(0, "end")
        just.ent_horas.insert(0, "1")
        just._crear()
        assert "Cuota agotada" in just.lbl_resultado.cget("text") or "Solo quedan" in just.lbl_resultado.cget("text")
        print("OK segunda justificacion bloqueada por cuota:", just.lbl_resultado.cget("text"))
        panel.destroy()

        dash = gui.EmployeeDashboard(app, db, juan, on_volver=lambda: None)
        dash.grid(row=0, column=0, sticky="nsew")
        assert dash.ent_hist_desde.get() == f"{date.today().year}-01-01"
        assert dash.ent_hist_hasta.get() == date.today().isoformat()
        dash._consultar_historial()
        assert dash.lbl_hist_resultado.cget("text").startswith("0 marcas") or "marcas" in dash.lbl_hist_resultado.cget("text")
        print("OK historial por defecto enero -> hoy:", dash.lbl_hist_resultado.cget("text"))
        dash.ent_hist_hasta.delete(0, "end")
        dash.ent_hist_hasta.insert(0, "2099-12-31")
        dash._consultar_historial()
        assert "no puede superar" in dash.lbl_hist_resultado.cget("text")
        print("OK historial con hasta futura rechazado en la GUI")
        dash.ent_hist_hasta.delete(0, "end")
        dash.ent_hist_hasta.insert(0, date.today().isoformat())
        dash.ent_hist_desde.delete(0, "end")
        dash.ent_hist_desde.insert(0, "2020-01-01")
        dash._consultar_historial()
        print("OK historial 2020-01-01 -> hoy:", dash.lbl_hist_resultado.cget("text"))
        db.cerrar()
    except Exception:
        ERRORES.append(traceback.format_exc())
    finally:
        app.destroy()


def main():
    app = gui.MarcacionApp()
    app.after(600, lambda: verificar(app))
    app.after(12000, app.destroy)
    app.mainloop()
    if ERRORES:
        print("FALLOS:")
        print("\n".join(ERRORES))
        sys.exit(1)
    print("SMOKE JUSTIFICACIONES + DASHBOARD COMPLETO")


if __name__ == "__main__":
    main()