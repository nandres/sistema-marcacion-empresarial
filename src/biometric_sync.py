"""Sincronización con reloj biométrico ZKTeco vía TCP/IP (puerto 4370).

Simula el diálogo por sockets de red con un reloj biométrico estándar de la
industria (ZKTeco): establece la conexión, descarga los identificadores de
huellas dactilares y reconocimiento facial y los asocia con la tabla
``users`` de PostgreSQL usando la cédula (username) como llave natural.

El protocolo emula el paquete de cabecera de 16 bytes del SDK de ZKTeco
(comando, checksum, sesión y réplica); ante la ausencia de hardware físico,
el modo ``--simular`` genera una descarga de prueba sin tocar la red.

Ejecución:
    python src/biometric_sync.py --host 192.168.1.201 --puerto 4370
    python src/biometric_sync.py --simular
"""

from __future__ import annotations

import argparse
import socket
import struct
from typing import Dict, List

from database import Database

PUERTO_DEFAULT: int = 4370
HOST_DEFAULT: str = "192.168.1.201"
TIMEOUT_CONEXION: float = 5.0
TAMANO_CABECERA: int = 16
TAMANO_REGISTRO: int = 72

CMD_CONNECT: int = 0x0001
CMD_READ_ALL_USER_ID: int = 0x0007
CMD_ACK_OK: int = 0x0050
CMD_ACK_ERROR: int = 0x0051

IDS_SIMULADOS: List[int] = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008]


class ErrorConexionBiometrica(Exception):
    """Error de red al comunicarse con el reloj biométrico."""


def _checksum(payload: bytes) -> int:
    """Calcula el checksum de 16 bits del paquete (suma de bytes mod 65536)."""
    return sum(payload) % 65536


def _paquete(comando: int, sesion: int, datos: bytes = b"") -> bytes:
    """Construye el paquete ZKTeco: cabecera de 16 bytes + datos."""
    cabecera = struct.pack(
        "<HHHH",
        comando,
        _checksum(datos),
        sesion,
        0x0000,
    )
    cabecera += bytes(8)
    return cabecera + datos


class RelojBiometricoZKTeco:
    """Cliente TCP/IP que dialoga con un reloj biométrico ZKTeco."""

    def __init__(
        self, host: str = HOST_DEFAULT, puerto: int = PUERTO_DEFAULT, timeout: float = TIMEOUT_CONEXION
    ) -> None:
        self.host = host
        self.puerto = puerto
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self.sesion: int = 0

    def conectar(self) -> bool:
        """Abre el socket TCP/IP hacia el reloj y negocia la sesión.

        Returns:
            ``True`` si el reloj responde con ACK al comando de conexión.
        """
        try:
            self.socket = socket.create_connection(
                (self.host, self.puerto), timeout=self.timeout
            )
        except OSError as error:
            raise ErrorConexionBiometrica(
                f"No se pudo conectar al reloj {self.host}:{self.puerto}. "
                f"Verifique la red o use --simular. ({error})"
            ) from error
        respuesta = self._transaccion(CMD_CONNECT)
        self.sesion = 0x0001
        return respuesta == CMD_ACK_OK

    def _transaccion(self, comando: int, datos: bytes = b"") -> int:
        """Envía un comando y devuelve el código de réplica recibido."""
        if self.socket is None:
            raise ErrorConexionBiometrica("El reloj no está conectado.")
        self.socket.sendall(_paquete(comando, self.sesion, datos))
        respuesta = self._leer_respuesta()
        return struct.unpack("<H", respuesta[:2])[0]

    def _leer_respuesta(self) -> bytes:
        """Lee la respuesta completa del reloj (cabecera + datos)."""
        if self.socket is None:
            raise ErrorConexionBiometrica("El reloj no está conectado.")
        cabecera = self._recibir_exacto(TAMANO_CABECERA)
        comando, _, _, _ = struct.unpack("<HHHH", cabecera[:8])
        if comando == CMD_ACK_ERROR:
            raise ErrorConexionBiometrica("El reloj respondió con error.")
        tamano_datos = self._tamano_datos(cabecera)
        return cabecera + self._recibir_exacto(tamano_datos)

    @staticmethod
    def _tamano_datos(cabecera: bytes) -> int:
        """Deriva el tamaño de datos esperado según el comando de réplica."""
        comando = struct.unpack("<H", cabecera[:2])[0]
        if comando == CMD_READ_ALL_USER_ID:
            return TAMANO_REGISTRO * 100
        return 0

    def _recibir_exacto(self, cantidad: int) -> bytes:
        """Lee exactamente ``cantidad`` bytes del socket."""
        if self.socket is None:
            raise ErrorConexionBiometrica("El reloj no está conectado.")
        fragmentos: List[bytes] = []
        restante = cantidad
        while restante > 0:
            fragmento = self.socket.recv(restante)
            if not fragmento:
                raise ErrorConexionBiometrica("El reloj cerró la conexión.")
            fragmentos.append(fragmento)
            restante -= len(fragmento)
        return b"".join(fragmentos)

    def descargar_ids_biometricos(self) -> List[int]:
        """Descarga los IDs de huellas/faciales registrados en el reloj.

        Interpreta el bloque de registros de usuario (72 bytes por plantilla)
        y extrae el identificador numérico de los primeros 4 bytes LE.

        Returns:
            Lista ordenada de IDs biométricos disponibles en el dispositivo.
        """
        datos = self._recibir_exacto(TAMANO_REGISTRO * 100)
        ids: List[int] = []
        for inicio in range(0, len(datos), TAMANO_REGISTRO):
            registro = datos[inicio : inicio + TAMANO_REGISTRO]
            identificador = struct.unpack("<I", registro[:4])[0]
            if identificador > 0:
                ids.append(identificador)
        return sorted(set(ids))

    def cerrar(self) -> None:
        """Cierra la conexión con el reloj si sigue abierta."""
        if self.socket is not None:
            self.socket.close()
            self.socket = None


