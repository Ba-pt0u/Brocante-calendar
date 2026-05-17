"""
Tests for the scraping layer.

Three levels:
- unit      : pure-Python logic (make_uid, dedup, geocache I/O)
- integration: full scrape_all() with httpx mocked via pytest-httpx
- live       : real HTTP requests — run with:  pytest -m live
               These are CONTRACT tests: they fail when a site changes its
               structure in a way that breaks our parsers.
"""
import json
import re
import pytest
import httpx
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup

from app.scraper import (
    make_uid,
    parse_french_date,
    _parse_jsonld,
    _parse_cards,
    _find_next_page,
    _load_geocache,
    _save_geocache,
    _haversine_km,
    _slugify,
    _dept_from_postcode,
    _classify_event,
    scrape_all,
    CARD_SELECTORS,
    _last_scrape_results,
)

# ── HTML fixtures (future dates so past-event filter doesn't discard them) ────

BROCABRAC_JSONLD = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
[
  {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Grande Brocante de Lyon",
    "startDate": "2026-07-15",
    "location": {
      "@type": "Place",
      "name": "Place Bellecour",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "Place Bellecour",
        "postalCode": "69002",
        "addressLocality": "Lyon"
      }
    },
    "description": "Brocante mensuelle",
    "url": "https://brocabrac.fr/event/123"
  },
  {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Vide-grenier Villeurbanne",
    "startDate": "2026-07-22",
    "location": {"@type": "Place", "name": "Place de la Mairie, Villeurbanne"},
    "url": "https://brocabrac.fr/event/456"
  }
]
</script>
</head><body><h1>Brocantes près de Lyon</h1></body></html>"""

# Event with venue-only name and no city in location — typical "Les Framboisines" case
BROCABRAC_VENUE_ONLY = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
[
  {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Brocante aux Framboisines",
    "startDate": "2026-08-10",
    "location": {
      "@type": "Place",
      "name": "Les Framboisines",
      "address": {
        "@type": "PostalAddress",
        "postalCode": "78720",
        "addressLocality": "Bullion"
      }
    },
    "url": "https://brocabrac.fr/event/999"
  }
]
</script>
</head><body></body></html>"""

BROCABRAC_CARDS = """<!DOCTYPE html><html><body>
<article class="card">
  <h3 class="title">Brocante Croix-Rousse</h3>
  <time class="date" datetime="2026-08-02">dimanche 2 août 2026</time>
  <span class="location">Boulevard de la Croix-Rousse, Lyon</span>
  <p class="description">Grande braderie estivale</p>
  <a href="/annonce/brocante-croix-rousse">Voir l'annonce</a>
</article>
<article class="card">
  <h3 class="title">Vide-grenier Caluire</h3>
  <time class="date" datetime="2026-08-09">dimanche 9 août 2026</time>
  <span class="location">Place Jules Ferry, Caluire-et-Cuire</span>
  <a href="/annonce/vide-grenier-caluire">Voir</a>
</article>
</body></html>"""

