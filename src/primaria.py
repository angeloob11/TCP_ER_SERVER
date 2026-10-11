import socket
import struct
from time import sleep
import numpy as np
from UR3e_rg2.dynamic_ur3e import UR3eRG2
from constantes import HOST, PORT

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

def robot_client(robot: UR3eRG2) -> None:
    data = 0
    num_pkg = 0
    Joint_state = np.zeros(6)

    with socket.create_connection((HOST, PORT)) as cliente:
        while True:
            bytes_recibidos = recibir_paquete(cliente, 5)
            mensaje = struct.unpack(">iB", bytes_recibidos)
            data = recibir_paquete(cliente, mensaje[0] - 5)
            print(f"Recibido mensaje tipo {mensaje[1]} con longitud {mensaje[0]}")
            if mensaje[1] == 16:
                longitud_pkg_1 = struct.unpack(">i", data[0:4])
                Robot_state = data[longitud_pkg_1[0]:longitud_pkg_1[0] + 251]
                Joint_state[0] = struct.unpack(">d", Robot_state[5:13])[0]
                Joint_state[1] = struct.unpack(">d", Robot_state[46:54])[0]
                Joint_state[2] = struct.unpack(">d", Robot_state[87:95])[0]
                Joint_state[3] = struct.unpack(">d", Robot_state[128:136])[0]
                Joint_state[4] = struct.unpack(">d", Robot_state[169:177])[0]
                Joint_state[5] = struct.unpack(">d", Robot_state[210:218])[0]
                num_pkg += 1
                print("Número de paquetes recibidos:", num_pkg)
                print("Estados de las articulaciones:", Joint_state*180/np.pi)
                robot.move_robot(0, Joint_state.tolist())

def main():
    robot =UR3eRG2()
    robot_client(robot)


if __name__ == "__main__":
	main()