# Brocantes & Vide-Greniers — Calendrier automatique

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

## Démarrage rapide

```bash
# Cloner le dépôt
git clone <url> brocantes-app
cd brocantes-app

# Lancer avec Docker Compose
docker compose up -d --build

# L'interface est disponible sur :
open http://localhost:8642
```

Le dossier `./data/` est monté comme volume Docker : la configuration et
les événements survivent aux redémarrages du conteneur.

---

## Déploiement sur Unraid

### Prérequis

| Élément | Détail |
|---|---|
| Unraid | 6.9 ou supérieur |
| Git | Plugin **NerdTools** (Unraid App Store) ou `opkg install git` |
| Accès SSH | Activé dans Réglages → Gestion SSH |

### Installation en 3 commandes

```bash
# 1. SSH dans Unraid, puis cloner le dépôt
git clone https://github.com/<user>/brocantes-app /mnt/user/repos/brocantes-app

# 2. Lancer le script (build + démarrage automatiques)
bash /mnt/user/repos/brocantes-app/scripts/unraid-setup.sh

# 3. Accéder à l'interface
# http://IP_UNRAID:8642
```

Le script est **idempotent** : le relancer suffit pour mettre à jour
(git pull + rebuild + recréation du conteneur, données conservées).

### Variables optionnelles

```bash
PORT=8123 DATA_DIR=/mnt/cache/appdata/brocantes-app \
  bash /mnt/user/repos/brocantes-app/scripts/unraid-setup.sh
```

| Variable | Défaut | Description |
|---|---|---|
| `PORT` | `8642` | Port exposé sur Unraid |
| `DATA_DIR` | `/mnt/user/appdata/brocantes-app` | Dossier appdata persistant |
| `IMAGE` | `brocantes-app:latest` | Nom de l'image Docker locale |
| `CONTAINER` | `brocantes-app` | Nom du conteneur |

### Intégration à l'interface Docker d'Unraid (template)

Pour que le conteneur apparaisse dans l'onglet Docker d'Unraid avec
tous les champs pré-remplis :

```bash
# Copier le template dans le dossier Unraid
cp /mnt/user/repos/brocantes-app/unraid/brocantes-app.xml \
   /boot/config/plugins/dockerMan/templates-user/
```

Puis dans l'interface Unraid :
**Docker → Add Container** → choisir **brocantes-app** dans la liste de templates.

> ⚠️ L'image `brocantes-app:latest` doit être construite **avant** d'appliquer
> le template (via le script ci-dessus). Elle est locale, pas sur Docker Hub.

### Structure des données sur Unraid

```
/mnt/user/appdata/brocantes-app/   ← DATA_DIR (volume Docker)
├── config.json                     ← localisation + rayon + fréquence
├── events.json                     ← événements scrappés
└── geocache.json                   ← cache Nominatim (lieux → lat/lng)
```

Ces fichiers survivent aux mises à jour et aux redémarrages du conteneur.

### Mise à jour

```bash
# Depuis n'importe où — le script fait git pull + rebuild + restart
bash /mnt/user/repos/brocantes-app/scripts/unraid-setup.sh
```

### Flux ICS depuis Unraid → iPhone

L'URL du flux ICS à utiliser est :
```
http://IP_UNRAID:8642/feed.ics
```

Remplacer `IP_UNRAID` par l'IP fixe de votre NAS Unraid
(conseillé : réserver l'IP via DHCP sur votre box/routeur).

---

## Structure des fichiers

```
brocantes-app/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── data/                  ← volume Docker (config.json + events.json)
└── app/
    ├── __init__.py
    ├── main.py            ← FastAPI, routes, scheduler
    ├── scraper.py         ← scraping multi-sources + parseur dates françaises
    ├── calendar_gen.py    ← génération ICS
    ├── config.py          ← lecture/écriture JSON dans ./data/
    └── static/
        └── index.html     ← frontend complet
```

## Endpoints API

| Méthode | URL | Description |
|---|---|---|
| GET | `/` | Interface web |
| GET | `/feed.ics` | Flux ICS (abonnement calendrier) |
| GET | `/api/config` | Configuration actuelle |
| POST | `/api/config` | Mettre à jour la config + refresh immédiat |
| GET | `/api/events` | Liste des événements + count + last_refresh |
| POST | `/api/refresh` | Forcer un nouveau scan |
| GET | `/api/status` | Statut du service |

## Sources de données

- **brocabrac.fr** — `https://brocabrac.fr/brocantes-vide-greniers?localisation={lat},{lng}&rayon={km}`
- **vide-greniers.org** — `https://vide-greniers.org/recherche?lat={lat}&lng={lng}&distance={km}`

Le scraper tente d'abord les blocs `<script type="application/ld+json">` (JSON-LD `Event`),
puis parcourt une liste de sélecteurs CSS en fallback. Les événements passés sont filtrés,
les doublons supprimés par hash MD5 de `titre|date|lieu`.

## Abonnement iPhone / iPad

1. **Réglages** → Calendrier → **Comptes**
2. → Ajouter un compte → **Autre**
3. → **S'abonner à un calendrier**
4. Coller l'URL : `http://<votre-serveur>:8642/feed.ics`
5. Appuyer sur **Suivant** puis **Enregistrer**

Le calendrier se met à jour automatiquement selon la fréquence configurée
(6 h / 12 h / 24 h — `X-PUBLISHED-TTL: PT12H` dans le flux).

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `DATA_DIR` | `./data` | Répertoire de persistance JSON |

## Développement local (sans Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p data
uvicorn app.main:app --reload --port 8000
```

Puis ouvrir http://localhost:8000.
