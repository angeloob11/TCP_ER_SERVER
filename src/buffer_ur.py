import socket
import struct
from time import sleep

LONGITUD_MAXIMA = 65536  # Un mensaje más grande que esto significa que estamos desalineados

class Buffer:
    """Conexión TCP con el robot y los bytes recibidos que aún no forman un mensaje completo."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.buffer = bytearray()
        self.client: socket.socket | None = None

    def conectar_con_reintento(self, intervalo: float = 1.0, timeout: float = 2.0,
                               timeout_recv: float = 2.0) -> socket.socket:
        # OSError cubre todos los casos: puerto cerrado (ConnectionRefusedError),
        # IP que no responde (TimeoutError), red inalcanzable, nombre inválido (gaierror)
        intento = 1
        while True:
            try:
                self.client = socket.create_connection((self.host, self.port), timeout=timeout)
                # Si el servidor muere sin cerrar la conexión (sin FIN/RST), recv() se quedaría
                # bloqueado para siempre. Con timeout, el silencio lanza TimeoutError (un OSError).
                self.client.settimeout(timeout_recv)
                # Conexión nueva: los bytes a medias de la anterior ya no sirven
                self.buffer.clear()
                print(f"Conectado a {self.host}:{self.port}", flush=True)
                return self.client
            except OSError as e:
                print(f"Servidor {self.host}:{self.port} no disponible (intento {intento}): {e}. Reintentando en {intervalo} s...", flush=True)
                intento += 1
                sleep(intervalo)

    def extraer_mensajes(self, tam_cabecera: int) -> list[bytes]:
        mensajes = []
        while len(self.buffer) >= tam_cabecera:
            (longitud,) = struct.unpack_from(">i", self.buffer, 0)
            if longitud < tam_cabecera or longitud > LONGITUD_MAXIMA:
                raise ValueError(f"Longitud imposible: {longitud} (flujo desalineado)")
            if len(self.buffer) < longitud:
                break
            mensajes.append(bytes(self.buffer[:longitud]))
            del self.buffer[:longitud]
        return mensajes

    def recibir_mensajes(self, tam_cabecera: int, depurar: bool = False) -> list[bytes]:
        datos = self.client.recv(4096)
        if not datos:
            raise ConnectionError("El servidor cerró la conexión")
        if depurar:
            print(f"recv() devolvió {len(datos)} bytes")
        self.buffer.extend(datos)
        return self.extraer_mensajes(tam_cabecera)
