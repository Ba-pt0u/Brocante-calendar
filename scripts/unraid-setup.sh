#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Brocantes App — script d'installation / mise à jour pour Unraid
#
# Par défaut : pull depuis ghcr.io (image pré-buildée par GitHub Actions).
# Fallback   : build local si --build est passé ou si le pull échoue.
#
# Usage :
#   bash scripts/unraid-setup.sh                     # pull ghcr.io (défaut)
#   bash scripts/unraid-setup.sh --build             # build local forcé
#   PORT=8123 bash scripts/unraid-setup.sh           # port personnalisé
#   DATA_DIR=/mnt/cache/appdata/brocantes-app bash scripts/unraid-setup.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE_IMAGE="${REMOTE_IMAGE:-ghcr.io/ba-pt0u/brocante-calendar:latest}"
LOCAL_IMAGE="${LOCAL_IMAGE:-brocantes-app:latest}"
CONTAINER="${CONTAINER:-brocantes-app}"
PORT="${PORT:-8642}"
DATA_DIR="${DATA_DIR:-/mnt/user/appdata/brocantes-app}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE_BUILD=false
[[ "${1:-}" == "--build" ]] && FORCE_BUILD=true

# ── Bannière ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🛍️  Brocantes App — Unraid Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "  Conteneur : %s\n"  "$CONTAINER"
printf "  Port      : %s\n"  "$PORT"
printf "  Données   : %s\n"  "$DATA_DIR"
echo ""

# ── 1. Récupération / build de l'image ───────────────────────────────────────
IMAGE="$REMOTE_IMAGE"
if $FORCE_BUILD; then
  echo "▶ Build local forcé…"
  docker build -t "$LOCAL_IMAGE" "$REPO"
  IMAGE="$LOCAL_IMAGE"
else
  echo "▶ Pull depuis ghcr.io…"
  if docker pull "$REMOTE_IMAGE"; then
    echo "  ✓ Image récupérée : $REMOTE_IMAGE"
  else
    echo "  ⚠ Pull échoué — build local en fallback…"
    docker build -t "$LOCAL_IMAGE" "$REPO"
    IMAGE="$LOCAL_IMAGE"
  fi
fi

# ── 2. Création du dossier de données ────────────────────────────────────────
mkdir -p "$DATA_DIR"
echo "▶ Dossier données : $DATA_DIR"

# ── 3. Arrêt / suppression de l'ancien conteneur ─────────────────────────────
if docker ps -aq --filter "name=^${CONTAINER}$" | grep -q .; then
  echo "▶ Suppression de l'ancien conteneur…"
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  docker rm   "$CONTAINER" >/dev/null 2>&1 || true
fi

# ── 4. Démarrage du nouveau conteneur ────────────────────────────────────────
echo "▶ docker run $IMAGE …"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "${PORT}:8000" \
  -v "${DATA_DIR}:/app/data" \
  -e DATA_DIR=/app/data \
  "$IMAGE"

# ── Résumé ────────────────────────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "IP_UNRAID")
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Brocantes App lancé !"
echo ""
printf "  Interface : http://%s:%s\n"          "$IP" "$PORT"
printf "  Flux ICS  : http://%s:%s/feed.ics\n" "$IP" "$PORT"
echo ""
echo "  Abonnement iPhone :"
echo "  Réglages → Calendrier → Comptes"
echo "  → Ajouter → Autre → S'abonner à un calendrier"
printf "  → Coller  http://%s:%s/feed.ics\n"  "$IP" "$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
