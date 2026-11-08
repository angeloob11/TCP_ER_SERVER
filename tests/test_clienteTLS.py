"""
Pruebas de la Fase 4 del enunciado: requisitos C1-C10 del cliente seguro (src/clienteTLS.py).

No hace falta robot ni pybullet: el cliente se prueba contra la pasarela real
(arrancada como proceso, igual que en test_pasarela.py) con un servidor de eco
como destino. Si pybullet o UR3e_rg2 no están instalados, se sustituyen por
módulos vacíos solo para poder importar multi_robot.py (la simulación no se usa).

Prueba opcional con robot real o URSim (la única que lo necesita):

    ROBOT=192.168.1.140:30001 PASARELA_CLAVE=xxxx python3 -m pytest tests/ -v

Requisitos: los mismos que test_pasarela.py (IP de la pasarela en este equipo y
PASARELA_CLAVE con la contraseña de las claves).
"""

import importlib
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import time
import types

import pytest

from test_pasarela import CLAVE, IP, PKI, RAIZ, Destino, Pasarela, puerto_libre, pytestmark  # noqa: F401

# --- Importar clienteTLS aunque falte la simulación -------------------------
def _atributo_falso(atributo):                     # p.B3G_RETURN, etc. valen 0
    if atributo.startswith('__'):
        raise AttributeError(atributo)
    return 0


for nombre in ('pybullet', 'UR3e_rg2', 'UR3e_rg2.dynamic_ur3e'):
    try:
        importlib.import_module(nombre)
    except ImportError:
        falso = types.ModuleType(nombre)
        falso.__getattr__ = _atributo_falso
        falso.UR3eRG2 = object
        sys.modules[nombre] = falso

sys.path.insert(0, str(RAIZ / 'src'))
import clienteTLS  # noqa: E402
from clienteTLS import BufferTLS, RobotTLS, LectorPrimariaTLS, LectorTiempoRealTLS  # noqa: E402
from multi_robot import Robot  # noqa: E402
from pasarela import SSL_CLIENT  # noqa: E402

CA_LAB = PKI / 'publico' / 'ca.crt'


# --------------------------------------------------------------------------
# Piezas de apoyo
# --------------------------------------------------------------------------

class SinReintento(Exception):
    """Sustituye al sleep() del reintento: un intento, su mensaje, y se para."""


@pytest.fixture
def un_intento(monkeypatch):
    def parar(_):
        raise SinReintento()
    monkeypatch.setattr(clienteTLS, 'sleep', parar)


def ctx_cliente(nombre='cliente-a', ca=CA_LAB, password=CLAVE):
    return SSL_CLIENT(PKI / nombre / f'{nombre}.crt', PKI / nombre / f'{nombre}.key', ca, password)


def mensaje_primaria(datos=b'ABCD', tipo=16):
    """Mensaje con la cabecera de la interfaz primaria: longitud(4) + tipo(1)."""
    return struct.pack('>iB', 5 + len(datos), tipo) + datos


def conectar(buffer):
    """conectar_con_reintento con un solo intento; devuelve el socket o None."""
    try:
        return buffer.conectar_con_reintento(timeout=2)
    except SinReintento:
        return None


@pytest.fixture(scope='module')
def eco():
    d = Destino()
    yield d
    d.cerrar()


@pytest.fixture(scope='module')
def pasarela(tmp_path_factory, eco):
    p = Pasarela(tmp_path_factory.mktemp('pasarela'), eco.port).arrancar()
    yield p
    p.parar()


@pytest.fixture(scope='module')
def pasarela_sin_robot(tmp_path_factory):
    p = Pasarela(tmp_path_factory.mktemp('sin_robot'), puerto_libre('127.0.0.1')).arrancar()
    yield p
    p.parar()


@pytest.fixture(scope='module')
def ca_ajena(tmp_path_factory):
    d = tmp_path_factory.mktemp('ca_ajena')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
                    '-keyout', d / 'ca.key', '-out', d / 'ca.crt', '-subj', '/CN=CA ajena'],
                   check=True, capture_output=True)
    return d / 'ca.crt'


# --------------------------------------------------------------------------
# C1-C4: configuración, autenticación mutua, confianza y versión
# --------------------------------------------------------------------------

