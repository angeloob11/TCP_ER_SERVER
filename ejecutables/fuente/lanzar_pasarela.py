"""Ejecutable de la pasarela: arranca la pasarela multi-interfaz sin escribir órdenes.

Usa config/pasarela_multi.json de la carpeta de los ejecutables (o el JSON que se
le pase como argumento). Pide la contraseña de pasarela.key y se para con Ctrl+C.
"""
import json
import sys
from pathlib import Path

from comun import CONFIG, elegir, localizar_pki, pausa, preparar_consola

import pasarela


def main() -> None:
    preparar_consola()
    print('=== Pasarela TLS del robot UR3e ===')

    if len(sys.argv) > 1:
        ruta = Path(sys.argv[1])
    else:
        candidatos = sorted(CONFIG.glob('pasarela*.json'))
        if not candidatos:
            print(f'No hay ningún pasarela*.json en {CONFIG}')
            return
        ruta = candidatos[0]
        if len(candidatos) > 1:
            ruta = candidatos[elegir('¿Qué configuración de pasarela?',
                                     [c.name for c in candidatos])]
    print(f'Configuración: {ruta}')

    try:
        cfg = pasarela.cargar_config(ruta)
        cfg['pki'] = localizar_pki(cfg['pki'])
        host = cfg['escucha']['host']
        interfaces = cfg['interfaces']
    except (OSError, json.JSONDecodeError) as e:
        print(f'No se puede leer la configuración: {e}')
        return
    except KeyError as e:
        print(f'Falta {e} en la configuración (debe ser de varias interfaces, '
              f'como pasarela_multi.json)')
        return

    print(f'Escucha en {host}, puertos {", ".join(str(i["port"]) for i in interfaces)}. '
          f'Registro: {Path(cfg["log"]).resolve()}')
    print('Para pararla: Ctrl+C\n')
    pasarela.modo_multi(cfg, host)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    pausa()
