import sys, time, struct
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
from buffer_ur import conectar_con_reintento, recibir_mensajes
puerto, cab, retardo = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
c = conectar_con_reintento("127.0.0.1", puerto); buf = bytearray(); t0 = None; w0 = time.monotonic()
marcas = {}
while time.monotonic() - w0 < 20:
    for m in recibir_mensajes(c, buf, cab):
        if cab == 4: (t,) = struct.unpack_from(">d", m, 4)
        else: continue
        if t0 is None: t0 = t
        edad = (time.monotonic() - w0) - (t - t0)
        s = int(time.monotonic() - w0)
        marcas[s] = edad
    if retardo: time.sleep(retardo)
print({k: round(v*1000) for k, v in marcas.items() if k in (1, 5, 10, 15, 19)})
