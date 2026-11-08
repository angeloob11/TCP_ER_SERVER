#!/usr/bin/env bash
# =====================================================================
#  crear_pki.sh · PKI de la práctica «Pasarela TLS para el UR3e»
#  Reproduce los pasos 1-7 y 9 del enunciado con parámetros editables.
#
#  Uso:
#    ./crear_pki.sh todo        Crea la PKI completa (CA + pasarela + clientes)
#    ./crear_pki.sh pasarela    Reemite SOLO el certificado de la pasarela
#                               (lo único que cambia si cambia la IP)
#    ./crear_pki.sh cliente X   Emite un cliente nuevo con CN=X (p. ej. cliente-c)
#    ./crear_pki.sh verificar   Comprueba todo y genera informe_E1.txt
#
#  Los parámetros se cambian en la sección CONFIGURACIÓN o en un fichero
#  pki.conf junto al script (tiene prioridad). Las contraseñas se piden
#  por teclado y no se guardan en ningún sitio.
# =====================================================================
set -euo pipefail

# ----------------------------- CONFIGURACIÓN -----------------------------
PKI_DIR="$HOME/pki"                                  # carpeta de trabajo
IP_PASARELA="10.172.21.30"                           # va en la SAN: la que asigne el profesor
CN_CA="CA Robot Lab - Estudiante Angel Ardila"
CN_PASARELA="pasarela-ur3e"
CLIENTES=("cliente-a" "cliente-b")
DIAS_CA=730
DIAS_EQUIPO=365
BITS=2048
# -------------------------------------------------------------------------

DIR_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

# ---------------------------- MODOS ----------------------------
modo="${1:-}"
case "$modo" in
  todo)
    paso1_carpetas; paso2_ca; paso3_ext
    emitir pasarela "$CN_PASARELA" pasarela.ext
    for c in "${CLIENTES[@]}"; do emitir "$c" "$c" cliente.ext; done
    unset PASS_CA; verificar ;;
  pasarela)
    cd "$PKI_DIR" || error "No existe $PKI_DIR: ejecuta primero 'todo'"
    [[ -f ca-privado/ca.key ]] || error "No hay CA en $PKI_DIR"
    paso3_ext; pedir_pass_ca; respaldar pasarela
    emitir pasarela "$CN_PASARELA" pasarela.ext
    unset PASS_CA; verificar ;;
  cliente)
    [[ -n "${2:-}" ]] || error "Indica el nombre: ./crear_pki.sh cliente cliente-c"
    cd "$PKI_DIR" || error "No existe $PKI_DIR"
    paso3_ext; pedir_pass_ca; respaldar "$2"
    emitir "$2" "$2" cliente.ext
    unset PASS_CA ;;
  verificar)
    verificar ;;
  *)
    sed -n '5,13p' "$0"; exit 1 ;;
esac