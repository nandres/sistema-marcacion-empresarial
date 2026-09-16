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
import sys
from typing import Dict, List, Set

RAIZ = pathlib.Path(__file__).resolve().parents[1]
FUENTE = RAIZ / "src" / "database.py"

sys.path.insert(0, str(RAIZ / "src"))
import esquema  # noqa: E402

TABLAS_DE_EMPRESA: Set[str] = set(esquema.TABLAS_DE_EMPRESA)
"""Las tablas de cliente, tomadas del esquema y no copiadas.

Esta lista estuvo escrita a mano y envejeció: cuando el esquema sumó
`dispositivos` y `turno_versiones`, el guardia siguió revisando catorce tablas
de dieciséis y las dos nuevas quedaron sin vigilar. Un verificador con su
propia copia de la regla verifica su copia, no la regla.
"""

EXCEPCIONES: Dict[str, str] = {
    "buscar_credenciales":
        "Búsqueda de login: es la única que cruza empresas a propósito.",
}
"""Métodos que pueden nombrar una tabla de cliente sin acotarla.

Cada uno con su motivo, y **solo mientras el motivo siga siendo cierto**: una
excepción que sobrevive al código que la justificaba es un permiso que nadie
volvió a mirar. `sobrantes()` las cuenta.
"""


def _cuerpo(fuente: str, nodo: ast.FunctionDef) -> str:
    return chr(10).join(fuente.splitlines()[nodo.lineno - 1: nodo.end_lineno])


def _sin_acotar(cuerpo: str) -> Set[str]:
    """Tablas de cliente que el método toca sin nombrar la empresa."""
    tocadas = {
        m.group(1)
        for m in re.finditer(r"(?:FROM|INTO|UPDATE|JOIN)\s+([a-z_]+)", cuerpo)
        if m.group(1) in TABLAS_DE_EMPRESA
    }
    if "empresa_id" in cuerpo or "self.empresa" in cuerpo:
        return set()
    return tocadas


def _metodos() -> List[ast.FunctionDef]:
    fuente = FUENTE.read_text(encoding="utf-8")
    clase = next(
        n for n in ast.parse(fuente).body
        if isinstance(n, ast.ClassDef) and n.name == "Database"
    )
    return [n for n in clase.body if isinstance(n, ast.FunctionDef)]


def sobrantes() -> List[str]:
    """Excepciones concedidas a métodos que ya no existen.

    La lista de excepciones es la parte del guardia que se degrada sola:
    mientras nadie la revise acumula permisos para código que se fue, y cada
    permiso de más es una consulta futura que entra sin que la miren —basta
    que alguien reutilice el nombre.

    Solo se comprueba la existencia. Decidir si un método *todavía* necesita
    su excepción pediría la misma heurística de texto que la excepción viene a
    anular: `buscar_credenciales` devuelve `empresa_id` como columna y por eso
    parece acotada, cuando justamente es la que cruza empresas a propósito.
    """
    presentes = {m.name for m in _metodos()}
    return [f"{nombre}: ya no existe ({motivo})"
            for nombre, motivo in EXCEPCIONES.items()
            if nombre not in presentes]


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
        tocadas = _sin_acotar(_cuerpo(fuente, metodo))
        if tocadas:
            faltantes.append(
                f"{metodo.name} (línea {metodo.lineno}) → {', '.join(sorted(tocadas))}"
            )
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

    de_mas = sobrantes()
    if de_mas:
        print(f"{len(de_mas)} excepción(es) que ya no hacen falta:")
        for linea in de_mas:
            print("  -", linea)
        sys.exit(1)

    print(f"GUARDIA OK · {len(TABLAS_DE_EMPRESA)} tablas de cliente, "
          f"toda consulta acotada por empresa")
