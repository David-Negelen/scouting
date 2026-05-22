"""Database models and helpers (SQLAlchemy + SQLite)."""

import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship

from config import DB_PATH

log = logging.getLogger(__name__)

DATABASE_URL = f"sqlite:///{DB_PATH}"


# ── ORM base ──────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────

class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fbref_id = Column(String, unique=True, nullable=False)  # stable FBref ID
    name = Column(String, nullable=False)
    age = Column(Integer)
    nationality = Column(String)
    position = Column(String)
    club = Column(String)
    league = Column(String)
    season = Column(String)

    raw_stats = relationship("PlayerStatsRaw", back_populates="player", cascade="all, delete-orphan")
    per90_stats = relationship("PlayerStatsPer90", back_populates="player", cascade="all, delete-orphan")
    scores = relationship("PlayerScore", back_populates="player", cascade="all, delete-orphan")


class PlayerStatsRaw(Base):
    __tablename__ = "player_stats_raw"
    __table_args__ = (UniqueConstraint("player_id", "season", "category"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    season = Column(String, nullable=False)
    category = Column(String, nullable=False)   # e.g. "shooting"
    minutes = Column(Float)
    stats_json = Column(Text, nullable=False)   # all raw stat columns as JSON

    player = relationship("Player", back_populates="raw_stats")

    @property
    def stats(self) -> dict:
        return json.loads(self.stats_json)


class PlayerStatsPer90(Base):
    __tablename__ = "player_stats_per90"
    __table_args__ = (UniqueConstraint("player_id", "season"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    season = Column(String, nullable=False)
    minutes = Column(Float)
    stats_json = Column(Text, nullable=False)   # normalised columns as JSON

    player = relationship("Player", back_populates="per90_stats")

    @property
    def stats(self) -> dict:
        return json.loads(self.stats_json)


class PlayerScore(Base):
    __tablename__ = "player_scores"
    __table_args__ = (UniqueConstraint("player_id", "season", "position_group"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    season = Column(String, nullable=False)
    position_group = Column(String, nullable=False)   # FW / MF / DF / GK
    talent_score = Column(Float)
    sub_scores = Column(JSON)                          # {metric: score, ...}

    player = relationship("Player", back_populates="scores")


# ── Engine & helpers ───────────────────────────────────────────────────────

def get_engine():
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    # Enable WAL for better concurrent read performance
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
    return engine


def init_db(engine=None) -> None:
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    log.info("Database initialised at %s", DB_PATH)


def get_or_create_player(session: Session, fbref_id: str, meta: dict) -> Player:
    player = session.query(Player).filter_by(fbref_id=fbref_id).first()
    if player is None:
        player = Player(fbref_id=fbref_id, **meta)
        session.add(player)
        session.flush()
    else:
        for k, v in meta.items():
            setattr(player, k, v)
    return player


def upsert_raw_stats(
    session: Session,
    player_id: int,
    season: str,
    category: str,
    minutes: float,
    stats: dict[str, Any],
) -> None:
    row = (
        session.query(PlayerStatsRaw)
        .filter_by(player_id=player_id, season=season, category=category)
        .first()
    )
    if row is None:
        row = PlayerStatsRaw(
            player_id=player_id,
            season=season,
            category=category,
            minutes=minutes,
            stats_json=json.dumps(stats, default=str),
        )
        session.add(row)
    else:
        row.minutes = minutes
        row.stats_json = json.dumps(stats, default=str)


def upsert_per90_stats(
    session: Session,
    player_id: int,
    season: str,
    minutes: float,
    stats: dict[str, Any],
) -> None:
    row = (
        session.query(PlayerStatsPer90)
        .filter_by(player_id=player_id, season=season)
        .first()
    )
    if row is None:
        row = PlayerStatsPer90(
            player_id=player_id,
            season=season,
            minutes=minutes,
            stats_json=json.dumps(stats, default=str),
        )
        session.add(row)
    else:
        row.minutes = minutes
        row.stats_json = json.dumps(stats, default=str)


def upsert_score(
    session: Session,
    player_id: int,
    season: str,
    position_group: str,
    talent_score: float,
    sub_scores: dict,
) -> None:
    row = (
        session.query(PlayerScore)
        .filter_by(player_id=player_id, season=season, position_group=position_group)
        .first()
    )
    if row is None:
        row = PlayerScore(
            player_id=player_id,
            season=season,
            position_group=position_group,
            talent_score=talent_score,
            sub_scores=sub_scores,
        )
        session.add(row)
    else:
        row.talent_score = talent_score
        row.sub_scores = sub_scores


def load_per90_dataframe(engine=None) -> pd.DataFrame:
    """Return per-90 stats joined with player metadata as a flat DataFrame."""
    if engine is None:
        engine = get_engine()
    query = """
        SELECT
            p.fbref_id,
            p.name,
            p.age,
            p.nationality,
            p.position,
            p.club,
            p.league,
            s.season,
            s.minutes,
            s.stats_json
        FROM player_stats_per90 s
        JOIN players p ON p.id = s.player_id
    """
    rows = []
    with engine.connect() as conn:
        for row in conn.execute(text(query)):
            d = dict(row._mapping)
            stats = json.loads(d.pop("stats_json"))
            rows.append({**d, **stats})
    return pd.DataFrame(rows)
