import struct
from time import sleep
from threading import Thread
from constantes import HOST, PORT_TIEMPO_REAL as PORT
from time import monotonic
from buffer_ur import recibir_mensajes, conectar_con_reintento

def contar_paquetes_por_segundo(paquetes_en_ventana: int,inicio_ventana: float,) -> tuple[int, float, float | None]:
    paquetes_en_ventana += 1
    ahora = monotonic()
    transcurrido = ahora - inicio_ventana

    if transcurrido >= 1.0:
        tasa = paquetes_en_ventana / transcurrido
        return 0, ahora, tasa

    return paquetes_en_ventana, inicio_ventana, None

def leer_robot(Joint_state: list[float], HOST: str, PORT: int) -> None:

    while True:  # Si se cae la conexión, se vuelve a esperar al servidor
        try:
            # El puerto 30003 envía a 500 Hz: 1 s sin datos = conexión muerta
            with conectar_con_reintento(HOST, PORT, timeout_recv=1.0) as cliente:
                buffer = bytearray()
                paquetes_en_ventana = 0
                inicio_ventana = monotonic()

                while True:
                    for mensaje in recibir_mensajes(cliente, buffer, 4):
                        paquetes_en_ventana, inicio_ventana, tasa = contar_paquetes_por_segundo(
                            paquetes_en_ventana, inicio_ventana
                        )
                        if tasa is not None:
                            print(f"[{HOST}] Tasa de recepción: {tasa:.2f} paquetes/s")
                        # La asignación por slice es atómica con el GIL: el hilo principal nunca lee un estado a medias
                        Joint_state[:] = struct.unpack_from(">6d", mensaje, 252)
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
