import argparse
import ssl
import socket
import getpass
import os
import json
import logging
import threading
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / 'config'
CONFIG_POR_DEFECTO = {'servidor': CONFIG_DIR / 'pasarela.json',
                      'multi':    CONFIG_DIR / 'pasarela_multi.json',
                      'cliente':  CONFIG_DIR / 'cliente.json'}
VARIABLE_CONFIG = {'servidor': 'PASARELA_CONFIG',
                   'multi':    'PASARELA_MULTI_CONFIG',
                   'cliente':  'CLIENTE_CONFIG'}


def configurar_log(ruta: Path) -> logging.Logger:
    log = logging.getLogger('pasarela')
    log.setLevel(logging.INFO)
    fh = logging.FileHandler(ruta, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(fh)
    log.addHandler(logging.StreamHandler())       # también por pantalla
    return log

def cargar_config(ruta: Path) -> dict:
    """Lee el JSON; sus rutas (pki y log) son relativas a la carpeta del propio JSON."""
    ruta = Path(ruta).resolve()
    with open(ruta) as f:
        cfg = json.load(f)
    for k, v in cfg['pki'].items():
        cfg['pki'][k] = ruta.parent / v
    if 'log' in cfg:
        cfg['log'] = ruta.parent / cfg['log']
    return cfg


def pedir_clave(req: str)->str:
    return os.environ.get('PASARELA_CLAVE') or getpass.getpass(f'Contraseña de {req}: ')

class SSL_SERVER(ssl.SSLContext):

    def __new__(cls, cert: Path, key: Path, ca: Path, clave: str):
        return super().__new__(cls, ssl.PROTOCOL_TLS_SERVER)

    def __init__(self, cert: Path, key: Path, ca: Path, clave: str):
        self.minimum_version = ssl.TLSVersion.TLSv1_3
        self.load_cert_chain(cert,
                             key,
                             password=clave)
        self.load_verify_locations(cafile=ca)
        self.verify_mode = ssl.CERT_REQUIRED


class SSL_CLIENT(ssl.SSLContext):

    def __new__(cls, cert: Path, key: Path, ca: Path, clave: str):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, cert: Path, key: Path, ca: Path, clave: str):
        # PROTOCOL_TLS_CLIENT ya pone CERT_REQUIRED y check_hostname
        self.minimum_version = ssl.TLSVersion.TLSv1_3
        self.load_verify_locations(cafile=ca)
        self.load_cert_chain(cert, key, password=clave)

class Servidor():

    def __init__(self, HOST: str, PORT: int, ctx: ssl.SSLContext, whitelist: list[str]) -> None:
        self.ctx = ctx
        self.whitelist = whitelist
        self.sock = socket.create_server((HOST, PORT))

    def Autorizado(self, tls: ssl.SSLSocket) -> tuple[bool, str]:
        cn = dict(c[0] for c in tls.getpeercert()['subject'])['commonName']
        return cn in self.whitelist, cn

    def Aceptar(self, conexion: socket.socket) -> ssl.SSLSocket:
        conexion.settimeout(10)
        try:
            tls = self.ctx.wrap_socket(conexion, server_side=True)
        except (ssl.SSLError, OSError):
            conexion.close()
            raise
        tls.settimeout(None)
        return tls

    def Reenviar(self, origen: socket.socket, destino: socket.socket) -> None:
        try:
            while datos := origen.recv(4096):
                destino.sendall(datos)
        except (ssl.SSLError, OSError):
            pass
        finally:
            for s in (destino, origen):
                if isinstance(s, ssl.SSLSocket):
                    try:
                        s.settimeout(2)        # que el close_notify no se quede esperando
                        s.unwrap()             # cierre TLS ordenado (close_notify)
                    except (ssl.SSLError, OSError, ValueError):
                        pass
                try:
                    s.shutdown(socket.SHUT_RDWR)   # despierta al otro hilo si está en recv
                except OSError:
                    pass
                s.close()

    def Close(self) -> None:
        self.sock.close()

