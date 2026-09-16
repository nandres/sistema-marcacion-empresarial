# Corre la suite completa contra la base local, como lo hace el pipeline.
#
# Los conjuntos se descubren del directorio en lugar de listarse acá: una
# lista escrita a mano envejece en el primer archivo que alguien agregue sin
# acordarse de anotarlo, y el que falta es justamente el que no se corre.
#
#     bash tests/correr_todo.sh          # todo
#     bash tests/correr_todo.sh turnos   # solo los que contengan "turnos"
#
# `test_respaldo` va último a propósito: restaura la base y reemplaza su
# contenido, así que nada que corra después puede depender de lo que quede.

cd "$(dirname "$0")/.." || exit 1
source tests/servidor.sh

FILTRO="${1:-}"
VERDES=0
ROJOS=""

conjuntos() {
    ls tests/test_*.py tests/smoke_*.py tests/validar_*.py tests/guardia_*.py \
        2>/dev/null | grep -v "test_respaldo" | sort
    ls tests/test_respaldo.py 2>/dev/null
}

arrancar || exit 1
trap detener EXIT

for archivo in $(conjuntos); do
    nombre=$(basename "$archivo" .py)
    [ -n "$FILTRO" ] && case "$nombre" in *"$FILTRO"*) ;; *) continue;; esac

    salida=$(WEB_BASE="$SERVIDOR" CARGA_EMPLEADOS=20 python "$archivo" 2>&1)
    if [ $? -eq 0 ]; then
        VERDES=$((VERDES + 1))
        printf "  ok    %s\n" "$nombre"
    else
        ROJOS="$ROJOS $nombre"
        printf "  FALLA %s\n" "$nombre"
        printf "%s\n" "$salida" | tail -12 | sed 's/^/        /'
    fi
done

echo
if [ -n "$ROJOS" ]; then
    echo "$VERDES en verde ·$ROJOS en rojo"
    exit 1
fi
echo "$VERDES conjuntos, todos en verde"
