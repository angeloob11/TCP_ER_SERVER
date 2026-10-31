import socket
import struct
from time import sleep
from threading import Thread, Lock
from constantes import HOST, PORT
from buffer_ur import Buffer


ROBOT_MESSAGE_TYPE_ERROR_CODE = 6
NIVELES_REPORTE = {0: "DEBUG", 1: "INFO", 2: "WARNING", 3: "VIOLATION", 4: "FAULT", 5: "DEVL_DEBUG"}

class RobotState:
    """Estado compartido entre el hilo lector y el principal: ángulos y modo del robot.
    Cualquier lectura o escritura de los atributos se hace con el lock cogido."""

    def __init__(self):
        self.q = [0.0] * 6
        self.isProgramRunning: bool | None = None
        self.isProtectiveStopped: bool | None = None
        self.error: str | None = None
        # Conexión del lector, para enviar URScript por ella (None mientras no hay conexión)
        self.socket: socket.socket | None = None
        self.lock = Lock()

class LectorPrimaria:
    """Lee la interfaz primaria (30001) y vuelca lo recibido en un RobotState."""

    TAM_CABECERA = 5  # longitud(4) + tipo(1)

    def __init__(self, Robot_state: RobotState, HOST: str, PORT: int):
        self.Robot_state = Robot_state
        self.buffer = Buffer(HOST, PORT)

    def leer_robot(self) -> None:
        while True:  # Si se cae la conexión, se vuelve a esperar al servidor
            try:
                with self.buffer.conectar_con_reintento() as cliente:
                    # Se publica la conexión para enviar órdenes por ella: este hilo sigue leyendo,
                    # así que el buffer de recepción nunca se llena. Tras reconectar se actualiza sola.
                    with self.Robot_state.lock:
                        self.Robot_state.socket = cliente
                    while True:
                        for mensaje in self.buffer.recibir_mensajes(self.TAM_CABECERA, depurar=False):   #MODO DE DEPURACION
                            self.procesar_mensaje(mensaje)
            except (OSError, ValueError, struct.error) as e:
                with self.Robot_state.lock:
                    self.Robot_state.socket = None
                print(f"[{self.buffer.host}] Conexión perdida: {e!r}. Reconectando...", flush=True)
                sleep(1)

    def procesar_mensaje(self, mensaje: bytes) -> None:
        longitud, tipo = struct.unpack_from(">iB", mensaje, 0)
        #print(f"[{self.buffer.host}] Recibido mensaje tipo {tipo} con longitud {longitud}")
        if tipo == 16:
            self.procesar_robot_state(mensaje, longitud)
        # Robot message: cabecera(5) + timestamp(8) + source(1) + robotMessageType(1)
        elif tipo == 20 and len(mensaje) >= 15:
            self.procesar_robot_message(mensaje, longitud)

    def procesar_robot_state(self, mensaje: bytes, longitud: int) -> None:
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
                with self.Robot_state.lock:
                    self.Robot_state.isProtectiveStopped = protective_stopped
                    self.Robot_state.isProgramRunning = program_running

            elif tipo_sub == 1:  # Joint data: 6 articulaciones de 41 bytes, q_actual al inicio de cada una
                q = [struct.unpack_from(">d", mensaje, offset + 5 + 41 * i)[0] for i in range(6)]
                # El lock solo cubre la copia: el hilo principal nunca lee un estado a medias
                with self.Robot_state.lock:
                    self.Robot_state.q = q

            offset += longitud_sub

    def procesar_robot_message(self, mensaje: bytes, longitud: int) -> None:
        (robot_message_type,) = struct.unpack_from(">B", mensaje, 14)
        # Error code: code(4) + argument(4) + reportLevel(4) + dataType(1) + data(4) + texto
        if robot_message_type == ROBOT_MESSAGE_TYPE_ERROR_CODE and len(mensaje) >= 32:
            codigo, argumento, nivel = struct.unpack_from(">iii", mensaje, 15)
            texto = mensaje[32:longitud].decode("utf-8", errors="replace")
            error = f"{NIVELES_REPORTE.get(nivel, nivel)} C{codigo}A{argumento}: {texto}"
            # Se mantiene hasta que quien lo atienda lo ponga a None
            with self.Robot_state.lock:
                self.Robot_state.error = error
            print(f"[{self.buffer.host}] Error del robot: {error}", flush=True)

def main():
    Robot_state = RobotState()  # Estado compartido con el hilo lector, con su propio lock
    lector = LectorPrimaria(Robot_state, HOST, PORT)
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