def test_C1_rutas_y_destino_configurables():
    cfg = json.loads((RAIZ / 'config' / 'cliente.json').read_text())
    assert {'ca', 'cert', 'key'} <= cfg['pki'].keys()
    for r in json.loads((RAIZ / 'config' / 'robots_tls.json').read_text()):
        assert {'host', 'puerto', 'interfaz'} <= r.keys()


def test_C1_contrasena_no_esta_en_el_codigo():
    codigo = (RAIZ / 'src' / 'clienteTLS.py').read_text()
    assert CLAVE not in codigo
    assert 'pedir_clave' in codigo, 'La contraseña debe pedirse al usuario (getpass) o leerse del entorno'


def test_C2_C3_C4_contexto_del_cliente():
    ctx = ctx_cliente()
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3          # C4
    assert ctx.verify_mode == ssl.CERT_REQUIRED                   # C3: valida a la pasarela
    assert ctx.check_hostname                                     # C3: comprueba la SAN
    assert ctx.cert_store_stats()['x509_ca'] == 1, 'Solo debe confiar en la CA del laboratorio'


def test_C2_presenta_certificado_y_conecta(pasarela, un_intento):
    s = conectar(BufferTLS(IP, pasarela.port, ctx_cliente()))
    assert s is not None
    with s:
        assert s.version() == 'TLSv1.3'
        fin = time.monotonic() + 3                                # la pasarela escribe el log en su hilo
        while 'cliente-a' not in pasarela.registro() and time.monotonic() < fin:
            time.sleep(0.05)
    assert 'cliente-a' in pasarela.registro()                     # la pasarela vio su certificado


def test_C3_rechaza_ip_fuera_de_la_san(pasarela, un_intento, capsys):
    # Misma pasarela, pero pidiendo validar otro nombre: la SAN no coincide
    ctx = ctx_cliente()
    b = BufferTLS(IP, pasarela.port, ctx)
    original = ctx.wrap_socket
    ctx.wrap_socket = lambda tcp, server_hostname: original(tcp, server_hostname='10.0.0.1')
    assert conectar(b) is None
    assert 'certificado de la pasarela no válido' in capsys.readouterr().out


# --------------------------------------------------------------------------
# C5: lo que se muestra al conectar
# --------------------------------------------------------------------------

def test_C5_muestra_version_suite_sujeto_y_caducidad(pasarela, un_intento, capsys):
    s = conectar(BufferTLS(IP, pasarela.port, ctx_cliente()))
    s.close()
    salida = capsys.readouterr().out
    for dato in ('TLSv1.3', 'TLS_', 'CN=pasarela-ur3e', 'caduca'):
        assert dato in salida, f'Falta {dato!r} en:\n{salida}'


# --------------------------------------------------------------------------
# C6: el resto del cliente se reutiliza sin cambios
# --------------------------------------------------------------------------

def test_C6_mensajes_iguales_a_traves_de_la_pasarela(pasarela, un_intento):
    b = BufferTLS(IP, pasarela.port, ctx_cliente())
    with conectar(b) as s:
        enviados = [mensaje_primaria(b'x' * n) for n in (1, 100, 3000)]
        s.sendall(b''.join(enviados))
        recibidos = []
        while len(recibidos) < len(enviados):
            recibidos += b.recibir_mensajes(5)       # el troceado lo hace el Buffer original
    assert recibidos == enviados


def test_C6_polimorfismo_con_las_clases_originales():
    robots = RobotTLS.cargar_robots(RAIZ / 'config' / 'robots_tls.json', ctx_cliente())
    for r in robots:
        assert isinstance(r, Robot)                  # MultiRobot lo acepta como un Robot más
        assert isinstance(r.lector, (LectorPrimariaTLS, LectorTiempoRealTLS))
        assert isinstance(r.lector.buffer, BufferTLS)


# --------------------------------------------------------------------------
# C7: conexión directa sin TLS
# --------------------------------------------------------------------------

def test_C7_modo_directo_sin_tls(eco, un_intento, capsys):
    b = BufferTLS('127.0.0.1', eco.port, None)
    with conectar(b) as s:
        assert not isinstance(s, ssl.SSLSocket)
        s.sendall(mensaje_primaria())
        assert b.recibir_mensajes(5) == [mensaje_primaria()]
    assert 'sin TLS' in capsys.readouterr().out


def test_C7_robots_json_directo_se_sigue_pudiendo_cargar():
    for r in RobotTLS.cargar_robots(RAIZ / 'config' / 'robots.json', None):
        assert r.lector.buffer.ctx is None


