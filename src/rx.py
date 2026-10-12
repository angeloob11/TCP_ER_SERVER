"""Mide cuántos mensajes por segundo entrega REALMENTE el robot.

Solo recibe y cuenta: sin cola, sin hilos y sin simulación. Así se separa
"el robot/la red no entrega 500 mensajes/s" de "mi aplicación no los lee".

Uso:
    python diagnostico_rx.py                      # usa HOST/PORT de constantes.py
    python diagnostico_rx.py --host 192.168.0.10 --port 30003 --segundos 15
"""

import argparse
import socket
import statistics
import struct
from time import perf_counter


def valores_por_defecto() -> tuple[str, int]:
    try:
        from constantes import HOST, PORT_TIEMPO_REAL

        return HOST, PORT_TIEMPO_REAL
    except ImportError:
        return "127.0.0.1", 30003


def main() -> None:
    host0, port0 = valores_por_defecto()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=host0)
    ap.add_argument("--port", type=int, default=port0)
    ap.add_argument("--segundos", type=float, default=10.0)
    args = ap.parse_args()

    buf = bytearray()
    trozo = bytearray(65536)
    vista = memoryview(trozo)

    n_msgs = 0
    n_recv = 0
    n_bytes = 0
    longitudes: set[int] = set()
    ts_primero = ts_prev = None
    deltas_ms: list[float] = []

    with socket.create_connection((args.host, args.port)) as sock:
        t0 = perf_counter()
        t_ultimo = t0
        while perf_counter() - t0 < args.segundos:
            n = sock.recv_into(vista)
            if n == 0:
                raise ConnectionError("El servidor cerró la conexión")
            n_recv += 1
            n_bytes += n
            buf += vista[:n]

            pos = 0
            while len(buf) - pos >= 4:
                (longitud,) = struct.unpack_from(">i", buf, pos)
                if not 12 <= longitud <= 100_000:
                    raise ValueError(f"Longitud inválida: {longitud}")
                if len(buf) - pos < longitud:
                    break  # mensaje incompleto: esperar más bytes
                # Segundo campo del mensaje: tiempo del controlador (segundos)
                (ts,) = struct.unpack_from(">d", buf, pos + 4)
                longitudes.add(longitud)
                if ts_primero is None:
                    ts_primero = ts
                if ts_prev is not None:
                    deltas_ms.append((ts - ts_prev) * 1000)
                ts_prev = ts
                n_msgs += 1
                t_ultimo = perf_counter()
                pos += longitud
            del buf[:pos]

    if n_msgs < 2 or ts_prev is None or ts_primero is None:
        print("Muy pocos mensajes para analizar.")
        return

    pared = t_ultimo - t0
    robot = ts_prev - ts_primero
    tasa_pared = n_msgs / pared
    tasa_robot = (n_msgs - 1) / robot if robot > 0 else float("nan")
    razon = robot / pared

    print(f"Mensajes recibidos      : {n_msgs} en {pared:.2f} s de reloj")
    print(f"Tamaño de mensaje (B)   : {sorted(longitudes)}")
    print(f"Mensajes/s (mi reloj)   : {tasa_pared:.1f}")
    print(f"Mensajes/s (reloj robot): {tasa_robot:.1f}  <- lo que el robot ENVÍA")
    print(f"Intervalo entre msgs ms : media {statistics.mean(deltas_ms):.2f} | "
          f"mediana {statistics.median(deltas_ms):.2f} | máx {max(deltas_ms):.2f}")
    print(f"Saltos > 1.5x el normal : {sum(d > 1.5 * statistics.median(deltas_ms) for d in deltas_ms)}")
    print(f"Mensajes por recv()     : {n_msgs / n_recv:.1f}")
    print(f"Tiempo robot / reloj    : {razon:.3f}  (1.000 = sin retraso acumulado)")

    print("\nInterpretación:")
    if tasa_robot < 0.9 * 500 and razon > 0.95:
        print("- El robot (o el simulador/red) ENVÍA menos de 500/s. Problema en el origen.")
    elif razon < 0.95:
        print("- Tu PC no consume al ritmo del robot: se acumula retraso (backlog).")
    else:
        print("- El origen entrega bien y esta lectura simple lo consume. "
              "Si tu aplicación lee menos, el problema está en tu aplicación.")


if __name__ == "__main__":
    main()