class Cliente():

    def __init__(self, HOST: str, PORT: int, ctx: ssl.SSLContext):
        self.ctx = ctx
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        try:
            # server_hostname: se comprueba que HOST está en la SAN de la pasarela
            self.tls = self.ctx.wrap_socket(self.sock, server_hostname=HOST)
        except (ssl.SSLError, OSError):
            self.sock.close()
            raise
        self.tls.settimeout(None)

    def Info(self) -> dict:
        cert = self.tls.getpeercert()
        return {'version': self.tls.version(),
                'suite': self.tls.cipher()[0],
                'sujeto': dict(c[0] for c in cert['subject'])['commonName'],
                'caduca': cert['notAfter']}

    def Enviar(self, datos: bytes):
        self.tls.sendall(datos)

    def Recibir(self, n: int = 4096) -> bytes:
        return self.tls.recv(n)       # b'' si la pasarela ha cerrado

    def Close(self):
        try:
            self.tls.unwrap().close()  # envía close_notify
        except (ssl.SSLError, OSError):
            self.tls.close()


class PasarelaMultiInterfaz():

    def __init__(self, host: str, interfaces: list[dict], ctx: ssl.SSLContext,
                 whitelist: list[str], log: logging.Logger) -> None:
        self.log = log
        self.parar = threading.Event()
        self.cerrojo = threading.Lock()            # protege activas e hilos
        self.activas: dict[ssl.SSLSocket, socket.socket] = {}   # cliente -> su conexión con el robot
        self.hilos: list[threading.Thread] = []
        self.escuchas: list[threading.Thread] = []
        # Una entrada por interfaz: (nombre, Servidor que escucha, destino en el robot)
        self.interfaces: list[tuple[str, Servidor, tuple[str, int]]] = []
        try:
            for i in interfaces:
                servidor = Servidor(host, i['port'], ctx, whitelist)
                servidor.sock.settimeout(0.5)      # para que accept() pueda ver self.parar
                destino = (i['destino']['host'], i['destino']['port'])
                self.interfaces.append((i['nombre'], servidor, destino))
                log.info('Interfaz %s escuchando en %s:%s, destino %s:%s',
                         i['nombre'], host, i['port'], *destino)
        except OSError:
            for _, servidor, _ in self.interfaces:  # no dejar puertos abiertos a medias
                servidor.Close()
            raise

    def Escuchar(self, nombre: str, servidor: Servidor, destino: tuple[str, int]) -> None:
        while not self.parar.is_set():
            try:
                conexion, direccion = servidor.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            hilo = threading.Thread(target=self.Atender, daemon=True,
                                    args=(nombre, servidor, destino, conexion, direccion))
            hilo.start()
            with self.cerrojo:
                self.hilos = [h for h in self.hilos if h.is_alive()] + [hilo]

    def Atender(self, nombre: str, servidor: Servidor, destino: tuple[str, int],
                conexion: socket.socket, direccion: tuple[str, int]) -> None:
        ip = direccion[0]
        try:
            tls = servidor.Aceptar(conexion)
        except (ssl.SSLError, OSError) as e:
            self.log.warning('interfaz=%s ip=%s motivo=handshake fallido: %s', nombre, ip, e)
            return

        autorizado, cn = servidor.Autorizado(tls)
        if not autorizado:
            self.log.warning('interfaz=%s ip=%s cn=%s version=%s suite=%s motivo=no autorizado',
                             nombre, ip, cn, tls.version(), tls.cipher()[0])
            self.CerrarTLS(tls)
            return
        try:
            robot = socket.create_connection(destino, timeout=5)
        except OSError as e:
            self.log.error('interfaz=%s ip=%s cn=%s motivo=destino %s:%s inalcanzable: %s',
                           nombre, ip, cn, *destino, e)
            self.CerrarTLS(tls)
            return
        robot.settimeout(None)

        self.log.info('interfaz=%s ip=%s cn=%s version=%s suite=%s conectado a %s:%s',
                      nombre, ip, cn, tls.version(), tls.cipher()[0], *destino)
        with self.cerrojo:
            self.activas[tls] = robot

        vuelta = threading.Thread(target=servidor.Reenviar, args=(robot, tls), daemon=True)
        vuelta.start()
        servidor.Reenviar(tls, robot)
        vuelta.join()

        with self.cerrojo:
            self.activas.pop(tls, None)
        self.log.info('interfaz=%s ip=%s cn=%s motivo=conexión cerrada', nombre, ip, cn)

    @staticmethod
    def CerrarTLS(tls: ssl.SSLSocket) -> None:
        try:
            tls.unwrap().close()
        except (ssl.SSLError, OSError):
            tls.close()

    def Iniciar(self) -> None:
        for nombre, servidor, destino in self.interfaces:
            hilo = threading.Thread(target=self.Escuchar, args=(nombre, servidor, destino),
                                    daemon=True)
            hilo.start()
            self.escuchas.append(hilo)

    def Servir(self) -> None:
        self.Iniciar()
        try:
            while not self.parar.wait(1):
                pass
        except KeyboardInterrupt:
            self.log.info('Cerrando pasarela')
        finally:
            self.Cerrar()

    def Cerrar(self) -> None:
        self.parar.set()
        for hilo in self.escuchas:
            hilo.join(2)
        for _, servidor, _ in self.interfaces:
            servidor.Close()
        with self.cerrojo:
            robots = list(self.activas.values())
            hilos = list(self.hilos)
        # Se corta el lado del robot de cada conexión: su hilo de reenvío despierta y
        # Reenviar hace el cierre TLS (close_notify) del cliente. Así todas las
        # conexiones se cierran a la vez, cada una en su hilo, y no una detrás de otra.
        for robot in robots:
            try:
                robot.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for hilo in hilos:
            hilo.join(3)


