"""Scraper: football-data.org REST API."""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

import config
from database import Session, get_engine, get_or_create_player, init_db, upsert_raw_stats

log = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"


# ── HTTP helper ────────────────────────────────────────────────────────────

def _get(path: str, params: Optional[dict] = None) -> dict:
    """GET request with retry + rate-limit handling."""
    url = f"{BASE_URL}{path}"
    headers = {"X-Auth-Token": config.FOOTBALL_DATA_API_KEY}

    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)

            if resp.status_code == 429:
                wait = 60
                print(f"  Rate limit erreicht — warte {wait}s …")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as exc:
            wait = config.RETRY_BACKOFF_BASE ** attempt
            print(f"  Fehler (Versuch {attempt+1}/{config.RETRY_MAX_ATTEMPTS}): {exc} — retry in {wait}s")
            time.sleep(wait)

    raise RuntimeError(f"Alle {config.RETRY_MAX_ATTEMPTS} Versuche fehlgeschlagen: {url}")


# ── Cache ──────────────────────────────────────────────────────────────────

def _cache_path(league_code: str, season: int, endpoint: str) -> Path:
    return config.CACHE_DIR / f"{league_code}_{season}_{endpoint}.json"


def _load_cache(league_code: str, season: int, endpoint: str) -> Optional[dict]:
    p = _cache_path(league_code, season, endpoint)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cache(league_code: str, season: int, endpoint: str, data: dict) -> None:
    p = _cache_path(league_code, season, endpoint)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _fetch_with_cache(league_code: str, season: int, endpoint: str,
                      path: str, params: Optional[dict] = None) -> dict:
    cached = _load_cache(league_code, season, endpoint)
    if cached is not None:
        print(f"  (aus Cache geladen)")
        return cached
    data = _get(path, params)
    _save_cache(league_code, season, endpoint, data)
    return data


# ── Scraping ───────────────────────────────────────────────────────────────

def scrape_league(league_code: str, league_name: str, season: int, engine) -> int:
    """Scrapt Top-Scorer + Team-Stats für eine Liga. Gibt Spieleranzahl zurück."""

    # Top-Scorer ────────────────────────────────────────────────────────────
    print(f"  Lade Top-Scorer …", end=" ", flush=True)
    data = _fetch_with_cache(
        league_code, season, "scorers",
        f"/competitions/{league_code}/scorers",
        params={"season": season, "limit": config.SCORERS_LIMIT},
    )
    scorers = data.get("scorers", [])
    print(f"{len(scorers)} Spieler")
    time.sleep(config.REQUEST_DELAY_SECONDS)

    # Teams (für Spieler-Minutenzahlen per Match) ───────────────────────────
    print(f"  Lade Teams …", end=" ", flush=True)
    teams_data = _fetch_with_cache(
        league_code, season, "teams",
        f"/competitions/{league_code}/teams",
        params={"season": season},
    )
    teams = {t["id"]: t for t in teams_data.get("teams", [])}
    print(f"{len(teams)} Teams")
    time.sleep(config.REQUEST_DELAY_SECONDS)

    if not scorers:
        print(f"  !! Keine Daten für {league_name}")
        return 0

    season_str = f"{season}-{season+1}"
    saved = 0

    with Session(engine) as session:
        for entry in scorers:
            p = entry.get("player", {})
            team = entry.get("team", {})

            player_id_ext = str(p.get("id", ""))
            if not player_id_ext:
                continue

            # Minuten schätzen: playedMatches * 90 (konservativ)
            played = entry.get("playedMatches", 0) or 0
            minutes = played * 90

            meta = {
                "name": p.get("name") or p.get("shortName") or "Unknown",
                "age": _calc_age(p.get("dateOfBirth")),
                "nationality": p.get("nationality"),
                "position": _map_position(p.get("position")),
                "club": team.get("shortName") or team.get("name"),
                "league": league_name,
                "season": season_str,
            }

            player = get_or_create_player(session, fbref_id=player_id_ext, meta=meta)

            stats = {
                "goals":        _safe_int(entry.get("goals")),
                "assists":      _safe_int(entry.get("assists")),
                "penalties":    _safe_int(entry.get("penalties")),
                "played_matches": played,
            }

            upsert_raw_stats(
                session,
                player_id=player.id,
                season=season_str,
                category="scorers",
                minutes=float(minutes),
                stats=stats,
            )
            saved += 1

        session.commit()

    return saved


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _calc_age(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        from datetime import date
        birth = date.fromisoformat(dob[:10])
        today = date.today()
        return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except Exception:
        return None


_POS_MAP = {
    "Goalkeeper":   "GK",
    "Defender":     "DF",
    "Midfielder":   "MF",
    "Offence":      "FW",
    "Forward":      "FW",
    "Attacker":     "FW",
}


def _map_position(pos: Optional[str]) -> Optional[str]:
    if not pos:
        return None
    return _POS_MAP.get(pos, pos)


# ── Entry point ────────────────────────────────────────────────────────────

def scrape_all(
    leagues: Optional[dict] = None,
    season: Optional[int] = None,
) -> None:
    if config.FOOTBALL_DATA_API_KEY == "DEIN_API_KEY_HIER":
        print("\n!! KEIN API KEY gesetzt!")
        print("   -> config.py öffnen und FOOTBALL_DATA_API_KEY eintragen")
        print("   -> Kostenlos registrieren: https://www.football-data.org/client/register\n")
        return

    if leagues is None:
        leagues = config.LEAGUES
    if season is None:
        season = config.SEASON

    engine = get_engine()
    init_db(engine)

    total = len(leagues)
    for i, (code, name) in enumerate(leagues.items(), 1):
        print(f"\n  Liga {i}/{total}: {name} ({code})")
        try:
            n = scrape_league(code, name, season, engine)
            print(f"  -> {n} Spieler gespeichert")
        except Exception as exc:
            print(f"  !! Fehler bei {name}: {exc}")
            log.error("League %s failed: %s", name, exc)
