import socket
import struct
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from UR3e_rg2.dynamic_ur3e import UR3eRG2
from constantes import HOST, PORT_TIEMPO_REAL as PORT


def recibir_paquete(cliente: socket.socket, cantidad_bytes: int) -> bytes:
    partes = []
    recibidos = 0

    while recibidos < cantidad_bytes:
        parte = cliente.recv(cantidad_bytes - recibidos)
        if not parte:
            raise ConnectionError("El servidor cerró la conexión")
        partes.append(parte)
        recibidos += len(parte)

    return b"".join(partes)


def recibir_estados(estados: Queue[tuple[float, ...]], finalizado: Event, errores: Queue[Exception],) -> None:
    try:
        with socket.create_connection((HOST, PORT)) as cliente:
            paquetes_en_ventana = 0
            inicio_ventana = monotonic()

            while not finalizado.is_set():
                cabecera = recibir_paquete(cliente, 4)
                longitud = struct.unpack(">i", cabecera)[0]
                if longitud < 300:
                    raise ValueError(f"Longitud de mensaje inválida: {longitud}")

                data = recibir_paquete(cliente, longitud - 4)
                estado = struct.unpack(">6d", data[248:296])

                paquetes_en_ventana += 1
                ahora = monotonic()
                transcurrido = ahora - inicio_ventana
                if transcurrido >= 1.0:
                    print(
                        f"Tasa de recepción: "
                        f"{paquetes_en_ventana / transcurrido:.2f} paquetes/s"
                    )
                    paquetes_en_ventana = 0
                    inicio_ventana = ahora

                try:
                    estados.put_nowait(estado)
                except Full:
                    try:
                        estados.get_nowait()
                    except Empty:
                        pass
                    estados.put_nowait(estado)
    except (OSError, ValueError, struct.error, ConnectionError) as error:
        errores.put(error)
    finally:
        finalizado.set()


def robot_client(robot: UR3eRG2) -> None:
    estados: Queue[tuple[float, ...]] = Queue(maxsize=1)
    finalizado = Event()
    errores: Queue[Exception] = Queue()
    receptor = Thread(
        target=recibir_estados,
        args=(estados, finalizado, errores),
        daemon=True,
    )
    receptor.start()

    intervalo_simulacion = 1 / 60
    siguiente_actualizacion = monotonic()

    try:
        while not finalizado.is_set() or not estados.empty():
            espera = siguiente_actualizacion - monotonic()
            if espera > 0:
                finalizado.wait(espera)

            estado = None
            while True:
                try:
                    estado = estados.get_nowait()
                except Empty:
                    break

            if estado is not None:
                robot.move_robot(0, list(estado))
                siguiente_actualizacion = monotonic() + intervalo_simulacion

        if not errores.empty():
            raise errores.get()
    finally:
        finalizado.set()


def main() -> None:
    robot = UR3eRG2()
    robot_client(robot)


if __name__ == "__main__":
    main()
