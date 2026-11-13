"""Lo que comparten los dos lanzadores: carpetas, menú y localización de la PKI."""
import sys
from pathlib import Path

# Carpeta de los ejecutables: junto al binario si está empaquetado con PyInstaller;
# si se lanza con python, la carpeta que contiene a fuente/.
if getattr(sys, 'frozen', False):
    CARPETA = Path(sys.executable).resolve().parent
else:
    CARPETA = Path(__file__).resolve().parent.parent
    # el código del proyecto está en src/, en la raíz
    sys.path.insert(0, str(CARPETA.parent / 'src'))

CONFIG = CARPETA / 'config'
PKI_LOCAL = CARPETA / 'PKI'               # PKI copiada junto a los ejecutables (otro equipo)
PKI_PROYECTO = CARPETA.parent / 'PKI'     # PKI del proyecto (este PC)


def preparar_consola() -> None:
    """Que un carácter que la consola de Windows no sepa pintar no pare el programa."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def localizar_pki(pki: dict) -> dict:
    """Los JSON apuntan a ../PKI (junto a los ejecutables). Si un fichero no está ahí,
    se usa el de la PKI del proyecto: así en este PC no hay que duplicar las claves."""
    resultado = {}
    for nombre, ruta in pki.items():
        ruta = Path(ruta).resolve()
        if not ruta.exists():
            try:
                alternativa = PKI_PROYECTO / ruta.relative_to(PKI_LOCAL.resolve())
            except ValueError:
                alternativa = None
            if alternativa is not None and alternativa.exists():
                ruta = alternativa
        resultado[nombre] = ruta
    return resultado


def elegir(titulo: str, opciones: list[str]) -> int:
    """Menú numerado. Devuelve el índice elegido; Enter elige la primera opción."""
    print(f'\n{titulo}')
    for i, texto in enumerate(opciones, 1):
        print(f'  {i}) {texto}')
    while True:
        respuesta = input(f'Elige un número [1]: ').strip()
        if not respuesta:
            return 0
        if respuesta.isdigit() and 1 <= int(respuesta) <= len(opciones):
            return int(respuesta) - 1
        print(f'  Escribe un número del 1 al {len(opciones)}')


def pausa() -> None:
    """Con doble clic la ventana se cierra al terminar: se espera para poder leer."""
    try:
        input('\nPulsa Enter para salir...')
    except (EOFError, KeyboardInterrupt):
        pass
