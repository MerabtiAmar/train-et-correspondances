#!/usr/bin/env bash
# Create the dedicated collector VM "tc-collector" (Always Free), from Oracle Cloud Shell:
#   curl -fsSL https://raw.githubusercontent.com/MerabtiAmar/train-et-correspondances/main/scripts/oci_create_vm.sh | bash
# Creates one instance in the same compartment and subnet as the family VM "siwel-turn", using an
# Always Free shape: VM.Standard.A1.Flex (1 OCPU, 6 GB), or VM.Standard.E2.1.Micro when A1 has no
# capacity. Never modifies siwel-turn, the network or the security lists. Safe to re-run: if
# tc-collector already exists, it only prints its IP.
set -euo pipefail

NAME=tc-collector
REF=siwel-turn
KEYFILE="$HOME/tc-collector-keys.pub"
ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT

say() { printf '\n== %s\n' "$*"; }
ok() { printf '  OK   %s\n' "$*"; }
warn() { printf '  !!   %s\n' "$*"; }
die() {
  printf '  STOP %s\n' "$*" >&2
  exit 1
}

command -v oci >/dev/null || die "outil « oci » absent : lancez ce script dans Oracle Cloud Shell."

find_instance() { # display name -> OCID of a non-terminated instance (empty if none)
  oci search resource structured-search \
    --query-text "query instance resources where displayName = '$1' && lifecycleState != 'TERMINATED'" \
    --query 'data.items[0].identifier' --raw-output 2>/dev/null || true
}

public_ip() {
  oci compute instance list-vnics --instance-id "$1" --query 'data[0]."public-ip"' --raw-output
}

say "Déjà créée ?"
existing=$(find_instance "$NAME")
if [ -n "$existing" ] && [ "$existing" != "null" ]; then
  ok "$NAME existe déjà"
  printf '\n  IP publique de %s : %s\n  -> envoyez cette IP à Claude.\n' "$NAME" "$(public_ip "$existing")"
  exit 0
fi
ok "non, création"

say "Réseau de $REF (réutilisé tel quel, rien n'y est modifié)"
ref=$(find_instance "$REF")
if [ -z "$ref" ] || [ "$ref" = "null" ]; then
  die "instance $REF introuvable dans la région ${OCI_REGION:-courante} : ouvrez Cloud Shell dans la région de $REF (Marseille)."
fi
C=$(oci compute instance get --instance-id "$ref" --query 'data."compartment-id"' --raw-output)
AD=$(oci compute instance get --instance-id "$ref" --query 'data."availability-domain"' --raw-output)
SUBNET=$(oci compute instance list-vnics --instance-id "$ref" --query 'data[0]."subnet-id"' --raw-output)
ok "domaine de disponibilité : $AD"

# Public keys only (safe to publish): Claude's dedicated key and the owner's own key.
cat >"$KEYFILE" <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID+QLMGNXFUv+FlhEwtYqFqO5MuCE+AtYFUj+LlIFCrv tc-collector-claude-code
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCX5kiRL/oXnpu6wUiU0YLlv0uhW6lqnhFCi+Dj8JbEA3e7I2Fjs1GF6qbCtj2KlGnVwEiQxrUaN+xpaUpDu+jm7O/husrNn+Z07DsCfEVQDCjwN2/Mb2kf3sCNijCzFqj7nTA1dwvesjoqHQoOGPgU7RDMjjzvXBOfyjs8YDOWcMbB8z5wAlZ0qcUOGgjIivp5hlMlajsmOCaxQt0jJqmaOV8JNTdXU4s7EgjLtDvplfpS1v5g4aVPAhcb0KZCtGpcYLU5aJgMT149Qp2ASblZSkgex9sk0sEkvh2MIYNnf90Rx08WL77rhfONl1PzIlXhu5ylHy8gN5suCTTfBgnx ssh-key-2026-06-13
EOF

image_for() { # latest Canonical Ubuntu 24.04 image compatible with a shape
  oci compute image list --compartment-id "$C" --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" --shape "$1" --sort-by TIMECREATED --sort-order DESC \
    --limit 1 --query 'data[0].id' --raw-output
}

launch() { # shape image [shape-config]
  local args=(compute instance launch --compartment-id "$C" --availability-domain "$AD"
    --display-name "$NAME" --shape "$1" --image-id "$2" --subnet-id "$SUBNET"
    --assign-public-ip true --ssh-authorized-keys-file "$KEYFILE"
    --wait-for-state RUNNING --max-wait-seconds 900 --query 'data.id' --raw-output)
  if [ -n "${3:-}" ]; then args+=(--shape-config "$3"); fi
  oci "${args[@]}"
}

say "Création de $NAME (Always Free) — 1 à 5 minutes"
SHAPE=VM.Standard.A1.Flex
if ID=$(launch "$SHAPE" "$(image_for "$SHAPE")" '{"ocpus": 1, "memoryInGBs": 6}' 2>"$ERR"); then
  ok "créée en $SHAPE (1 OCPU, 6 Go)"
elif grep -qE 'Out of host capacity|LimitExceeded|QuotaExceeded' "$ERR"; then
  warn "Ampere A1 indisponible pour l'instant : $(grep -oE 'Out of host capacity|LimitExceeded|QuotaExceeded' "$ERR" | head -n 1)"
  SHAPE=VM.Standard.E2.1.Micro
  ID=$(launch "$SHAPE" "$(image_for "$SHAPE")" "" 2>"$ERR") || {
    sed 's/^/       /' "$ERR" >&2
    die "création impossible aussi en $SHAPE (copiez ces lignes à Claude)"
  }
  ok "créée en $SHAPE (1 Go de RAM)"
else
  sed 's/^/       /' "$ERR" >&2
  die "création impossible (copiez ces lignes à Claude)"
fi

printf '\n  IP publique de %s : %s   (forme %s)\n  -> envoyez cette IP à Claude.\n' \
  "$NAME" "$(public_ip "$ID")" "$SHAPE"