def modo_servidor(cfg: dict, host: str, port: int):
    pki = cfg['pki']
    log = configurar_log(cfg['log'])
    try:
        ctx = SSL_SERVER(pki['cert'], pki['key'], pki['ca'], pedir_clave(pki['key'].name))
    except ssl.SSLError:
        print('Contraseña incorrecta o la clave no corresponde al certificado')
        return
    servidor = Servidor(host, port, ctx, cfg['autorizados'])
    destino: tuple[str, int] = (cfg['destino']['host'], cfg['destino']['port'])
    activas: set[ssl.SSLSocket] = set()     
    cerrojo = threading.Lock()              
    hilos: list[threading.Thread] = []
    log.info('Pasarela escuchando en %s:%s, destino %s:%s', host, port, *destino)

    def atender(conexion: socket.socket, direccion: tuple[str, int]) -> None:
        ip = direccion[0]
        try:
            tls = servidor.Aceptar(conexion)
        except (ssl.SSLError, OSError) as e:
            log.warning('ip=%s motivo=handshake fallido: %s', ip, e)
            return

        autorizado, cn = servidor.Autorizado(tls)

        if not autorizado:
            log.warning('ip=%s cn=%s version=%s suite=%s motivo=no autorizado', ip, cn, tls.version(), tls.cipher()[0])
            try:
                tls.unwrap().close()       
            except (ssl.SSLError, OSError):
                tls.close()
            return                         
        try:
            robot = socket.create_connection(destino, timeout=5)     
        except OSError as e:
            log.error('ip=%s cn=%s motivo=destino %s:%s inalcanzable: %s', ip, cn, *destino, e)  
            try:
                tls.unwrap().close()
            except (ssl.SSLError, OSError):
                tls.close()
            return
        robot.settimeout(None)

        log.info('ip=%s cn=%s version=%s suite=%s conectado',
                 ip, cn, tls.version(), tls.cipher()[0])
        with cerrojo:
            activas.add(tls)

        vuelta = threading.Thread(target=servidor.Reenviar, args=(robot, tls), daemon=True)
        vuelta.start()
        servidor.Reenviar(tls, robot)
        vuelta.join()

        with cerrojo:
            activas.discard(tls)
        log.info('ip=%s cn=%s motivo=conexión cerrada', ip, cn)            
        
    try:
        while True:
            conexion, direccion = servidor.sock.accept()
            hilo = threading.Thread(target=atender, args=(conexion, direccion), daemon=True)
            hilo.start()
            hilos = [h for h in hilos if h.is_alive()] + [hilo]
    except KeyboardInterrupt:
        log.info('Cerrando pasarela')
    finally:
        servidor.Close()                    
        with cerrojo:
            abiertas = list(activas)
        for tls in abiertas:               
            try:
                tls.settimeout(2)
                tls.unwrap()
            except (ssl.SSLError, OSError, ValueError):
                pass
            try:
                tls.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for h in hilos:                     
            h.join(3)


