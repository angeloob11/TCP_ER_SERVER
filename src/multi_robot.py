import json
import math
import socket
import sys
from pathlib import Path
from time import sleep, monotonic
from threading import Thread
from primaria import RobotState, LectorPrimaria
from tiempo_real import LectorTiempoReal
import pybullet as p
from UR3e_rg2.dynamic_ur3e import UR3eRG2
from constantes import PORT, PORT_TIEMPO_REAL, VELOCIDAD_MAX, ACELERACION_MAX

# Empaquetado con PyInstaller: config/ va junto al .exe; en desarrollo, en la raíz del proyecto
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent.parent

RUTA_CONFIG = BASE / "config" / "robots.json"
RUTA_PUNTOS = BASE / "config" / "puntos.json"

TOLERANCIA_Q = 0.01      # rad: desviación máxima por articulación para dar un punto por alcanzado
TIMEOUT_INICIO = 2.0     # s: si el programa no arranca en este tiempo, la orden se ha rechazado
PERIODO_SONDEO = 0.02    # s: cada cuánto se consulta el estado compartido

# Cada puerto tiene su propio protocolo, así que se elige el lector según el puerto
LECTORES = {
    PORT: LectorPrimaria,
    PORT_TIEMPO_REAL: LectorTiempoReal,
}

class Keys:
    """Códigos de tecla que devuelve p.getKeyboardEvents()"""
    # Navegación
    TAB = ord("t")                            # Pasar al siguiente robot activo

    # Puntos
    G = ord("g")                        # guardar_posicion() del robot activo
    S = ord("s")                        # guardar_pos_desde_sim(), deslizadores
    L = ord("l")                        # Listar puntos guardados
    B = ord("b")                        # Borrar el último punto guardado

    # Serie
    N = ord("n")                        # Empezar a escribir la serie de IDs
    P = ord("p")                        # Previsualizar con mover_fantasma
    E = ord("e")                        # Ejecutar la serie (pide confirmación)
    Y = ord("y")                        # Confirmar la ejecución
    X = ord("x")                        # Cancelar la ejecución
    C = ord("c")                        # Continuar tras un error

    # Ayuda
    H = ord("h")                        # Mostrar la ayuda

    # Edición de la serie (modo n)
    ENTER = p.B3G_RETURN                # Terminar la serie
    BACKSPACE = p.B3G_BACKSPACE         # Borrar el último carácter
    SPACE = ord(" ")                    # Separador entre IDs
    DIGITOS = range(ord("0"), ord("9") + 1)

AYUDA = """
t   siguiente robot activo        n  escribir serie (n 0 2 1 Enter)
g     guardar posición del robot    p  previsualizar serie
s     guardar punto de deslizadores e  ejecutar serie (confirmar con y)
l     listar puntos                 x  cancelar ejecución
b     borrar último punto           c  continuar tras un error
h     ayuda
"""


class Puntos:
    """Puntos guardados en disco como [{"id": 0, "q": [q0..q5]}, ...]"""

    def __init__(self, ruta: Path = RUTA_PUNTOS):
        self.ruta = ruta

    def leer(self) -> list[dict]:
        if not self.ruta.exists():
            return []
        with open(self.ruta, encoding="utf-8") as f:
            return json.load(f)

    def escribir(self, puntos: list[dict]) -> None:
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(puntos, f, indent=2)

    def cargar_puntos(self) -> dict[int, list[float]]:
        return {pt["id"]: pt["q"] for pt in self.leer()}

    def guardar(self, q: list[float]) -> int:
        puntos = self.leer()
        # IDs consecutivos desde 0; se parte del mayor existente para no repetir si se borra alguno
        id_punto = max((pt["id"] for pt in puntos), default=-1) + 1
        puntos.append({"id": id_punto, "q": q})
        self.escribir(puntos)
        print(f"Punto {id_punto} guardado en {self.ruta.name}: {[round(math.degrees(x), 2) for x in q]} grados", flush=True)
        return id_punto

    def guardar_posicion(self, Robot_state: RobotState) -> int:
        # Copia bajo el lock para no guardar un estado a medio escribir por el hilo lector
        with Robot_state.lock:
            q = Robot_state.q[:]
        return self.guardar(q)

    def guardar_pos_desde_sim(self, Sim: UR3eRG2) -> int:
        q_raw = Sim.get_param_values()
        return self.guardar([q_raw[f" j{i}"] for i in range(6)])

    def borrar_ultimo(self) -> None:
        puntos = self.leer()
        if not puntos:
            print("No hay puntos guardados", flush=True)
            return
        borrado = puntos.pop()
        self.escribir(puntos)
        print(f"Punto {borrado['id']} borrado", flush=True)