VIDEGRENIERS_JSONLD = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "Vide-grenier de Bron",
  "startDate": "2026-09-06",
  "location": {"name": "Parc de la Feyssine, Bron"},
  "url": "https://vide-greniers.org/annonce/789"
}
</script>
</head><body></body></html>"""

EMPTY_HTML = """<!DOCTYPE html><html><body><p>Aucun résultat.</p></body></html>"""

NOMINATIM_RESPONSE = json.dumps([{
    "lat": "45.7640",
    "lon": "4.8357",
    "display_name": "Lyon, Métropole de Lyon, Ain, France",
    "address": {"postcode": "69001", "city": "Lyon", "country": "France"},
}])


# ── Unit: make_uid ─────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMakeUid:
    def test_deterministic(self):
        a = make_uid("Brocante Lyon", "2026-07-15", "Place Bellecour")
        b = make_uid("Brocante Lyon", "2026-07-15", "Place Bellecour")
        assert a == b

    def test_case_insensitive(self):
        a = make_uid("BROCANTE LYON", "2026-07-15", "PLACE BELLECOUR")
        b = make_uid("brocante lyon", "2026-07-15", "place bellecour")
        assert a == b

    def test_different_dates_give_different_uids(self):
        a = make_uid("Brocante", "2026-07-15", "Lyon")
        b = make_uid("Brocante", "2026-07-16", "Lyon")
        assert a != b

    def test_different_locations_give_different_uids(self):
        a = make_uid("Brocante", "2026-07-15", "Lyon")
        b = make_uid("Brocante", "2026-07-15", "Paris")
        assert a != b

    def test_returns_hex_string(self):
        uid = make_uid("Test", "2026-01-01", "Ville")
        assert re.fullmatch(r"[0-9a-f]{32}", uid)


# ── Unit: _parse_jsonld ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestParseJsonld:
    def _soup(self, html):
        return BeautifulSoup(html, "lxml")

    def test_extracts_event_from_list(self):
        soup = self._soup(BROCABRAC_JSONLD)
        events = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")
        assert len(events) == 2

    def test_event_fields(self):
        soup = self._soup(BROCABRAC_JSONLD)
        ev = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")[0]
        assert ev["title"] == "Grande Brocante de Lyon"
        assert ev["date_parsed"] == "2026-07-15"
        assert ev["location"] == "Place Bellecour"  # venue name for display
        assert ev["source"] == "brocabrac.fr"
        assert ev["url"] == "https://brocabrac.fr/event/123"
        assert ev["ev_type"] == "brocante"

    def test_geo_query_built_from_full_address(self):
        soup = self._soup(BROCABRAC_JSONLD)
        ev = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")[0]
        # geo_query should include street + postcode + locality for Nominatim
        geo_query = ev.get("geo_query", "")
        assert "Lyon" in geo_query
        assert "69002" in geo_query

    def test_venue_only_name_gets_locality_in_geo_query(self):
        # "Les Framboisines" pattern: venue name with no city → geo_query must include locality
        soup = self._soup(BROCABRAC_VENUE_ONLY)
        ev = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")[0]
        assert ev["location"] == "Les Framboisines"      # display: venue name
        geo_query = ev.get("geo_query", "")
        assert "Bullion" in geo_query                     # geocoding: includes city
        assert "78720" in geo_query                       # geocoding: includes postcode

    def test_address_locality_stored_as_city(self):
        """addressLocality from JSON-LD must be stored as ev['city'] for ICS titles."""
        soup = self._soup(BROCABRAC_JSONLD)
        ev = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")[0]
        # BROCABRAC_JSONLD first event has addressLocality "Lyon"
        assert ev.get("city") == "Lyon"

    def test_city_stored_for_venue_only_location(self):
        """When location is a venue name, city must still be stored from addressLocality."""
        soup = self._soup(BROCABRAC_VENUE_ONLY)
        ev = _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")[0]
        assert ev["location"] == "Les Framboisines"  # venue name kept for display
        assert ev.get("city") == "Bullion"            # commune from addressLocality

    def test_no_geo_query_when_location_equals_query(self):
        # If the geo_query would be the same as location, no extra field is stored
        soup = self._soup(VIDEGRENIERS_JSONLD)
        ev = _parse_jsonld(soup, "https://vide-greniers.org", "vide-greniers.org")[0]
        # location has no structured address → geo_query == location → not stored
        assert "geo_query" not in ev

    def test_vide_grenier_event_type(self):
        soup = self._soup(VIDEGRENIERS_JSONLD)
        ev = _parse_jsonld(soup, "https://vide-greniers.org", "vide-greniers.org")[0]
        assert ev["ev_type"] == "vide-grenier"

    def test_single_event_object(self):
        soup = self._soup(VIDEGRENIERS_JSONLD)
        events = _parse_jsonld(soup, "https://vide-greniers.org", "vide-greniers.org")
        assert len(events) == 1
        assert events[0]["title"] == "Vide-grenier de Bron"

    def test_past_events_excluded(self):
        html = """<html><head>
        <script type="application/ld+json">
        [{"@type":"Event","name":"Passé","startDate":"2020-01-01",
          "location":{"name":"Paris"},"url":"https://x.com"}]
        </script></head><body></body></html>"""
        soup = self._soup(html)
        events = _parse_jsonld(soup, "https://x.com", "test")
        assert events == []

    def test_non_event_type_ignored(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type":"Organization","name":"Brocante Inc"}
        </script></head><body></body></html>"""
        soup = self._soup(html)
        assert _parse_jsonld(soup, "https://x.com", "test") == []

    def test_invalid_json_skipped(self):
        html = """<html><head>
        <script type="application/ld+json">{ not valid json }</script>
        </head><body></body></html>"""
        soup = self._soup(html)
        assert _parse_jsonld(soup, "https://x.com", "test") == []

    def test_empty_page(self):
        soup = self._soup(EMPTY_HTML)
        assert _parse_jsonld(soup, "https://x.com", "test") == []


# ── Unit: _parse_cards ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestParseCards:
    def _soup(self, html):
        return BeautifulSoup(html, "lxml")

    def test_extracts_two_cards(self):
        soup = self._soup(BROCABRAC_CARDS)
        events = _parse_cards(soup, "https://brocabrac.fr", "brocabrac.fr", "https://brocabrac.fr")
        assert len(events) == 2

    def test_card_fields(self):
        soup = self._soup(BROCABRAC_CARDS)
        ev = _parse_cards(soup, "https://brocabrac.fr", "brocabrac.fr", "https://brocabrac.fr")[0]
        assert ev["title"] == "Brocante Croix-Rousse"
        assert ev["date_parsed"] == "2026-08-02"
        assert "Croix-Rousse" in ev["location"]
        assert ev["url"] == "https://brocabrac.fr/annonce/brocante-croix-rousse"

    def test_relative_url_made_absolute(self):
        soup = self._soup(BROCABRAC_CARDS)
        events = _parse_cards(soup, "https://brocabrac.fr", "brocabrac.fr", "https://brocabrac.fr")
        for ev in events:
            assert ev["url"].startswith("https://brocabrac.fr")

    def test_datetime_attribute_preferred_over_text(self):
        html = """<html><body><article class="card">
          <h3 class="title">Test</h3>
          <time class="date" datetime="2026-09-01">1er septembre 2026 quelque chose de long</time>
          <span class="location">Ville</span>
        </article></body></html>"""
        soup = self._soup(html)
        events = _parse_cards(soup, "https://x.com", "test", "https://x.com")
        assert len(events) == 1
        assert events[0]["date_parsed"] == "2026-09-01"

    def test_empty_page_returns_nothing(self):
        soup = self._soup(EMPTY_HTML)
        events = _parse_cards(soup, "https://x.com", "test", "https://x.com")
        assert events == []