def modo_multi(cfg: dict, host: str):
    pki = cfg['pki']
    log = configurar_log(cfg['log'])
    try:
        ctx = SSL_SERVER(pki['cert'], pki['key'], pki['ca'], pedir_clave(pki['key'].name))
    except ssl.SSLError:
        print('Contraseña incorrecta o la clave no corresponde al certificado')
        return
    except OSError as e:
        print(f'No se pueden leer los certificados: {e}')
        return
    try:
        pasarela = PasarelaMultiInterfaz(host, cfg['interfaces'], ctx, cfg['autorizados'], log)
    except OSError as e:
        print(f'No se puede escuchar en {host}: {e}')
        return
    pasarela.Servir()


def modo_cliente(cfg: dict, host: str, port: int):
    pki = cfg['pki']
    try:
        ctx = SSL_CLIENT(pki['cert'], pki['key'], pki['ca'], pedir_clave(pki['key'].name))
    except ssl.SSLError:
        print('Contraseña incorrecta o la clave no corresponde al certificado')
        return
    try:
        cliente = Cliente(host, port, ctx)
    except ssl.SSLCertVerificationError as e:
        print('Certificado de la pasarela no válido:', e.verify_message)
        return
    except ssl.SSLError as e:
        print('Error TLS:', e.reason)
        return
    except OSError as e:
        print('No se puede conectar con la pasarela:', e)
        return

    print('Conectado:', cliente.Info())
    try:
        while (linea := input('> ')):
            cliente.Enviar(linea.encode())
            respuesta = cliente.Recibir()
            if not respuesta:
                print('La pasarela ha cerrado la conexión')
                break
            print('<', respuesta.decode(errors='replace'))
    except ssl.SSLError as e:
        # TLS 1.3: el rechazo del certificado del cliente aparece aquí (C9)
        print('La pasarela rechazó la conexión:', e.reason)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        cliente.Close()


def main():
    parser = argparse.ArgumentParser(description='Pasarela TLS y cliente de prueba')
    parser.add_argument('modo', choices=['servidor', 'multi', 'cliente'])
    parser.add_argument('--config', type=Path,
                        help='JSON de configuración (por defecto config/pasarela.json, '
                             'config/pasarela_multi.json o config/cliente.json según el modo)')
    parser.add_argument('--host', help='sustituye la IP del JSON')
    parser.add_argument('--port', type=int, help='sustituye el puerto del JSON')
    args = parser.parse_args()

    ruta = (args.config or os.environ.get(VARIABLE_CONFIG[args.modo])
            or CONFIG_POR_DEFECTO[args.modo])
    try:
        cfg = cargar_config(ruta)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f'No se puede leer la configuración {ruta}: {e}')
        return

    if args.modo == 'multi':
        # varias interfaces: los puertos van en "interfaces" del JSON, no en --port
        try:
            modo_multi(cfg, args.host or cfg['escucha']['host'])
        except KeyError as e:
            print(f'Falta {e} en la configuración {ruta}')
        return

    # servidor: dónde escucha; cliente: a qué pasarela se conecta
    red = cfg['escucha'] if args.modo == 'servidor' else cfg['pasarela']
    host = args.host or red['host']
    port = args.port or red['port']

    if args.modo == 'servidor':
        modo_servidor(cfg, host, port)
    else:
        modo_cliente(cfg, host, port)


if __name__ == '__main__':
    main()
