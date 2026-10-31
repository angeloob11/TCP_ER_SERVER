import struct
from time import sleep
from threading import Thread
from constantes import HOST, PORT_TIEMPO_REAL as PORT
from time import monotonic
from buffer_ur import Buffer
from primaria import RobotState

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

class LectorTiempoReal:
    """Lee la interfaz de tiempo real (30003) y vuelca lo recibido en un RobotState."""

    TAM_CABECERA = 4  # longitud(4)

    def __init__(self, Robot_state: RobotState, HOST: str, PORT: int):
        self.Robot_state = Robot_state
        self.buffer = Buffer(HOST, PORT)
        self.paquetes_en_ventana = 0
        self.inicio_ventana = monotonic()

    def contar_paquetes_por_segundo(self) -> float | None:
        # Devuelve la tasa al cerrar cada ventana de 1 s; None mientras la ventana sigue abierta
        self.paquetes_en_ventana += 1
        ahora = monotonic()
        transcurrido = ahora - self.inicio_ventana

        if transcurrido >= 1.0:
            tasa = self.paquetes_en_ventana / transcurrido
            self.paquetes_en_ventana, self.inicio_ventana = 0, ahora
            return tasa

        return None

    def leer_robot(self) -> None:
        while True:  # Si se cae la conexión, se vuelve a esperar al servidor
            try:
                # El puerto 30003 envía a 500 Hz: 1 s sin datos = conexión muerta
                with self.buffer.conectar_con_reintento(timeout_recv=1.0) as cliente:
                    # Se publica la conexión para enviar órdenes por ella: este hilo sigue leyendo,
                    # así que el buffer de recepción nunca se llena. Tras reconectar se actualiza sola.
                    with self.Robot_state.lock:
                        self.Robot_state.socket = cliente
                    self.paquetes_en_ventana = 0
                    self.inicio_ventana = monotonic()

                    while True:
                        for mensaje in self.buffer.recibir_mensajes(self.TAM_CABECERA):
                            tasa = self.contar_paquetes_por_segundo()
                            if tasa is not None:
                                print(f"[{self.buffer.host}] Tasa de recepción: {tasa:.2f} paquetes/s")
                            self.procesar_mensaje(mensaje)
            except (OSError, ValueError, struct.error) as e:
                with self.Robot_state.lock:
                    self.Robot_state.socket = None
                print(f"[{self.buffer.host}] Conexión perdida: {e!r}. Reconectando...", flush=True)
                sleep(1)

    def procesar_mensaje(self, mensaje: bytes) -> None:
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
        with self.Robot_state.lock:
            self.Robot_state.q = list(q)
            self.Robot_state.isProtectiveStopped = protective_stopped
            self.Robot_state.isProgramRunning = program_running
            self.Robot_state.error = error

def main():
    Robot_state = RobotState()  # Estado compartido con el hilo lector, con su propio lock
    lector = LectorTiempoReal(Robot_state, HOST, PORT)
    # daemon=True: el hilo muere solo cuando termina el programa principal
    lector_hilo = Thread(target=lector.leer_robot, daemon=True)
    lector_hilo.start()

    from UR3e_rg2.dynamic_ur3e import UR3eRG2
    robot = UR3eRG2()

    try:
        while True:
            with Robot_state.lock:
                q = Robot_state.q[:]
            robot.move_robot(0, q)
            sleep(1/30)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