def sincronizar_empleados(db: Database, ids_biometricos: List[int]) -> Dict[str, int]:
    """Empareja los IDs del reloj con ``users`` usando la cédula (username).

    Cada ID biométrico debe coincidir con el ``username`` de un empleado;
    en caso afirmativo se persiste en ``users.biometrico_id``.

    Args:
        db: Capa de persistencia conectada.
        ids_biometricos: Identificadores descargados del dispositivo.

    Returns:
        Resumen con ``descargados``, ``emparejados`` y ``sin_emparejar``.
    """
    emparejados = 0
    for identificador in ids_biometricos:
        usuario = db.get_user_by_username(str(identificador))
        if usuario:
            db.asignar_biometrico_id(usuario["id"], identificador)
            emparejados += 1
    return {
        "descargados": len(ids_biometricos),
        "emparejados": emparejados,
        "sin_emparejar": len(ids_biometricos) - emparejados,
    }


def main() -> None:
    """Punto de entrada CLI de la sincronización biométrica."""
    parser = argparse.ArgumentParser(
        description="Sincroniza IDs biométricos (ZKTeco) con PostgreSQL."
    )
    parser.add_argument("--host", default=HOST_DEFAULT, help="IP del reloj biométrico.")
    parser.add_argument("--puerto", type=int, default=PUERTO_DEFAULT, help="Puerto TCP/IP.")
    parser.add_argument(
        "--simular",
        action="store_true",
        help="Genera una descarga simulada sin conectarse a la red.",
    )
    argumentos = parser.parse_args()

    db = Database()
    db.initialize()

    if argumentos.simular:
        ids = IDS_SIMULADOS
        print(f"Modo simulación: {len(ids)} IDs generados sin red.")
    else:
        reloj = RelojBiometricoZKTeco(argumentos.host, argumentos.puerto)
        try:
            reloj.conectar()
            ids = reloj.descargar_ids_biometricos()
            print(f"Reloj {argumentos.host}:{argumentos.puerto} respondió correctamente.")
        except ErrorConexionBiometrica as error:
            print(f"ERROR: {error}")
            return
        finally:
            reloj.cerrar()

    resumen = sincronizar_empleados(db, ids)
    print(
        f"Sincronización completada: {resumen['descargados']} descargados, "
        f"{resumen['emparejados']} emparejados, "
        f"{resumen['sin_emparejar']} sin emparejar."
    )


if __name__ == "__main__":
    main()