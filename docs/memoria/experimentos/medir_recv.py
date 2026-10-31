"""Registra cuántos bytes devuelve cada recv() y cuántos mensajes completos salen, usando buffer_ur."""
import sys, time, json, collections
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
from buffer_ur import conectar_con_reintento, recibir_mensajes

puerto, cab, seg, retardo = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
c = conectar_con_reintento("127.0.0.1", puerto)
tam_recv, msgs_por_recv, longitudes = collections.Counter(), collections.Counter(), collections.Counter()
class Espia:
    def __init__(self, s): self.s = s
    def recv(self, n):
        d = self.s.recv(n); tam_recv[len(d)] += 1; return d
c = Espia(c)
buf = bytearray(); fin = time.monotonic() + seg; nrecv = 0; nmsg = 0; parcial_restante = 0
while time.monotonic() < fin:
    ms = recibir_mensajes(c, buf, cab)
    nrecv += 1; nmsg += len(ms); msgs_por_recv[len(ms)] += 1
    if buf: parcial_restante += 1
    for m in ms: longitudes[len(m)] += 1
    if retardo: time.sleep(retardo)
print(json.dumps({"recv": nrecv, "mensajes": nmsg, "recv_con_resto": parcial_restante,
  "tam_recv_top": tam_recv.most_common(8), "distintos_tam": len(tam_recv),
  "msgs_por_recv": sorted(msgs_por_recv.items()), "longitudes": sorted(longitudes.items())}))
