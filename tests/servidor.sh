# Levanta el servidor web para un paso de CI y no sigue hasta que responde.
#
# Cada paso del workflow corre en su propia shell, pero un proceso lanzado en
# segundo plano sobrevive a la shell que lo lanzó. Con `timeout 25 ... &` el
# servidor de un paso seguía ocupando el puerto durante el siguiente: el
# segundo servidor no llegaba a ligar, el fallo quedaba enterrado en segundo
# plano, y las pruebas corrían contra el servidor del paso anterior hasta que
# a ese se le vencía el plazo a mitad de camino.
#
#     source tests/servidor.sh
#     arrancar
#     WEB_BASE=$SERVIDOR python tests/lo_que_sea.py
#     detener

PUERTO="${PUERTO_PRUEBAS:-8000}"
SERVIDOR="http://127.0.0.1:${PUERTO}"
SERVIDOR_PID=""
SERVIDOR_LOG="${RUNNER_TEMP:-/tmp}/servidor-${PUERTO}.log"
# El PID va a un archivo y no solo a una variable: la shell que lo lanzó muere
# al terminar su paso, y el paso siguiente necesita poder matar lo que quedó.
SERVIDOR_PID_ARCHIVO="${RUNNER_TEMP:-/tmp}/servidor-${PUERTO}.pid"

# Con SO_REUSEADDR, igual que uvicorn: un socket en TIME_WAIT no es un puerto
# ocupado, y esperarlo sería esperar por nada.
_puerto_libre() {
    python - "$PUERTO" <<'PY'
import socket, sys
with socket.socket() as sonda:
    sonda.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sonda.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        sys.exit(1)
PY
}

detener() {
    if [ -n "$SERVIDOR_PID" ]; then
        kill "$SERVIDOR_PID" 2>/dev/null || true
        wait "$SERVIDOR_PID" 2>/dev/null || true
        SERVIDOR_PID=""
    fi
    if [ -s "$SERVIDOR_PID_ARCHIVO" ]; then
        kill "$(cat "$SERVIDOR_PID_ARCHIVO")" 2>/dev/null || true
        rm -f "$SERVIDOR_PID_ARCHIVO"
    fi
    # Por nombre solo como último recurso: el patrón depende de con qué ruta
    # se haya invocado al intérprete, así que acierta o no según el sistema.
    pkill -f "web_server\.py" 2>/dev/null || true
    for _ in $(seq 50); do
        _puerto_libre && return 0
        sleep 0.2
    done
    echo "el puerto ${PUERTO} sigue ocupado después de 10 s" >&2
    return 1
}

arrancar() {
    detener || return 1
    python src/web_server.py >"$SERVIDOR_LOG" 2>&1 &
    SERVIDOR_PID=$!
    echo "$SERVIDOR_PID" >"$SERVIDOR_PID_ARCHIVO"
    for _ in $(seq 100); do
        curl -fsS -o /dev/null "${SERVIDOR}/" 2>/dev/null && return 0
        kill -0 "$SERVIDOR_PID" 2>/dev/null || break
        sleep 0.3
    done
    echo "el servidor no respondió en ${SERVIDOR}; su registro:" >&2
    cat "$SERVIDOR_LOG" >&2
    SERVIDOR_PID=""
    return 1
}
