"""
Pruebas de la Fase 3 del enunciado: requisitos P1-P16 de la pasarela TLS.

Las pruebas son de caja negra: arrancan la pasarela como un proceso aparte
(igual que en el laboratorio) y hablan con ella por la red, con clientes TLS
reales y un servidor de eco de pruebas como destino.

Requisitos para ejecutarlas
---------------------------
- La IP de "escucha" de config/pasarela.json asignada a este equipo
  (y en la SAN del certificado de la pasarela).
- La contraseña de las claves en la variable de entorno PASARELA_CLAVE
  (las pruebas la necesitan para cargar cliente-a.key y cliente-b.key):

      PASARELA_CLAVE=xxxx python3 -m pytest tests/ -v

Cómo recibe la pasarela su configuración en las pruebas
------------------------------------------------------
- Línea de órdenes:   pasarela.py servidor --host IP --port PUERTO
- Variable PASARELA_CONFIG con la ruta de un JSON como este:
      {
        "escucha":    {"host": "10.172.21.30", "port": 3000},
        "destino":    {"host": "127.0.0.1",    "port": 5000},
        "autorizados": ["cliente-a"],
        "log":        "/ruta/pasarela.log",
        "pki": {"ca": "...", "cert": "...", "key": "..."}
      }
- Variable PASARELA_CLAVE con la contraseña de pasarela.key.
Si tu pasarela lee la configuración de otra forma, cambia solo
Pasarela.arrancar() más abajo.
"""

import json
import os
import random
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / 'src' / 'pasarela.py'
PKI = RAIZ / 'PKI'
CA_LAB = PKI / 'publico' / 'ca.crt'
# La IP de escucha de config/pasarela.json (debe ser la de la SAN del certificado)
IP = json.loads((RAIZ / 'config' / 'pasarela.json').read_text())['escucha']['host']
CLAVE = os.environ.get('PASARELA_CLAVE')
SUITES_TLS13 = ('TLS_AES_256_GCM_SHA384', 'TLS_AES_128_GCM_SHA256',
                'TLS_CHACHA20_POLY1305_SHA256')


def _ip_local(ip):
    try:
        with socket.create_server((ip, 0)):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not _ip_local(IP), reason=f'{IP} no está asignada a este equipo'),
    pytest.mark.skipif(CLAVE is None, reason='define PASARELA_CLAVE con la contraseña de las claves'),
]


# --------------------------------------------------------------------------
# Piezas de apoyo
# --------------------------------------------------------------------------

def puerto_libre(host):
    with socket.create_server((host, 0)) as s:
        return s.getsockname()[1]


