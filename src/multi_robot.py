import json
import sys
from pathlib import Path
from time import sleep
from threading import Thread, Lock
import primaria
import tiempo_real
from constantes import PORT, PORT_TIEMPO_REAL

RUTA_CONFIG = Path(__file__).resolve().parent.parent / "config" / "robots.json"

# Cada puerto tiene su propio protocolo, así que se elige el lector según el puerto
LECTORES = {
    PORT: primaria.leer_robot,
    PORT_TIEMPO_REAL: tiempo_real.leer_robot,
}

def cargar_robots(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        robots = json.load(f)
    for r in robots:
        if r["puerto"] not in LECTORES:
            raise ValueError(f"{r['nombre']}: puerto {r['puerto']} no soportado (usa {list(LECTORES)})")
        if len(r["pose"]) != 6:
            raise ValueError(f"{r['nombre']}: la pose debe ser [x, y, z, roll, pitch, yaw]")
    return robots

def leer_robot(Joint_state: list[float], HOST: str, PORT: int, Lock: Lock) -> None:
    lector = LECTORES[PORT]
    lector(Joint_state, HOST, PORT)
    with Lock:
        sleep(1)  # Evita que el hilo muera inmediatamente y deje de imprimir el error de conexión

def send_to_real_robot(Joint_state: list[float], HOST: str, PORT: int, Lock: Lock) -> None:
    pass

def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else RUTA_CONFIG
    robots = cargar_robots(ruta)

    # Un hilo lector y una lista compartida por robot (daemon=True: mueren al terminar el programa)
    for r in robots:
        r["Joint_state"] = [0.0] * 6
        #lector = LECTORES[r["puerto"]]
        #Thread(target=lector, args=(r["Joint_state"], r["host"], r["puerto"]),name=r["nombre"], daemon=True).start()
        Thread(target=leer_robot, args=(r["Joint_state"], r["host"], r["puerto"], Lock()), name=r["nombre"], daemon=True).start()


    import pybullet as p
    from UR3e_rg2.dynamic_ur3e import UR3eRG2
    sim = UR3eRG2()

    # El constructor ya crea el robot 0 en el origen: se reutiliza para el primero y se recoloca
    for i, r in enumerate(robots):
        if i == 0:
            r["id"] = 0
            p.resetBasePositionAndOrientation(0, r["pose"][:3], p.getQuaternionFromEuler(r["pose"][3:]))
        else:
            r["id"] = sim.spawn_robot(r["pose"])
        print(f"{r['nombre']} -> {r['host']}:{r['puerto']} (id {r['id']})", flush=True)

    try:
        while True:
            for r in robots:
                sim.move_robot(r["id"], r["Joint_state"][:])
            sleep(1/30)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
	main()