class Robot:
    """Un robot de robots.json: su conexión, su estado compartido y su modelo en la simulación."""

    def __init__(self, nombre: str, host: str, puerto: int, pose: list[float]):
        self.nombre = nombre
        self.host = host
        self.puerto = puerto
        self.pose = pose
        self.id: int | None = None  # ID en PyBullet, se asigna al crear la simulación
        self.Robot_state = RobotState()
        self.lector = LECTORES[puerto](self.Robot_state, host, puerto)

    @classmethod
    def cargar_robots(cls, ruta: Path) -> list["Robot"]:
        with open(ruta, encoding="utf-8") as f:
            robots = json.load(f)
        for r in robots:
            if r["puerto"] not in LECTORES:
                raise ValueError(f"{r['nombre']}: puerto {r['puerto']} no soportado (usa {list(LECTORES)})")
            if len(r["pose"]) != 6:
                raise ValueError(f"{r['nombre']}: la pose debe ser [x, y, z, roll, pitch, yaw]")
        return [cls(r["nombre"], r["host"], r["puerto"], r["pose"]) for r in robots]

    def iniciar_lector(self) -> None:
        # daemon=True: el hilo muere al terminar el programa
        Thread(target=self.lector.leer_robot, name=self.nombre, daemon=True).start()

    def leer_q(self) -> list[float]:
        with self.Robot_state.lock:
            return self.Robot_state.q[:]

    def leer_socket(self) -> socket.socket | None:
        with self.Robot_state.lock:
            return self.Robot_state.socket

    def reconectar(self, timeout: float = 5.0) -> socket.socket:
        """Corta la conexión actual para que el hilo lector abra otra nueva, y espera a que la publique."""
        viejo = self.leer_socket()
        if viejo is not None:
            try:
                # shutdown (no close) es seguro desde otro hilo: el recv() del lector
                # devuelve vacío, lanza ConnectionError y el lector reconecta solo
                viejo.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        limite = monotonic() + timeout
        while monotonic() < limite:
            nuevo = self.leer_socket()
            if nuevo is not None and nuevo is not viejo:
                sleep(0.5)  # margen para que llegue estado fresco por la conexión nueva
                return nuevo
            sleep(0.1)
        raise RuntimeError(f"{self.nombre}: no se pudo reconectar en {timeout} s")

    @staticmethod
    def enviar_movimiento(q: list[float], Robot_client: socket.socket, v: float = VELOCIDAD_MAX, a: float = ACELERACION_MAX) -> None:
        q = [float(x) for x in q]
        if len(q) != 6:
            raise ValueError(f"Se esperaban 6 ángulos, llegaron {len(q)}")
        # movej trabaja en radianes: un valor fuera de ±2π casi seguro viene en grados
        if any(not math.isfinite(x) or abs(x) > 2 * math.pi for x in q):
            raise ValueError(f"Ángulos fuera de [-2*pi, 2*pi] (¿grados en vez de radianes?): {q}")
        if v <= 0 or a <= 0:
            raise ValueError(f"Velocidad y aceleración deben ser positivas (v={v}, a={a})")
        if v > VELOCIDAD_MAX or a > ACELERACION_MAX:
            print(f" Limitando v={v} -> {min(v, VELOCIDAD_MAX)}, a={a} -> {min(a, ACELERACION_MAX)}", flush=True)
            v, a = min(v, VELOCIDAD_MAX), min(a, ACELERACION_MAX)
        # Programa URScript: def en la columna 0, cuerpo indentado, end en la columna 0 y \n final
        programa = (
            "def mover():\n"
            f"  movej([{', '.join(f'{x:.6f}' for x in q)}], a={a:.3f}, v={v:.3f})\n"
            "end\n"
        )
        Robot_client.sendall(programa.encode("ascii"))
        print(f"Enviado movej a {[round(math.degrees(x), 2) for x in q]} grados", flush=True)

    def esperar_movimiento(self, q_objetivo: list[float], Robot_client: socket.socket, v: float = VELOCIDAD_MAX) -> None:
        # El robot no responde a las órdenes: el cumplimiento se deduce del estado que sigue publicando.
        # Lanza RuntimeError con el motivo si la orden no se cumple.
        def leer():
            with self.Robot_state.lock:
                return (self.Robot_state.q[:], self.Robot_state.isProgramRunning,
                        self.Robot_state.isProtectiveStopped, self.Robot_state.error)

        def comprobar_fallos(parada, error):
            if parada:
                raise RuntimeError("Parada de protección activa")
            if error is not None:
                raise RuntimeError(f"Error del robot: {error}")

        def desviacion(q):
            return max(abs(a - b) for a, b in zip(q, q_objetivo))

        inicio = monotonic()
        q, _, _, _ = leer()
        # Margen generoso sobre lo que tardaría el eje más lento a velocidad v (ignora la aceleración)
        limite_total = TIMEOUT_INICIO + 3 * desviacion(q) / v + 2.0

        # 1. Esperar a que el programa arranque
        while True:
            q, running, parada, error = leer()
            comprobar_fallos(parada, error)
            if running:
                break
            # Con la primaria a 10 Hz, un movimiento muy corto puede empezar y acabar entre dos mensajes
            if desviacion(q) < TOLERANCIA_Q:
                return
            if monotonic() - inicio > TIMEOUT_INICIO:
                raise RuntimeError(f"El programa no arrancó en {TIMEOUT_INICIO} s "
                                   "(orden rechazada: revisa el modo remoto y los mensajes del robot)")
            sleep(PERIODO_SONDEO)

        # 2. Esperar a que termine, vigilando errores y paradas
        while True:
            q, running, parada, error = leer()
            comprobar_fallos(parada, error)
            if not running:
                break
            if monotonic() - inicio > limite_total:
                Robot_client.sendall(f"stopj({ACELERACION_MAX})\n".encode("ascii"))
                raise RuntimeError(f"El movimiento no terminó en {limite_total:.1f} s: robot detenido con stopj")
            sleep(PERIODO_SONDEO)

        # 3. Comprobar que ha llegado al objetivo
        if desviacion(q) >= TOLERANCIA_Q:
            raise RuntimeError(f"El programa terminó lejos del objetivo (desviación {desviacion(q):.4f} rad)")

    def enviar_serie(self, Pos: list[int], Robot_client: socket.socket, puntos_guardados: Puntos,
                     v: float = VELOCIDAD_MAX, a: float = ACELERACION_MAX) -> None:
        if len(Pos) == 0:
            raise ValueError("La serie de puntos está vacía")
        puntos = puntos_guardados.cargar_puntos()
        faltan = [id_punto for id_punto in Pos if id_punto not in puntos]
        if faltan:
            raise ValueError(f"Puntos {faltan} no encontrados en {puntos_guardados.ruta}")

        for n, id_punto in enumerate(Pos, start=1):
            with self.Robot_state.lock:
                running = self.Robot_state.isProgramRunning
                # Se descarta un error antiguo de la primaria; en tiempo real se vuelve a rellenar si sigue activo
                self.Robot_state.error = None
            if running is None:
                raise RuntimeError("Estado del programa desconocido: aún no ha llegado información del robot")
            if running:
                raise RuntimeError("Ya hay un programa en ejecución: enviar otro lo abortaría")

            print(f"Punto {id_punto} ({n}/{len(Pos)})", flush=True)
            self.enviar_movimiento(puntos[id_punto], Robot_client, v, a)
            try:
                self.esperar_movimiento(puntos[id_punto], Robot_client, min(v, VELOCIDAD_MAX))
            except RuntimeError as e:
                raise RuntimeError(f"Secuencia detenida en el punto {id_punto} ({n}/{len(Pos)}): {e}") from None
            print(f"Punto {id_punto} alcanzado", flush=True)
        print("Secuencia completada", flush=True)


