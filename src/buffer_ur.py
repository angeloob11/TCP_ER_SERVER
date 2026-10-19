import socket
import struct
from time import sleep

LONGITUD_MAXIMA = 65536  # Un mensaje más grande que esto significa que estamos desalineados


def conectar_con_reintento(host: str, port: int, intervalo: float = 1.0, timeout: float = 2.0,
                           timeout_recv: float = 2.0) -> socket.socket:
    # OSError cubre todos los casos: puerto cerrado (ConnectionRefusedError),
    # IP que no responde (TimeoutError), red inalcanzable, nombre inválido (gaierror)
    intento = 1
    while True:
        try:
            cliente = socket.create_connection((host, port), timeout=timeout)
            # Si el servidor muere sin cerrar la conexión (sin FIN/RST), recv() se quedaría
            # bloqueado para siempre. Con timeout, el silencio lanza TimeoutError (un OSError).
            cliente.settimeout(timeout_recv)
            print(f"Conectado a {host}:{port}", flush=True)
            return cliente
        except OSError as e:
            print(f"Servidor {host}:{port} no disponible (intento {intento}): {e}. Reintentando en {intervalo} s...", flush=True)
            intento += 1
            sleep(intervalo)


def extraer_mensajes(buffer: bytearray, tam_cabecera: int) -> list[bytes]:
   
    mensajes = []
    while len(buffer) >= tam_cabecera:                    
        (longitud,) = struct.unpack_from(">i", buffer, 0) 
        if longitud < tam_cabecera or longitud > LONGITUD_MAXIMA:
            raise ValueError(f"Longitud imposible: {longitud} (flujo desalineado)")
        if len(buffer) < longitud:                       
            break
        mensajes.append(bytes(buffer[:longitud]))        
        del buffer[:longitud]                            
    return mensajes


def recibir_mensajes(cliente: socket.socket, buffer: bytearray,tam_cabecera: int, depurar: bool = False) -> list[bytes]:
    
    datos = cliente.recv(4096)
    if not datos:                                      
        raise ConnectionError("El servidor cerró la conexión")
    if depurar:
        print(f"recv() devolvió {len(datos)} bytes") 
    buffer.extend(datos)
    return extraer_mensajes(buffer, tam_cabecera)