# --------------------------------------------------------------------------
# C8 y C9: un mensaje distinto para cada situación
# --------------------------------------------------------------------------

def test_C8_pasarela_inalcanzable(un_intento, capsys):
    assert conectar(BufferTLS('10.255.255.1', 3000, ctx_cliente())) is None
    assert 'inalcanzable' in capsys.readouterr().out


def test_C8_conexion_rechazada(un_intento, capsys):
    assert conectar(BufferTLS(IP, puerto_libre(IP), ctx_cliente())) is None
    assert 'conexión rechazada' in capsys.readouterr().out


def test_C8_certificado_de_la_pasarela_no_valido(pasarela, un_intento, ca_ajena, capsys):
    assert conectar(BufferTLS(IP, pasarela.port, ctx_cliente(ca=ca_ajena))) is None
    salida = capsys.readouterr().out
    assert 'certificado de la pasarela no válido' in salida
    assert 'self-signed certificate' in salida or 'unable to get local issuer' in salida  # el motivo


def test_C8_C9_certificado_del_cliente_rechazado(pasarela, un_intento):
    # pasarela.crt es serverAuth: la pasarela lo rechaza, y con TLS 1.3 se ve en la PRIMERA LECTURA
    b = BufferTLS(IP, pasarela.port, ctx_cliente('pasarela'))
    s = conectar(b)
    assert s is not None, 'El rechazo no debe aparecer al conectar (C9)'
    with s, pytest.raises(ConnectionError, match='rechazó el certificado del cliente'):
        b.recibir_mensajes(5)


def test_C8_cerrada_por_no_autorizado(pasarela, un_intento):
    b = BufferTLS(IP, pasarela.port, ctx_cliente('cliente-b'))
    with conectar(b), pytest.raises(ConnectionError, match='la pasarela cerró la conexión'):
        b.recibir_mensajes(5)


def test_C8_cerrada_por_robot_no_disponible(pasarela_sin_robot, un_intento):
    b = BufferTLS(IP, pasarela_sin_robot.port, ctx_cliente())
    with conectar(b), pytest.raises(ConnectionError, match='robot no disponible'):
        b.recibir_mensajes(5)


def test_C8_los_cinco_mensajes_son_distintos():
    codigo = (RAIZ / 'src' / 'clienteTLS.py').read_text()
    for texto in ('inalcanzable', 'conexión rechazada', 'certificado de la pasarela no válido',
                  'rechazó el certificado del cliente', 'la pasarela cerró la conexión'):
        assert texto in codigo


# --------------------------------------------------------------------------
# C10: tiempos
# --------------------------------------------------------------------------

def test_C10_mide_tiempos_con_y_sin_tls(pasarela, eco, un_intento, capsys):
    conectar(BufferTLS(IP, pasarela.port, ctx_cliente())).close()
    conectar(BufferTLS('127.0.0.1', eco.port, None)).close()
    salida = capsys.readouterr().out
    assert 'handshake TLS' in salida and ' ms' in salida
    assert 'TCP' in salida


# --------------------------------------------------------------------------
# Opcional: robot real o URSim (variable ROBOT=ip:puerto de la interfaz primaria)
# --------------------------------------------------------------------------

@pytest.mark.skipif('ROBOT' not in os.environ, reason='define ROBOT=ip:puerto para probar con un robot')
def test_robot_real_a_traves_de_la_pasarela(tmp_path):
    host, puerto = os.environ['ROBOT'].rsplit(':', 1)
    p = Pasarela(tmp_path, int(puerto))
    cfg = json.loads(p.config.read_text())
    cfg['destino']['host'] = host
    p.config.write_text(json.dumps(cfg))
    p.arrancar()
    try:
        robot = RobotTLS('robot', IP, p.port, [0] * 6, 'primaria', ctx_cliente())
        robot.iniciar_lector()
        fin = time.monotonic() + 10
        while time.monotonic() < fin:
            with robot.Robot_state.lock:
                if robot.Robot_state.isProgramRunning is not None:
                    break
            time.sleep(0.1)
        with robot.Robot_state.lock:
            assert robot.Robot_state.isProgramRunning is not None, \
                'No llegó el estado del robot a través de la pasarela en 10 s'
            assert robot.Robot_state.socket is not None
    finally:
        p.parar()
