"""
Ensayo de la demostración del laboratorio con la pasarela de varias interfaces
(clase PasarelaMultiInterfaz, modo «multi» de src/pasarela.py).

Qué comprueba
-------------
MI  Condición del profesor: hasta 4 clientes a la vez, cada uno en la interfaz
    que quiera (las 4 iguales, las 4 distintas o mezcladas).
A   Las 8 pruebas de aceptación de la Fase 5 del enunciado.
D   Lo que hay que enseñar en la demostración (arranque con contraseña, datos
    que muestra el cliente, mensajes del cliente, línea del registro).
P   Requisitos de la Fase 3 que dependen de la clase nueva (P1, P3, P8, P13, P16).

Cómo se ejecuta (desde la carpeta del proyecto, en el PC de la pasarela)
-----------------------------------------------------------------------
    PASARELA_CLAVE=xxxx python3 -m pytest tests/test_pasarela_lab.py -v

Es de caja negra: arranca la pasarela como proceso (igual que en el laboratorio)
con 4 interfaces en puertos libres, y cada interfaz reenvía a un servidor de eco
distinto que hace de robot. No necesita robot, URSim ni pybullet.

Necesita: la IP de "escucha" de config/pasarela_multi.json asignada a este
equipo, las claves de PKI/ (pasarela, cliente-a y cliente-b, con la misma
contraseña) y esa contraseña en PASARELA_CLAVE. Si falta algo, se omiten.

Opcional, con el robot o URSim encendido (interfaces 30001 y 30003):
    ROBOT=10.172.18.80 PASARELA_CLAVE=xxxx python3 -m pytest tests/test_pasarela_lab.py -v
"""

import importlib
import json
import os
import random
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / 'src' / 'pasarela.py'
PKI = RAIZ / 'PKI'
CA_LAB = PKI / 'publico' / 'ca.crt'
CONFIG_MULTI = RAIZ / 'config' / 'pasarela_multi.json'
# La IP de escucha (debe ser la de la SAN del certificado de la pasarela)
IP = json.loads(CONFIG_MULTI.read_text())['escucha']['host']
CLAVE = os.environ.get('PASARELA_CLAVE')
INTERFACES = ('primaria', 'tiempo_real', 'secundaria', 'rtde')
SUITES_TLS13 = ('TLS_AES_256_GCM_SHA384', 'TLS_AES_128_GCM_SHA256',
                'TLS_CHACHA20_POLY1305_SHA256')
CLAVES = [PKI / n / f'{n}.key' for n in ('pasarela', 'cliente-a', 'cliente-b')]


def _ip_local(ip):
    try:
        with socket.create_server((ip, 0)):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not _ip_local(IP), reason=f'{IP} no está asignada a este equipo'),
    pytest.mark.skipif(CLAVE is None, reason='define PASARELA_CLAVE con la contraseña de las claves'),
    pytest.mark.skipif(not all(k.exists() for k in CLAVES),
                       reason='faltan las .key de pasarela, cliente-a o cliente-b en PKI/'),
]


# --------------------------------------------------------------------------
# Piezas de apoyo
# --------------------------------------------------------------------------

def puerto_libre(host):
    """Puerto libre en host y también en 127.0.0.1 (donde escuchan los destinos de prueba),
    para que un puerto de la pasarela no coincida nunca con el de un destino."""
    while True:
        with socket.create_server((host, 0)) as s:
            puerto = s.getsockname()[1]
        try:
            socket.create_server(('127.0.0.1', puerto)).close()
            return puerto
        except OSError:
            continue


