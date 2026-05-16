# 🛍️ Brocantes & Vide-Greniers — Calendrier automatique

[![Build & Push Docker image](https://github.com/Ba-pt0u/Brocante-calendar/actions/workflows/docker.yml/badge.svg)](https://github.com/Ba-pt0u/Brocante-calendar/actions/workflows/docker.yml)

Application web Docker qui scrape automatiquement les brocantes et vide-greniers
à proximité d'une localisation configurable et expose un flux ICS pour abonnement
calendrier iPhone / macOS / Google Calendar.

## Stack

| Couche | Technologie |
|---|---|
| Runtime | Python 3.12 |
| Web | FastAPI + Uvicorn |
| Scraping | httpx + BeautifulSoup4 + lxml |
| Calendrier | python-icalendar |
| Planification | APScheduler 3.x (AsyncIO) |
| Géocodage | Nominatim (OpenStreetMap) — aucune clé API |
| Frontend | HTML / CSS / JS pur (single-page) |
| Persistance | Fichiers JSON dans `./data/` (volume Docker) |
| CI/CD | GitHub Actions → ghcr.io (multi-arch amd64 + arm64) |

---

## Démarrage rapide

### Option A — image pré-buildée (recommandé)

```bash
# Créer un dossier de données persistant
mkdir -p data

# Lancer directement depuis ghcr.io (pas de build nécessaire)
docker run -d \
  --name brocantes-app \
  --restart unless-stopped \
  -p 8642:8000 \
  -v "$(pwd)/data:/app/data" \
  -e DATA_DIR=/app/data \
  ghcr.io/ba-pt0u/brocante-calendar:latest

open http://localhost:8642
```

### Option B — build local avec Docker Compose

```bash
git clone https://github.com/Ba-pt0u/Brocante-calendar brocantes-app
cd brocantes-app
docker compose up -d --build
open http://localhost:8642
```

Le dossier `./data/` est monté comme volume Docker : la configuration et
les événements survivent aux redémarrages et aux mises à jour du conteneur.

---

## Déploiement sur Unraid

### Option 1 — Community Applications (custom repo)

Dans l'interface Unraid :

> **Apps → Settings → Extra Templates** → ajouter `Ba-pt0u/Brocante-calendar`

CA découvre automatiquement `templates/brocantes-app.xml`. L'image est
pullée depuis `ghcr.io` — aucun build local requis.

### Option 2 — Script one-liner

```bash
# SSH dans Unraid, puis :
bash <(curl -fsSL https://raw.githubusercontent.com/Ba-pt0u/Brocante-calendar/main/scripts/unraid-setup.sh)
```

Le script **pull depuis ghcr.io** par défaut (build local en fallback si le pull échoue).
Il est idempotent : le relancer suffit pour mettre à jour.

```bash
# Options disponibles
PORT=8123 bash scripts/unraid-setup.sh          # port personnalisé
DATA_DIR=/mnt/cache/appdata/brocantes-app bash scripts/unraid-setup.sh
bash scripts/unraid-setup.sh --build            # forcer le build local
```

| Variable | Défaut | Description |
|---|---|---|
| `PORT` | `8642` | Port exposé sur Unraid |
| `DATA_DIR` | `/mnt/user/appdata/brocantes-app` | Dossier appdata persistant |
| `REMOTE_IMAGE` | `ghcr.io/ba-pt0u/brocante-calendar:latest` | Image distante |
| `CONTAINER` | `brocantes-app` | Nom du conteneur |

### Option 3 — Docker Compose Manager (plugin)

```bash
# Sur Unraid, depuis le répertoire cloné :
cp unraid/docker-compose.unraid.yml /mnt/user/appdata/brocantes-app/docker-compose.yml
# Puis ajouter via le plugin Docker Compose Manager
```

### Option 4 — Template Docker manuel

```bash
cp unraid/brocantes-app.xml /boot/config/plugins/dockerMan/templates-user/
# Docker → Add Container → choisir brocantes-app dans la liste
```

### Structure des données sur Unraid

```
/mnt/user/appdata/brocantes-app/   ← DATA_DIR (volume Docker)
├── config.json                     ← localisation, rayon, fréquence
├── events.json                     ← événements scrappés
└── geocache.json                   ← cache Nominatim (lieux → coordonnées)
```

### Abonnement iPhone depuis Unraid

```
http://IP_UNRAID:8642/feed.ics
```

Réglages → Calendrier → Comptes → Ajouter → Autre → **S'abonner à un calendrier**

---

## Abonnement calendrier (iOS / macOS / Google)

| Étape | Action |
|---|---|
| 1 | **Réglages** → Calendrier → **Comptes** → Ajouter un compte |
| 2 | Choisir **Autre** → **S'abonner à un calendrier** |
| 3 | Coller `http://<serveur>:8642/feed.ics` |
| 4 | **Suivant** → **Enregistrer** |

Le calendrier se synchronise selon `REFRESH-INTERVAL: PT1H` (RFC 7986)
et `X-PUBLISHED-TTL: PT12H`. Le flux supporte les requêtes conditionnelles
**ETag** — iOS ne retélécharge que si le contenu a changé.

Chaque événement dans le calendrier iOS bénéficie de :
- 🗺️ Carte intégrée (GEO + X-APPLE-STRUCTURED-LOCATION)
- 🔔 Rappel automatique (18h la veille pour les week-ends, midi pour les jours de semaine)
- 🔗 Lien direct vers la page de l'événement

---

## API

| Méthode | URL | Description |
|---|---|---|
| `GET` | `/` | Interface web |
| `GET` | `/feed.ics` | Flux ICS avec ETag (abonnement calendrier) |
| `GET` | `/api/config` | Configuration actuelle |
| `POST` | `/api/config` | Mettre à jour la config + refresh immédiat |
| `GET` | `/api/events` | Liste des événements scrappés |
| `POST` | `/api/refresh` | Forcer un nouveau scan |
| `GET` | `/api/status` | Statut détaillé par source |

### POST /api/config — validation

```json
{
  "lat": 45.764,
  "lng": 4.836,
  "city": "Lyon",
  "radius_km": 30,
  "refresh_hours": 12
}
```

| Champ | Type | Contraintes |
|---|---|---|
| `lat` | float | 41.0 – 51.5 (métropole française) |
| `lng` | float | -5.5 – 9.5 (métropole française) |
| `city` | string | non vide |
| `radius_km` | int | 1 – 500 |
| `refresh_hours` | int | 1 – 168 |

Retourne `422` si une contrainte n'est pas respectée.

### GET /api/status — réponse

```json
{
  "last_refresh": "2026-05-16T10:00:00",
  "is_refreshing": false,
  "refresh_count": 3,
  "event_count": 24,
  "config": { "city": "Lyon", "radius_km": 30, "..." : "..." },
  "sources": {
    "brocabrac.fr": {
      "count": 18,
      "strategy": "json-ld",
      "error": null,
      "duration_s": 1.2,
      "last_run": "2026-05-16T10:00:01"
    },
    "vide-greniers.org": {
      "count": 6,
      "strategy": "css",
      "error": null,
      "duration_s": 0.9,
      "last_run": "2026-05-16T10:00:02"
    }
  }
}
```

Le champ `sources` permet de diagnostiquer immédiatement pourquoi un site
retourne 0 événements (`"error": "HTTP 403"`, `"strategy": null`, etc.).

---

## Sources et stratégie de scraping

| Source | URL de recherche |
|---|---|
| brocabrac.fr | `https://brocabrac.fr/brocantes-vide-greniers?localisation={lat},{lng}&rayon={km}` |
| vide-greniers.org | `https://vide-greniers.org/recherche?lat={lat}&lng={lng}&distance={km}` |

**Stratégie par priorité :**
1. JSON-LD `<script type="application/ld+json">` (schéma `Event`) — rapide et fiable
2. CSS selectors en cascade (14 sélecteurs testés en ordre décroissant de spécificité)

**Robustesse :**
- Retry jusqu'à 3 fois avec backoff exponentiel sur erreurs réseau
- Les erreurs HTTP (403, 500…) ne sont pas retentées (résultat déterministe)
- Déduplication par hash MD5 `titre|date|lieu`
- Filtrage automatique des événements passés
- Géocodage Nominatim avec cache persistant (`geocache.json`)

---

## CI/CD

Le workflow `.github/workflows/docker.yml` se déclenche à chaque push sur `main` :

- Build **multi-arch** (`linux/amd64` + `linux/arm64`)
- Push sur `ghcr.io/ba-pt0u/brocante-calendar` avec tags `latest` et `sha-XXXXXXX`
- Cache des layers via GitHub Actions

> **Premier déploiement :** rendre le package public sur GitHub :
> Profile → Packages → brocante-calendar → Package settings → Change visibility → **Public**

---

## Structure du projet

```
brocantes-app/
├── .github/
│   └── workflows/
│       └── docker.yml          ← CI/CD : build + push ghcr.io
├── app/
│   ├── main.py                 ← FastAPI, routes, scheduler APScheduler
│   ├── scraper.py              ← scraping multi-sources, dates françaises, retry
│   ├── calendar_gen.py         ← génération ICS (GEO, VALARM, ETag…)
│   ├── config.py               ← lecture/écriture JSON dans ./data/
│   └── static/
│       └── index.html          ← frontend single-page
├── tests/
│   ├── conftest.py             ← fixtures (isolation I/O, reset état, client)
│   ├── test_api.py             ← 41 tests d'intégration FastAPI
│   ├── test_calendar_gen.py    ← 47 tests unitaires ICS
│   ├── test_config.py          ← 17 tests unitaires config
│   ├── test_date_parser.py     ← 28 tests unitaires parseur de dates
│   └── test_scraper.py         ← 32 tests unitaires + intégration + @live
├── templates/
│   └── brocantes-app.xml       ← template Unraid Community Applications
├── unraid/
│   ├── brocantes-app.xml       ← template Unraid (copie de templates/)
│   └── docker-compose.unraid.yml
├── scripts/
│   └── unraid-setup.sh         ← installation / mise à jour Unraid
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Développement local

```bash
# Installer les dépendances
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Lancer l'application
mkdir -p data
DATA_DIR=./data uvicorn app.main:app --reload --port 8000

# Ouvrir http://localhost:8000
```

## Tests

```bash
# Lancer tous les tests (165 tests, ~1s)
pytest

# Par catégorie
pytest -m unit          # tests purement unitaires
pytest -m integration   # tests avec I/O mockée
pytest -m live          # tests contrat contre les vrais sites (réseau requis)
```

Les tests `@live` vérifient que la structure HTML des sites source n'a pas changé.
Ils **skippent** si le site est inaccessible (réseau, 403) et **échouent** si la
structure a changé de façon à casser le scraper.

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `DATA_DIR` | `./data` | Répertoire de persistance JSON |
