import socket
import struct
from time import sleep

HOST = "10.172.18.84"  # Cambia por la IP del equipo que ejecuta el servidor.
PORT = 30001

data = 0

def main():

    with socket.create_connection((HOST, PORT)) as cliente:

        while True:
                bytes_recibidos = cliente.recv(5)
                mensaje = struct.unpack(">iB", bytes_recibidos)
                if mensaje[1] == 16:
                      data = cliente.recv(mensaje[0]-5)
                print("Mensaje recibido:", mensaje)
                print("número de bytes recibidos:", len(data))
                sleep(1)


    print("cosa.")


if __name__ == "__main__":
	main()