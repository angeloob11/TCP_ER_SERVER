"""Ejecutable del cliente: se elige con un menú a qué robots conectarse y con qué
certificado (cliente-a, cliente-b...), sin escribir órdenes.

Las opciones salen de los ficheros de la carpeta config/ que hay junto al
ejecutable: cada robots*.json es una opción de robots y cada cliente*.json una
opción de certificado. Para añadir una opción basta con copiar un JSON y cambiarlo.
"""
import json
import ssl
from pathlib import Path

from comun import CONFIG, elegir, localizar_pki, pausa, preparar_consola

import multi_robot
from clienteTLS import RobotTLS
from pasarela import SSL_CLIENT, cargar_config, pedir_clave


def interfaces_pasarela() -> dict[tuple[str, int], str]:
    """(IP, puerto) de la pasarela -> nombre de la interfaz, para describir las opciones."""
    nombres = {}
    for ruta in CONFIG.glob('pasarela*.json'):
        try:
            cfg = json.loads(ruta.read_text(encoding='utf-8'))
            for i in cfg['interfaces']:
                nombres[(cfg['escucha']['host'], i['port'])] = i['nombre']
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return nombres


def opciones_robots() -> list[tuple[Path, bool, str]]:
    """Una opción por robots*.json: (fichero, es directo sin TLS, descripción).
    Es directo si ningún robot lleva "interfaz" (va al puerto del propio robot)."""
    nombres = interfaces_pasarela()
    opciones = []
    for ruta in sorted(CONFIG.glob('robots*.json')):
        try:
            robots = json.loads(ruta.read_text(encoding='utf-8'))
            directo = not any('interfaz' in r for r in robots)
            partes = []
            for r in robots:
                interfaz = nombres.get((r['host'], r['puerto'])) or r.get('interfaz', '')
                partes.append(f"{r['nombre']} en {r['host']}:{r['puerto']} {interfaz}".strip())
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f'  (se ignora {ruta.name}: {e})')
            continue
        modo = 'DIRECTO al robot, sin TLS' if directo else 'por la pasarela TLS'
        opciones.append((ruta, directo, f'{ruta.name:<22} {modo}: {", ".join(partes)}'))
    opciones.sort(key=lambda o: o[1])      # primero las de la pasarela; el modo directo, al final
    return opciones


def opciones_cliente() -> list[tuple[Path, str]]:
    """Una opción por cliente*.json: (fichero, descripción con el certificado que usa)."""
    opciones = []
    for ruta in sorted(CONFIG.glob('cliente*.json')):
        try:
            pki = localizar_pki(cargar_config(ruta)['pki'])
        except (OSError, ValueError, KeyError) as e:
            print(f'  (se ignora {ruta.name}: {e})')
            continue
        falta = [p.name for p in pki.values() if not p.exists()]
        aviso = f'   (FALTA {", ".join(falta)})' if falta else ''
        opciones.append((ruta, f'{pki["cert"].stem:<12} ({ruta.name}){aviso}'))
    return opciones


def crear_contexto() -> ssl.SSLContext | None:
    clientes = opciones_cliente()
    if not clientes:
        print(f'No hay ningún cliente*.json en {CONFIG}')
        return None
    ruta = clientes[elegir('¿Con qué certificado de cliente?', [c[1] for c in clientes])][0]
    pki = localizar_pki(cargar_config(ruta)['pki'])
    try:
        return SSL_CLIENT(pki['cert'], pki['key'], pki['ca'], pedir_clave(pki['key'].name))
    except ssl.SSLError:
        print('Contraseña incorrecta o la clave no corresponde al certificado')
    except OSError as e:
        print(f'No se pueden leer los certificados: {e}')
    return None


def main() -> None:
    preparar_consola()
    print('=== Cliente del robot UR3e ===')
    print(f'Configuración: {CONFIG}')

    robots_disponibles = opciones_robots()
    if not robots_disponibles:
        print(f'No hay ningún robots*.json en {CONFIG}')
        return
    ruta, directo, _ = robots_disponibles[elegir('¿A qué te conectas?',
                                                  [o[2] for o in robots_disponibles])]

    if directo:
        ctx = None
        print('\nModo directo: sin TLS')
    else:
        ctx = crear_contexto()
        if ctx is None:
            return

    # Los puntos guardados, en la config/ de los ejecutables
    multi_robot.RUTA_PUNTOS = CONFIG / 'puntos.json'

    try:
        robots = RobotTLS.cargar_robots(ruta, ctx)
    except (ValueError, KeyError) as e:
        print(f'{ruta.name} no es válido: {e}')
        return
    print('\nSe abre la ventana de simulación. Pulsa h en ella para ver las teclas.\n',
          flush=True)
    for r in robots:
        r.iniciar_lector()
    multi_robot.MultiRobot(robots).bucle_principal()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    pausa()
