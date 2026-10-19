import socket
import struct

LONGITUD_MAXIMA = 65536  # Un mensaje más grande que esto significa que estamos desalineados


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
