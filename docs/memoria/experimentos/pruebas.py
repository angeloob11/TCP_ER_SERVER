import sys, struct, random, threading, time, math
import os; _d = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(_d, "..", "..", "..", "src")); sys.path.insert(0, _d)
from buffer_ur import extraer_mensajes
import emulador, primaria, tiempo_real

# 1) Troceo arbitrario: el resultado no depende de cómo lleguen los bytes
flujo = emulador.msg_version() + emulador.msg_estado(0, True) + b"".join(emulador.msg_estado(k/10) + struct.pack(">iBi",9,5,0) for k in range(200))
ref = extraer_mensajes(bytearray(flujo), 5)
for prueba in range(200):
    buf, out, i = bytearray(), [], 0
    while i < len(flujo):
        n = random.randint(1, 3000); buf.extend(flujo[i:i+n]); i += n
        out += extraer_mensajes(buf, 5)
    assert out == ref and not buf
print(f"troceo aleatorio: 200 repeticiones, {len(ref)} mensajes idénticos en todas")
# byte a byte
buf, out = bytearray(), []
for b in flujo: buf.append(b); out += extraer_mensajes(buf, 5)
assert out == ref; print("byte a byte: OK")
# desalineado
try: extraer_mensajes(bytearray(flujo[3:]), 5)
except ValueError as e: print("desalineado 3 B:", e)

# 2) Decodificación real con leer_robot contra el emulador
for mod, puerto in ((primaria, 31001), (tiempo_real, 31003)):
    est, lk = primaria.nuevo_estado(), threading.Lock()
    threading.Thread(target=mod.leer_robot, args=(est, "127.0.0.1", puerto, lk), daemon=True).start()
    time.sleep(1.5)
    with lk: q = est["q"][:]; r = est["isProgramRunning"]; ps = est["isProtectiveStopped"]
    print(mod.__name__, "q(grados) =", [round(math.degrees(x),2) for x in q], "running", r, "pstop", ps)

# 3) Endianness
q = -1.6007
be = struct.pack(">d", q)
print("bytes BE:", be.hex(" "))
print("leído '<d':", struct.unpack("<d", be)[0], " leído '>d':", struct.unpack(">d", be)[0])
for v in (0.0, 1.5708, -2.2030, 3.14159):
    print(v, struct.pack(">d", v).hex(" "), struct.unpack("<d", struct.pack(">d", v))[0])
print("longitud 1220 BE:", struct.pack(">i",1220).hex(" "), "leída '<i':", struct.unpack("<i", struct.pack(">i",1220))[0])
print("calcsize >dddffffB:", struct.calcsize(">dddffffB"), " sin '>':", struct.calcsize("dddffffB"))
