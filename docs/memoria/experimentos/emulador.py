"""Emulador mínimo de las interfaces 30001/30003 de un UR (solo para pruebas locales).
Genera tramas con el formato de Client Interfaces y longitudes vistas en URSim 5.25."""
import socket, struct, threading, time, math, sys

MSS = 1448  # carga útil típica de un segmento TCP en Ethernet (1500 - IP - TCP con timestamps)

def q_en(t):
    return [-1.6007 + 0.3*math.sin(t), -1.7271, -2.2030, -0.8080, 1.5951, -0.0310]

def sub(tipo, cuerpo):
    return struct.pack(">iB", 5 + len(cuerpo), tipo) + cuerpo

def msg_estado(t, primero=False):
    modo = struct.pack(">Q", int(t*1000)) + struct.pack(">???????", True, True, True, False, False, False, False) \
         + struct.pack(">dBBdd", 1.0, 7, 0, 1.0, 0.0)[:47-5-8-7]
    s0 = sub(0, modo.ljust(42, b"\0"))                     # 47 B
    art = b"".join(struct.pack(">dddffffB", q, q, 0.0, 0.0, 0.0, 30.0, 0.0, 253) for q in q_en(t))
    s1 = sub(1, art)                                        # 251 B
    relleno_total = (1477 if primero else 722) - 5 - len(s0) - len(s1)
    otros, tipo = b"", 2
    while relleno_total > 0:                                # subpaquetes 2,3,4,... de relleno
        n = min(relleno_total, 120)
        if relleno_total - n < 6 and relleno_total - n != 0: n = relleno_total
        otros += sub(tipo, b"\0" * (n - 5)); relleno_total -= n; tipo += 1
    cuerpo = s0 + s1 + otros
    return struct.pack(">iB", 5 + len(cuerpo), 16) + cuerpo

def msg_version():
    c = struct.pack(">QbB", 0, -1, 3) + b"\x07URSim" + struct.pack(">BBi", 5, 25, 2) + b"130406".ljust(25, b"\0")
    return struct.pack(">iB", 5 + len(c), 20) + c

def msg_rt(t):
    b = bytearray(1220)
    struct.pack_into(">i", b, 0, 1220)
    struct.pack_into(">d", b, 4, t)
    struct.pack_into(">6d", b, 252, *q_en(t))
    struct.pack_into(">d", b, 812, 1.0)    # safety mode NORMAL
    struct.pack_into(">d", b, 1052, 1.0)   # program state STOPPED
    return bytes(b)

def enviar(c, datos, segmentar):
    if not segmentar:
        c.sendall(datos); return
    for i in range(0, len(datos), MSS):
        c.sendall(datos[i:i+MSS]); time.sleep(0.0002)

def servir(puerto, rt, segmentar):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", puerto)); srv.listen()
    while True:
        c, _ = srv.accept(); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        def atender(c=c):
            t0 = time.monotonic(); k = 0
            try:
                if not rt:
                    enviar(c, msg_version(), segmentar)
                    enviar(c, msg_estado(0, True) + struct.pack(">iBi", 9, 5, 0), segmentar)
                while True:
                    k += 1; t = k * (0.002 if rt else 0.1)
                    datos = msg_rt(t) if rt else msg_estado(t) + struct.pack(">iBi", 9, 5, 0)
                    enviar(c, datos, segmentar)
                    espera = t0 + t - time.monotonic()
                    if espera > 0: time.sleep(espera)
            except OSError:
                pass
        threading.Thread(target=atender, daemon=True).start()

if __name__ == "__main__":
    puerto, rt, seg = int(sys.argv[1]), sys.argv[2] == "rt", sys.argv[3] == "seg"
    servir(puerto, rt, seg)
