import socket
import struct
from time import sleep
import numpy as np
from UR3e_rg2.dynamic_ur3e import UR3eRG2
from constantes import HOST, PORT
from buffer_ur import recibir_mensajes, conectar_con_reintento


def robot_client(robot: UR3eRG2) -> None:
    data = 0
    num_pkg = 0
    Joint_state = np.zeros(6)

    while True:  # Si se cae la conexión, se vuelve a esperar al servidor
        try:
            with conectar_con_reintento(HOST, PORT) as cliente:
                buffer = bytearray()
                while True:
                    for mensaje in recibir_mensajes(cliente, buffer, 5, depurar=False):   #MODO DE DEPURACION
                        longitud, tipo = struct.unpack_from(">iB", mensaje, 0)
                        print(f"Recibido mensaje tipo {tipo} con longitud {longitud}")

                        if tipo == 16:
                            
                            fin = min(longitud, len(mensaje))
                            offset = 5
                            hay_joint_data = False
                            while offset + 5 <= fin:
                                longitud_sub, tipo_sub = struct.unpack_from(">iB", mensaje, offset)
                                if longitud_sub < 5 or offset + longitud_sub > fin:
                                    raise ValueError(f"Subpaquete tipo {tipo_sub} con longitud inválida {longitud_sub}")

                                if tipo_sub == 1:  # Joint data: 6 articulaciones de 41 bytes, q_actual al inicio de cada una
                                    for i in range(6):
                                        Joint_state[i] = struct.unpack_from(">d", mensaje, offset + 5 + 41 * i)[0]
                                    hay_joint_data = True

                                offset += longitud_sub

                            if hay_joint_data:
                                #num_pkg += 1
                                #print("Número de paquetes recibidos:", num_pkg)
                                print("Estados de las articulaciones:", Joint_state*180/np.pi)
                                robot.move_robot(0, Joint_state.tolist())
        except (OSError, ValueError, struct.error) as e:
            print(f"Conexión perdida: {e!r}. Reconectando...", flush=True)
            sleep(1)

def main():
    robot =UR3eRG2()
    robot_client(robot)


if __name__ == "__main__":
	main()