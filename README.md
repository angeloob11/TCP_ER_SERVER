# Visualizador y control de robots UR3e por TCP/IP

Práctica guiada de **Redes de Comunicación Industriales** (MUAECI, Universidad de Deusto).

El programa se conecta por TCP/IP a uno o varios robots UR3e (reales o simulados con URSim) y hace tres cosas:

- **Visualiza** en PyBullet la posición de cada robot en tiempo real.
- **Guarda puntos**, ya sea la posición actual del robot o la que marcan los deslizadores del simulador.
- **Ejecuta series de puntos** en el robot enviando programas URScript. Antes se pueden previsualizar con un robot "fantasma", y se pueden cancelar o reanudar tras un error.

---

## Índice

1. [Requisitos](#requisitos)
2. [Instalación](#instalación)
3. [Preparar URSim](#preparar-ursim)
4. [Estructura del proyecto](#estructura-del-proyecto)
5. [Configuración](#configuración)
6. [Ejecución](#ejecución)
7. [Controles de teclado (`multi_robot.py`)](#controles-de-teclado-multi_robotpy)
8. [Funcionamiento interno](#funcionamiento-interno)
9. [Evidencias](#evidencias)
10. [Problemas conocidos](#problemas-conocidos)
11. [Referencias](#referencias)

---

## Requisitos

- Python 3.12 (probado con 3.12.3 en Windows)
- Visualizador `ur3e_rg2` (wheel distribuido en el aula virtual). Instala también `pybullet`, `pybullet_planning` y `numpy`.
- URSim 5.25 (máquina virtual `URSim_VIRTUAL-5.25.2.130406`) o un UR3e real accesible por red

## Instalación

```powershell
# 1. Crear y activar el entorno virtual
python -m venv venv
venv\Scripts\activate

# 2. Instalar el visualizador (incluye pybullet) y las dependencias
pip install ur3e_rg2-0.1.0-py3-none-any.whl
pip install -r requirements.txt
```

> El wheel, la máquina virtual de URSim y los PDF de `referencias/` no se suben al repositorio (ver `.gitignore`).

## Preparar URSim

1. Descomprimir y arrancar la máquina virtual de URSim.
2. En la interfaz del robot (PolyScope):
   - encender el robot y liberar los frenos;
   - activar el **modo remoto** para que acepte los programas URScript que llegan por los puertos 30001/30003.
3. Anotar la IP de la máquina virtual y escribirla en `config/robots.json` (o en `src/constantes.py` para los scripts de un solo robot).
4. Comprobar desde el PC que la IP responde (`ping <ip>`).

## Estructura del proyecto

```
ENTREGA_1/
├── config/
│   ├── robots.json        # Robots a los que conectarse: nombre, IP, puerto y posición en la escena
│   └── puntos.json        # Puntos guardados (se crea y actualiza desde multi_robot.py)
├── docs/
│   └── evidencias/        # Salidas de consola de las pruebas
├── referencias/           # Documentación de Universal Robots y enunciado (no versionado)
├── src/
│   ├── constantes.py      # IP y puertos por defecto, límites de velocidad y aceleración
│   ├── buffer_ur.py       # Conexión con reintento y troceo del flujo TCP en mensajes
│   ├── primaria.py        # Lector de la interfaz primaria (puerto 30001) + visualizador de un robot
│   ├── tiempo_real.py     # Lector de la interfaz de tiempo real (puerto 30003) + visualizador de un robot
│   ├── rx.py              # Herramienta de diagnóstico: mide los mensajes/s que entrega el robot
│   └── multi_robot.py     # Programa principal: varios robots, puntos, series y control por teclado
├── requirements.txt
└── README.md
```

### Qué hace cada módulo

| Archivo | Responsabilidad |
|---|---|
| `constantes.py` | `HOST`, `PORT` (30001), `PORT_TIEMPO_REAL` (30003), `VELOCIDAD_MAX` (0.3 rad/s) y `ACELERACION_MAX` (0.5 rad/s²). |
| `buffer_ur.py` | `conectar_con_reintento()` reintenta cada segundo hasta que el servidor responde y aplica un timeout de recepción para detectar conexiones muertas. `recibir_mensajes()` y `extraer_mensajes()` acumulan bytes en un buffer y separan los mensajes completos usando el campo de longitud de la cabecera. Si la longitud es imposible, el flujo está desalineado y se lanza un error. |
| `primaria.py` | `nuevo_estado()` crea el diccionario compartido de un robot. `leer_robot()` decodifica la interfaz primaria (ver [Protocolos](#protocolos)), actualiza el estado bajo un `Lock` y publica el socket para poder enviar órdenes por él. Reconecta sola si se cae la conexión. |
| `tiempo_real.py` | Igual que `primaria.py`, pero para la interfaz de tiempo real (500 Hz). Además mide la tasa de recepción en paquetes/s. |
| `rx.py` | Solo recibe y cuenta, sin hilos ni simulación. Compara el reloj del robot con el del PC para distinguir si "el robot no envía 500 msg/s" o si "mi aplicación no los consume". |
| `multi_robot.py` | Lanza un hilo lector por robot, los dibuja en una única escena de PyBullet y gestiona puntos, series, previsualización, ejecución y teclado. |

## Configuración

### `config/robots.json`

Lista de robots. Cada uno se conecta por el puerto que se indique, y el lector se elige según ese puerto:

```json
[
  { "nombre": "robot_1", "host": "192.168.1.140", "puerto": 30001, "pose": [0, 0, 0, 0, 0, 0] },
  { "nombre": "robot_2", "host": "192.168.1.5",   "puerto": 30003, "pose": [0.8, 0, 0, 0, 0, 0] }
]
```

| Campo | Descripción |
|---|---|
| `nombre` | Nombre que aparece en la consola. |
| `host` | IP del robot o de URSim. |
| `puerto` | `30001` (primaria) o `30003` (tiempo real). Cualquier otro valor se rechaza al arrancar. |
| `pose` | Posición de la base en la escena: `[x, y, z, roll, pitch, yaw]` (m y rad). Sirve para que los robots no se solapen. |

### `config/puntos.json`

Lo gestiona el programa. Cada punto tiene un ID consecutivo y sus 6 ángulos articulares **en radianes**:

```json
[
  { "id": 0, "q": [0.0, -1.57, 1.57, 0.0, 1.57, 0.0] }
]
```

Para calcular el ID nuevo se parte del mayor existente, así que borrar un punto no provoca IDs repetidos.

### `src/constantes.py`

IP y puertos por defecto de los scripts de un solo robot, y los límites de velocidad y aceleración que se aplican a todos los `movej`.

## Ejecución

Todos los comandos se lanzan con el entorno virtual activado.

```powershell
# Programa principal: varios robots con control por teclado
python src/multi_robot.py                     # usa config/robots.json
python src/multi_robot.py ruta/a/otro.json    # otra configuración

# Visualizador de un único robot
python src/primaria.py                        # interfaz primaria (30001)
python src/tiempo_real.py                     # interfaz de tiempo real (30003)

# Diagnóstico de la tasa de mensajes
python src/rx.py --host 192.168.1.140 --port 30003 --segundos 15
```

Al arrancar `multi_robot.py`:

- La consola muestra cada robot con su IP, puerto e ID en la escena, seguido de la tabla de ayuda.
- Si un robot no está disponible, su hilo reintenta la conexión cada segundo sin bloquear al resto.
- El programa se cierra con `Ctrl+C` en la consola.

## Controles de teclado (`multi_robot.py`)

> Las teclas se leen desde **la ventana de PyBullet**, no desde la consola. Haz clic en la escena 3D antes de pulsar.

| Tecla | Acción |
|---|---|
| `t` | Pasar al siguiente robot activo (la consola muestra cuál). |
| `g` | Guardar la posición **real** del robot activo. |
| `s` | Guardar el punto que marcan los **deslizadores** del simulador. |
| `l` | Listar los puntos guardados (ID y ángulos en grados). |
| `n` + dígitos + `Enter` | Escribir la serie de IDs que se va a ejecutar, separados por espacios. Por ejemplo `n 0 2 1 Enter`. `Backspace` borra. |
| `p` | Previsualizar la serie con un robot fantasma semitransparente. Pulsa cualquier tecla para cerrarlo. |
| `e` | Ejecutar la serie en el robot activo. Pide confirmación con `y`, y cualquier otra tecla cancela. |
| `x` | Cancelar la ejecución en curso: envía `stopj` y detiene el robot. |
| `c` | Continuar tras un error o una cancelación, empezando por el punto donde se paró. |
| `b` | Borrar el último punto guardado. |
| `h` | Mostrar la ayuda. |

### Ejemplo de uso

1. Mover los deslizadores hasta una postura y pulsar `s` → se guarda el punto 0.
2. Otra postura y `s` → punto 1. Con `l` se comprueban los dos.
3. `n 0 1 0 Enter` → la serie queda definida como `[0, 1, 0]`.
4. `p` → el fantasma recorre la serie. Pulsar cualquier tecla para cerrarlo.
5. `e`, después `y` → el robot real ejecuta la serie.
6. Si durante la ejecución hay que pararlo: `x`. Para retomarla desde ese punto: `c`.

## Funcionamiento interno

### Arquitectura de hilos

```
 ┌──────────────── hilo principal (~30 Hz) ───────────────┐
 │  1. copia q de cada robot (bajo su lock) → PyBullet    │
 │  2. lee el teclado → manejar_tecla()                   │
 └────────────────────────────────────────────────────────┘
        ▲ Robot_state + lock                 │ lanza
        │ (uno por robot)                    ▼
 ┌──────────────────────┐        ┌──────────────────────────┐
 │ hilo lector (x robot)│        │ hilo de ejecución        │
 │ primaria/tiempo_real │        │ envía movej punto a punto│
 │ actualiza q, estado  │        │ y espera a que se cumpla │
 │ y publica el socket  │        └──────────────────────────┘
 └──────────────────────┘
```

- **Hilos lectores.** Cada robot tiene un hilo lector (`daemon=True`) con su propio `Robot_state` y su propio `Lock`. El lock solo protege la copia de datos, así que el hilo principal nunca lee un estado a medio escribir.
- **Envío por la misma conexión.** El socket del lector se publica en `Robot_state["socket"]` y las órdenes URScript se envían por él. Como ese hilo no deja de leer, el buffer de recepción nunca se llena. Si la conexión se cae, el socket pasa a `None` y se vuelve a publicar al reconectar.
- **Bucle principal sin bloqueos.** Las acciones que requieren varias teclas (`n`, `e` → `y`) se resuelven con una pequeña máquina de estados (`modo`: `normal`, `serie`, `confirmar`). La ejecución de la serie corre en un hilo aparte. Así la visualización sigue actualizándose y `x` responde en cualquier momento.

### Protocolos

**Interfaz primaria (puerto 30001, ~10 Hz).** Cabecera de 5 bytes: longitud (`int32`) y tipo (`uint8`). Se usan dos tipos de mensaje:

- **Tipo 16, *Robot State*.** Se recorren sus subpaquetes:
  - subpaquete 0, *Robot mode data*: `isProtectiveStopped` e `isProgramRunning`, a partir del byte 17;
  - subpaquete 1, *Joint data*: 6 bloques de 41 bytes; el primer `double` de cada bloque es `q_actual`.
- **Tipo 20, *Robot message*.** Si `robotMessageType == 6` (*error code*), se extraen código, argumento, nivel y texto, y se guardan en `Robot_state["error"]`.

**Interfaz de tiempo real (puerto 30003, 500 Hz).** Cabecera de 4 bytes (longitud). Campos que se leen, según la hoja *Client Interfaces*:

| Offset | Campo | Uso |
|---|---|---|
| 252 | `q_actual` (6 × double) | ángulos articulares |
| 812 | `safety_mode` (double) | 3 = parada de protección; 5–9 se tratan como error |
| 1052 | `program_state` (double) | 2 = programa en ejecución |

Las versiones antiguas envían paquetes más cortos. En ese caso los campos de estado quedan en `None`.

Ambos lectores usan `buffer_ur.py` para reconstruir mensajes completos, porque `recv()` puede devolver medio mensaje o varios mensajes juntos.

### Envío y verificación de movimientos

1. **`enviar_movimiento()`.**
   - Valida los ángulos: deben ser 6, finitos y dentro de ±2π. Un valor mayor casi seguro está en grados.
   - Limita la velocidad y la aceleración a los máximos de `constantes.py`.
   - Envía este programa:
     ```
     def mover():
       movej([q0, ..., q5], a=..., v=...)
     end
     ```
2. **`esperar_movimiento()`.** El robot no responde a las órdenes, así que el cumplimiento se deduce del estado que sigue publicando:
   1. Espera a que `isProgramRunning` pase a `True`. Si no ocurre en 2 s, la orden se ha rechazado (por ejemplo, porque el robot no está en modo remoto).
   2. Espera a que termine, vigilando paradas de protección, errores y un tiempo máximo. Si se supera ese tiempo, envía `stopj`.
   3. Comprueba que la desviación final de cada articulación es menor que `TOLERANCIA_Q` (0.01 rad).
3. **`enviar_serie()`.** Valida que los IDs existan y que no haya otro programa en marcha (enviar otro lo abortaría). Después recorre los puntos con las dos funciones anteriores.
4. **`hilo_ejecucion()`.** Llama a `enviar_serie` con un único punto cada vez. Así sabe en qué posición falló o se canceló, y la tecla `c` puede reanudar desde ahí.

### Previsualización

`mover_fantasma()` crea un robot extra semitransparente en la `pose` del robot activo. Lo mueve entre los puntos con interpolación lineal, a la misma velocidad máxima que usará el robot real, y lo elimina al cerrar la previsualización (también si se interrumpe con `Ctrl+C`).

## Evidencias

En `docs/evidencias/` están las salidas de consola de las pruebas:

| Archivo | Contenido |
|---|---|
| `salida_primaria.txt` | Ejecución de `primaria.py`: reintentos de conexión y arranque del visualizador. |
| `salida_tiempo_real.txt` | Ejecución de `tiempo_real.py` con la tasa de recepción medida. |
| `errores_primaria.txt`, `errores_tiempo_real.txt` | Salida de error (stderr) de esas ejecuciones: solo el aviso de compilación de PyBullet, sin errores. |

La interfaz de tiempo real tiene una tasa nominal de 500 mensajes/s, pero con URSim llegan menos (`salida_tiempo_real.txt` registra la tasa medida segundo a segundo). `rx.py` sirve para comprobar si la diferencia viene del simulador o de la aplicación.

## Problemas conocidos

- **El teclado no responde.** Las teclas solo llegan si la ventana de PyBullet tiene el foco. Haz clic en la escena 3D.
- **Las teclas escriben en la caja de un deslizador.** Si se pincha en el número de un deslizador, la caja se queda con el foco. Para recuperarlo, haz clic en una zona vacía de la escena 3D o pulsa `Enter`. Es mejor mover los deslizadores arrastrando el botón.
- **`Esc` cierra la ventana de PyBullet** aunque los atajos estén desactivados. No lo uses para salir de una caja de texto.
- **`Tab` no llega a `getKeyboardEvents()`** en Windows, por eso el cambio de robot activo usa la tecla `t`.
- **Mientras dura la previsualización (`p`) el bucle principal está parado.** Los robots no siguen al real hasta que se cierra el fantasma.
- **Parada de protección.** Si salta una, `c` vuelve a fallar hasta que se libere en PolyScope.

## Referencias

En `referencias/` (no versionado, copyright de Universal Robots):

- *Client Interfaces – Primary* y *Client Interfaces – Realtime*: formato de los mensajes.
- *The URScript Programming Language* (G5): `movej`, `stopj`.
- Enunciado: *Práctica guiada visualizador de un UR3e por TCP/IP*.
- *Diagnóstico visualizador UR3e*.