class Ejecucion:
    """Serie de puntos que se ejecuta en un hilo aparte, para poder cancelarla (x) o continuarla (c)."""

    def __init__(self, puntos: Puntos):
        self.puntos = puntos
        self.serie: list[int] = []
        self.hilo: Thread | None = None
        self.robot_ejec: Robot | None = None   # Robot que ejecuta (o ejecutó) la serie
        self.fallo: int | None = None          # Índice de la serie donde se detuvo
        self.cancelar = False

    def ejecutando(self) -> bool:
        return self.hilo is not None and self.hilo.is_alive()

    def lanzar_ejecucion(self, robot: Robot, desde: int) -> None:
        self.cancelar = False
        self.robot_ejec = robot
        self.hilo = Thread(target=self.hilo_ejecucion, args=(robot, self.serie, desde), daemon=True)
        self.hilo.start()

    def hilo_ejecucion(self, robot: Robot, serie: list[int], desde: int) -> None:
        # Punto a punto para saber dónde falló y poder continuar desde ahí con c
        self.fallo = None
        for i in range(desde, len(serie)):
            if self.cancelar:
                self.fallo = i
                print("Ejecución cancelada", flush=True)
                return
            cliente = robot.leer_socket()
            if cliente is None:
                self.fallo = i
                print(f"{robot.nombre}: sin conexión. Pulsa c para reintentar", flush=True)
                return
            try:
                try:
                    robot.enviar_serie([serie[i]], cliente, self.puntos)
                except RuntimeError as e:
                    # Orden rechazada (p. ej. la conexión se abrió con el robot en Manual/Local):
                    # se abre una conexión nueva y se reintenta una vez
                    # Rechazo por conexión en solo lectura:
                    #  - primaria (30001): el robot avisa con el código C210
                    #  - tiempo real (30003): no hay aviso, salta el timeout de "no arrancó"
                    motivo = str(e)
                    rechazada = "C210" in motivo or "no arrancó" in motivo
                    if not rechazada or self.cancelar:
                        raise
                    print(f"{robot.nombre}: orden rechazada, reconectando y reintentando...", flush=True)
                    cliente = robot.reconectar()
                    robot.enviar_serie([serie[i]], cliente, self.puntos)
            except (RuntimeError, ValueError, OSError) as e:
                self.fallo = i
                if self.cancelar:
                    print(f"Ejecución cancelada en el punto {serie[i]}", flush=True)
                else:
                    print(f"Error en el punto {serie[i]}: {e}\nPulsa c para continuar desde él", flush=True)
                return
        print("Serie completada", flush=True)