class Destino:
    """Servidor TCP de pruebas que hace de una interfaz del robot: eco que
    apunta lo que recibe y puede «caerse» con los clientes conectados."""

    def __init__(self):
        self.sock = socket.create_server(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.recibido = bytearray()
        self.conexiones = 0
        self.abiertas = []
        self.eof = threading.Event()        # la pasarela cerró su lado
        threading.Thread(target=self._aceptar, daemon=True).start()

    def _aceptar(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conexiones += 1
            self.abiertas.append(c)
            threading.Thread(target=self._atender, args=(c,), daemon=True).start()

    def _atender(self, c):
        try:
            while datos := c.recv(65536):
                self.recibido += datos
                c.sendall(datos)
            self.eof.set()
        except OSError:
            self.eof.set()

    def caer(self):
        """El robot se desconecta: corta las conexiones que tiene abiertas."""
        for c in self.abiertas:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def cerrar(self):
        self.sock.close()
        self.caer()


class PasarelaMulti:
    """Arranca «pasarela.py multi» como proceso, con una interfaz por destino."""

    def __init__(self, carpeta, destinos, autorizados=('cliente-a',), clave=CLAVE):
        self.destinos = destinos
        self.autorizados = list(autorizados)
        self.log = carpeta / 'pasarela_multi.log'
        self.salida_path = carpeta / 'salida.txt'
        self.config = carpeta / 'pasarela_multi.json'
        self.clave = clave
        self.proc = None
        self._salida = None
        self.escribir_config()

    def escribir_config(self):
        self.puertos = [puerto_libre(IP) for _ in self.destinos]
        self.config.write_text(json.dumps({
            'escucha': {'host': IP},
            'interfaces': [{'nombre': INTERFACES[i], 'port': self.puertos[i],
                            'destino': {'host': host, 'port': port}}
                           for i, (host, port) in enumerate(self.destinos)],
            'autorizados': self.autorizados,
            'log': str(self.log),
            'pki': {'ca': str(CA_LAB),
                    'cert': str(PKI / 'pasarela' / 'pasarela.crt'),
                    'key': str(PKI / 'pasarela' / 'pasarela.key')},
        }, indent=2))

    def lanzar(self):
        env = dict(os.environ, PASARELA_CLAVE=self.clave)
        self._salida = open(self.salida_path, 'w')
        self.proc = subprocess.Popen(
            [sys.executable, '-u', str(SRC), 'multi', '--config', str(self.config)],
            env=env, stdin=subprocess.DEVNULL, stdout=self._salida, stderr=subprocess.STDOUT,
            cwd=RAIZ, start_new_session=True)
        return self

    def arrancar(self, intentos=3):
        self.lanzar()
        fin = time.monotonic() + 10
        pendientes = list(self.puertos)
        while pendientes and time.monotonic() < fin:
            if self.proc.poll() is not None:
                self._salida.close()
                if 'No se puede escuchar' in self.salida() and intentos > 1:
                    self.escribir_config()         # otro programa cogió uno de los puertos libres
                    return self.arrancar(intentos - 1)
                pytest.fail(f'La pasarela terminó al arrancar:\n{self.salida()}')
            try:
                socket.create_connection((IP, pendientes[0]), timeout=0.5).close()
                pendientes.pop(0)
            except OSError:
                time.sleep(0.1)
        if pendientes:
            pytest.fail(f'La pasarela no escucha en {IP}:{pendientes}:\n{self.salida()}')
        time.sleep(0.2)
        return self

    def salida(self):
        return self.salida_path.read_text() if self.salida_path.exists() else ''

    def registro(self):
        return self.log.read_text() if self.log.exists() else ''

    def esperar_registro(self, *textos, segundos=3):
        """Líneas del registro que contienen todos los textos (la pasarela escribe en sus hilos)."""
        fin = time.monotonic() + segundos
        while True:
            lineas = [l for l in self.registro().splitlines() if all(t in l for t in textos)]
            if lineas or time.monotonic() > fin:
                return lineas
            time.sleep(0.05)

    def ctrl_c(self):
        self.proc.send_signal(signal.SIGINT)

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        if self._salida:
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


def conectar(pasarela, interfaz, ctx, timeout=5):
    """Conexión TLS con la interfaz número «interfaz» (0-3) de la pasarela."""
    tcp = socket.create_connection((IP, pasarela.puertos[interfaz]), timeout=timeout)
    try:
        return ctx.wrap_socket(tcp, server_hostname=IP)
    except Exception:
        tcp.close()
        raise


def peticion(s, datos):
    """Envía y lee la respuesta completa (vale para socket TCP y para TLS)."""
    s.sendall(datos)
    recibido = b''
    while len(recibido) < len(datos):
        trozo = s.recv(65536)
        if not trozo:
            break
        recibido += trozo
    return recibido


def comprobar_rechazado(pasarela, interfaz, ctx):
    """Rechazo en el handshake o, con TLS 1.3, en la primera lectura."""
    try:
        tls = conectar(pasarela, interfaz, ctx)
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
def destinos():
    """Cuatro «interfaces del robot»: un servidor de eco independiente por interfaz."""
    lista = [Destino() for _ in INTERFACES]
    yield lista
    for d in lista:
        d.cerrar()


@pytest.fixture
def pasarela(tmp_path, destinos):
    p = PasarelaMulti(tmp_path, [('127.0.0.1', d.port) for d in destinos]).arrancar()
    yield p
    p.parar()


@pytest.fixture(scope='session')
def pki_ajena(tmp_path_factory):
    """La PKI de «otro estudiante»: otra CA y un cliente-a válido solo para ella."""
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


# --- El cliente de la práctica (clienteTLS.py), aunque falte la simulación ---

def _atributo_falso(atributo):
    if atributo.startswith('__'):
        raise AttributeError(atributo)
    return 0


@pytest.fixture(scope='session')
def cliente_tls():
    """Módulo clienteTLS; pybullet y UR3e_rg2 se sustituyen por módulos vacíos si faltan."""
    for nombre in ('pybullet', 'UR3e_rg2', 'UR3e_rg2.dynamic_ur3e'):
        try:
            importlib.import_module(nombre)
        except ImportError:
            falso = types.ModuleType(nombre)
            falso.__getattr__ = _atributo_falso
            falso.UR3eRG2 = object
            sys.modules[nombre] = falso
    if str(RAIZ / 'src') not in sys.path:
        sys.path.insert(0, str(RAIZ / 'src'))
    return importlib.import_module('clienteTLS')


class SinReintento(Exception):
    """Sustituye al sleep() del reintento del cliente: un intento y se para."""


@pytest.fixture
def buffer_tls(cliente_tls, monkeypatch):
    """Devuelve conectar(interfaz, ctx) -> (BufferTLS, socket o None), con un solo intento."""
    def parar(_):
        raise SinReintento()
    monkeypatch.setattr(cliente_tls, 'sleep', parar)

    def crear(pasarela, interfaz, ctx):
        b = cliente_tls.BufferTLS(IP, pasarela.puertos[interfaz], ctx)
        try:
            return b, b.conectar_con_reintento(timeout=2)
        except SinReintento:
            return b, None
    return crear


def ctx_practica(nombre='cliente-a', ca=CA_LAB):
    """El contexto que crea el cliente de la práctica (SSL_CLIENT de pasarela.py)."""
    sys.path.insert(0, str(RAIZ / 'src'))
    from pasarela import SSL_CLIENT
    return SSL_CLIENT(PKI / nombre / f'{nombre}.crt', PKI / nombre / f'{nombre}.key', ca, CLAVE)


# ==========================================================================
# MI · Condición del profesor: 4 clientes, cada uno en la interfaz que quiera
# ==========================================================================

REPARTOS = {
    'cuatro interfaces distintas': (0, 1, 2, 3),
    'los cuatro en la misma':      (0, 0, 0, 0),
    'dos y dos':                   (0, 0, 1, 1),
    'mezcla 2+1+1':                (2, 3, 3, 0),
}


@pytest.mark.parametrize('reparto', REPARTOS.values(), ids=REPARTOS.keys())
def test_MI_cuatro_clientes_a_la_vez_en_cualquier_interfaz(pasarela, destinos, reparto):
    todos_conectados = threading.Barrier(4)
    resultados = {}

    def cliente(n, interfaz):
        try:
            with conectar(pasarela, interfaz, ctx_equipo('cliente-a')) as tls:
                assert tls.version() == 'TLSv1.3'
                todos_conectados.wait(10)          # los 4 están dentro A LA VEZ
                for i in range(20):
                    msg = f'[cliente {n} interfaz {interfaz} mensaje {i}]'.encode()
                    assert peticion(tls, msg) == msg
            resultados[n] = True
        except Exception as e:                     # noqa: BLE001
            resultados[n] = repr(e)

    hilos = [threading.Thread(target=cliente, args=(n, i)) for n, i in enumerate(reparto)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(20)
    assert resultados == {0: True, 1: True, 2: True, 3: True}, f'Algún cliente falló: {resultados}'

    # Cada interfaz ha llevado a SU destino solo a SUS clientes, con todos sus bytes
    for i, d in enumerate(destinos):
        suyos = [n for n, interfaz in enumerate(reparto) if interfaz == i]
        assert d.conexiones == len(suyos), \
            f'La interfaz {INTERFACES[i]} abrió {d.conexiones} conexiones con el destino, no {len(suyos)}'
        for n in range(4):
            esperado = 20 if n in suyos else 0
            visto = bytes(d.recibido).count(f'[cliente {n} interfaz {i} '.encode())
            assert visto == esperado, \
                f'Al destino de {INTERFACES[i]} le llegaron {visto} mensajes del cliente {n} (esperado {esperado})'


def test_MI_un_mismo_cliente_usa_dos_interfaces_a_la_vez(pasarela, destinos):
    # Lo que hace clienteTLS.py con un robots_tls.json de dos entradas
    with conectar(pasarela, 0, ctx_equipo('cliente-a')) as a, \
         conectar(pasarela, 1, ctx_equipo('cliente-a')) as b:
        assert peticion(a, b'por la primaria') == b'por la primaria'
        assert peticion(b, b'por tiempo real') == b'por tiempo real'
        assert peticion(a, b'otra vez') == b'otra vez'
    assert bytes(destinos[0].recibido) == b'por la primariaotra vez'
    assert bytes(destinos[1].recibido) == b'por tiempo real'


def test_MI_el_registro_dice_la_interfaz_de_cada_cliente(pasarela):
    for i in range(4):
        with conectar(pasarela, i, ctx_equipo('cliente-a')) as tls:
            suite = tls.cipher()[0]
            mi_ip = tls.getsockname()[0]
            peticion(tls, b'hola')
    for nombre in INTERFACES:
        lineas = pasarela.esperar_registro(f'interfaz={nombre}', 'cn=cliente-a', 'conectado')
        assert lineas, f'No hay línea de conexión de la interfaz {nombre}:\n{pasarela.registro()}'
        assert all(d in lineas[0] for d in (f'ip={mi_ip}', 'TLSv1.3', suite)), \
            f'A la línea le falta IP, versión o suite: {lineas[0]}'
        assert pasarela.esperar_registro(f'interfaz={nombre}', 'conexión cerrada'), \
            f'No se registró el cierre en la interfaz {nombre}'
    assert 'Logging error' not in pasarela.salida(), pasarela.salida()


def test_MI_una_interfaz_sin_robot_no_afecta_a_las_demas(tmp_path, destinos):
    # La interfaz 1 apunta a un puerto donde no escucha nadie
    destino_de = [('127.0.0.1', d.port) for d in destinos]
    destino_de[1] = ('127.0.0.1', puerto_libre('127.0.0.1'))
    p = PasarelaMulti(tmp_path, destino_de).arrancar()
    try:
        with conectar(p, 1, ctx_equipo('cliente-a')) as caida:
            assert caida.recv(100) == b''          # P13: cierre TLS ordenado
        assert p.esperar_registro('interfaz=tiempo_real', 'inalcanzable'), p.registro()
        for i in (0, 2, 3):
            with conectar(p, i, ctx_equipo('cliente-a')) as tls:
                assert peticion(tls, b'sigo vivo') == b'sigo vivo'
    finally:
        p.parar()


def test_MI_la_autorizacion_vale_en_todas_las_interfaces(pasarela, destinos):
    for i in range(4):
        comprobar_rechazado(pasarela, i, ctx_equipo('cliente-b'))
        comprobar_rechazado(pasarela, i, contexto_cliente())      # sin certificado
    time.sleep(0.3)
    assert [d.conexiones for d in destinos] == [0, 0, 0, 0]


# ==========================================================================
# A · Pruebas de aceptación de la Fase 5 (las que se repiten en la demostración)
# ==========================================================================

def test_A1_mismas_operaciones_mismos_resultados_que_en_directo(pasarela, destinos):
    operaciones = [b'movej([0,0,0,0,0,0], a=0.5, v=0.3)\n', bytes(range(256)) * 40,
                   random.randbytes(50_000), b'\x00', b'stopj(2)\n']
    for i, d in enumerate(destinos):
        with socket.create_connection(('127.0.0.1', d.port), timeout=5) as directo:
            en_directo = [peticion(directo, op) for op in operaciones]
        with conectar(pasarela, i, ctx_equipo('cliente-a')) as tls:
            por_pasarela = [peticion(tls, op) for op in operaciones]
        assert por_pasarela == en_directo, f'Resultados distintos en la interfaz {INTERFACES[i]}'
        # y al «robot» le llega exactamente lo mismo las dos veces (reenvío sin modificar)
        assert bytes(d.recibido) == b''.join(operaciones) * 2


def test_A2_cliente_b_handshake_correcto_cierre_inmediato_y_registro(pasarela, destinos):
    tls = conectar(pasarela, 0, ctx_equipo('cliente-b'))      # el handshake funciona
    with tls:
        assert tls.version() == 'TLSv1.3'
        mi_ip = tls.getsockname()[0]
        inicio = time.monotonic()
        try:
            assert tls.recv(100) == b'', 'La pasarela no cerró a cliente-b'
        except ssl.SSLError as e:
            pytest.fail(f'Cierre sin close_notify: {e}')
        assert time.monotonic() - inicio < 2, 'El cierre no fue inmediato'
    lineas = pasarela.esperar_registro('cn=cliente-b', 'no autorizado')
    assert lineas, f'El rechazo de cliente-b no está en el registro con su motivo:\n{pasarela.registro()}'
    assert f'ip={mi_ip}' in lineas[0] and 'TLSv1.3' in lineas[0], lineas[0]
    assert destinos[0].conexiones == 0, 'Se abrió conexión con el robot para cliente-b'


def test_A3_cliente_sin_certificado_rechazado_en_el_handshake(pasarela, destinos):
    comprobar_rechazado(pasarela, 0, contexto_cliente())
    assert pasarela.esperar_registro('handshake fallido', 'did not return a certificate'), \
        f'El registro no dice que faltaba el certificado:\n{pasarela.registro()}'
    assert destinos[0].conexiones == 0


def test_A4_certificado_serverauth_rechazado_por_uso_no_permitido(pasarela, destinos):
    # pasarela.crt lo firma la CA del laboratorio, pero su uso es serverAuth, no clientAuth
    comprobar_rechazado(pasarela, 0, ctx_equipo('pasarela'))
    assert pasarela.esperar_registro('handshake fallido', 'unsuitable certificate purpose'), \
        f'El registro no dice que el uso del certificado no vale:\n{pasarela.registro()}'
    assert destinos[0].conexiones == 0


def test_A5_el_robot_se_desconecta_con_un_cliente_conectado(pasarela, destinos, buffer_tls):
    b, s = buffer_tls(pasarela, 0, ctx_practica())
    with s:
        s.sendall(struct.pack('>iB', 9, 16) + b'ABCD')         # un mensaje con cabecera primaria
        assert b.recibir_mensajes(5), 'El cliente no recibió nada antes de la caída'
        otro = conectar(pasarela, 1, ctx_equipo('cliente-a'))  # cliente de otra interfaz
        destinos[0].caer()
        # el cliente informa de que la conexión se ha cerrado
        with pytest.raises(ConnectionError, match='la pasarela cerró la conexión'):
            while True:
                b.recibir_mensajes(5)
    # la pasarela lo detecta y lo registra
    assert pasarela.esperar_registro('interfaz=primaria', 'conexión cerrada'), pasarela.registro()
    # y a los clientes de las otras interfaces no les pasa nada
    with otro:
        assert peticion(otro, b'sigo') == b'sigo'


@pytest.mark.parametrize('bloqueo', ['no hace el handshake', 'deja de leer'])
def test_A6_cuatro_clientes_y_uno_bloqueado(pasarela, bloqueo):
    if bloqueo == 'no hace el handshake':
        bloqueado = socket.create_connection((IP, pasarela.puertos[0]))
    else:
        # Autorizado, envía mucho y no lee el eco: los hilos de SU conexión se quedan
        # parados en sendall; los de los demás clientes no deben notarlo
        bloqueado = conectar(pasarela, 0, ctx_equipo('cliente-a'))

        def inundar():
            try:
                bloqueado.sendall(bytes(16_000_000))
            except OSError:
                pass
        threading.Thread(target=inundar, daemon=True).start()
        time.sleep(1)
    try:
        resultados = {}

        def cliente(n, interfaz):
            try:
                with conectar(pasarela, interfaz, ctx_equipo('cliente-a'), timeout=3) as tls:
                    for i in range(30):
                        msg = f'cliente {n} mensaje {i}'.encode()
                        assert peticion(tls, msg) == msg
                resultados[n] = True
            except Exception as e:                 # noqa: BLE001
                resultados[n] = repr(e)

        # uno en la misma interfaz que el bloqueado y dos en otras
        hilos = [threading.Thread(target=cliente, args=(n, i)) for n, i in enumerate((0, 0, 1))]
        inicio = time.monotonic()
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        assert resultados == {0: True, 1: True, 2: True}, \
            f'Con un cliente bloqueado ({bloqueo}), los otros tres no funcionan: {resultados}'
        assert time.monotonic() - inicio < 5, 'El cliente bloqueado retrasa a los demás'
    finally:
        bloqueado.close()


def test_A7_cliente_con_la_ca_de_otro_estudiante(pasarela, pki_ajena, buffer_tls, capsys):
    # Con el módulo ssl: falla la validación EN EL CLIENTE, y la causa está en el error
    ctx = contexto_cliente(PKI / 'cliente-a' / 'cliente-a.crt', PKI / 'cliente-a' / 'cliente-a.key',
                           ca=pki_ajena / 'ca.crt')
    with pytest.raises(ssl.SSLCertVerificationError) as error:
        conectar(pasarela, 0, ctx)
    assert error.value.verify_message in ('self-signed certificate in certificate chain',
                                          'unable to get local issuer certificate')
    # Con el cliente de la práctica: el mensaje que se enseña en la demostración
    capsys.readouterr()
    _, s = buffer_tls(pasarela, 0, ctx_practica(ca=pki_ajena / 'ca.crt'))
    assert s is None, 'El cliente se conectó con una CA que no es la del laboratorio'
    salida = capsys.readouterr().out
    assert 'certificado de la pasarela no válido' in salida
    assert error.value.verify_message in salida, f'El mensaje no indica la causa:\n{salida}'


def test_A7_la_pasarela_tampoco_acepta_un_cliente_de_otra_ca(pasarela, destinos, pki_ajena):
    ctx = contexto_cliente(pki_ajena / 'c.crt', pki_ajena / 'c.key', password=None)
    comprobar_rechazado(pasarela, 0, ctx)
    assert destinos[0].conexiones == 0


def test_A8_tiempo_directo_y_a_traves_de_la_pasarela(pasarela, destinos, capsys):
    msg = b'get_actual_joint_positions()\n'

    def medir(s, n=200):
        tiempos = []
        for _ in range(n):
            t0 = time.perf_counter()
            assert peticion(s, msg) == msg
            tiempos.append(time.perf_counter() - t0)
        return sorted(tiempos)[n // 2] * 1000                   # mediana, en ms

    t0 = time.perf_counter()
    directo = socket.create_connection(('127.0.0.1', destinos[0].port), timeout=5)
    conexion_directa = (time.perf_counter() - t0) * 1000
    with directo:
        ms_directo = medir(directo)
    t0 = time.perf_counter()
    tls = conectar(pasarela, 0, ctx_equipo('cliente-a'))
    conexion_tls = (time.perf_counter() - t0) * 1000
    with tls:
        ms_pasarela = medir(tls)
    with capsys.disabled():
        print(f'\n    A8 conexión: directa {conexion_directa:.2f} ms | pasarela (TCP + handshake TLS) '
              f'{conexion_tls:.2f} ms'
              f'\n    A8 petición (mediana de 200): directa {ms_directo:.3f} ms | pasarela '
              f'{ms_pasarela:.3f} ms | diferencia {ms_pasarela - ms_directo:+.3f} ms')
    assert ms_directo > 0 and ms_pasarela > 0


def test_A8_el_cliente_de_la_practica_muestra_los_tiempos(pasarela, destinos, buffer_tls,
                                                          cliente_tls, capsys):
    _, s = buffer_tls(pasarela, 0, ctx_practica())
    s.close()
    try:
        cliente_tls.BufferTLS('127.0.0.1', destinos[0].port, None).conectar_con_reintento().close()
    except SinReintento:
        pytest.fail('El modo directo (sin TLS) no conectó')
    salida = capsys.readouterr().out
    assert 'handshake TLS' in salida and 'sin TLS' in salida and ' ms' in salida, salida


# ==========================================================================
# D · Lo que se enseña en la demostración
# ==========================================================================

def test_D1_la_pasarela_arranca_solo_con_la_contrasena_correcta(tmp_path, destinos):
    p = PasarelaMulti(tmp_path, [('127.0.0.1', destinos[0].port)], clave='no-es-la-clave').lanzar()
    try:
        assert p.proc.wait(10) is not None
    except subprocess.TimeoutExpired:
        pytest.fail('La pasarela siguió arrancada con una contraseña incorrecta')
    finally:
        p.parar()
    assert 'Contraseña incorrecta' in p.salida(), p.salida()
    with pytest.raises(OSError):
        socket.create_connection((IP, p.puertos[0]), timeout=1)


def test_D1_la_contrasena_no_esta_en_el_codigo_ni_en_la_configuracion():
    primera = (PKI / 'pasarela' / 'pasarela.key').read_text().splitlines()[0]
    assert primera == '-----BEGIN ENCRYPTED PRIVATE KEY-----', 'pasarela.key no está cifrada'
    ficheros = [SRC, RAIZ / 'src' / 'clienteTLS.py', *(RAIZ / 'config').glob('*')]
    con_clave = [str(f.relative_to(RAIZ)) for f in ficheros
                 if f.is_file() and CLAVE in f.read_text(errors='ignore')]
    assert not con_clave, f'La contraseña aparece escrita en: {con_clave}'


def test_D2_cliente_a_muestra_version_suite_y_certificado(pasarela, buffer_tls, capsys):
    for i in range(4):
        capsys.readouterr()
        _, s = buffer_tls(pasarela, i, ctx_practica())
        assert s is not None, f'cliente-a no conectó con la interfaz {INTERFACES[i]}'
        s.close()
        salida = capsys.readouterr().out
        for dato in ('TLSv1.3', 'TLS_', 'CN=pasarela-ur3e', 'caduca'):
            assert dato in salida, f'Falta {dato!r} en lo que muestra el cliente:\n{salida}'


def test_D4_cliente_b_mensaje_del_cliente_y_linea_del_registro(pasarela, buffer_tls):
    b, s = buffer_tls(pasarela, 0, ctx_practica('cliente-b'))
    assert s is not None, 'Con cliente-b el handshake debe funcionar'
    with s, pytest.raises(ConnectionError, match='la pasarela cerró la conexión'):
        b.recibir_mensajes(5)
    assert pasarela.esperar_registro('interfaz=primaria', 'cn=cliente-b', 'motivo=no autorizado')


def test_D5_cliente_sin_certificado_mensaje_del_cliente(pasarela, buffer_tls):
    # TLS 1.3 (C9): el rechazo no aparece al conectar, sino en la primera lectura
    b, s = buffer_tls(pasarela, 0, contexto_cliente())
    assert s is not None
    with s, pytest.raises(ConnectionError, match='rechazó el certificado del cliente'):
        b.recibir_mensajes(5)


def test_D5_los_mensajes_del_cliente_son_distintos_en_cada_caso(cliente_tls):
    codigo = Path(cliente_tls.__file__).read_text()
    for texto in ('inalcanzable', 'conexión rechazada', 'certificado de la pasarela no válido',
                  'rechazó el certificado del cliente', 'la pasarela cerró la conexión'):
        assert texto in codigo


# ==========================================================================
# P · Requisitos de la Fase 3 que dependen de la clase nueva
# ==========================================================================

def test_P1_todas_las_interfaces_escuchan_solo_en_la_ip_de_la_pasarela(pasarela):
    for puerto in pasarela.puertos:
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(('127.0.0.1', puerto), timeout=2)


def test_P3_solo_tls13_en_todas_las_interfaces(pasarela):
    for i in range(4):
        with conectar(pasarela, i, ctx_equipo('cliente-a')) as tls:
            assert tls.version() == 'TLSv1.3' and tls.cipher()[0] in SUITES_TLS13
        with pytest.raises(ssl.SSLError):
            conectar(pasarela, i, ctx_equipo('cliente-a', max_version=ssl.TLSVersion.TLSv1_2))


def test_P5_no_usa_las_ca_del_sistema():
    codigo = SRC.read_text()
    for prohibido in ('create_default_context', 'load_default_certs'):
        assert prohibido not in codigo, f'{prohibido} carga las CA del sistema'


def test_P8_handshake_con_tiempo_limite(pasarela):
    inicio = time.monotonic()
    with socket.create_connection((IP, pasarela.puertos[3]), timeout=20) as mudo:
        try:
            mudo.recv(1)                    # no envía nada: espera a que lo corten
        except ConnectionResetError:
            pass
    assert time.monotonic() - inicio < 15, 'La pasarela no corta un handshake que no llega'


def test_P12_reenvio_binario_en_trozos_irregulares(pasarela, destinos):
    datos = random.randbytes(300_000)
    recibido = bytearray()
    with conectar(pasarela, 2, ctx_equipo('cliente-a'), timeout=10) as tls:
        i = 0
        while i < len(datos):
            n = random.randint(1, 20_000)
            tls.sendall(datos[i:i + n])
            i = min(i + n, len(datos))
            while len(recibido) < i:
                trozo = tls.recv(65536)
                if not trozo:
                    break
                recibido += trozo
            if len(recibido) < i:
                break
    assert bytes(destinos[2].recibido) == datos
    assert bytes(recibido) == datos


def test_P14_si_se_va_el_cliente_se_cierra_la_conexion_con_el_robot(pasarela, destinos):
    with conectar(pasarela, 1, ctx_equipo('cliente-a')) as tls:
        peticion(tls, b'hola')
    assert destinos[1].eof.wait(5), 'La pasarela no cerró la conexión con el destino'


def test_P16_ctrl_c_cierra_todas_las_interfaces_ordenadamente(pasarela):
    clientes = [conectar(pasarela, i, ctx_equipo('cliente-a')) for i in range(4)]
    try:
        for c in clientes:
            assert peticion(c, b'hola') == b'hola'
        pasarela.ctrl_c()
        for i, c in enumerate(clientes):
            try:
                assert c.recv(100) == b'', f'Tras Ctrl+C la interfaz {INTERFACES[i]} siguió abierta'
            except ssl.SSLError as e:
                pytest.fail(f'Cierre sin close_notify en {INTERFACES[i]}: {e}')
            except TimeoutError:
                pytest.fail(f'Tras Ctrl+C no se cerró el cliente de {INTERFACES[i]}')
    finally:
        for c in clientes:
            c.close()
    try:
        pasarela.proc.wait(8)
    except subprocess.TimeoutExpired:
        pytest.fail('La pasarela no terminó (quedan hilos vivos)')
    for puerto in pasarela.puertos:
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection((IP, puerto), timeout=2)


def test_P_la_configuracion_de_ejemplo_es_coherente():
    cfg = json.loads(CONFIG_MULTI.read_text())
    puertos = [i['port'] for i in cfg['interfaces']]
    assert len(puertos) == len(set(puertos)), 'Dos interfaces con el mismo puerto de escucha'
    assert len({i['nombre'] for i in cfg['interfaces']}) == len(puertos), 'Nombres de interfaz repetidos'
    assert 'cliente-a' in cfg['autorizados'] and 'cliente-b' not in cfg['autorizados']
    assert not any('clave' in k.lower() or 'pass' in k.lower() for k in cfg), 'La contraseña no va en el JSON'


# ==========================================================================
# Opcional · Con el robot o URSim (ROBOT=ip): interfaces primaria y tiempo real
# ==========================================================================

@pytest.mark.skipif('ROBOT' not in os.environ, reason='define ROBOT=ip para probar con el robot o URSim')
def test_robot_real_por_las_dos_interfaces_a_la_vez(tmp_path):
    robot = os.environ['ROBOT']
    p = PasarelaMulti(tmp_path, [(robot, 30001), (robot, 30003)]).arrancar()

    def cabecera(s):
        datos = b''
        while len(datos) < 5:
            trozo = s.recv(5 - len(datos))
            assert trozo, 'Conexión cerrada antes de recibir un mensaje del robot'
            datos += trozo
        return struct.unpack('>iB', datos)

    try:
        # los dos clientes conectados a la vez, cada uno en su interfaz
        with conectar(p, 0, ctx_equipo('cliente-a')) as primaria, \
             conectar(p, 1, ctx_equipo('cliente-a')) as tiempo_real:
            por_pasarela = cabecera(primaria), cabecera(tiempo_real)
        with socket.create_connection((robot, 30001), timeout=5) as d1, \
             socket.create_connection((robot, 30003), timeout=5) as d2:
            directo = cabecera(d1), cabecera(d2)
        # primaria: el primer mensaje (versión) tiene la misma longitud y tipo
        assert por_pasarela[0] == directo[0], f'Primaria: {por_pasarela[0]} != {directo[0]}'
        # tiempo real: todos los mensajes miden lo mismo
        assert por_pasarela[1][0] == directo[1][0], 'Tiempo real: longitud de mensaje distinta'
    finally:
        p.parar()
