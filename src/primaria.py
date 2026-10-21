import struct
from time import sleep
from threading import Thread, Lock
from constantes import HOST, PORT
from buffer_ur import recibir_mensajes, conectar_con_reintento


ROBOT_MESSAGE_TYPE_ERROR_CODE = 6
NIVELES_REPORTE = {0: "DEBUG", 1: "INFO", 2: "WARNING", 3: "VIOLATION", 4: "FAULT", 5: "DEVL_DEBUG"}

def nuevo_estado() -> dict:
    # "socket": conexión del lector, para enviar URScript por ella (None mientras no hay conexión)
    return {"q": [0.0] * 6, "isProgramRunning": None, "isProtectiveStopped": None, "error": None,
            "socket": None}

def leer_robot(Robot_state: dict, HOST: str, PORT: int, lock: Lock) -> None:

    print(f"{Robot_state}", flush=True)
    while True:  # Si se cae la conexión, se vuelve a esperar al servidor
        try:
            with conectar_con_reintento(HOST, PORT) as cliente:
                # Se publica la conexión para enviar órdenes por ella: este hilo sigue leyendo,
                # así que el buffer de recepción nunca se llena. Tras reconectar se actualiza sola.
                with lock:
                    Robot_state["socket"] = cliente
                buffer = bytearray()
                while True:
                    for mensaje in recibir_mensajes(cliente, buffer, 5, depurar=False):   #MODO DE DEPURACION
                        longitud, tipo = struct.unpack_from(">iB", mensaje, 0)
                        #print(f"[{HOST}] Recibido mensaje tipo {tipo} con longitud {longitud}")

                        if tipo == 16:

                            fin = min(longitud, len(mensaje))
                            offset = 5
                            while offset + 5 <= fin:
                                longitud_sub, tipo_sub = struct.unpack_from(">iB", mensaje, offset)
                                if longitud_sub < 5 or offset + longitud_sub > fin:
                                    raise ValueError(f"Subpaquete tipo {tipo_sub} con longitud inválida {longitud_sub}")

                                if tipo_sub == 0:  # Robot mode data: cabecera(5) + timestamp(8) + 7 bools
                                    if longitud_sub < 19:
                                        raise ValueError(f"Subpaquete Robot mode data demasiado corto: {longitud_sub}")
                                    # Bools tras el timestamp: isRealRobotConnected, isRealRobotEnabled,
                                    # isRobotPowerOn, isEmergencyStopped, isProtectiveStopped, isProgramRunning
                                    protective_stopped, program_running = struct.unpack_from(">??", mensaje, offset + 17)
                                    with lock:
                                        Robot_state["isProtectiveStopped"] = protective_stopped
                                        Robot_state["isProgramRunning"] = program_running

                                elif tipo_sub == 1:  # Joint data: 6 articulaciones de 41 bytes, q_actual al inicio de cada una
                                    q = [struct.unpack_from(">d", mensaje, offset + 5 + 41 * i)[0] for i in range(6)]
                                    # El lock solo cubre la copia: el hilo principal nunca lee un estado a medias
                                    with lock:
                                        Robot_state["q"] = q

                                offset += longitud_sub

                        # Robot message: cabecera(5) + timestamp(8) + source(1) + robotMessageType(1)
                        elif tipo == 20 and len(mensaje) >= 15:
                            (robot_message_type,) = struct.unpack_from(">B", mensaje, 14)
                            # Error code: code(4) + argument(4) + reportLevel(4) + dataType(1) + data(4) + texto
                            if robot_message_type == ROBOT_MESSAGE_TYPE_ERROR_CODE and len(mensaje) >= 32:
                                codigo, argumento, nivel = struct.unpack_from(">iii", mensaje, 15)
                                texto = mensaje[32:longitud].decode("utf-8", errors="replace")
                                error = f"{NIVELES_REPORTE.get(nivel, nivel)} C{codigo}A{argumento}: {texto}"
                                # Se mantiene hasta que quien lo atienda lo ponga a None
                                with lock:
                                    Robot_state["error"] = error
                                print(f"[{HOST}] Error del robot: {error}", flush=True)
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
