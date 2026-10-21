import struct
from time import sleep
from threading import Thread, Lock
from constantes import HOST, PORT_TIEMPO_REAL as PORT
from time import monotonic
from buffer_ur import recibir_mensajes, conectar_con_reintento
from primaria import nuevo_estado

# Desplazamientos (bytes) dentro del paquete de tiempo real, según la hoja Client Interfaces
OFFSET_Q_ACTUAL = 252        # 6 doubles
OFFSET_SAFETY_MODE = 812     # double
OFFSET_PROGRAM_STATE = 1052  # double
SAFETY_MODE_PROTECTIVE_STOP = 3
PROGRAM_STATE_PLAYING = 2
# Modos de seguridad que indican un error; el resto (NORMAL, REDUCED...) se considera sin error
SAFETY_MODES_ERROR = {
    3: "PROTECTIVE_STOP",
    5: "SAFEGUARD_STOP",
    6: "EMERGENCY_STOP",   # SYSTEM_EMERGENCY_STOP
    7: "EMERGENCY_STOP",   # ROBOT_EMERGENCY_STOP
    8: "VIOLATION",
    9: "FAULT",
}

def contar_paquetes_por_segundo(paquetes_en_ventana: int,inicio_ventana: float,) -> tuple[int, float, float | None]:
    paquetes_en_ventana += 1
    ahora = monotonic()
    transcurrido = ahora - inicio_ventana

    if transcurrido >= 1.0:
        tasa = paquetes_en_ventana / transcurrido
        return 0, ahora, tasa

    return paquetes_en_ventana, inicio_ventana, None

def leer_robot(Robot_state: dict, HOST: str, PORT: int, lock: Lock) -> None:

    while True:  # Si se cae la conexión, se vuelve a esperar al servidor
        try:
            # El puerto 30003 envía a 500 Hz: 1 s sin datos = conexión muerta
            with conectar_con_reintento(HOST, PORT, timeout_recv=1.0) as cliente:
                # Se publica la conexión para enviar órdenes por ella: este hilo sigue leyendo,
                # así que el buffer de recepción nunca se llena. Tras reconectar se actualiza sola.
                with lock:
                    Robot_state["socket"] = cliente
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
                        q = struct.unpack_from(">6d", mensaje, OFFSET_Q_ACTUAL)
                        # Las versiones antiguas envían paquetes más cortos: si no llegan estos campos, se quedan en None
                        protective_stopped = program_running = error = None
                        if len(mensaje) >= OFFSET_PROGRAM_STATE + 8:
                            (safety_mode,) = struct.unpack_from(">d", mensaje, OFFSET_SAFETY_MODE)
                            (program_state,) = struct.unpack_from(">d", mensaje, OFFSET_PROGRAM_STATE)
                            protective_stopped = safety_mode == SAFETY_MODE_PROTECTIVE_STOP
                            program_running = program_state == PROGRAM_STATE_PLAYING
                            # Es un estado, no un evento: vuelve a None en cuanto el robot sale del modo de error
                            error = SAFETY_MODES_ERROR.get(int(safety_mode))
                        # El lock solo cubre la copia: el hilo principal nunca lee un estado a medias
                        with lock:
                            Robot_state["q"] = list(q)
                            Robot_state["isProtectiveStopped"] = protective_stopped
                            Robot_state["isProgramRunning"] = program_running
                            Robot_state["error"] = error
        except (OSError, ValueError, struct.error) as e:
            with lock:
                Robot_state["socket"] = None
            print(f"[{HOST}] Conexión perdida: {e!r}. Reconectando...", flush=True)
            sleep(1)

def main():
    Robot_state = nuevo_estado()  # Diccionario compartido con el hilo lector: ángulos y modo del robot
    # daemon=True: el hilo muere solo cuando termina el programa principal
    lock = Lock()  # Compartido entre el hilo lector y el principal para proteger Robot_state
    lector_hilo = Thread(target=leer_robot, args=(Robot_state, HOST, PORT, lock), daemon=True)
    lector_hilo.start()

    from UR3e_rg2.dynamic_ur3e import UR3eRG2
    robot = UR3eRG2()

    try:
        while True:
            with lock:
                q = Robot_state["q"][:]
            robot.move_robot(0, q)
            sleep(1/30)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
	main()
