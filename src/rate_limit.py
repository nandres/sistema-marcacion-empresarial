"""Freno de intentos fallidos para los puntos de entrada con contraseña.

Sin esto, `/api/login` y `/api/marcar` aceptan intentos sin límite: con una
cédula válida —que en Paraguay es pública— y un diccionario de contraseñas,
entrar es cuestión de tiempo. El costo de bcrypt no alcanza como defensa
cuando el atacante puede paralelizar peticiones.

La ventana es deslizante y en memoria del proceso. Es honesto decir qué
cubre y qué no: con varios workers cada uno lleva su propia cuenta, así que
el umbral efectivo se multiplica por la cantidad de procesos. Alcanza para
frenar un ataque de diccionario y no pretende ser un WAF; la defensa
definitiva ante un atacante distribuido es un límite en el proxy de entrada.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

INTENTOS_MAXIMOS: int = 8
VENTANA_SEGUNDOS: int = 300
BLOQUEO_SEGUNDOS: int = 900


class LimiteExcedido(Exception):
    """Se agotaron los intentos permitidos para una identidad."""

    def __init__(self, restante: int) -> None:
        self.restante = restante
        super().__init__(
            f"Demasiados intentos fallidos. Volvé a probar en "
            f"{max(1, restante // 60)} minuto(s)."
        )


class Freno:
    """Cuenta intentos fallidos por clave con una ventana deslizante."""

    def __init__(
        self,
        maximos: int = INTENTOS_MAXIMOS,
        ventana: int = VENTANA_SEGUNDOS,
        bloqueo: int = BLOQUEO_SEGUNDOS,
    ) -> None:
        self.maximos = maximos
        self.ventana = ventana
        self.bloqueo = bloqueo
        self._fallos: Dict[str, Deque[float]] = defaultdict(deque)
        self._bloqueados: Dict[str, float] = {}
        self._candado = threading.Lock()

    def _purgar(self, clave: str, ahora: float) -> Deque[float]:
        intentos = self._fallos[clave]
        while intentos and ahora - intentos[0] > self.ventana:
            intentos.popleft()
        return intentos

    def verificar(self, clave: str) -> None:
        """Deja pasar el intento o lo rechaza si la clave está bloqueada.

        Raises:
            LimiteExcedido: con los segundos que faltan para reintentar.
        """
        ahora = time.monotonic()
        with self._candado:
            hasta = self._bloqueados.get(clave)
            if hasta is not None:
                if ahora < hasta:
                    raise LimiteExcedido(int(hasta - ahora))
                del self._bloqueados[clave]
                self._fallos.pop(clave, None)

    def fallo(self, clave: str) -> int:
        """Registra un intento fallido y devuelve los que quedan disponibles."""
        ahora = time.monotonic()
        with self._candado:
            intentos = self._purgar(clave, ahora)
            intentos.append(ahora)
            if len(intentos) >= self.maximos:
                self._bloqueados[clave] = ahora + self.bloqueo
                return 0
            return self.maximos - len(intentos)

    def exito(self, clave: str) -> None:
        """Limpia el historial: una autenticación válida cierra el episodio."""
        with self._candado:
            self._fallos.pop(clave, None)
            self._bloqueados.pop(clave, None)

    def estado(self, clave: str) -> Tuple[int, int]:
        """Intentos restantes y segundos de bloqueo pendientes."""
        ahora = time.monotonic()
        with self._candado:
            hasta = self._bloqueados.get(clave, 0.0)
            restantes = self.maximos - len(self._purgar(clave, ahora))
            return max(0, restantes), max(0, int(hasta - ahora))

    def reiniciar(self) -> None:
        """Vacía el estado. Para las pruebas, no para la operación."""
        with self._candado:
            self._fallos.clear()
            self._bloqueados.clear()


ACCESO = Freno()
"""Freno compartido por el login del portal y el kiosco web."""