class Destino:
    """Servidor TCP de pruebas (hace de robot): eco que registra lo que recibe."""

    def __init__(self, eco=True, cerrar_tras_recibir=False):
        self.eco = eco
        self.cerrar_tras_recibir = cerrar_tras_recibir
        self.sock = socket.create_server(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.recibido = bytearray()
        self.conexiones = 0
        self.eof = threading.Event()        # el otro extremo (la pasarela) cerró
        self._parar = False
        threading.Thread(target=self._aceptar, daemon=True).start()

    def _aceptar(self):
        while not self._parar:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conexiones += 1
            threading.Thread(target=self._atender, args=(c,), daemon=True).start()

    def _atender(self, c):
        with c:
            try:
                while datos := c.recv(65536):
                    self.recibido += datos
                    if self.cerrar_tras_recibir:
                        return
                    if self.eco:
                        c.sendall(datos)
                self.eof.set()
            except OSError:
                self.eof.set()

    def cerrar(self):
        self._parar = True
        self.sock.close()


class Pasarela:
    """Arranca src/pasarela.py como proceso y lo para al terminar la prueba."""

    def __init__(self, tmp_path, destino_port, autorizados=('cliente-a',)):
        self.port = puerto_libre(IP)
        self.log = tmp_path / 'pasarela.log'
        self.salida_path = tmp_path / 'salida.txt'
        self.config = tmp_path / 'config.json'
        self.config.write_text(json.dumps({
            'escucha': {'host': IP, 'port': self.port},
            'destino': {'host': '127.0.0.1', 'port': destino_port},
            'autorizados': list(autorizados),
            'log': str(self.log),
            'pki': {'ca': str(CA_LAB),
                    'cert': str(PKI / 'pasarela' / 'pasarela.crt'),
                    'key': str(PKI / 'pasarela' / 'pasarela.key')},
        }, indent=2))
        self.proc = None

    def arrancar(self):
        env = dict(os.environ, PASARELA_CONFIG=str(self.config), PASARELA_CLAVE=CLAVE)
        self._salida = open(self.salida_path, 'w')
        self.proc = subprocess.Popen(
            [sys.executable, '-u', str(SRC), 'servidor', '--host', IP, '--port', str(self.port)],
            env=env, stdin=subprocess.DEVNULL, stdout=self._salida, stderr=subprocess.STDOUT,
            cwd=RAIZ, start_new_session=True)
        fin = time.monotonic() + 10
        while time.monotonic() < fin:
            if self.proc.poll() is not None:
                pytest.fail(f'La pasarela terminó al arrancar:\n{self.salida()}')
            try:
                socket.create_connection((IP, self.port), timeout=0.5).close()
                time.sleep(0.2)
                return self
            except OSError:
                time.sleep(0.1)
        pytest.fail(f'La pasarela no empezó a escuchar en {IP}:{self.port}:\n{self.salida()}')

    def salida(self):
        return self.salida_path.read_text() if self.salida_path.exists() else ''

    def registro(self):
        return self.log.read_text() if self.log.exists() else ''

    def ctrl_c(self):
        self.proc.send_signal(signal.SIGINT)

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        if self.proc:
            self._salida.close()


def contexto_cliente(cert=None, key=None, ca=CA_LAB, password=CLAVE, max_version=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=ca)
    if max_version:
        ctx.maximum_version = max_version
    if cert:
        ctx.load_cert_chain(cert, key, password=password)
    return ctx


def ctx_equipo(nombre, **kw):
    return contexto_cliente(PKI / nombre / f'{nombre}.crt', PKI / nombre / f'{nombre}.key', **kw)


def conectar(pasarela, ctx, timeout=5):
    tcp = socket.create_connection((IP, pasarela.port), timeout=timeout)
    try:
        return ctx.wrap_socket(tcp, server_hostname=IP)
    except Exception:
        tcp.close()
        raise


def peticion(tls, datos):
    tls.sendall(datos)
    recibido = b''
    while len(recibido) < len(datos):
        trozo = tls.recv(65536)
        if not trozo:
            break
        recibido += trozo
    return recibido


def comprobar_rechazado(pasarela, ctx):
    """El cliente debe ser rechazado: en el handshake o, con TLS 1.3, en la primera lectura."""
    try:
        tls = conectar(pasarela, ctx)
    except (ssl.SSLError, ConnectionResetError):
        return
    with tls:
        try:
            tls.sendall(b'hola')
            r = tls.recv(100)
        except (ssl.SSLError, ConnectionResetError, BrokenPipeError):
            return
        except TimeoutError:
            pytest.fail('La pasarela no cerró la conexión en 5 s')
        assert r == b'', f'La pasarela aceptó la conexión y respondió {r!r}'


@pytest.fixture
def destino():
    d = Destino()
    yield d
    d.cerrar()


@pytest.fixture
def pasarela(tmp_path, destino):
    p = Pasarela(tmp_path, destino.port).arrancar()
    yield p
    p.parar()


@pytest.fixture
def nueva_pasarela(tmp_path):
    """Para pruebas que necesitan otro destino u otra lista de autorizados."""
    creadas = []

    def crear(destino_port, autorizados=('cliente-a',)):
        p = Pasarela(tmp_path, destino_port, autorizados).arrancar()
        creadas.append(p)
        return p
    yield crear
    for p in creadas:
        p.parar()


@pytest.fixture(scope='session')
def pki_ajena(tmp_path_factory):
    """Otra CA (la de 'otro estudiante') con un cliente-a válido para ELLA."""
    d = tmp_path_factory.mktemp('pki_ajena')

    def ossl(*args):
        subprocess.run(['openssl', *args], cwd=d, check=True, capture_output=True)
    ossl('req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
         '-keyout', 'ca.key', '-out', 'ca.crt', '-subj', '/CN=CA ajena',
         '-addext', 'basicConstraints=critical,CA:TRUE',
         '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    ossl('req', '-new', '-newkey', 'rsa:2048', '-noenc',
         '-keyout', 'c.key', '-out', 'c.csr', '-subj', '/CN=cliente-a')
    (d / 'c.ext').write_text('basicConstraints=critical,CA:FALSE\n'
                             'keyUsage=critical,digitalSignature\n'
                             'extendedKeyUsage=clientAuth\n')
    ossl('x509', '-req', '-in', 'c.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key',
         '-CAcreateserial', '-days', '1', '-extfile', 'c.ext', '-out', 'c.crt')
    return d


# --------------------------------------------------------------------------
# Requisitos de seguridad (TLS)
# --------------------------------------------------------------------------

def test_P1_escucha_solo_en_la_ip_de_la_pasarela(pasarela):
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(('127.0.0.1', pasarela.port), timeout=2)


def test_P2_clave_privada_cifrada_en_pem():
    primera = (PKI / 'pasarela' / 'pasarela.key').read_text().splitlines()[0]
    assert primera == '-----BEGIN ENCRYPTED PRIVATE KEY-----'


def test_P2_contrasena_no_esta_en_el_codigo_ni_en_la_configuracion():
    ficheros = [SRC, *(RAIZ / 'config').glob('*')]
    con_clave = [str(f.relative_to(RAIZ)) for f in ficheros
                 if f.is_file() and CLAVE in f.read_text(errors='ignore')]
    assert not con_clave, f'La contraseña aparece escrita en: {con_clave}'


def test_P3_negocia_tls13(pasarela):
    with conectar(pasarela, ctx_equipo('cliente-a')) as tls:
        assert tls.version() == 'TLSv1.3'
        assert tls.cipher()[0] in SUITES_TLS13


def test_P3_rechaza_tls12(pasarela):
    ctx = ctx_equipo('cliente-a', max_version=ssl.TLSVersion.TLSv1_2)
    with pytest.raises(ssl.SSLError):
        conectar(pasarela, ctx)


def test_P4_rechaza_cliente_sin_certificado(pasarela, destino):
    comprobar_rechazado(pasarela, contexto_cliente())
    assert destino.conexiones == 0


def test_P5_rechaza_certificado_de_otra_ca(pasarela, destino, pki_ajena):
    ctx = contexto_cliente(pki_ajena / 'c.crt', pki_ajena / 'c.key', password=None)
    comprobar_rechazado(pasarela, ctx)
    assert destino.conexiones == 0


def test_P5_no_usa_las_ca_del_sistema():
    codigo = SRC.read_text()
    for prohibido in ('create_default_context', 'load_default_certs'):
        assert prohibido not in codigo, f'{prohibido} carga las CA del sistema'


def test_P6_rechaza_certificado_de_cliente_con_serverauth(pasarela, destino):
    # pasarela.crt está firmado por la CA del laboratorio, pero su uso es serverAuth
    comprobar_rechazado(pasarela, ctx_equipo('pasarela'))
    assert destino.conexiones == 0


def test_P7_cliente_b_no_autorizado(pasarela, destino):
    # El handshake funciona (su certificado es válido)...
    tls = conectar(pasarela, ctx_equipo('cliente-b'))
    tls.close()
    # ...pero la pasarela lo cierra sin abrir conexión con el destino
    comprobar_rechazado(pasarela, ctx_equipo('cliente-b'))
    time.sleep(0.3)
    assert destino.conexiones == 0, 'La pasarela abrió conexión con el destino para cliente-b'
    assert 'cliente-b' in pasarela.registro(), 'El rechazo de cliente-b no está en el registro'


def test_P7_lista_de_autorizados_se_lee_de_la_configuracion(nueva_pasarela, destino):
    p = nueva_pasarela(destino.port, autorizados=['cliente-b'])
    comprobar_rechazado(p, ctx_equipo('cliente-a'))
    with conectar(p, ctx_equipo('cliente-b')) as tls:
        assert peticion(tls, b'ping') == b'ping'


def test_P8_handshake_con_tiempo_limite(pasarela):
    inicio = time.monotonic()
    with socket.create_connection((IP, pasarela.port), timeout=20) as mudo:
        try:
            mudo.recv(1)                    # no envía nada: espera a que lo corten
        except ConnectionResetError:
            pass
    assert time.monotonic() - inicio < 15, 'La pasarela no corta un handshake que no llega'


def test_P9_registro_de_conexiones(pasarela):
    with conectar(pasarela, ctx_equipo('cliente-a')) as tls:
        suite = tls.cipher()[0]
        peticion(tls, b'hola')
    comprobar_rechazado(pasarela, ctx_equipo('cliente-b'))
    time.sleep(0.5)
    registro = pasarela.registro()
    assert registro, f'No existe el fichero de registro {pasarela.log}'
    assert 'Logging error' not in pasarela.salida(), \
        f'Hay llamadas a log con argumentos que no cuadran:\n{pasarela.salida()}'
    # Cada conexión, en UNA línea con todos sus datos
    for cn in ('cliente-a', 'cliente-b'):
        lineas = [l for l in registro.splitlines() if cn in l]
        assert any(all(d in l for d in (IP, 'TLSv1.3', suite)) for l in lineas), \
            f'Ninguna línea del registro tiene IP, versión y suite de {cn}:\n{registro}'


# --------------------------------------------------------------------------
# Requisitos de comunicación
# --------------------------------------------------------------------------

def test_P10_cuatro_clientes_y_uno_bloqueado(pasarela):
    bloqueado = socket.create_connection((IP, pasarela.port))  # ni siquiera hace handshake
    try:
        def cliente(n, resultados):
            try:
                with conectar(pasarela, ctx_equipo('cliente-a'), timeout=3) as tls:
                    msg = f'cliente {n}'.encode()
                    resultados[n] = peticion(tls, msg) == msg
            except Exception as e:
                resultados[n] = e
        resultados = {}
        hilos = [threading.Thread(target=cliente, args=(n, resultados)) for n in range(3)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        assert resultados == {0: True, 1: True, 2: True}, \
            f'Con un cliente bloqueado, los otros tres no funcionan: {resultados}'
    finally:
        bloqueado.close()


def test_P11_conecta_con_el_destino_configurado(pasarela, destino):
    with conectar(pasarela, ctx_equipo('cliente-a')) as tls:
        peticion(tls, b'hola')
    assert destino.conexiones == 1, 'La pasarela no abrió conexión con el destino configurado'


def test_P12_reenvio_transparente_en_los_dos_sentidos(pasarela, destino):
    datos = random.randbytes(300_000)       # binario, más grande que cualquier lectura
    recibido = bytearray()
    with conectar(pasarela, ctx_equipo('cliente-a'), timeout=10) as tls:
        # Un solo hilo, enviando y leyendo por turnos: un SSLSocket no debe usarse
        # desde dos hilos a la vez (con TLS 1.3 falla de forma intermitente)
        i = 0
        while i < len(datos):
            n = random.randint(1, 20_000)          # trozos de tamaño irregular
            tls.sendall(datos[i:i + n])
            i = min(i + n, len(datos))
            while len(recibido) < i:               # el eco puede volver en trozos distintos
                trozo = tls.recv(65536)
                if not trozo:
                    break
                recibido += trozo
            if len(recibido) < i:
                break
    assert bytes(destino.recibido) == datos, 'Al destino no llegan exactamente los bytes del cliente'
    assert bytes(recibido) == datos, 'Al cliente no vuelven exactamente los bytes del destino'


def test_P13_destino_inalcanzable(nueva_pasarela):
    p = nueva_pasarela(puerto_libre('127.0.0.1'))   # nadie escucha ahí
    with conectar(p, ctx_equipo('cliente-a')) as tls:
        try:
            r = tls.recv(100)
        except TimeoutError:
            pytest.fail('La pasarela no cerró la conexión del cliente')
        except ssl.SSLError as e:
            pytest.fail(f'Cierre no ordenado (sin close_notify): {e}')
        assert r == b''                     # b'' sin excepción = llegó close_notify
    time.sleep(0.3)
    assert p.registro(), 'El fallo de conexión con el destino no está en el registro'


def test_P14_si_cae_el_destino_se_cierra_el_cliente(nueva_pasarela):
    d = Destino(cerrar_tras_recibir=True)
    try:
        p = nueva_pasarela(d.port)
        with conectar(p, ctx_equipo('cliente-a')) as tls:
            tls.sendall(b'hola')
            try:
                assert tls.recv(100) == b''
            except TimeoutError:
                pytest.fail('La pasarela no cerró el cliente al caer el destino')
    finally:
        d.cerrar()


def test_P14_si_se_va_el_cliente_se_cierra_el_destino(pasarela, destino):
    with conectar(pasarela, ctx_equipo('cliente-a')) as tls:
        peticion(tls, b'hola')
    assert destino.eof.wait(5), 'La pasarela no cerró la conexión con el destino'


def test_P15_registra_las_desconexiones(pasarela, destino):
    with conectar(pasarela, ctx_equipo('cliente-a')) as tls:
        peticion(tls, b'hola')
        antes = pasarela.registro()
    time.sleep(0.5)
    assert len(pasarela.registro()) > len(antes), 'La desconexión del cliente no se registró'


def test_P16_cierre_ordenado(pasarela):
    tls = conectar(pasarela, ctx_equipo('cliente-a'))
    try:
        assert peticion(tls, b'hola') == b'hola'
        pasarela.ctrl_c()
        try:
            assert tls.recv(100) == b'', 'Tras Ctrl+C la conexión siguió abierta'
        except ssl.SSLError as e:
            pytest.fail(f'Cierre sin close_notify: {e}')
        except TimeoutError:
            pytest.fail('Tras Ctrl+C la pasarela no cerró las conexiones abiertas')
    finally:
        tls.close()
    try:
        pasarela.proc.wait(5)
    except subprocess.TimeoutExpired:
        pytest.fail('La pasarela no terminó (quedan hilos vivos)')
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((IP, pasarela.port), timeout=2)
