import sys, threading, struct
sys.setswitchinterval(1e-6)
N = 300_000
PAQ = struct.pack(">6d", *[0.1]*6)
def experimento(modo):
    est = {"q": [0.0]*6, "run": False}; lk = threading.Lock(); fin = [False]; malos = [0, 0]; n = [0]
    def escritor():
        for k in range(1, N):
            run = (k % 2 == 0)
            if modo == "sin_lock_en_sitio":
                for i in range(6): est["q"][i] = float(k)
                est["run"] = run
            elif modo == "lock_por_subpaquete":          # como primaria.py: sub 0 y sub 1 en secciones distintas
                with lk: est["run"] = run
                struct.unpack_from(">6d", PAQ, 0)        # decodificación del subpaquete 1 entre medias
                with lk: est["q"] = [float(k)]*6
            else:                                        # un único lock por mensaje
                q = [float(k)]*6
                with lk: est["run"] = run; est["q"] = q
        fin[0] = True
    def lector():
        while not fin[0]:
            if modo == "sin_lock_en_sitio":
                q = est["q"][:]; r = est["run"]
            else:
                with lk: q = est["q"][:]; r = est["run"]
            n[0] += 1
            if len(set(q)) > 1: malos[0] += 1
            elif q[0] and (int(q[0]) % 2 == 0) != r: malos[1] += 1
    a, b = threading.Thread(target=escritor), threading.Thread(target=lector)
    b.start(); a.start(); a.join(); b.join()
    print(f"{modo:22s} lecturas={n[0]:7d}  q_mezclada={malos[0]:6d} ({100*malos[0]/n[0]:5.2f} %)  q/run de mensajes distintos={malos[1]:6d} ({100*malos[1]/n[0]:5.2f} %)")
for m in ("sin_lock_en_sitio", "lock_por_subpaquete", "lock_por_mensaje"): experimento(m)
