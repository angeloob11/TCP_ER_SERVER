import json
import math
import socket
import sys
from pathlib import Path
from time import sleep, monotonic
from threading import Thread, Lock
import primaria
import tiempo_real
import pybullet as p
from UR3e_rg2.dynamic_ur3e import UR3eRG2
from constantes import PORT, PORT_TIEMPO_REAL, VELOCIDAD_MAX, ACELERACION_MAX

RUTA_CONFIG = Path(__file__).resolve().parent.parent / "config" / "robots.json"
RUTA_PUNTOS = Path(__file__).resolve().parent.parent / "config" / "puntos.json"

TOLERANCIA_Q = 0.01      # rad: desviación máxima por articulación para dar un punto por alcanzado
TIMEOUT_INICIO = 2.0     # s: si el programa no arranca en este tiempo, la orden se ha rechazado
PERIODO_SONDEO = 0.02    # s: cada cuánto se consulta el estado compartido

# Cada puerto tiene su propio protocolo, así que se elige el lector según el puerto
LECTORES = {
    PORT: primaria.leer_robot,
    PORT_TIEMPO_REAL: tiempo_real.leer_robot,
}

class Keys:
    """Códigos de tecla que devuelve p.getKeyboardEvents()"""
    # Navegación
    TAB = 9                             # Pasar al siguiente robot activo

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
Tab   siguiente robot activo        n  escribir serie (n 0 2 1 Enter)
g     guardar posición del robot    p  previsualizar serie
s     guardar punto de deslizadores e  ejecutar serie (confirmar con y)
l     listar puntos                 x  cancelar ejecución
b     borrar último punto           c  continuar tras un error
h     ayuda
"""

def nuevo_estado_teclado() -> dict:
    return {"activo": 0, "modo": "normal", "buffer": "", "serie": [],
            "hilo": None, "robot_ejec": 0, "fallo": None, "cancelar": False}

def cargar_puntos() -> dict[int, list[float]]:
    if not RUTA_PUNTOS.exists():
        return {}
    with open(RUTA_PUNTOS, encoding="utf-8") as f:
        return {pt["id"]: pt["q"] for pt in json.load(f)}

def ejecutando(estado: dict) -> bool:
    return estado["hilo"] is not None and estado["hilo"].is_alive()

def hilo_ejecucion(robot: dict, serie: list[int], desde: int, estado: dict) -> None:
    # Punto a punto para saber dónde falló y poder continuar desde ahí con c
    estado["fallo"] = None
    for i in range(desde, len(serie)):
        if estado["cancelar"]:
            estado["fallo"] = i
            print("Ejecución cancelada", flush=True)
            return
        with robot["lock"]:
            cliente = robot["Robot_state"]["socket"]
        if cliente is None:
            estado["fallo"] = i
            print(f"{robot['nombre']}: sin conexión. Pulsa c para reintentar", flush=True)
            return
        try:
            enviar_serie([serie[i]], cliente, robot["Robot_state"], robot["lock"])
        except (RuntimeError, ValueError, OSError) as e:
            estado["fallo"] = i
            if estado["cancelar"]:
                print(f"Ejecución cancelada en el punto {serie[i]}", flush=True)
            else:
                print(f"Error en el punto {serie[i]}: {e}\nPulsa c para continuar desde él", flush=True)
            return
    print("Serie completada", flush=True)

def lanzar_ejecucion(robots: list, estado: dict, idx_robot: int, desde: int) -> None:
    estado["cancelar"] = False
    estado["robot_ejec"] = idx_robot
    estado["hilo"] = Thread(target=hilo_ejecucion,
                            args=(robots[idx_robot], estado["serie"], desde, estado), daemon=True)
    estado["hilo"].start()

def manejar_tecla(tecla: int, sim: UR3eRG2, robots: list, estado: dict) -> None:
    # --- Modo escribir serie ---
    if estado["modo"] == "serie":
        if tecla in Keys.DIGITOS or tecla == Keys.SPACE:
            estado["buffer"] += chr(tecla)
            print(chr(tecla), end="", flush=True)
        elif tecla == Keys.BACKSPACE:
            estado["buffer"] = estado["buffer"][:-1]
        elif tecla == Keys.ENTER:
            estado["serie"] = [int(t) for t in estado["buffer"].split()]
            estado["modo"] = "normal"
            faltan = [i for i in estado["serie"] if i not in cargar_puntos()]
            print(f"\nSerie: {estado['serie']}", flush=True)
            if faltan:
                print(f"  Aviso: los puntos {faltan} no existen", flush=True)
        return

    # --- Modo confirmar ejecución ---
    if estado["modo"] == "confirmar":
        estado["modo"] = "normal"
        if tecla == Keys.Y:
            lanzar_ejecucion(robots, estado, estado["activo"], 0)
        else:
            print("Ejecución no confirmada", flush=True)
        return

    # --- Modo normal ---
    activo = robots[estado["activo"]]
    match tecla:
        case Keys.TAB:
            estado["activo"] = (estado["activo"] + 1) % len(robots)
            print(f"Robot activo: {robots[estado['activo']]['nombre']}", flush=True)

        case Keys.G:
            guardar_posicion(activo["Robot_state"], activo["lock"], RUTA_PUNTOS)

        case Keys.S:
            guardar_pos_desde_sim(sim, RUTA_PUNTOS)

        case Keys.L:
            puntos = cargar_puntos()
            if not puntos:
                print("No hay puntos guardados", flush=True)
            for id_punto, q in puntos.items():
                print(f"  {id_punto}: {[round(math.degrees(a), 1) for a in q]}", flush=True)

        case Keys.N:
            estado["modo"], estado["buffer"] = "serie", ""
            print("Serie (IDs separados por espacio, Enter para terminar): ", end="", flush=True)

        case Keys.P:
            puntos = cargar_puntos()
            if not estado["serie"]:
                print("No hay serie: usa n", flush=True)
            elif faltan := [i for i in estado["serie"] if i not in puntos]:
                print(f"Los puntos {faltan} no existen", flush=True)
            else:
                mover_fantasma([puntos[i] for i in estado["serie"]], sim, activo["pose"])

        case Keys.E:
            if not estado["serie"]:
                print("No hay serie: usa n", flush=True)
            elif ejecutando(estado):
                print("Ya hay una ejecución en curso", flush=True)
            else:
                estado["modo"] = "confirmar"
                print(f"¿Ejecutar {estado['serie']} en {activo['nombre']}? (y para confirmar)", flush=True)

        case Keys.X:
            if not ejecutando(estado):
                print("No hay ninguna ejecución en curso", flush=True)
            else:
                estado["cancelar"] = True
                robot = robots[estado["robot_ejec"]]
                with robot["lock"]:
                    cliente = robot["Robot_state"]["socket"]
                if cliente is not None:
                    cliente.sendall(f"stopj({ACELERACION_MAX})\n".encode("ascii"))
                print("Cancelando...", flush=True)

        case Keys.C:
            if ejecutando(estado):
                print("La ejecución sigue en curso", flush=True)
            elif estado["fallo"] is None:
                print("No hay ningún punto pendiente del que continuar", flush=True)
            else:
                print(f"Continuando desde el punto {estado['serie'][estado['fallo']]}", flush=True)
                lanzar_ejecucion(robots, estado, estado["robot_ejec"], estado["fallo"])

        case Keys.B:
            if not RUTA_PUNTOS.exists():
                print("No hay puntos guardados", flush=True)
                return
            with open(RUTA_PUNTOS, encoding="utf-8") as f:
                puntos = json.load(f)
            if not puntos:
                print("No hay puntos guardados", flush=True)
                return
            borrado = puntos.pop()
            with open(RUTA_PUNTOS, "w", encoding="utf-8") as f:
                json.dump(puntos, f, indent=2)
            print(f"Punto {borrado['id']} borrado", flush=True)

        case Keys.H:
            print(AYUDA, flush=True)


def cargar_robots(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        robots = json.load(f)
    for r in robots:
        if r["puerto"] not in LECTORES:
            raise ValueError(f"{r['nombre']}: puerto {r['puerto']} no soportado (usa {list(LECTORES)})")
        if len(r["pose"]) != 6:
            raise ValueError(f"{r['nombre']}: la pose debe ser [x, y, z, roll, pitch, yaw]")
    return robots

def guardar_posicion(Robot_state: dict, lock: Lock, ruta: Path = RUTA_PUNTOS) -> int:
    # Copia bajo el lock para no guardar un estado a medio escribir por el hilo lector
    with lock:
        q = Robot_state["q"][:]
    puntos = []
    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            puntos = json.load(f)
    # IDs consecutivos desde 0; se parte del mayor existente para no repetir si se borra alguno
    id_punto = max((p["id"] for p in puntos), default=-1) + 1
    puntos.append({"id": id_punto, "q": q})
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(puntos, f, indent=2)
    print(f"Punto {id_punto} guardado en {ruta.name}: {[round(math.degrees(x), 2) for x in q]} grados", flush=True)
    return id_punto

def guardar_pos_desde_sim(Sim: UR3eRG2, ruta: Path = RUTA_PUNTOS) -> int:
    q_raw = Sim.get_param_values()
    q = [q_raw[f" j{i}"] for i in range(6)]
    puntos = []
    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            puntos = json.load(f)
    # IDs consecutivos desde 0; se parte del mayor existente para no repetir si se borra alguno
    id_punto = max((p["id"] for p in puntos), default=-1) + 1
    puntos.append({"id": id_punto, "q": q})
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(puntos, f, indent=2)
    print(f"Punto {id_punto} guardado en {ruta.name}: {[round(math.degrees(x), 2) for x in q]} grados", flush=True)
    return id_punto

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

def esperar_movimiento(q_objetivo: list[float], Robot_state: dict, lock: Lock,
                       Robot_client: socket.socket, v: float = VELOCIDAD_MAX) -> None:
    # El robot no responde a las órdenes: el cumplimiento se deduce del estado que sigue publicando.
    # Lanza RuntimeError con el motivo si la orden no se cumple.
    def leer():
        with lock:
            return (Robot_state["q"][:], Robot_state["isProgramRunning"],
                    Robot_state["isProtectiveStopped"], Robot_state["error"])

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

def enviar_serie(Pos: list[int], Robot_client: socket.socket, Robot_state: dict, lock: Lock,
                 v: float = VELOCIDAD_MAX, a: float = ACELERACION_MAX) -> None:
    if len(Pos) == 0:
        raise ValueError("La serie de puntos está vacía")
    with open(RUTA_PUNTOS, encoding="utf-8") as f:
        puntos = {punto["id"]: punto["q"] for punto in json.load(f)}
    faltan = [id_punto for id_punto in Pos if id_punto not in puntos]
    if faltan:
        raise ValueError(f"Puntos {faltan} no encontrados en {RUTA_PUNTOS}")

    for n, id_punto in enumerate(Pos, start=1):
        with lock:
            running = Robot_state["isProgramRunning"]
            # Se descarta un error antiguo de la primaria; en tiempo real se vuelve a rellenar si sigue activo
            Robot_state["error"] = None
        if running is None:
            raise RuntimeError("Estado del programa desconocido: aún no ha llegado información del robot")
        if running:
            raise RuntimeError("Ya hay un programa en ejecución: enviar otro lo abortaría")

        print(f"Punto {id_punto} ({n}/{len(Pos)})", flush=True)
        enviar_movimiento(puntos[id_punto], Robot_client, v, a)
        try:
            esperar_movimiento(puntos[id_punto], Robot_state, lock, Robot_client, min(v, VELOCIDAD_MAX))
        except RuntimeError as e:
            raise RuntimeError(f"Secuencia detenida en el punto {id_punto} ({n}/{len(Pos)}): {e}") from None
        print(f"Punto {id_punto} alcanzado", flush=True)
    print("Secuencia completada", flush=True)

def mover_fantasma(puntos: list, Sim: UR3eRG2, pose: list[float] = [0, 0, 0, 0, 0, 0]) -> None:
    # Acepta un punto [q0..q5] o una serie [[q0..q5], [q0..q5], ...]
    serie = [puntos] if isinstance(puntos[0], (int, float)) else puntos
    serie = [[float(x) for x in q] for q in serie]

    # PyBullet asigna el ID: el siguiente libre, mayor que los de los robots ya creados
    id_fantasma = Sim.spawn_robot(pose)
    # Semitransparente para distinguirlo del robot que sigue al real
    for link in range(-1, p.getNumJoints(id_fantasma)):
        p.changeVisualShape(id_fantasma, link, rgbaColor=[0.3, 0.6, 1.0, 0.4])
    print(f"Fantasma creado con id {id_fantasma}", flush=True)

    try:
        Sim.move_robot(id_fantasma, list(serie[0]))
        # Interpolación lineal entre puntos a la velocidad máxima permitida, como lo haría el robot real
        for q_desde, q_hasta in zip(serie, serie[1:]):
            duracion = max(abs(b - a) for a, b in zip(q_desde, q_hasta)) / VELOCIDAD_MAX
            pasos = max(1, int(duracion * 60))
            for k in range(1, pasos + 1):
                t = k / pasos
                Sim.move_robot(id_fantasma, [a + (b - a) * t for a, b in zip(q_desde, q_hasta)])
                sleep(1/60)

        print("Previsualización terminada: pulsa cualquier tecla en la ventana de PyBullet para cerrarla", flush=True)
        p.getKeyboardEvents()  # Descarta las teclas pulsadas durante la animación
        while not any(estado & p.KEY_WAS_TRIGGERED for estado in p.getKeyboardEvents().values()):
            sleep(1/60)
    finally:
        # Se elimina siempre, también si se interrumpe con Ctrl+C
        p.removeBody(id_fantasma)
        print(f"Fantasma {id_fantasma} eliminado", flush=True)

def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else RUTA_CONFIG
    robots = cargar_robots(ruta)

    # Un hilo lector, un estado compartido y un lock por robot (daemon=True: mueren al terminar el programa)
    for r in robots:
        r["Robot_state"] = primaria.nuevo_estado()
        r["lock"] = Lock()
        lector = LECTORES[r["puerto"]]
        Thread(target=lector, args=(r["Robot_state"], r["host"], r["puerto"], r["lock"]),
               name=r["nombre"], daemon=True).start()

    sim = UR3eRG2()

    # El constructor ya crea el robot 0 en el origen: se reutiliza para el primero y se recoloca
    for i, r in enumerate(robots):
        if i == 0:
            r["id"] = 0
            p.resetBasePositionAndOrientation(0, r["pose"][:3], p.getQuaternionFromEuler(r["pose"][3:]))
        else:
            r["id"] = sim.spawn_robot(r["pose"])
        print(f"{r['nombre']} -> {r['host']}:{r['puerto']} (id {r['id']})", flush=True)

    p.configureDebugVisualizer(p.COV_ENABLE_KEYBOARD_SHORTCUTS, 0)
    estado = nuevo_estado_teclado()
    print(AYUDA, flush=True)

    try:
        while True:
            for r in robots:
                with r["lock"]:
                    q = r["Robot_state"]["q"][:]
                sim.move_robot(r["id"], q)
             #Lectura del teclado
                for tecla, ev in p.getKeyboardEvents().items():
                    if ev & p.KEY_WAS_TRIGGERED:
                        manejar_tecla(tecla, sim, robots, estado)
              
            sleep(1/30)
    except KeyboardInterrupt:
        pass
   



if __name__ == "__main__":
	main()
