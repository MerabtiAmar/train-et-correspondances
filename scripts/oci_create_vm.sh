#!/usr/bin/env bash
# Create the dedicated collector VM "tc-collector" (Always Free), from Oracle Cloud Shell:
#   curl -fsSL https://raw.githubusercontent.com/MerabtiAmar/train-et-correspondances/main/scripts/oci_create_vm.sh | bash
# Creates one instance in the same compartment and subnet as the family VM "siwel-turn", using an
# Always Free shape: VM.Standard.A1.Flex (1 OCPU, 6 GB), or VM.Standard.E2.1.Micro when A1 is
# unavailable (no capacity, or Oracle throttling free A1 requests with HTTP 429). Never modifies
# siwel-turn, the network or the security lists. Safe to re-run: if tc-collector already exists,
# it only prints its IP, so it can never create two VMs.
set -euo pipefail

NAME=tc-collector
REF=siwel-turn
KEYFILE="$HOME/tc-collector-keys.pub"
A1_CONFIG='{"ocpus": 1, "memoryInGBs": 6}'
ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT

say() { printf '\n== %s\n' "$*"; }
ok() { printf '  OK   %s\n' "$*"; }
warn() { printf '  !!   %s\n' "$*"; }
die() {
  printf '  STOP %s\n' "$*" >&2
  exit 1
}
show_err() { sed 's/^/       /' "$ERR" >&2; }

command -v oci >/dev/null || die "outil « oci » absent : lancez ce script dans Oracle Cloud Shell."

say "Réseau de $REF (réutilisé tel quel, rien n'y est modifié)"
ref=$(oci search resource structured-search \
  --query-text "query instance resources where displayName = '$REF' && lifecycleState = 'RUNNING'" \
  --query 'data.items[0].identifier' --raw-output 2>/dev/null || true)
if [ -z "$ref" ] || [ "$ref" = "null" ]; then
  die "instance $REF introuvable dans la région ${OCI_REGION:-courante} : ouvrez Cloud Shell dans la région de $REF (Marseille)."
fi
C=$(oci compute instance get --instance-id "$ref" --query 'data."compartment-id"' --raw-output)
AD=$(oci compute instance get --instance-id "$ref" --query 'data."availability-domain"' --raw-output)
SUBNET=$(oci compute instance list-vnics --instance-id "$ref" --query 'data[0]."subnet-id"' --raw-output)
ok "domaine de disponibilité : $AD"

existing() { # OCID of a non-terminated tc-collector (direct API, no search-index delay)
  oci compute instance list --compartment-id "$C" --display-name "$NAME" --all \
    --query 'data[?"lifecycle-state" != `TERMINATED`].id | [0]' --raw-output 2>/dev/null || true
}

done_with() { # OCID -> print the IP and stop
  local ip
  ip=$(oci compute instance list-vnics --instance-id "$1" --query 'data[0]."public-ip"' --raw-output)
  local shape
  shape=$(oci compute instance get --instance-id "$1" --query 'data.shape' --raw-output)
  printf '\n  IP publique de %s : %s   (forme %s)\n  -> envoyez cette ligne à Claude.\n' "$NAME" "$ip" "$shape"
  exit 0
}

say "Déjà créée ?"
id=$(existing)
if [ -n "$id" ] && [ "$id" != "null" ]; then
  ok "$NAME existe déjà"
  done_with "$id"
fi
ok "non, création"

# Public keys only (safe to publish): Claude's dedicated key and the owner's own key.
cat >"$KEYFILE" <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID+QLMGNXFUv+FlhEwtYqFqO5MuCE+AtYFUj+LlIFCrv tc-collector-claude-code
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCX5kiRL/oXnpu6wUiU0YLlv0uhW6lqnhFCi+Dj8JbEA3e7I2Fjs1GF6qbCtj2KlGnVwEiQxrUaN+xpaUpDu+jm7O/husrNn+Z07DsCfEVQDCjwN2/Mb2kf3sCNijCzFqj7nTA1dwvesjoqHQoOGPgU7RDMjjzvXBOfyjs8YDOWcMbB8z5wAlZ0qcUOGgjIivp5hlMlajsmOCaxQt0jJqmaOV8JNTdXU4s7EgjLtDvplfpS1v5g4aVPAhcb0KZCtGpcYLU5aJgMT149Qp2ASblZSkgex9sk0sEkvh2MIYNnf90Rx08WL77rhfONl1PzIlXhu5ylHy8gN5suCTTfBgnx ssh-key-2026-06-13
EOF

image_for() { # latest Canonical Ubuntu 24.04 image compatible with a shape
  oci compute image list --compartment-id "$C" --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" --shape "$1" --sort-by TIMECREATED --sort-order DESC \
    --limit 1 --query 'data[0].id' --raw-output 2>/dev/null || true
}

launch() { # shape image [shape-config] -> prints the new OCID
  local args=(compute instance launch --compartment-id "$C" --availability-domain "$AD"
    --display-name "$NAME" --shape "$1" --image-id "$2" --subnet-id "$SUBNET"
    --assign-public-ip true --ssh-authorized-keys-file "$KEYFILE"
    --wait-for-state RUNNING --max-wait-seconds 900 --query 'data.id' --raw-output)
  if [ -n "${3:-}" ]; then args+=(--shape-config "$3"); fi
  oci "${args[@]}"
}

try_shape() { # shape config -> 0 created (done_with exits), 2 try the next shape, 1 fatal
  local shape=$1 config=$2 img attempt new
  img=$(image_for "$shape")
  if [ -z "$img" ] || [ "$img" = "null" ]; then
    warn "aucune image Ubuntu 24.04 pour $shape"
    return 2
  fi
  for attempt in 1 2 3; do
    if new=$(launch "$shape" "$img" "$config" 2>"$ERR"); then
      ok "créée en $shape"
      done_with "$new"
    fi
    # The request may have created the VM before failing (e.g. while waiting): never launch twice.
    new=$(existing)
    if [ -n "$new" ] && [ "$new" != "null" ]; then
      ok "créée en $shape (l'attente a échoué, mais la VM existe)"
      done_with "$new"
    fi
    if grep -q 'TooManyRequests' "$ERR"; then
      warn "Oracle rationne les demandes (429) pour $shape : nouvel essai dans 60 s ($attempt/3)"
      sleep 60
    elif grep -qE 'Out of host capacity|LimitExceeded|QuotaExceeded' "$ERR"; then
      warn "$shape indisponible : $(grep -oE 'Out of host capacity|LimitExceeded|QuotaExceeded' "$ERR" | head -n 1)"
      return 2
    else
      return 1
    fi
  done
  warn "$shape toujours rationné après 3 essais"
  return 2
}

say "Création de $NAME (Always Free) — 1 à 10 minutes"
for shape in VM.Standard.A1.Flex VM.Standard.E2.1.Micro; do
  config=""
  if [ "$shape" = "VM.Standard.A1.Flex" ]; then config=$A1_CONFIG; fi
  rc=0
  try_shape "$shape" "$config" || rc=$?
  if [ "$rc" -eq 1 ]; then
    show_err
    die "création impossible (copiez ces lignes à Claude)"
  fi
done
show_err
die "aucune forme gratuite disponible pour l'instant : relancez la même commande dans une heure."