class MultiRobot:
    """Simulación con todos los robots y el manejo del teclado."""

    def __init__(self, robots: list[Robot]):
        self.robots = robots
        self.puntos = Puntos(RUTA_PUNTOS)
        self.ejecucion = Ejecucion(self.puntos)
        self.activo = 0          # Índice del robot al que van las órdenes
        self.modo = "normal"     # "normal", "serie" (escribiendo IDs) o "confirmar"
        self.buffer = ""         # Texto de la serie mientras se escribe

        self.sim = UR3eRG2()
        # El constructor ya crea el robot 0 en el origen: se reutiliza para el primero y se recoloca
        for i, r in enumerate(robots):
            if i == 0:
                r.id = 0
                p.resetBasePositionAndOrientation(0, r.pose[:3], p.getQuaternionFromEuler(r.pose[3:]))
            else:
                r.id = self.sim.spawn_robot(r.pose)
            print(f"{r.nombre} -> {r.host}:{r.puerto} (id {r.id})", flush=True)

        p.configureDebugVisualizer(p.COV_ENABLE_KEYBOARD_SHORTCUTS, 0)

    def manejar_tecla(self, tecla: int) -> None:
        ejecucion = self.ejecucion

        # --- Modo escribir serie ---
        if self.modo == "serie":
            if tecla in Keys.DIGITOS or tecla == Keys.SPACE:
                self.buffer += chr(tecla)
                print(chr(tecla), end="", flush=True)
            elif tecla == Keys.BACKSPACE:
                self.buffer = self.buffer[:-1]
            elif tecla == Keys.ENTER:
                ejecucion.serie = [int(t) for t in self.buffer.split()]
                self.modo = "normal"
                faltan = [i for i in ejecucion.serie if i not in self.puntos.cargar_puntos()]
                print(f"\nSerie: {ejecucion.serie}", flush=True)
                if faltan:
                    print(f"  Aviso: los puntos {faltan} no existen", flush=True)
            return

        # --- Modo confirmar ejecución ---
        if self.modo == "confirmar":
            self.modo = "normal"
            if tecla == Keys.Y:
                ejecucion.lanzar_ejecucion(self.robots[self.activo], 0)
            else:
                print("Ejecución no confirmada", flush=True)
            return

        # --- Modo normal ---
        activo = self.robots[self.activo]
        match tecla:
            case Keys.TAB:
                self.activo = (self.activo + 1) % len(self.robots)
                print(f"Robot activo: {self.robots[self.activo].nombre}", flush=True)

            case Keys.G:
                self.puntos.guardar_posicion(activo.Robot_state)

            case Keys.S:
                self.puntos.guardar_pos_desde_sim(self.sim)

            case Keys.L:
                puntos = self.puntos.cargar_puntos()
                if not puntos:
                    print("No hay puntos guardados", flush=True)
                for id_punto, q in puntos.items():
                    print(f"  {id_punto}: {[round(math.degrees(a), 1) for a in q]}", flush=True)

            case Keys.N:
                self.modo, self.buffer = "serie", ""
                print("Serie (IDs separados por espacio, Enter para terminar): ", end="", flush=True)

            case Keys.P:
                puntos = self.puntos.cargar_puntos()
                if not ejecucion.serie:
                    print("No hay serie: usa n", flush=True)
                elif faltan := [i for i in ejecucion.serie if i not in puntos]:
                    print(f"Los puntos {faltan} no existen", flush=True)
                else:
                    self.mover_fantasma([puntos[i] for i in ejecucion.serie], activo.pose)

            case Keys.E:
                if not ejecucion.serie:
                    print("No hay serie: usa n", flush=True)
                elif ejecucion.ejecutando():
                    print("Ya hay una ejecución en curso", flush=True)
                else:
                    self.modo = "confirmar"
                    print(f"¿Ejecutar {ejecucion.serie} en {activo.nombre}? (y para confirmar)", flush=True)

            case Keys.X:
                if not ejecucion.ejecutando():
                    print("No hay ninguna ejecución en curso", flush=True)
                else:
                    ejecucion.cancelar = True
                    cliente = ejecucion.robot_ejec.leer_socket()
                    if cliente is not None:
                        cliente.sendall(f"stopj({ACELERACION_MAX})\n".encode("ascii"))
                    print("Cancelando...", flush=True)

            case Keys.C:
                if ejecucion.ejecutando():
                    print("La ejecución sigue en curso", flush=True)
                elif ejecucion.fallo is None:
                    print("No hay ningún punto pendiente del que continuar", flush=True)
                else:
                    print(f"Continuando desde el punto {ejecucion.serie[ejecucion.fallo]}", flush=True)
                    ejecucion.lanzar_ejecucion(ejecucion.robot_ejec, ejecucion.fallo)

            case Keys.B:
                self.puntos.borrar_ultimo()

            case Keys.H:
                print(AYUDA, flush=True)

    def mover_fantasma(self, puntos: list, pose: list[float] = [0, 0, 0, 0, 0, 0]) -> None:
        # Acepta un punto [q0..q5] o una serie [[q0..q5], [q0..q5], ...]
        serie = [puntos] if isinstance(puntos[0], (int, float)) else puntos
        serie = [[float(x) for x in q] for q in serie]

        # PyBullet asigna el ID: el siguiente libre, mayor que los de los robots ya creados
        id_fantasma = self.sim.spawn_robot(pose)
        # Semitransparente para distinguirlo del robot que sigue al real
        for link in range(-1, p.getNumJoints(id_fantasma)):
            p.changeVisualShape(id_fantasma, link, rgbaColor=[0.3, 0.6, 1.0, 0.4])
        print(f"Fantasma creado con id {id_fantasma}", flush=True)

        try:
            self.sim.move_robot(id_fantasma, list(serie[0]))
            # Interpolación lineal entre puntos a la velocidad máxima permitida, como lo haría el robot real
            for q_desde, q_hasta in zip(serie, serie[1:]):
                duracion = max(abs(b - a) for a, b in zip(q_desde, q_hasta)) / VELOCIDAD_MAX
                pasos = max(1, int(duracion * 60))
                for k in range(1, pasos + 1):
                    t = k / pasos
                    self.sim.move_robot(id_fantasma, [a + (b - a) * t for a, b in zip(q_desde, q_hasta)])
                    sleep(1/60)

            print("Previsualización terminada: pulsa cualquier tecla en la ventana de PyBullet para cerrarla", flush=True)
            p.getKeyboardEvents()  # Descarta las teclas pulsadas durante la animación
            while not any(estado & p.KEY_WAS_TRIGGERED for estado in p.getKeyboardEvents().values()):
                sleep(1/60)
        finally:
            # Se elimina siempre, también si se interrumpe con Ctrl+C
            p.removeBody(id_fantasma)
            print(f"Fantasma {id_fantasma} eliminado", flush=True)

    def bucle_principal(self) -> None:
        print(AYUDA, flush=True)
        try:
            while True:
                for r in self.robots:
                    self.sim.move_robot(r.id, r.leer_q())
                # Lectura del teclado
                for tecla, ev in p.getKeyboardEvents().items():
                    if ev & p.KEY_WAS_TRIGGERED:
                        self.manejar_tecla(tecla)
                sleep(1/30)
        except KeyboardInterrupt:
            pass


def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else RUTA_CONFIG
    robots = Robot.cargar_robots(ruta)

    # Un hilo lector y un estado compartido (con su lock) por robot
    for r in robots:
        r.iniciar_lector()

    MultiRobot(robots).bucle_principal()


if __name__ == "__main__":
    main()
