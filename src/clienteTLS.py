import argparse
import json
import socket
import ssl
from pathlib import Path
from time import monotonic, perf_counter, sleep

from buffer_ur import Buffer
from constantes import PORT, PORT_TIEMPO_REAL, VELOCIDAD_MAX, ACELERACION_MAX
from primaria import LectorPrimaria, RobotState
from tiempo_real import LectorTiempoReal
from multi_robot import (Robot, MultiRobot, BASE, RUTA_CONFIG,
                         TIMEOUT_INICIO, TOLERANCIA_Q)
from pasarela import SSL_CLIENT, cargar_config, pedir_clave

RUTA_CLIENTE = BASE / 'config' / 'cliente.json'        # certificados del cliente (C1)
RUTA_ROBOTS_TLS = BASE / 'config' / 'robots_tls.json'  # robots a través de la pasarela


class BufferTLS(Buffer):

    def __init__(self, host: str, port: int, ctx: ssl.SSLContext | None):
        super().__init__(host, port)
        self.ctx = ctx

    def conectar_con_reintento(self, intervalo: float = 1.0, timeout: float = 2.0,
                               timeout_recv: float = 2.0) -> socket.socket:
        intento = 1
        while True:
            try:
                t0 = perf_counter()
                tcp = socket.create_connection((self.host, self.port), timeout=timeout)
                t1 = perf_counter()
                if self.ctx is None:
                    self.client = tcp
                else:
                    try:
                        self.client = self.ctx.wrap_socket(tcp, server_hostname=self.host)
                    except Exception:
                        tcp.close()
                        raise
                t2 = perf_counter()
                self.client.settimeout(timeout_recv)
                self.buffer.clear()
                self.mostrar_conexion(t1 - t0, t2 - t1)
                return self.client
            except ssl.SSLCertVerificationError as e:
                motivo = f'certificado de la pasarela no válido: {e.verify_message}'
            except ssl.SSLError as e:
                motivo = f'error TLS en el handshake: {e.reason}'
            except ConnectionRefusedError:
                motivo = 'conexión rechazada: nadie escucha en ese puerto'
            except TimeoutError:
                motivo = 'inalcanzable: no responde'
            except OSError as e:
                motivo = f'inalcanzable: {e}'
            print(f'[{self.host}:{self.port}] {motivo} (intento {intento}). '
                  f'Reintentando en {intervalo} s...', flush=True)
            intento += 1
            sleep(intervalo)

    def mostrar_conexion(self, t_tcp: float, t_tls: float) -> None:
        if self.ctx is None:
            print(f'[{self.host}:{self.port}] Conectado en directo, sin TLS '
                  f'(TCP {t_tcp * 1000:.1f} ms)', flush=True)
            return
        cert = self.client.getpeercert()
        sujeto = dict(c[0] for c in cert['subject'])
        print(f'[{self.host}:{self.port}] Conectado a la pasarela\n'
              f'    {self.client.version()}  {self.client.cipher()[0]}\n'
              f'    certificado: CN={sujeto.get("commonName")}, caduca {cert["notAfter"]}\n'
              f'    tiempos: TCP {t_tcp * 1000:.1f} ms + handshake TLS {t_tls * 1000:.1f} ms',
              flush=True)

    def recibir_mensajes(self, tam_cabecera: int, depurar: bool = False) -> list[bytes]:
        try:
            return super().recibir_mensajes(tam_cabecera, depurar)
        except ssl.SSLError as e:
            raise ConnectionError(f'la pasarela rechazó el certificado del cliente: {e.reason}') from None
        except ConnectionError as e:
            # Buffer lanza ConnectionError('El servidor cerró la conexión') al leer b''
            if self.ctx is not None and type(e) is ConnectionError:
                raise ConnectionError('la pasarela cerró la conexión '
                                      '(cliente no autorizado o robot no disponible)') from None
            raise


class LectorPrimariaTLS(LectorPrimaria):
    def __init__(self, Robot_state: RobotState, HOST: str, PORT: int, ctx: ssl.SSLContext | None):
        super().__init__(Robot_state, HOST, PORT)
        self.buffer = BufferTLS(HOST, PORT, ctx)


class LectorTiempoRealTLS(LectorTiempoReal):
    def __init__(self, Robot_state: RobotState, HOST: str, PORT: int, ctx: ssl.SSLContext | None):
        super().__init__(Robot_state, HOST, PORT)
        self.buffer = BufferTLS(HOST, PORT, ctx)


