#!/usr/bin/env bash
# =====================================================================
#  crear_pki.sh · PKI de la práctica «Pasarela TLS para el UR3e»
#  Reproduce los pasos 1-7 y 9 del enunciado con parámetros editables.
#
#  Uso:
#    ./crear_pki.sh todo        Crea la PKI completa (CA + pasarela + clientes)
#    ./crear_pki.sh pasarela    Reemite SOLO el certificado de la pasarela
#                               (lo único que cambia si cambia la IP)
#    ./crear_pki.sh cliente X   Emite un cliente nuevo con CN=X (p. ej. cliente-d)
#    ./crear_pki.sh falso       Crea el cliente FALSO cliente-c (CA ajena y
#                               certificado alterado): la pasarela debe rechazarlo
#    ./crear_pki.sh config      Solo escribe los JSON (p. ej. si cambia la IP del robot)
#    ./crear_pki.sh verificar   Comprueba todo y genera informe_E1.txt
#
#  Todos los modos menos 'verificar' escriben además un juego NUEVO de JSON en
#  ejecutables/config con las IP de pki.conf: cliente-a-1.json, pasarela-1.json,
#  robots_tls-1.json...; la siguiente vez serán los -2. Nunca se sobrescribe.
#
#  Los parámetros se cambian en la sección CONFIGURACIÓN o en un fichero
#  pki.conf junto al script (tiene prioridad). Las contraseñas se piden
#  por teclado y no se guardan en ningún sitio.
# =====================================================================
set -euo pipefail
DIR_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ----------------------------- CONFIGURACIÓN -----------------------------
PKI_DIR="$HOME/pki"                                  # carpeta de trabajo
IP_PASARELA="10.172.21.30"                           # va en la SAN: la que asigne el profesor
IP_ROBOT="10.172.18.80"                              # destino: el robot al que reenvía la pasarela
CONFIG_DIR="$DIR_SCRIPT/../ejecutables/config"       # dónde se escriben los JSON
AUTORIZADOS=("cliente-a")                            # CN que la pasarela deja pasar
CLIENTE_FALSO="cliente-c"                            # carpeta del cliente falso (modo 'falso')
CN_CA="CA Robot Lab - Estudiante Angel Ardila"
CN_PASARELA="pasarela-ur3e"
CLIENTES=("cliente-a" "cliente-b")
DIAS_CA=730
DIAS_EQUIPO=365
BITS=2048
# -------------------------------------------------------------------------

[[ -f "$DIR_SCRIPT/pki.conf" ]] && source "$DIR_SCRIPT/pki.conf"

azul()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok()    { printf '\033[32m   ✓ %s\033[0m\n' "$*"; }
error() { printf '\033[31m   ✗ %s\033[0m\n' "$*" >&2; exit 1; }
cmd()   { printf '\033[90m   $ %s\033[0m\n' "$*"; "$@"; }   # muestra y ejecuta

