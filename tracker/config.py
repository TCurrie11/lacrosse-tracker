"""
Configuration loading.

Reads config.yaml into a frozen `Config` object. Two environment variables override paths:
TRACKER_CONFIG points at a different YAML file, TRACKER_DATA_DIR at a different data folder
(the tests use this to work in a throwaway directory).

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    skills: list[str]
    coach_traits: list[str]
    positions: dict[str, list[str]]  # position -> its core skills
    scale_min: float
    scale_max: float
    max_hours_per_skill_week: float
    detection: dict
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def rejected_dir(self) -> Path:
        return self.data_dir / "rejected"

    def core_skills(self, position: str) -> list[str]:
        return self.positions.get(position, list(self.skills))

    def is_core(self, position: str, skill: str) -> bool:
        return skill in self.core_skills(position)


def display_label(name: str) -> str:
    """ground_balls -> Ground balls"""
    return name.replace("_", " ").capitalize()


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path or os.environ.get("TRACKER_CONFIG", PROJECT_ROOT / "config.yaml"))
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    skills = list(raw_config["skills"])

    positions: dict[str, list[str]] = {}
    for position_name, position_spec in raw_config["positions"].items():
        core_skills = (position_spec or {}).get("core", "all")
        positions[position_name] = list(skills) if core_skills == "all" else list(core_skills)
        unknown_skills = set(positions[position_name]) - set(skills)
        if unknown_skills:
            raise ValueError(f"Position '{position_name}' lists unknown skills: {sorted(unknown_skills)}")

    data_dir = Path(os.environ.get("TRACKER_DATA_DIR", PROJECT_ROOT / raw_config.get("data_dir", "data")))
    return Config(
        skills=skills,
        coach_traits=list(raw_config.get("coach_traits", [])),
        positions=positions,
        scale_min=float(raw_config["scale"]["min"]),
        scale_max=float(raw_config["scale"]["max"]),
        max_hours_per_skill_week=float(raw_config.get("max_hours_per_skill_week", 25)),
        detection=dict(raw_config["detection"]),
        data_dir=data_dir,
    )


CFG = load_config()
