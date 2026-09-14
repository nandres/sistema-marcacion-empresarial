"""Verificador estático: ninguna consulta de datos de cliente sin acotar.

Aislar empresas no se sostiene con disciplina. Son más de setenta consultas
y basta una sin `empresa_id` para que un cliente vea la planilla de otro, así
que la regla se comprueba leyendo el código en lugar de confiar en que nadie
se olvide al agregar el método setenta y cuatro.

Cada método de ``Database`` que toca una tabla de empresa tiene que acotar de
alguna de estas formas:

- nombrar ``empresa_id`` en la consulta, o
- estar declarado como excepción con su motivo.

Filtrar por la clave del padre (``user_id``, ``turno_id``) **no** alcanza: el
identificador llega por URL en buena parte de la API, así que Recursos
Humanos de un cliente podría editar el legajo de otro probando números.

Se usa tanto desde ``tests/test_multiempresa.py`` como a mano, corriéndolo
directamente, cuando se acaba de agregar una consulta.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Dict, List, Set

RAIZ = pathlib.Path(__file__).resolve().parents[1]
FUENTE = RAIZ / "src" / "database.py"

TABLAS_DE_EMPRESA: Set[str] = {
    "users", "marcajes", "justificaciones", "alertas", "fotos",
    "solicitudes_correccion", "condiciones_dia", "solicitudes_permiso",
    "turnos", "turno_tramos", "asignaciones_turno", "logs_auditoria",
    "ciclos_rotacion", "ciclo_turnos",
}

EXCEPCIONES: Dict[str, str] = {
    "initialize": "Conecta y delega; no consulta datos de cliente.",
    "migrar": "DDL: crea el esquema antes de que exista ninguna empresa.",
    "_aplicar_politicas_rls": "DDL de las políticas por fila.",
    "crear_rol_de_aplicacion": "Concede permisos; no lee datos de cliente.",
    "_aplicar_arrendamiento": "Es la migración que instala el arrendamiento.",
    "_sembrar_turno_predeterminado": "Recibe la empresa como parámetro explícito.",
    "buscar_credenciales": "Búsqueda de login: es la única que cruza empresas a propósito.",
    "esquema_listo": "Consulta el catálogo de PostgreSQL, no datos de cliente.",
}


def _cuerpo(fuente: str, nodo: ast.FunctionDef) -> str:
    return "\n".join(fuente.splitlines()[nodo.lineno - 1: nodo.end_lineno])


def revisar() -> List[str]:
    """Devuelve la lista de métodos sin acotar; vacía si todo está en regla."""
    fuente = FUENTE.read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    clase = next(
        n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == "Database"
    )
    faltantes: List[str] = []
    for metodo in [n for n in clase.body if isinstance(n, ast.FunctionDef)]:
        if metodo.name in EXCEPCIONES:
            continue
        cuerpo = _cuerpo(fuente, metodo)
        tocadas = {
            m.group(1)
            for m in re.finditer(r"(?:FROM|INTO|UPDATE|JOIN)\s+([a-z_]+)", cuerpo)
            if m.group(1) in TABLAS_DE_EMPRESA
        }
        if not tocadas:
            continue
        if "empresa_id" in cuerpo or "self.empresa" in cuerpo:
            continue
        faltantes.append(f"{metodo.name} (línea {metodo.lineno}) → {', '.join(sorted(tocadas))}")
    return faltantes


def tablas_sin_columna(db) -> List[str]:
    """Tablas de datos de cliente que no llegaron a tener ``empresa_id``."""
    presentes = {
        fila["table_name"]
        for fila in db._execute(
            """
            SELECT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'empresa_id'
            """,
            fetch="all",
        )
    }
    return sorted(TABLAS_DE_EMPRESA - presentes)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pendientes = revisar()
    if pendientes:
        print(f"{len(pendientes)} consulta(s) de datos de cliente sin acotar:")
        for linea in pendientes:
            print("  -", linea)
        sys.exit(1)
    print("GUARDIA OK · toda consulta de datos de cliente está acotada por empresa")
