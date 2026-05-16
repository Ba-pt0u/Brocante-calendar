# Brocantes App — contexte projet pour Claude

## Ce que fait ce projet

Application Docker qui scrape les brocantes/vide-greniers (brocabrac.fr + vide-greniers.org),
génère un flux ICS et expose une interface web de configuration.

## Règles de développement

- **Toujours mettre à jour README.md** après chaque modification fonctionnelle.
  La doc doit refléter l'état réel du code (API, options, structure fichiers…).
- Travailler sur la branche `claude/brocantes-scraper-app-elRjg`, merger dans `main` sur demande.
- Lancer `pytest` avant chaque commit — 165 tests doivent passer.
- Ajouter des tests pour toute nouvelle fonctionnalité.

## Stack technique

- **Python 3.12**, FastAPI + Uvicorn, APScheduler 3.x AsyncIO
- **httpx** (async HTTP), BeautifulSoup4 + lxml, python-icalendar
- **Pydantic v2** pour la validation des modèles
- **Nominatim** (OpenStreetMap) pour le géocodage — pas de clé API
- Image publiée sur **ghcr.io** via GitHub Actions (multi-arch amd64+arm64)

## Points d'attention

- `app.config.DATA_DIR`, `CONFIG_FILE`, `EVENTS_FILE` et `app.scraper._GEOCACHE_FILE`
  sont des Path définis au moment de l'import → patcher via `monkeypatch.setattr` dans les tests.
- `app.main.scrape_all` (pas `app.scraper.scrape_all`) doit être patché dans les tests API
  car `main.py` l'importe avec `from app.scraper import scrape_all`.
- `_last_scrape_results` dans `scraper.py` est un dict mutable global — réinitialisé par
  la fixture `reset_app_state` dans `conftest.py`.
- Le `ConfigPayload` Pydantic valide les coordonnées France (lat 41–51.5, lng -5.5–9.5).
- `/feed.ics` supporte les requêtes conditionnelles ETag (304 Not Modified).

## Structure des fichiers clés

```
app/main.py          ← FastAPI app, lifespan, 7 endpoints, ConfigPayload Pydantic
app/scraper.py       ← scrape_all, _scrape_source (retry 3x), _last_scrape_results
app/calendar_gen.py  ← generate_ics (GEO, VALARM, REFRESH-INTERVAL, ETag-friendly)
app/config.py        ← load/save config + events JSON
tests/conftest.py    ← isolated_data, reset_app_state, client fixtures
templates/brocantes-app.xml  ← template Unraid Community Applications (ghcr.io)
.github/workflows/docker.yml ← CI/CD build + push ghcr.io
```