# ── Unit: _find_next_page ─────────────────────────────────────────────────────

@pytest.mark.unit
class TestFindNextPage:
    def _soup(self, html):
        return BeautifulSoup(html, "lxml")

    def test_link_rel_next(self):
        soup = self._soup('<html><head><link rel="next" href="/page/2/"></head><body></body></html>')
        assert _find_next_page(soup, "https://brocabrac.fr/78/bullion/", "https://brocabrac.fr") \
               == "https://brocabrac.fr/page/2/"

    def test_a_rel_next(self):
        soup = self._soup('<html><body><a rel="next" href="/78/bullion/?page=2">Suivant</a></body></html>')
        assert _find_next_page(soup, "https://brocabrac.fr/78/bullion/", "https://brocabrac.fr") \
               == "https://brocabrac.fr/78/bullion/?page=2"

    def test_a_class_next(self):
        soup = self._soup('<html><body><a class="next" href="/page/2">›</a></body></html>')
        result = _find_next_page(soup, "https://brocabrac.fr/", "https://brocabrac.fr")
        assert result == "https://brocabrac.fr/page/2"

    def test_absolute_href_returned_as_is(self):
        soup = self._soup('<html><body><a rel="next" href="https://other.fr/page/2">»</a></body></html>')
        assert _find_next_page(soup, "https://brocabrac.fr/", "https://brocabrac.fr") \
               == "https://other.fr/page/2"

    def test_no_next_returns_none(self):
        soup = self._soup('<html><body><p>Fin des résultats.</p></body></html>')
        assert _find_next_page(soup, "https://brocabrac.fr/78/bullion/", "https://brocabrac.fr") is None

    def test_empty_href_ignored(self):
        soup = self._soup('<html><body><a rel="next" href="#">Suivant</a></body></html>')
        assert _find_next_page(soup, "https://x.com/", "https://x.com") is None


# ── Unit: deduplication logic ──────────────────────────────────────────────────

@pytest.mark.unit
class TestDeduplication:
    def test_same_uid_deduplicated(self):
        uid = make_uid("Brocante Lyon", "2026-07-15", "Lyon")
        ev1 = {"title": "Brocante Lyon", "date_parsed": "2026-07-15",
               "location": "Lyon", "uid": uid, "source": "src1"}
        ev2 = {**ev1, "source": "src2"}  # same uid, different source
        all_events = [ev1, ev2]
        seen = {}
        for ev in all_events:
            if ev["uid"] not in seen:
                seen[ev["uid"]] = ev
        assert len(seen) == 1
        assert seen[uid]["source"] == "src1"  # first occurrence kept

    def test_different_uids_both_kept(self):
        ev1 = {"uid": make_uid("A", "2026-07-15", "Lyon"), "source": "s1"}
        ev2 = {"uid": make_uid("B", "2026-07-16", "Paris"), "source": "s2"}
        seen = {}
        for ev in [ev1, ev2]:
            if ev["uid"] not in seen:
                seen[ev["uid"]] = ev
        assert len(seen) == 2


# ── Unit: geocache persistence ─────────────────────────────────────────────────