# A través de la pasarela el puerto (3000) ya no dice qué interfaz es: se indica en el JSON
LECTORES_TLS = {'primaria': LectorPrimariaTLS, 'tiempo_real': LectorTiempoRealTLS}
INTERFAZ_POR_PUERTO = {PORT: 'primaria', PORT_TIEMPO_REAL: 'tiempo_real'}


class RobotTLS(Robot):

    def __init__(self, nombre: str, host: str, puerto: int, pose: list[float],
                 interfaz: str, ctx: ssl.SSLContext | None):
        # No se llama a Robot.__init__: elige el lector con LECTORES[puerto] y el
        # puerto de la pasarela no está ahí. Se crean los mismos atributos.
        self.nombre = nombre
        self.host = host
        self.puerto = puerto
        self.pose = pose
        self.id: int | None = None
        self.Robot_state = RobotState()
        self.lector = LECTORES_TLS[interfaz](self.Robot_state, host, puerto, ctx)
        self.t_envio = 0.0

    @classmethod
    def cargar_robots(cls, ruta: Path, ctx: ssl.SSLContext | None) -> list['RobotTLS']:
        with open(ruta, encoding='utf-8') as f:
            robots = json.load(f)
        resultado = []
        for r in robots:
            interfaz = r.get('interfaz') or INTERFAZ_POR_PUERTO.get(r['puerto'])
            if interfaz not in LECTORES_TLS:
                raise ValueError(f"{r['nombre']}: indica \"interfaz\": {list(LECTORES_TLS)}")
            if len(r['pose']) != 6:
                raise ValueError(f"{r['nombre']}: la pose debe ser [x, y, z, roll, pitch, yaw]")
            resultado.append(cls(r['nombre'], r['host'], r['puerto'], r['pose'], interfaz, ctx))
        return resultado

    def enviar_movimiento(self, q: list[float], Robot_client: socket.socket,
                          v: float = VELOCIDAD_MAX, a: float = ACELERACION_MAX) -> None:
        self.t_envio = perf_counter()
        Robot.enviar_movimiento(q, Robot_client, v, a)

    def esperar_movimiento(self, q_objetivo: list[float], Robot_client: socket.socket,
                           v: float = VELOCIDAD_MAX) -> None:
        # Respuesta = el estado que publica el robot indica que el programa ha arrancado
        limite = monotonic() + TIMEOUT_INICIO
        while monotonic() < limite:
            with self.Robot_state.lock:
                running = self.Robot_state.isProgramRunning
                q = self.Robot_state.q[:]
            if running:
                print(f'    respuesta del robot en {(perf_counter() - self.t_envio) * 1000:.1f} ms',
                      flush=True)
                break
            if max(abs(x - y) for x, y in zip(q, q_objetivo)) < TOLERANCIA_Q:
                break              # movimiento tan corto que no se vio arrancar
            sleep(0.001)
        super().esperar_movimiento(q_objetivo, Robot_client, v)
        print(f'    orden completada en {perf_counter() - self.t_envio:.3f} s', flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description='Cliente del robot a través de la pasarela TLS')
    ap.add_argument('--directo', action='store_true',
                    help='conectar directamente al robot, sin TLS, para comparar (C7)')
    ap.add_argument('--robots', type=Path,
                    help='JSON de robots (por defecto robots_tls.json, o robots.json con --directo)')
    ap.add_argument('--config', type=Path, default=RUTA_CLIENTE,
                    help='JSON con los certificados del cliente (por defecto config/cliente.json)')
    args = ap.parse_args()

    if args.directo:
        ctx = None
        ruta = args.robots or RUTA_CONFIG
        print('Modo directo: sin TLS', flush=True)
    else:
        try:
            pki = cargar_config(args.config)['pki']
        except (OSError, json.JSONDecodeError, KeyError) as e:
            print(f'No se puede leer la configuración {args.config}: {e}')
            return
        try:
            ctx = SSL_CLIENT(pki['cert'], pki['key'], pki['ca'], pedir_clave(pki['key'].name))
        except ssl.SSLError:
            print('Contraseña incorrecta o la clave no corresponde al certificado')
            return
        except OSError as e:
            print(f'No se pueden leer los certificados: {e}')
            return
        ruta = args.robots or RUTA_ROBOTS_TLS

    robots = RobotTLS.cargar_robots(ruta, ctx)
    for r in robots:
        r.iniciar_lector()
    MultiRobot(robots).bucle_principal()


if __name__ == '__main__':
    main()
