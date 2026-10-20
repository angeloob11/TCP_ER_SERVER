import struct
from time import sleep
from threading import Thread
from constantes import HOST, PORT
from buffer_ur import recibir_mensajes, conectar_con_reintento


def leer_robot(Joint_state: list[float], HOST: str, PORT: int) -> None:

    while True:  # Si se cae la conexión, se vuelve a esperar al servidor
        try:
            with conectar_con_reintento(HOST, PORT) as cliente:
                buffer = bytearray()
                while True:
                    for mensaje in recibir_mensajes(cliente, buffer, 5, depurar=False):   #MODO DE DEPURACION
                        longitud, tipo = struct.unpack_from(">iB", mensaje, 0)
                        print(f"[{HOST}] Recibido mensaje tipo {tipo} con longitud {longitud}")

                        if tipo == 16:

                            fin = min(longitud, len(mensaje))
                            offset = 5
                            while offset + 5 <= fin:
                                longitud_sub, tipo_sub = struct.unpack_from(">iB", mensaje, offset)
                                if longitud_sub < 5 or offset + longitud_sub > fin:
                                    raise ValueError(f"Subpaquete tipo {tipo_sub} con longitud inválida {longitud_sub}")

                                if tipo_sub == 1:  # Joint data: 6 articulaciones de 41 bytes, q_actual al inicio de cada una
                                    # La asignación por slice es atómica con el GIL: el hilo principal nunca lee un estado a medias
                                    Joint_state[:] = [struct.unpack_from(">d", mensaje, offset + 5 + 41 * i)[0]
                                                      for i in range(6)]

                                offset += longitud_sub
        except (OSError, ValueError, struct.error) as e:
            print(f"[{HOST}] Conexión perdida: {e!r}. Reconectando...", flush=True)
            sleep(1)

def main():
    Joint_state = [0.0] * 6  # Lista compartida con el hilo lector para almacenar los estados de las articulaciones
    # daemon=True: el hilo muere solo cuando termina el programa principal
    lector_hilo = Thread(target=leer_robot, args=(Joint_state, HOST, PORT), daemon=True)
    lector_hilo.start()

    from UR3e_rg2.dynamic_ur3e import UR3eRG2
    robot = UR3eRG2()

    try:
        while True:
            robot.move_robot(0, Joint_state[:])
            sleep(1/30)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
	main()
