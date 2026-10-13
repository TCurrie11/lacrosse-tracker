"""Loads config.yaml. Set TRACKER_CONFIG / TRACKER_DATA_DIR env vars to override paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    skills: list[str]
    coach_traits: list[str]
    positions: dict[str, list[str]]
    scale_min: float
    scale_max: float
    max_hours_per_skill_week: float
    detection: dict
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def inbox(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def archive(self) -> Path:
        return self.data_dir / "archive"

    @property
    def rejected(self) -> Path:
        return self.data_dir / "rejected"

    def core_skills(self, position: str) -> list[str]:
        return self.positions.get(position, list(self.skills))

    def is_core(self, position: str, skill: str) -> bool:
        return skill in self.core_skills(position)


def label(name: str) -> str:
    """ground_balls -> Ground balls"""
    return name.replace("_", " ").capitalize()


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("TRACKER_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    skills = list(raw["skills"])
    positions: dict[str, list[str]] = {}
    for name, spec in raw["positions"].items():
        core = (spec or {}).get("core", "all")
        positions[name] = list(skills) if core == "all" else list(core)
        unknown = set(positions[name]) - set(skills)
        if unknown:
            raise ValueError(f"Position '{name}' lists unknown skills: {sorted(unknown)}")
    data_dir = Path(os.environ.get("TRACKER_DATA_DIR", ROOT / raw.get("data_dir", "data")))
    return Config(
        skills=skills,
        coach_traits=list(raw.get("coach_traits", [])),
        positions=positions,
        scale_min=float(raw["scale"]["min"]),
        scale_max=float(raw["scale"]["max"]),
        max_hours_per_skill_week=float(raw.get("max_hours_per_skill_week", 25)),
        detection=dict(raw["detection"]),
        data_dir=data_dir,
    )


CFG = load_config()