# Pide una contraseña dos veces y la deja en la variable de entorno $1
pedir_pass() {
  local var="$1" quien="$2" p1 p2
  while true; do
    read -rsp "   Contraseña para $quien: " p1; echo
    read -rsp "   Repítela: " p2; echo
    [[ "$p1" == "$p2" ]] || { echo "   No coinciden, otra vez."; continue; }
    [[ ${#p1} -ge 4 ]]   || { echo "   Mínimo 4 caracteres (OpenSSL lo exige)."; continue; }
    export "$var=$p1"; break
  done
}

pedir_pass_ca() {   # para firmar solo hace falta escribirla una vez
  read -rsp "   Contraseña de la CA (ca.key): " PASS_CA; echo; export PASS_CA
  openssl pkey -in ca-privado/ca.key -passin env:PASS_CA -noout 2>/dev/null \
    || error "Contraseña de la CA incorrecta"
}

# ---------------------------- PASOS ----------------------------
paso1_carpetas() {
  azul "Paso 1 · Carpetas en $PKI_DIR"
  mkdir -p "$PKI_DIR"/{ca-privado,publico,pasarela}
  for c in "${CLIENTES[@]}"; do mkdir -p "$PKI_DIR/$c"; done
  cd "$PKI_DIR"
  cmd chmod 700 ca-privado
  ok "Carpetas listas"
}

paso2_ca() {
  azul "Paso 2 · CA: $CN_CA"
  [[ -f ca-privado/ca.key ]] && error "Ya existe una CA. Usa 'pasarela' o borra $PKI_DIR para empezar de cero."
  pedir_pass PASS_CA "la CA"
  cmd openssl req -x509 -newkey "rsa:$BITS" -sha256 -days "$DIAS_CA" \
      -keyout ca-privado/ca.key -out publico/ca.crt -passout env:PASS_CA \
      -subj "/CN=$CN_CA" \
      -addext "basicConstraints=critical,CA:TRUE" \
      -addext "keyUsage=critical,keyCertSign,cRLSign"
  chmod 600 ca-privado/ca.key
  ok "publico/ca.crt creado"
}

paso3_ext() {
  azul "Paso 3 · Ficheros de extensiones (SAN = IP:$IP_PASARELA)"
  cat > pasarela.ext <<EOF
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = IP:$IP_PASARELA
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
EOF
  cat > cliente.ext <<EOF
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
EOF
  ok "pasarela.ext y cliente.ext escritos"
}

# emitir <carpeta> <CN> <fichero.ext>   (pasos 4, 5 y 6 para un equipo)
emitir() {
  local dir="$1" cn="$2" ext="$3" var="PASS_${1//-/_}"
  azul "Pasos 4-6 · $cn"
  mkdir -p "$dir"
  pedir_pass "$var" "$dir/$dir.key"
  cmd openssl req -new -newkey "rsa:$BITS" \
      -keyout "$dir/$dir.key" -out "$dir/$dir.csr" -passout "env:$var" -subj "/CN=$cn"
  cmd openssl req -in "$dir/$dir.csr" -noout -verify -subject
  local serial=(-CAserial ca-privado/ca.srl)
  [[ -f ca-privado/ca.srl ]] || serial+=(-CAcreateserial)
  cmd openssl x509 -req -in "$dir/$dir.csr" \
      -CA publico/ca.crt -CAkey ca-privado/ca.key -passin env:PASS_CA "${serial[@]}" \
      -days "$DIAS_EQUIPO" -sha256 -extfile "$ext" -out "$dir/$dir.crt"
  cmd chmod 600 "$dir/$dir.key"
  unset "$var"
  ok "$dir/$dir.crt emitido"
}

# Guarda una copia con fecha antes de sobrescribir
respaldar() {
  local dir="$1"
  if [[ -f "$dir/$dir.crt" ]]; then
    local b="$dir/antiguo_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$b"; mv "$dir/$dir".{key,csr,crt} "$b/" 2>/dev/null || true
    ok "Versión anterior guardada en $b"
  fi
}

verificar() {
  cd "$PKI_DIR"
  local inf="informe_E1.txt" equipos=(pasarela "${CLIENTES[@]}")
  {
    echo "INFORME E1 · $(date '+%Y-%m-%d %H:%M')"; echo "OpenSSL: $(openssl version)"; echo
    echo "== Paso 7 · openssl verify"
    openssl verify -CAfile publico/ca.crt $(for e in "${equipos[@]}"; do echo "$e/$e.crt"; done) 2>&1 || true
    echo; echo "== Paso 7 · clave pública: certificado vs clave (deben coincidir)"
    for e in "${equipos[@]}"; do
      echo "$e  crt: $(openssl x509 -in "$e/$e.crt" -noout -pubkey | openssl sha256 | awk '{print $2}')"
    done
    echo "(las de las .key se comprueban al pedirte su contraseña con: openssl pkey -in X.key -pubout | openssl sha256)"
    echo; echo "== Paso 6 · permisos"
    ls -ld ca-privado; ls -l ca-privado/ca.key $(for e in "${equipos[@]}"; do echo "$e/$e.key"; done)
    echo; echo "== Paso 6 · claves cifradas"
    for k in ca-privado/ca.key $(for e in "${equipos[@]}"; do echo "$e/$e.key"; done); do
      echo "$k: $(head -1 "$k")"; done
    echo; echo "== SAN de la pasarela"
    openssl x509 -in pasarela/pasarela.crt -noout -ext subjectAltName
    echo; echo "== Paso 9 · inventario"
    for c in publico/ca.crt $(for e in "${equipos[@]}"; do echo "$e/$e.crt"; done); do
      echo "--- $c"
      openssl x509 -in "$c" -noout -subject -issuer -serial -dates -fingerprint -sha256 -ext keyUsage,extendedKeyUsage 2>/dev/null
    done
  } | tee "$inf"
  ok "Guardado en $PKI_DIR/$inf (cópialo a la memoria)"
}

# ---------------------------- JSON DE CONFIGURACIÓN ----------------------------
# Número del juego nuevo: el mayor -N.json que haya en CONFIG_DIR, más uno
siguiente_n() {
  local n=0 f
  for f in "$CONFIG_DIR"/*-[0-9]*.json; do
    [[ "$f" =~ -([0-9]+)\.json$ ]] || continue
    (( 10#${BASH_REMATCH[1]} > n )) && n=$((10#${BASH_REMATCH[1]}))
  done
  echo $((n + 1))
}

# escribir <fichero>: guarda la entrada estándar sin pisar nunca un fichero existente
escribir() {
  [[ -e "$CONFIG_DIR/$1" ]] && error "$CONFIG_DIR/$1 ya existe: no se sobrescribe"
  cat > "$CONFIG_DIR/$1"
  ok "$1"
}

# json_cliente <N> <nombre> <certificado> <clave>   (rutas relativas a PKI/)
json_cliente() {
  local nombre="$2"
  [[ "$nombre" == cliente* ]] || nombre="cliente-$nombre"   # el menú busca cliente*.json
  escribir "$nombre-$1.json" <<EOF
{
  "pasarela": { "host": "$IP_PASARELA", "port": 3000 },
  "pki": {
    "ca":   "../PKI/publico/ca.crt",
    "cert": "../PKI/$3",
    "key":  "../PKI/$4"
  }
}
EOF
}

json_pasarela() {
  local n="$1" lista
  lista=$(printf '"%s", ' "${AUTORIZADOS[@]}")
  escribir "pasarela-$n.json" <<EOF
{
  "escucha":     { "host": "$IP_PASARELA" },
  "interfaces": [
    { "nombre": "primaria",    "port": 3000, "destino": { "host": "$IP_ROBOT", "port": 30001 } },
    { "nombre": "tiempo_real", "port": 3001, "destino": { "host": "$IP_ROBOT", "port": 30003 } },
    { "nombre": "secundaria",  "port": 3002, "destino": { "host": "$IP_ROBOT", "port": 30002 } },
    { "nombre": "rtde",        "port": 3003, "destino": { "host": "$IP_ROBOT", "port": 30004 } }
  ],
  "autorizados": [${lista%, }],
  "log":         "../pasarela_multi.log",
  "pki": {
    "ca":   "../PKI/publico/ca.crt",
    "cert": "../PKI/pasarela/pasarela.crt",
    "key":  "../PKI/pasarela/pasarela.key"
  }
}
EOF
}

# json_robots <N> <sufijo> <puerto de la pasarela> <interfaz>
json_robots() {
  escribir "robots_tls$2-$1.json" <<EOF
[
  { "nombre": "robot_1", "host": "$IP_PASARELA", "puerto": $3, "interfaz": "$4", "pose": [0, 0, 0, 0, 0, 0] }
]
EOF
}

# Clientes que hay en la PKI: toda carpeta X con X/X.crt, menos la pasarela
clientes_pki() {
  local d
  for d in "$PKI_DIR"/*/; do
    d="$(basename "$d")"
    [[ "$d" != pasarela && -f "$PKI_DIR/$d/$d.crt" ]] && echo "$d"
  done
  return 0
}

# Juego completo: pasarela, robots y un JSON por cliente, todos con el mismo número
generar_config() {
  mkdir -p "$CONFIG_DIR"
  local n c alterado="$CLIENTE_FALSO/${AUTORIZADOS[0]}-alterado.crt"
  n="$(siguiente_n)"
  azul "JSON nº $n en $CONFIG_DIR (pasarela $IP_PASARELA, robot $IP_ROBOT)"
  json_pasarela "$n"
  json_robots "$n" ""     3000 primaria
  json_robots "$n" "_tr"  3001 tiempo_real
  json_robots "$n" "_sec" 3002 primaria
  for c in $(clientes_pki); do json_cliente "$n" "$c" "$c/$c.crt" "$c/$c.key"; done
  # el certificado alterado se presenta con la clave auténtica del cliente suplantado
  if [[ -f "$PKI_DIR/$alterado" ]]; then
    json_cliente "$n" "$CLIENTE_FALSO-alterado" "$alterado" "${AUTORIZADOS[0]}/${AUTORIZADOS[0]}.key"
  fi
  ok "Los anteriores no se han tocado: bórralos a mano cuando ya no sirvan"
}

# ---------------------------- CLIENTE FALSO ----------------------------
# Dos falsificaciones del cliente autorizado, para comprobar que la pasarela las rechaza:
#  1) $CLIENTE_FALSO.crt: mismo CN que el autorizado y mismo nombre de CA, pero
#     firmado por una CA inventada (otra clave). El atacante que no tiene ca.key.
#  2) <autorizado>-alterado.crt: el certificado auténtico con UN bit de la firma cambiado.
cliente_falso() {
  local dir="$CLIENTE_FALSO" real="${AUTORIZADOS[0]}" var="PASS_falso" tmp
  [[ -f "$real/$real.crt" ]] || error "No existe $real/$real.crt: ejecuta primero 'todo'"
  azul "Cliente falso · $dir (suplanta a $real)"
  respaldar "$dir"
  mkdir -p "$dir"
  tmp="$(mktemp -d)"

  # 1) CA inventada con el mismo nombre que la auténtica; su clave se tira al acabar
  openssl req -x509 -newkey "rsa:$BITS" -sha256 -days "$DIAS_EQUIPO" -nodes \
      -keyout "$tmp/ca-falsa.key" -out "$dir/ca-falsa.crt" -subj "/CN=$CN_CA" \
      -addext "basicConstraints=critical,CA:TRUE" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
  pedir_pass "$var" "$dir/$dir.key"
  openssl req -new -newkey "rsa:$BITS" -keyout "$dir/$dir.key" -out "$dir/$dir.csr" \
      -passout "env:$var" -subj "/CN=$real" 2>/dev/null
  openssl x509 -req -in "$dir/$dir.csr" -CA "$dir/ca-falsa.crt" -CAkey "$tmp/ca-falsa.key" \
      -CAcreateserial -CAserial "$tmp/ca-falsa.srl" -days "$DIAS_EQUIPO" -sha256 \
      -extfile cliente.ext -out "$dir/$dir.crt" 2>/dev/null
  chmod 600 "$dir/$dir.key"
  unset "$var"
  ok "$dir/$dir.crt: CN=$real firmado por una CA inventada"

  # 2) Certificado auténtico con el último byte de la firma cambiado (un bit).
  #    Se cambia en el DER y no en el texto: en un PEM acabado en '=' la última
  #    letra lleva bits de relleno, y cambiarla puede dejar el certificado igual.
  local der="$tmp/alterado.der" tam byte
  openssl x509 -in "$real/$real.crt" -outform DER -out "$der"
  tam=$(stat -c %s "$der")
  byte=$(tail -c 1 "$der" | od -An -tu1 | tr -d ' ')
  printf "\\x$(printf '%02x' $((byte ^ 1)))" | dd of="$der" bs=1 seek=$((tam - 1)) conv=notrunc 2>/dev/null
  openssl x509 -inform DER -in "$der" -out "$dir/$real-alterado.crt"
  rm -rf "$tmp"
  ok "$dir/$real-alterado.crt: el auténtico con un bit de la firma cambiado"

  # Ninguno de los dos puede validar contra la CA auténtica
  local c
  for c in "$dir/$dir.crt" "$dir/$real-alterado.crt"; do
    if openssl verify -CAfile publico/ca.crt "$c" >/dev/null 2>&1; then
      error "$c VALIDA contra la CA auténtica: no debería"
    fi
    ok "openssl verify rechaza $c"
  done
}

# ---------------------------- MODOS ----------------------------
modo="${1:-}"
case "$modo" in
  todo)
    paso1_carpetas; paso2_ca; paso3_ext
    emitir pasarela "$CN_PASARELA" pasarela.ext
    for c in "${CLIENTES[@]}"; do emitir "$c" "$c" cliente.ext; done
    unset PASS_CA; verificar; generar_config ;;
  pasarela)
    cd "$PKI_DIR" || error "No existe $PKI_DIR: ejecuta primero 'todo'"
    [[ -f ca-privado/ca.key ]] || error "No hay CA en $PKI_DIR"
    paso3_ext; pedir_pass_ca; respaldar pasarela
    emitir pasarela "$CN_PASARELA" pasarela.ext
    unset PASS_CA; verificar; generar_config ;;
  cliente)
    [[ -n "${2:-}" ]] || error "Indica el nombre: ./crear_pki.sh cliente cliente-d"
    [[ "$2" != "$CLIENTE_FALSO" ]] || error "$CLIENTE_FALSO es el cliente falso: usa './crear_pki.sh falso'"
    cd "$PKI_DIR" || error "No existe $PKI_DIR"
    paso3_ext; pedir_pass_ca; respaldar "$2"
    emitir "$2" "$2" cliente.ext
    unset PASS_CA
    mkdir -p "$CONFIG_DIR"
    azul "JSON del cliente en $CONFIG_DIR"
    json_cliente "$(siguiente_n)" "$2" "$2/$2.crt" "$2/$2.key" ;;
  falso)
    cd "$PKI_DIR" || error "No existe $PKI_DIR: ejecuta primero 'todo'"
    [[ -f cliente.ext ]] || paso3_ext
    cliente_falso; generar_config ;;
  config)
    [[ -d "$PKI_DIR" ]] || error "No existe $PKI_DIR: ejecuta primero 'todo'"
    generar_config ;;
  verificar)
    verificar ;;
  *)
    sed -n '6,18p' "$0"; exit 1 ;;
esac