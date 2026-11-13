# -*- mode: python ; coding: utf-8 -*-
# Receta de PyInstaller para los dos ejecutables (un solo fichero cada uno).
# No se lanza a mano: usa construir_linux.sh o construir_windows.bat.
import os
from PyInstaller.utils.hooks import collect_all

FUENTE = SPECPATH                                            # ejecutables/fuente
SRC = os.path.abspath(os.path.join(SPECPATH, '..', '..', 'src'))


def ejecutable(script, nombre, paquetes=(), excluir=()):
    datas, binaries, hiddenimports = [], [], []
    for paquete in paquetes:
        d, b, h = collect_all(paquete)
        datas += d; binaries += b; hiddenimports += h
    a = Analysis(
        [os.path.join(FUENTE, script)],
        pathex=[FUENTE, SRC],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=list(excluir),
    )
    return EXE(
        PYZ(a.pure),
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=nombre,
        console=True,          # los dos piden la contraseña y escriben por consola
        upx=False,
    )


# La pasarela solo usa la biblioteca estándar
pasarela = ejecutable('lanzar_pasarela.py', 'pasarela')

# El cliente lleva dentro la simulación (pybullet y el modelo del UR3e)
cliente = ejecutable('lanzar_cliente.py', 'cliente',
                     paquetes=['UR3e_rg2', 'pybullet_planning'],
                     excluir=['pybullet_envs', 'pybullet_examples', 'pybullet_robots',
                              'pytest', 'tkinter'])
