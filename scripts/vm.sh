#!/usr/bin/env bash
# Collector helper for the Oracle VM, run by the owner:  bash scripts/vm.sh <command>
#   install    checks, .env questions, build, start, wait until healthy, safety checks
#   status     health, caps, ports, disk use, last important log lines
#   configure  change contact e-mail / HF dataset / HF token, then restart
#   update     git pull, rebuild, restart
#   start | stop | uninstall
# It only touches this folder, the tc-collector container and its image: never another
# container, never system configuration. The HF token is never printed.
set -euo pipefail

CONTAINER=tc-collector
DOCKER=(docker)

say() { printf '\n== %s\n' "$*"; }
ok() { printf '  OK   %s\n' "$*"; }
warn() { printf '  !!   %s\n' "$*"; }
die() {
  printf '  STOP %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

docker_cmd() { "${DOCKER[@]}" "$@"; }
dc() { docker_cmd compose -f "$ROOT/docker/compose.yaml" "$@"; }

init_docker() {
  command -v docker >/dev/null || die "docker n'est pas installé. N'installez rien au hasard : demandez d'abord."
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    die "docker ne répond pas (service arrêté ?)."
  fi
  docker_cmd compose version >/dev/null 2>&1 ||
    die "le plugin « docker compose » (v2) est absent. N'installez rien : envoyez-moi la sortie de « docker-compose --version » et « docker ps »."
}

# ---- .env ----------------------------------------------------------------------------------

get_env() {
  grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

set_env() { # set_env KEY VALUE: replace (or add) KEY= in .env; the value is never printed
  local tmp
  tmp=$(mktemp "$ROOT/.env.XXXXXX")
  if ! K="$1" V="$2" awk '
      index($0, ENVIRON["K"] "=") == 1 { print ENVIRON["K"] "=" ENVIRON["V"]; found = 1; next }
      { print }
      END { if (!found) print ENVIRON["K"] "=" ENVIRON["V"] }' "$ROOT/.env" >"$tmp"; then
    rm -f "$tmp"
    die "impossible de mettre à jour .env"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$ROOT/.env"
}

configure() {
  say "Configuration (.env) — Entrée = garder la valeur actuelle"
  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    ok ".env créé à partir de .env.example"
  fi
  chmod 600 "$ROOT/.env"
  local v cur hint
  cur=$(get_env CONTACT_EMAIL)
  read -r -p "  Adresse de contact (envoyée aux serveurs des flux) [${cur:-vide}] : " v
  if [ -n "$v" ]; then set_env CONTACT_EMAIL "$v"; fi

  cur=$(get_env HF_REPO)
  read -r -p "  Dataset Hugging Face, forme pseudo/nom [${cur:-vide}] : " v
  if [ -n "$v" ]; then
    if [[ ! "$v" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
      warn "« $v » n'a pas la forme pseudo/nom : vérifiez sur la page du dataset"
    fi
    set_env HF_REPO "$v"
  fi

  if [ -n "$(get_env HF_TOKEN)" ]; then hint="déjà renseigné"; else hint="vide = dry-run"; fi
  read -r -s -p "  Jeton Hugging Face hf_... (rien ne s'affiche) [$hint] : " v
  printf '\n'
  if [ -n "$v" ]; then
    if [[ "$v" != hf_* ]]; then warn "le jeton ne commence pas par hf_ : vérifiez le copier-coller"; fi
    set_env HF_TOKEN "$v"
  fi

  if [ -n "$(get_env CONTACT_EMAIL)" ]; then ok "contact renseigné"; else warn "pas d'adresse de contact"; fi
  if [ -n "$(get_env HF_REPO)" ]; then ok "dataset : $(get_env HF_REPO)"; fi
  if [ -n "$(get_env HF_TOKEN)" ] && [ -n "$(get_env HF_REPO)" ]; then
    ok "jeton renseigné : envoi quotidien vers Hugging Face activé"
  else
    warn "jeton ou dataset vide : DRY-RUN, rien n'est envoyé (relancez 'bash scripts/vm.sh configure' plus tard)"
  fi
}

# ---- checks --------------------------------------------------------------------------------

list_ports() { ss -tuln | awk 'NR > 1 { print $1, $5 }' | sort -u; }

check_prereqs() {
  say "Vérifications (lecture seule)"
  if [ "$(hostname)" = "cloudshell" ] || [ -n "${OCI_CLI_CLOUD_SHELL:-}" ]; then
    die "vous êtes dans Oracle Cloud Shell (le terminal du navigateur), pas sur la VM. Connectez-vous d'abord à la VM (ssh), puis relancez ce script là-bas."
  fi
  ok "architecture : $(uname -m)"
  command -v git >/dev/null || die "git est absent"
  init_docker
  ok "docker et docker compose v2 présents ($(docker_cmd compose version --short 2>/dev/null || echo '?'))"
  local avail free_mb
  avail=$(awk '/MemAvailable/ { print int($2 / 1024) }' /proc/meminfo)
  if [ "$avail" -ge 400 ]; then
    ok "mémoire disponible : $avail Mo"
  else
    warn "seulement $avail Mo disponibles : le collecteur peut monter vers 350 Mo (plafond dur 768 Mo)"
  fi
  free_mb=$(df -Pm "$ROOT" | awk 'NR == 2 { print $4 }')
  if [ "$free_mb" -ge 5000 ]; then
    ok "disque libre : $free_mb Mo"
  else
    warn "disque libre faible : $free_mb Mo (le collecteur limite data/ à MAX_LOCAL_MB)"
  fi
  if command -v systemctl >/dev/null; then
    if systemctl is-enabled docker >/dev/null 2>&1; then
      ok "docker démarre au boot : le collecteur repartira seul après un redémarrage de la VM"
    else
      warn "docker n'est pas activé au boot : après un redémarrage de la VM, relancez 'bash scripts/vm.sh start'"
    fi
  fi
}

prepare_data() {
  mkdir -p "$ROOT/data" "$ROOT/logs"
  if [ "$(stat -c %u "$ROOT/data")" != "10001" ]; then
    say "Droits du dossier data/ (le conteneur tourne sous l'utilisateur 10001, non-root)"
    sudo chown 10001:10001 "$ROOT/data"
    ok "data/ appartient maintenant à 10001"
  fi
}

wait_healthy() {
  say "Attente de l'état « healthy » (jusqu'à 4 minutes)"
  local s=absent
  for _ in $(seq 1 48); do
    s=$(docker_cmd inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo absent)
    case "$s" in
      healthy)
        ok "conteneur healthy"
        return 0
        ;;
      unhealthy) break ;;
    esac
    sleep 5
  done
  warn "état : $s"
  dc logs --tail 40 || true
  return 1
}

verify() {
  say "Sécurité de l'application existante"
  docker_cmd stats --no-stream --format '  ..   mémoire {{.MemUsage}}   cpu {{.CPUPerc}}' "$CONTAINER"
  local caps ports mode
  caps=$(docker_cmd inspect -f '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}' "$CONTAINER")
  if [ "$caps" = "500000000 805306368 128" ]; then
    ok "plafonds appliqués : 0,5 CPU, 768 Mo de mémoire, 128 processus"
  else
    warn "plafonds inattendus : $caps"
  fi
  ports=$(docker_cmd port "$CONTAINER" 2>/dev/null || true)
  if [ -z "$ports" ]; then ok "aucun port publié"; else warn "ports publiés : $ports"; fi
  mode=$(docker_cmd inspect -f '{{.HostConfig.NetworkMode}}' "$CONTAINER")
  if [ "$mode" != "host" ]; then ok "réseau isolé ($mode)"; else warn "réseau « host » : anormal"; fi
  if [ -f "$ROOT/logs/ports-before.txt" ] && command -v ss >/dev/null; then
    list_ports >"$ROOT/logs/ports-after.txt"
    if diff -q "$ROOT/logs/ports-before.txt" "$ROOT/logs/ports-after.txt" >/dev/null; then
      ok "ports ouverts de la VM identiques à avant l'installation"
    else
      warn "les ports de la VM ont changé depuis l'installation (souvent l'autre application, ex. un appel en cours) :"
      diff "$ROOT/logs/ports-before.txt" "$ROOT/logs/ports-after.txt" | sed 's/^/       /' || true
    fi
  fi
}

# ---- commands ------------------------------------------------------------------------------

restart_if_running() {
  init_docker
  if docker_cmd inspect "$CONTAINER" >/dev/null 2>&1; then
    dc up -d --force-recreate
    wait_healthy || die "le collecteur n'est pas healthy : copiez les lignes ci-dessus"
  fi
}

install() {
  check_prereqs
  mkdir -p "$ROOT/logs"
  if [ ! -f "$ROOT/logs/ports-before.txt" ] && command -v ss >/dev/null; then
    list_ports >"$ROOT/logs/ports-before.txt"
    ok "photo des ports ouverts enregistrée (logs/ports-before.txt)"
  fi
  if [ -f "$ROOT/.env" ] && [ -n "$(get_env HF_REPO)" ]; then
    ok ".env déjà configuré (pour le modifier : bash scripts/vm.sh configure)"
  else
    configure
  fi
  prepare_data
  say "Construction et démarrage (quelques minutes, plus sur ARM)"
  dc up -d --build
  wait_healthy || die "le collecteur n'est pas healthy : copiez les lignes ci-dessus"
  verify
  say "En place. Tout est automatique à partir de maintenant :"
  printf '  - collecte toutes les 2 minutes, 24 h/24 ; redémarrage seul en cas de plantage\n'
  printf '  - chaque nuit après 03:00 (Paris) : envoi de la veille sur Hugging Face, puis nettoyage\n'
  printf '  - demain matin : bash scripts/vm.sh status   (cherchez « day uploaded »)\n'
}

status() {
  init_docker
  say "État du collecteur"
  local s
  s=$(docker_cmd inspect -f '{{.State.Status}}, santé {{.State.Health.Status}}, redémarrages {{.RestartCount}}, tué faute de mémoire {{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null) ||
    die "conteneur $CONTAINER absent : lancez 'bash scripts/vm.sh install'"
  ok "$s"
  dc exec -T collector python -m tc.health || warn "heartbeat trop ancien : la boucle est bloquée"
  verify
  say "Disque"
  ok "taille de data/ : $(du -sh "$ROOT/data" 2>/dev/null | cut -f1) (plafond MAX_LOCAL_MB, 2 Go par défaut)"
  say "Évènements importants des dernières 48 h"
  dc logs --no-log-prefix --since 48h 2>/dev/null |
    grep -E '"(ERROR|WARNING)"|day uploaded|upload failed|dry-run|disk cap|collector started' |
    tail -n 15 || true
}

update() {
  init_docker
  say "Mise à jour du code"
  git -C "$ROOT" pull --ff-only
  dc up -d --build
  wait_healthy || die "le collecteur n'est pas healthy après la mise à jour : copiez les lignes ci-dessus"
  verify
}

uninstall() {
  init_docker
  local n answer
  n=$(find "$ROOT/data/reduced" "$ROOT/data/alerts" "$ROOT/data/raw" -type f 2>/dev/null | wc -l)
  if [ "$n" -gt 0 ]; then
    warn "$n fichier(s) de données encore sur la VM (dont la journée en cours, pas encore envoyée)"
  fi
  read -r -p "  Tapez OUI pour supprimer le conteneur et l'image du collecteur : " answer
  [ "$answer" = "OUI" ] || die "annulé, rien n'a été supprimé"
  dc down --rmi local
  ok "conteneur et image supprimés. Le dossier $ROOT (et data/) reste : supprimez-le vous-même si besoin."
}

main() {
  ROOT=$(cd "$(dirname "$0")/.." && pwd)
  cd "$ROOT"
  case "${1:-help}" in
    install) install ;;
    status) status ;;
    configure)
      configure
      restart_if_running
      ;;
    update) update ;;
    start)
      init_docker
      dc up -d
      wait_healthy || die "le collecteur n'est pas healthy : copiez les lignes ci-dessus"
      ;;
    stop)
      init_docker
      dc down
      ok "arrêté proprement (l'heure en cours a été écrite, données conservées dans data/)"
      ;;
    uninstall) uninstall ;;
    *) usage ;;
  esac
}

# Sourcing the file (tests) defines the functions without running anything.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
