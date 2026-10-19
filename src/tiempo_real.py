import socket
import struct
from time import sleep
import numpy as np
from multiprocessing import Process, Array
from constantes import HOST, PORT_TIEMPO_REAL as PORT
from time import monotonic
from buffer_ur import recibir_mensajes

def contar_paquetes_por_segundo(paquetes_en_ventana: int,inicio_ventana: float,) -> tuple[int, float, float | None]:
    paquetes_en_ventana += 1
    ahora = monotonic()
    transcurrido = ahora - inicio_ventana

    if transcurrido >= 1.0:
        tasa = paquetes_en_ventana / transcurrido
        return 0, ahora, tasa

    return paquetes_en_ventana, inicio_ventana, None

def recibir_paquete(cliente: socket.socket, cantidad_bytes: int) -> bytes:
    partes = []
    recibidos = 0

    while recibidos < cantidad_bytes:
        parte = cliente.recv(cantidad_bytes - recibidos)
        if not parte:
            raise ConnectionError("El servidor cerró la conexión")
        partes.append(parte)
        recibidos += len(parte)

    return b"".join(partes)

def leer_robot(Joint_state) -> None:

    with socket.create_connection((HOST, PORT)) as cliente:
        buffer = bytearray()
        paquetes_en_ventana = 0
        inicio_ventana = monotonic()

        while True:
            for mensaje in recibir_mensajes(cliente, buffer, 4):
                paquetes_en_ventana, inicio_ventana, tasa = contar_paquetes_por_segundo(
                    paquetes_en_ventana, inicio_ventana
                )
                if tasa is not None:
                    print(f"Tasa de recepción: {tasa:.2f} paquetes/s")
                Joint_state[:] = struct.unpack_from(">6d", mensaje, 252)


def main():
    Joint_state = Array('d', 6)  # Array compartido para almacenar los estados de las articulaciones
    lector_proceso = Process(target=leer_robot, args=(Joint_state,), daemon=True)
    lector_proceso.start()

    from UR3e_rg2.dynamic_ur3e import UR3eRG2
    robot = UR3eRG2()

    try:
        while True:
            robot.move_robot(0, Joint_state[:])
            sleep(1/30)
    except KeyboardInterrupt:
        pass
    finally:
        lector_proceso.terminate()
    
if __name__ == "__main__":
	main()