@pytest.mark.unit
class TestGeocache:
    def test_save_and_reload(self, isolated_data):
        cache = {"place bellecour, lyon": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        _save_geocache(cache)
        reloaded = _load_geocache()
        assert reloaded == cache

    def test_load_returns_empty_when_missing(self, isolated_data):
        assert _load_geocache() == {}

    def test_load_returns_empty_on_corrupt_json(self, isolated_data):
        from app.scraper import _GEOCACHE_FILE
        _GEOCACHE_FILE.write_text("{ not json }", encoding="utf-8")
        assert _load_geocache() == {}

    def test_postcode_stored_in_cache(self, isolated_data):
        cache = {"place bellecour, lyon": {"lat": 45.76, "lng": 4.83, "city": "Lyon", "postcode": "69001"}}
        _save_geocache(cache)
        assert _load_geocache()["place bellecour, lyon"]["postcode"] == "69001"


# ── Unit: Haversine distance ───────────────────────────────────────────────────

@pytest.mark.unit
class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine_km(48.86, 2.35, 48.86, 2.35) == pytest.approx(0.0)

    def test_paris_to_lyon_approx(self):
        # ~392 km by straight line
        d = _haversine_km(48.8566, 2.3522, 45.764, 4.836)
        assert 380 < d < 410

    def test_symmetry(self):
        a = _haversine_km(48.0, 2.0, 45.0, 5.0)
        b = _haversine_km(45.0, 5.0, 48.0, 2.0)
        assert a == pytest.approx(b)


# ── Unit: _slugify ─────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSlugify:
    def test_simple_name(self):
        assert _slugify("Lyon") == "lyon"

    def test_hyphenated_name(self):
        assert _slugify("Saint-Arnoult-en-Yvelines") == "saint-arnoult-en-yvelines"

    def test_accented_chars_removed(self):
        assert _slugify("Clairefontaine-en-Yvelines") == "clairefontaine-en-yvelines"
        assert _slugify("Île-de-France") == "ile-de-france"

    def test_spaces_become_hyphens(self):
        assert _slugify("Le Mans") == "le-mans"

    def test_apostrophe_becomes_hyphen(self):
        assert _slugify("L'Haÿ-les-Roses") == "l-hay-les-roses"


# ── Unit: _dept_from_postcode ──────────────────────────────────────────────────

@pytest.mark.unit
class TestDeptFromPostcode:
    def test_mainland(self):
        assert _dept_from_postcode("78730") == "78"
        assert _dept_from_postcode("69001") == "69"
        assert _dept_from_postcode("75001") == "75"

    def test_overseas(self):
        assert _dept_from_postcode("97100") == "971"
        assert _dept_from_postcode("98800") == "988"

    def test_empty_or_short(self):
        assert _dept_from_postcode("") == ""
        assert _dept_from_postcode("7") == ""
        assert _dept_from_postcode(None) == ""  # type: ignore[arg-type]


# ── Unit: _classify_event ─────────────────────────────────────────────────────

@pytest.mark.unit
class TestClassifyEvent:
    def test_brocante(self):
        assert _classify_event("Grande Brocante de printemps") == "brocante"

    def test_vide_grenier(self):
        assert _classify_event("Grand vide-grenier communal") == "vide-grenier"
        assert _classify_event("Vide greniers du village") == "vide-grenier"

    def test_braderie(self):
        assert _classify_event("Braderie annuelle de la commune") == "braderie"

    def test_bourse(self):
        assert _classify_event("Bourse aux vêtements enfants") == "bourse"
        assert _classify_event("Bourse aux jouets") == "bourse"

    def test_marche_puces(self):
        assert _classify_event("Marché aux puces") == "marche-puces"

    def test_vide_grenier_beats_brocante(self):
        # "vide-grenier" checked before "brocante"
        assert _classify_event("Grande Brocante et Vide-Grenier") == "vide-grenier"

    def test_autre(self):
        assert _classify_event("Fête du village") == "autre"
        assert _classify_event("") == "autre"


# ── Integration: scrape_all with mocked HTTP ──────────────────────────────────

def _make_geocode_mock(lat: float = 45.764, lng: float = 4.836):
    """Return a _geocode_batch stub that fills the cache with nearby coordinates."""
    async def _mock(locations, cache):
        for loc in locations:
            key = loc.strip().lower()
            cache[key] = {"lat": lat, "lng": lng, "city": "Lyon", "postcode": "69001"}
    return _mock

@pytest.mark.integration
@pytest.mark.asyncio
async def test_scrape_all_jsonld_source(httpx_mock, monkeypatch, isolated_data):
    """scrape_all returns events parsed from JSON-LD."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(
        url=re.compile(r"https://brocabrac\.fr/.*"),
        text=BROCABRAC_JSONLD,
        headers={"Content-Type": "text/html; charset=utf-8"},
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    events = await scrape_all(45.764, 4.836, 30)

    assert len(events) == 2
    titles = {e["title"] for e in events}
    assert "Grande Brocante de Lyon" in titles
    assert "Vide-grenier Villeurbanne" in titles


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scrape_all_css_fallback(httpx_mock, monkeypatch, isolated_data):
    """scrape_all falls back to CSS selectors when no JSON-LD found."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(
        url=re.compile(r"https://brocabrac\.fr/.*"),
        text=BROCABRAC_CARDS,
        headers={"Content-Type": "text/html; charset=utf-8"},
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    events = await scrape_all(45.764, 4.836, 30)

    assert len(events) == 2
    assert all(e["source"] == "brocabrac.fr" for e in events)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scrape_all_deduplicates_across_sources(httpx_mock, monkeypatch, isolated_data):
    """Same event appearing on both sources is kept once."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    # Return the same JSON-LD event from both sources
    httpx_mock.add_response(
        url=re.compile(r"https://brocabrac\.fr/.*"), text=BROCABRAC_JSONLD,
        is_reusable=True)
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"), text=BROCABRAC_JSONLD,
        is_reusable=True)

    events = await scrape_all(45.764, 4.836, 30)
    assert len(events) > 0, "Expected events to be returned"
    uids = [e["uid"] for e in events]
    assert len(uids) == len(set(uids)), "Duplicate UIDs found after deduplication"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scrape_all_sorted_by_date(httpx_mock, monkeypatch, isolated_data):
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),   text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    events = await scrape_all(45.764, 4.836, 30)
    dates = [e["date_parsed"] for e in events]
    assert dates == sorted(dates)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brocabrac_url_uses_city_name(httpx_mock, monkeypatch, isolated_data):
    """When city is provided, brocabrac URL uses the city name (not raw coordinates)."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(48.59, 1.89, 30, city="Clairefontaine-en-Yvelines")

    # brocabrac must have been called with the city name, not raw coordinates
    brocabrac_req = next(
        r for r in httpx_mock.get_requests() if "brocabrac.fr" in str(r.url)
    )
    assert "Clairefontaine" in str(brocabrac_req.url)
    assert "48.59" not in str(brocabrac_req.url)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brocabrac_url_falls_back_to_coords_without_city(httpx_mock, monkeypatch, isolated_data):
    """When no city is configured, brocabrac URL falls back to lat,lng coordinates."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(48.59, 1.89, 30, city="")

    brocabrac_req = next(
        r for r in httpx_mock.get_requests() if "brocabrac.fr" in str(r.url)
    )
    assert "48.59" in str(brocabrac_req.url)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brocabrac_url_uses_dept_slug_format(httpx_mock, monkeypatch, isolated_data):
    """When city geocoding returns a postcode, brocabrac URL uses /{dept}/{slug}/ format."""
    nominatim_saint_arnoult = json.dumps([{
        "lat": "48.5912",
        "lon": "1.8763",
        "display_name": "Saint-Arnoult-en-Yvelines, Yvelines, Île-de-France, France",
        "address": {"postcode": "78730", "village": "Saint-Arnoult-en-Yvelines", "country": "France"},
    }])
    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(
        url=re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        text=nominatim_saint_arnoult,
        headers={"Content-Type": "application/json"},
        is_reusable=True,
    )

    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    await scrape_all(48.591, 1.876, 10, city="Saint-Arnoult-en-Yvelines")

    brocabrac_req = next(r for r in httpx_mock.get_requests() if "brocabrac.fr" in str(r.url))
    url_str = str(brocabrac_req.url)
    assert "/78/saint-arnoult-en-yvelines/" in url_str
    assert "localisation" not in url_str
    assert "48.591" not in url_str
    assert "rayon=10" in url_str  # radius parameter forwarded to brocabrac


@pytest.mark.integration
@pytest.mark.asyncio
async def test_result_includes_url_field(httpx_mock, monkeypatch, isolated_data):
    """_last_scrape_results stores the URL that was actually queried."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(45.764, 4.836, 30, city="Lyon")

    assert "url" in _last_scrape_results["brocabrac.fr"]
    assert "brocabrac.fr" in _last_scrape_results["brocabrac.fr"]["url"]
    assert "url" in _last_scrape_results["vide-greniers.org"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_broca_distance_cookie_sent_to_brocabrac(httpx_mock, monkeypatch, isolated_data):
    """BROCA_DISTANCE cookie must be sent to brocabrac.fr with the configured radius."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(48.59, 1.89, 25, city="Lyon")

    broca_req = next(r for r in httpx_mock.get_requests() if "brocabrac.fr" in str(r.url))
    cookie_header = broca_req.headers.get("cookie", "")
    assert "BROCA_DISTANCE=25" in cookie_header, (
        f"Expected BROCA_DISTANCE=25 in Cookie header, got: {cookie_header!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_broca_distance_cookie_not_sent_to_videgreniers(httpx_mock, monkeypatch, isolated_data):
    """BROCA_DISTANCE cookie must NOT leak to vide-greniers.org."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(48.59, 1.89, 25, city="Lyon")

    vg_req = next(r for r in httpx_mock.get_requests() if "vide-greniers.org" in str(r.url))
    cookie_header = vg_req.headers.get("cookie", "")
    assert "BROCA_DISTANCE" not in cookie_header


@pytest.mark.integration
@pytest.mark.asyncio
async def test_speculative_pagination_uses_p_param(httpx_mock, monkeypatch, isolated_data):
    """Speculative pagination must use ?p= (brocabrac.fr format), not ?page=."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"), text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(45.764, 4.836, 30)

    broca_reqs = [r for r in httpx_mock.get_requests() if "brocabrac.fr" in str(r.url)]
    # At least one speculative page 2 request should have been made
    page2_reqs = [r for r in broca_reqs if "p=2" in str(r.url)]
    assert len(page2_reqs) >= 1, "Speculative pagination should use ?p=2, not ?page=2"
    # Must NOT use the old ?page= format
    assert not any("page=2" in str(r.url) for r in broca_reqs), "Must use ?p= not ?page="


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scrape_all_handles_http_error_gracefully(httpx_mock, monkeypatch, isolated_data):
    """A 403 or 500 on one source returns empty list for that source, not a crash."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),    status_code=403)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=BROCABRAC_JSONLD,
                            is_reusable=True)

    events = await scrape_all(45.764, 4.836, 30)
    # Only vide-greniers events (from BROCABRAC_JSONLD reused) survive
    assert isinstance(events, list)
    assert len(events) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_results_populated_on_success(httpx_mock, monkeypatch, isolated_data):
    """_last_scrape_results is populated with per-source stats after a successful scrape."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),      text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(45.764, 4.836, 30)

    assert "brocabrac.fr" in _last_scrape_results
    r = _last_scrape_results["brocabrac.fr"]
    assert r["count"] == 2
    assert r["strategy"] == "json-ld"
    assert r["error"] is None
    assert "last_run" in r
    assert "duration_s" in r


@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_error_recorded_in_results(httpx_mock, monkeypatch, isolated_data):
    """HTTP errors from a source are captured in _last_scrape_results."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),      status_code=403)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    await scrape_all(45.764, 4.836, 30)

    assert _last_scrape_results["brocabrac.fr"]["error"] == "HTTP 403"
    assert _last_scrape_results["vide-greniers.org"]["error"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_error(httpx_mock, monkeypatch, isolated_data):
    """Two network failures followed by success → events returned, no crash."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    # Replace asyncio.sleep to avoid real delays during retry backoff
    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    # pytest-httpx serves responses in order; first two are network errors
    httpx_mock.add_exception(httpx.ConnectError("refused"))        # attempt 1
    httpx_mock.add_exception(httpx.ConnectError("refused"))        # attempt 2
    httpx_mock.add_response(text=BROCABRAC_JSONLD, is_reusable=True)  # attempt 3 (success)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    events = await scrape_all(45.764, 4.836, 30)
    assert len(events) == 2
    assert _last_scrape_results["brocabrac.fr"]["error"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_retries_exhausted_records_error(httpx_mock, monkeypatch, isolated_data):
    """Three consecutive network failures → error recorded, empty results, no crash."""
    async def _noop_geocode(locations, cache):
        pass
    monkeypatch.setattr("app.scraper._geocode_batch", _noop_geocode)

    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    httpx_mock.add_exception(httpx.ConnectError("refused"))  # attempt 1
    httpx_mock.add_exception(httpx.ConnectError("refused"))  # attempt 2
    httpx_mock.add_exception(httpx.ConnectError("refused"))  # attempt 3
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    events = await scrape_all(45.764, 4.836, 30)
    assert isinstance(events, list)
    assert _last_scrape_results["brocabrac.fr"]["error"] is not None
    assert "Network error" in _last_scrape_results["brocabrac.fr"]["error"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_geocoding_attaches_geo_to_events(httpx_mock, monkeypatch, isolated_data):
    """Geocoded results are attached to events as ev['geo']."""
    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),      text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"),  text=EMPTY_HTML)
    # BROCABRAC_JSONLD has 2 distinct locations → up to 2 Nominatim calls
    httpx_mock.add_response(
        url=re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        text=NOMINATIM_RESPONSE,
        headers={"Content-Type": "application/json"},
        is_reusable=True,
    )

    # asyncio.sleep must be replaced with an async no-op (not a sync lambda)
    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    events = await scrape_all(45.764, 4.836, 30)
    geocoded = [e for e in events if e.get("geo")]
    assert len(geocoded) > 0
    for ev in geocoded:
        assert "lat" in ev["geo"]
        assert "lng" in ev["geo"]
        assert "postcode" in ev["geo"]

    # Nominatim mock returns postcode "69001" — verify it propagates
    assert geocoded[0]["geo"]["postcode"] == "69001"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_distance_filter_drops_far_events(httpx_mock, monkeypatch, isolated_data):
    """Events geocoded far beyond the radius must be dropped."""
    # Nominatim returns Angers (47.47, -0.55) — ~370 km from Lyon (45.764, 4.836)
    far_nominatim = json.dumps([{
        "lat": "47.4736",
        "lon": "-0.5542",
        "display_name": "Angers, Maine-et-Loire, Pays de la Loire, France",
        "address": {"postcode": "49000", "city": "Angers", "country": "France"},
    }])

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),     text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(
        url=re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        text=far_nominatim,
        headers={"Content-Type": "application/json"},
        is_reusable=True,
    )

    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    # Search from Lyon (45.764, 4.836) with radius 30 km — Angers is ~370 km away
    events = await scrape_all(45.764, 4.836, 30)
    # All events from BROCABRAC_JSONLD geocode to Angers → geocoded ones dropped
    geocoded = [e for e in events if e.get("geo")]
    assert len(geocoded) == 0, "Events with geo outside radius must be dropped"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_un_geocoded_events_kept_despite_location(httpx_mock, monkeypatch, isolated_data):
    """Events whose location Nominatim cannot resolve are kept, not silently dropped.

    brocabrac.fr already filters results by distance server-side; dropping
    un-geocoded events would silently hide valid events with uncommon venue names.
    """
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock(lat=999, lng=999))

    # Patch _assign_geo so no event gets a geo (simulates geocoding failure)
    async def _fail_geocode(locations, cache):
        pass  # nothing cached → no geo assigned

    monkeypatch.setattr("app.scraper._geocode_batch", _fail_geocode)

    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),      text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)

    events = await scrape_all(45.764, 4.836, 30)
    assert len(events) == 2, "Un-geocoded events must be kept, not dropped"
    assert not any(e.get("geo") for e in events)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_distance_filter_keeps_close_events(httpx_mock, monkeypatch, isolated_data):
    """Events geocoded within the radius must be kept."""
    # Nominatim returns Lyon (45.764, 4.836) — same as search center
    httpx_mock.add_response(url=re.compile(r"https://brocabrac\.fr/.*"),     text=BROCABRAC_JSONLD,
                            is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://vide-greniers\.org/.*"), text=EMPTY_HTML)
    httpx_mock.add_response(
        url=re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        text=NOMINATIM_RESPONSE,
        headers={"Content-Type": "application/json"},
        is_reusable=True,
    )

    async def _noop_sleep(_):
        pass
    monkeypatch.setattr("app.scraper.asyncio.sleep", _noop_sleep)

    events = await scrape_all(45.764, 4.836, 30)
    geocoded = [e for e in events if e.get("geo")]
    assert len(geocoded) > 0, "Events within radius must be kept"


# ── Live contract tests ────────────────────────────────────────────────────────
#
# These hit real websites. Run with:  pytest -m live
# They SKIP when the site is unreachable and FAIL when the structure
# has changed in a way that breaks our parsers.

SCRAPER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


async def _fetch_and_parse(url: str, source_name: str):
    """Helper: fetch URL, return (soup, jsonld_events, matched_css_selector)."""
    try:
        async with httpx.AsyncClient(headers=SCRAPER_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url, timeout=20)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        pytest.skip(f"{source_name} unreachable: {exc}")

    if resp.status_code in (403, 429):
        pytest.skip(f"{source_name} returned {resp.status_code} (bot protection)")

    assert resp.status_code == 200, (
        f"{source_name} returned unexpected status {resp.status_code}"
    )
    assert "html" in resp.headers.get("content-type", ""), (
        f"{source_name} did not return HTML"
    )

    soup = BeautifulSoup(resp.text, "lxml")
    jsonld = _parse_jsonld(soup, url, source_name)
    matched = next((s for s in CARD_SELECTORS if soup.select(s)), None)
    return soup, jsonld, matched


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_brocabrac_is_parseable():
    """
    CONTRACT: brocabrac.fr must return parseable HTML.
    Fails → site structure changed, update CARD_SELECTORS or _parse_jsonld.
    """
    url = "https://brocabrac.fr/brocantes-vide-greniers?localisation=48.8566,2.3522&rayon=50"
    soup, jsonld, matched_selector = await _fetch_and_parse(url, "brocabrac.fr")

    assert jsonld or matched_selector, (
        "brocabrac.fr: aucun JSON-LD ni sélecteur CSS reconnu.\n"
        f"Sélecteurs testés : {CARD_SELECTORS}\n"
        f"JSON-LD trouvés : {len(jsonld)}\n"
        f"Extrait HTML (500c) :\n{str(soup)[:500]}"
    )

    if jsonld:
        for ev in jsonld:
            assert ev.get("title"), "JSON-LD event manque le champ 'name'"
            parsed = parse_french_date(ev.get("date_parsed", ""))
            assert parsed is not None or ev.get("date_parsed"), (
                f"Date non parseable : {ev.get('date_parsed')}"
            )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_videgrenier_is_parseable():
    """
    CONTRACT: vide-greniers.org must return parseable HTML.
    Fails → site structure changed, update selectors.
    """
    url = "https://vide-greniers.org/recherche?lat=48.8566&lng=2.3522&distance=50"
    soup, jsonld, matched_selector = await _fetch_and_parse(url, "vide-greniers.org")

    assert jsonld or matched_selector, (
        "vide-greniers.org: aucun JSON-LD ni sélecteur CSS reconnu.\n"
        f"Sélecteurs testés : {CARD_SELECTORS}\n"
        f"JSON-LD trouvés : {len(jsonld)}\n"
        f"Extrait HTML (500c) :\n{str(soup)[:500]}"
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_brocabrac_url_structure():
    """CONTRACT: brocabrac.fr search URL accepts lat/lng/rayon parameters."""
    url = "https://brocabrac.fr/brocantes-vide-greniers?localisation=48.8566,2.3522&rayon=30"
    async with httpx.AsyncClient(headers=SCRAPER_HEADERS, follow_redirects=True) as client:
        try:
            resp = await client.get(url, timeout=20)
        except Exception as exc:
            pytest.skip(f"Network error: {exc}")

    # A 404 here means the URL pattern has changed
    assert resp.status_code != 404, (
        "brocabrac.fr retourne 404 — le chemin de recherche a changé !\n"
        "Vérifier le paramètre 'localisation' et 'rayon'."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pagination_stops_on_page_error(httpx_mock, monkeypatch, isolated_data):
    """Page 1 succeeds, page 2 raises a network timeout → events from page 1 returned, no crash."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())

    # Page 1 returns valid HTML with a "next page" link pointing to page 2
    page1_html = BROCABRAC_JSONLD.replace(
        "</head>",
        '<link rel="next" href="https://brocabrac.fr/page/2/"></head>',
    )

    httpx_mock.add_response(
        url=re.compile(r"https://brocabrac\.fr/(?!page).*"),
        text=page1_html,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    httpx_mock.add_exception(
        httpx.TimeoutException("timed out"),
        url="https://brocabrac.fr/page/2/",
    )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    events = await scrape_all(45.764, 4.836, 30)

    assert isinstance(events, list)
    assert len(events) >= 1
    titles = {e["title"] for e in events}
    assert "Grande Brocante de Lyon" in titles


# ── T2 : JSON-LD edge cases ───────────────────────────────────────────────────

@pytest.mark.unit
class TestJsonldEdgeCases:
    """_parse_jsonld must survive missing fields, bad JSON, unknown types."""

    def _parse(self, html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        return _parse_jsonld(soup, "https://brocabrac.fr", "brocabrac.fr")

    def _wrap(self, obj) -> str:
        import json as _json
        return (
            f'<!DOCTYPE html><html><head>'
            f'<script type="application/ld+json">{_json.dumps(obj)}</script>'
            f'</head><body></body></html>'
        )

    def test_event_without_start_date_is_dropped(self):
        """Events without a parseable startDate are intentionally excluded."""
        html = self._wrap([{"@context": "https://schema.org", "@type": "Event",
                             "name": "Brocante sans date",
                             "location": {"@type": "Place", "name": "Lyon"}}])
        events = self._parse(html)
        assert events == []  # date is required for inclusion

    def test_event_without_location(self):
        html = self._wrap([{"@context": "https://schema.org", "@type": "Event",
                             "name": "Brocante sans lieu", "startDate": "2026-08-01"}])
        events = self._parse(html)
        assert len(events) == 1
        assert events[0]["title"] == "Brocante sans lieu"

    def test_empty_jsonld_array(self):
        html = self._wrap([])
        assert self._parse(html) == []

    def test_non_event_type_ignored(self):
        html = self._wrap([{"@context": "https://schema.org",
                             "@type": "Organization", "name": "Pas un événement"}])
        assert self._parse(html) == []

    def test_malformed_json_does_not_raise(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{this: is not valid JSON}'
            '</script></head><body></body></html>'
        )
        events = self._parse(html)
        assert events == []

    def test_multiple_events_all_parsed(self):
        html = self._wrap([
            {"@context": "https://schema.org", "@type": "Event",
             "name": f"Événement {i}", "startDate": f"2026-08-0{i}"}
            for i in range(1, 4)
        ])
        assert len(self._parse(html)) == 3

    def test_event_with_no_name_skipped(self):
        html = self._wrap([{"@context": "https://schema.org", "@type": "Event",
                             "startDate": "2026-08-01"}])
        events = self._parse(html)
        # Either skipped or given a fallback title — must not crash
        for ev in events:
            assert isinstance(ev.get("title", ""), str)

    def test_location_string_instead_of_object(self):
        html = self._wrap([{"@context": "https://schema.org", "@type": "Event",
                             "name": "Brocante", "startDate": "2026-08-01",
                             "location": "Place du Marché, Lyon"}])
        events = self._parse(html)
        assert len(events) == 1


# ── T3 : network error scenarios ──────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_connect_error_recorded_as_error(httpx_mock, monkeypatch, isolated_data):
    """ConnectError (DNS/connection failure) is recorded in results, no crash."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())
    # Scraper retries 3 times — register exception for each attempt
    for _ in range(3):
        httpx_mock.add_exception(
            httpx.ConnectError("Name or service not known"),
            url=re.compile(r"https://brocabrac\.fr/.*"),
        )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    events = await scrape_all(45.764, 4.836, 30)
    assert isinstance(events, list)
    assert "brocabrac.fr" in _last_scrape_results
    assert _last_scrape_results["brocabrac.fr"].get("error") is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_200_response_returns_no_events(httpx_mock, monkeypatch, isolated_data):
    """HTTP 200 with no parseable events → empty list, no crash."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())
    httpx_mock.add_response(
        url=re.compile(r"https://brocabrac\.fr/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=EMPTY_HTML,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert await scrape_all(45.764, 4.836, 30) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_source_down_other_still_returns_events(httpx_mock, monkeypatch, isolated_data):
    """If brocabrac.fr is unreachable, events from vide-greniers.org are still returned."""
    monkeypatch.setattr("app.scraper._geocode_batch", _make_geocode_mock())
    # Scraper retries 3 times — register exception for each attempt
    for _ in range(3):
        httpx_mock.add_exception(
            httpx.ConnectError("connection refused"),
            url=re.compile(r"https://brocabrac\.fr/.*"),
        )
    httpx_mock.add_response(
        url=re.compile(r"https://vide-greniers\.org/.*"),
        text=VIDEGRENIERS_JSONLD,
        headers={"Content-Type": "text/html; charset=utf-8"},
        is_reusable=True,
    )
    events = await scrape_all(45.764, 4.836, 30)
    assert len(events) >= 1
    assert any(e["source"] == "vide-greniers.org" for e in events)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_videgrenier_url_structure():
    """CONTRACT: vide-greniers.org search URL accepts lat/lng/distance parameters."""
    url = "https://vide-greniers.org/recherche?lat=48.8566&lng=2.3522&distance=30"
    async with httpx.AsyncClient(headers=SCRAPER_HEADERS, follow_redirects=True) as client:
        try:
            resp = await client.get(url, timeout=20)
        except Exception as exc:
            pytest.skip(f"Network error: {exc}")

    assert resp.status_code != 404, (
        "vide-greniers.org retourne 404 — le chemin de recherche a changé !\n"
        "Vérifier les paramètres 'lat', 'lng', 'distance'."
    )
