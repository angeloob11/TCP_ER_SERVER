# Experimentos de la memoria

Scripts usados para obtener los datos de las preguntas 1, 3, 7 y 8. Usan `src/buffer_ur.py`, `src/primaria.py` y `src/tiempo_real.py` tal cual.

| Script | Qué hace |
|---|---|
| `emulador.py PUERTO pri|rt seg|noseg` | Servidor local que imita 30001 (10 Hz) o 30003 (500 Hz) con las longitudes de URSim 5.25. `seg` trocea en segmentos de 1448 B, como Ethernet. |
| `medir_recv.py PUERTO CABECERA SEGUNDOS PAUSA` | Cuenta los bytes de cada `recv()` y los mensajes extraídos (tabla 3). |
| `pruebas.py` | Troceo aleatorio del buffer, decodificación contra el emulador y ejemplo de endianness (P3). Necesita los emuladores en 31001 y 31003. |
| `retraso.py PUERTO 4 PAUSA` | Edad de los datos con un consumidor normal o lento (P7). |
| `demo_lock2.py` | Lecturas incoherentes con y sin `Lock` (P8). |

Ejemplo:

```
python emulador.py 31003 rt noseg      # en una consola
python medir_recv.py 31003 4 60 0      # en otra
```

Para la P1 con el robot real basta con poner `depurar=True` en `recibir_mensajes()`.
