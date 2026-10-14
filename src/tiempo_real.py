import socket
import struct
from time import sleep
import numpy as np
from multiprocessing import Process, Array
from constantes import HOST, PORT_TIEMPO_REAL as PORT
from time import monotonic

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
        paquetes_en_ventana = 0
        inicio_ventana = monotonic()

        while True:
            bytes_recibidos = recibir_paquete(cliente, 1220)

            paquetes_en_ventana, inicio_ventana, tasa = contar_paquetes_por_segundo(
                paquetes_en_ventana, inicio_ventana
            )
            if tasa is not None:
                print(f"Tasa de recepción: {tasa:.2f} paquetes/s")
            Joint_state[:] = struct.unpack_from(">6d", bytes_recibidos, 252)

#def robot_client(robot: UR3eRG2) -> None:
#    data = 0
#    Joint_state = np.zeros(6)
#
#    with socket.create_connection((HOST, PORT)) as cliente:
#        paquetes_en_ventana = 0
#        inicio_ventana = monotonic()
#
#        while True:
#            bytes_recibidos = recibir_paquete(cliente, 1220)
#
#            paquetes_en_ventana, inicio_ventana, tasa = contar_paquetes_por_segundo(
#            paquetes_en_ventana, inicio_ventana
#            )
#            if tasa is not None:
#                print(f"Tasa de recepción: {tasa:.2f} paquetes/s")
#
#            #print(f"Recibido mensaje con longitud {len(bytes_recibidos)}")
#            Robot_state = bytes_recibidos[252:300]
#            Joint_state[0] = struct.unpack(">d", Robot_state[0:8])[0]
#            Joint_state[1] = struct.unpack(">d", Robot_state[8:16])[0]
#            Joint_state[2] = struct.unpack(">d", Robot_state[16:24])[0]
#            Joint_state[3] = struct.unpack(">d", Robot_state[24:32])[0]
#            Joint_state[4] = struct.unpack(">d", Robot_state[32:40])[0]
#            Joint_state[5] = struct.unpack(">d", Robot_state[40:48])[0]
#            #print("Estados de las articulaciones:", Joint_state * 180 / np.pi)
#            #robot.move_robot(0, Joint_state.tolist())

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
