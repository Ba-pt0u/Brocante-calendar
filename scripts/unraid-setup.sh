#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Brocantes App — script d'installation / mise à jour pour Unraid
#
# Usage :
#   bash scripts/unraid-setup.sh                     # valeurs par défaut
#   PORT=8123 bash scripts/unraid-setup.sh           # port personnalisé
#   DATA_DIR=/mnt/cache/appdata/brocantes-app bash scripts/unraid-setup.sh
#
# Ce script est idempotent : il peut être relancé pour mettre à jour
# (git pull + rebuild + recréation du conteneur avec les mêmes volumes/ports).
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

IMAGE="${IMAGE:-brocantes-app:latest}"
CONTAINER="${CONTAINER:-brocantes-app}"
PORT="${PORT:-8642}"
DATA_DIR="${DATA_DIR:-/mnt/user/appdata/brocantes-app}"

# Répertoire racine du dépôt (parent de ce script)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Bannière ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🛍️  Brocantes App — Unraid Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "  Image     : %s\n"  "$IMAGE"
printf "  Conteneur : %s\n"  "$CONTAINER"
printf "  Port      : %s\n"  "$PORT"
printf "  Données   : %s\n"  "$DATA_DIR"
printf "  Source    : %s\n"  "$REPO"
echo ""

# ── 1. Mise à jour du code source (si dépôt git) ─────────────────────────────
if git -C "$REPO" rev-parse --is-inside-work-tree &>/dev/null; then
  echo "▶ git pull…"
  git -C "$REPO" pull --ff-only
else
  echo "⚠  Pas de dépôt git détecté, étape git pull ignorée."
fi

# ── 2. Construction de l'image Docker ────────────────────────────────────────
echo "▶ docker build -t $IMAGE …"
docker build -t "$IMAGE" "$REPO"

# ── 3. Création du dossier de données ────────────────────────────────────────
mkdir -p "$DATA_DIR"
echo "▶ Dossier données : $DATA_DIR"

# ── 4. Arrêt / suppression de l'ancien conteneur ─────────────────────────────
if docker ps -aq --filter "name=^${CONTAINER}$" | grep -q .; then
  echo "▶ Suppression de l'ancien conteneur…"
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  docker rm   "$CONTAINER" >/dev/null 2>&1 || true
fi

# ── 5. Démarrage du nouveau conteneur ────────────────────────────────────────
echo "▶ docker run…"
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
