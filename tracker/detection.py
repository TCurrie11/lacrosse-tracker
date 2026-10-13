"""Cheat detection.

Self-reported data can't be proven false, so every rule produces a *reason to review*, with the
numbers behind it, never a verdict. Each rule returns a severity 0-1. A player's score is
sum(rule weight x severity), capped at 1, over the rolling window ending at `as_of`.
Dismissed flags (by fingerprint) don't count.

Rules
-----
hours_vs_gain  Lots of reported hours on a skill, but the coaches' competency rating for that skill
               is flat compared with teammates who have it as a core skill. Halved when *all* the
               player's skills are flat (more likely a plateau or rating issue than false reports).
effort_gap     Self-rated effort far above the coach's effort rating, relative to the team's normal gap.
plausibility   Weekly totals that are hard to believe, or sudden jumps vs. the player's own history.
patterns       Too-perfect data: identical effort on every row, all 10.0s, identical hours every week.
backfill       Reports received long after the week ended, or several weeks submitted in one file.
copy           A week's report identical to a teammate's.
resubmission   A week re-submitted with substantially higher hours.

Coach-only traits (attitude, coachability, competitiveness) are deliberately NOT used here.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import queries as q
from .config import CFG, label
from .db import now_iso

RULE_LABELS = {
    "hours_vs_gain": "Hours vs. improvement",
    "effort_gap": "Effort gap",
    "plausibility": "Implausible hours",
    "patterns": "Too-perfect data",
    "backfill": "Late / backfilled reports",
    "copy": "Copied report",
    "resubmission": "Edited resubmission",
}

STATUS_LABELS = {"green": "No flags", "yellow": "Review", "red": "Flagged", "gray": "Not enough data"}


@dataclass
class Flag:
    player_id: int
    rule: str
    severity: float
    message: str
    skill: str | None = None
    evidence: dict = field(default_factory=dict)
    fingerprint: str = ""
    weight: float = 0.0
    dismissed: bool = False

    @property
    def contribution(self) -> float:
        return 0.0 if self.dismissed else self.weight * self.severity


@dataclass
class DetectionResult:
    as_of: str | None
    window: list[str]
    flags: list[Flag]
    status: pd.DataFrame
    skill_stats: pd.DataFrame

    def player_flags(self, player_id: int, include_dismissed: bool = False) -> list[Flag]:
        return [f for f in self.flags if f.player_id == player_id and (include_dismissed or not f.dismissed)]

    def status_of(self, player_id: int) -> str:
        row = self.status[self.status.player_id == player_id]
        return row.status.iloc[0] if len(row) else "gray"


def _clip(x, lo=0.0, hi=1.0) -> float:
    return float(min(hi, max(lo, x)))


def ordinal(n: float) -> str:
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------- per player/skill stats

def skill_stats(rep: pd.DataFrame, coach: pd.DataFrame, window: list[str], players: pd.DataFrame) -> pd.DataFrame:
    idx = {w: i for i, w in enumerate(window)}
    cols = ["player_id", "skill", "hours", "weeks_submitted", "hours_per_week",
            "n_coach_weeks", "slope", "start", "end"]
    sub_weeks = rep.groupby("player_id")["week_start"].nunique()
    hours = rep.groupby(["player_id", "skill"], as_index=False)["hours"].sum()
    hours["weeks_submitted"] = hours["player_id"].map(sub_weeks)
    hours["hours_per_week"] = hours["hours"] / hours["weeks_submitted"]

    rows = []
    for (pid, skill), g in coach.dropna(subset=["competency"]).groupby(["player_id", "skill"]):
        g = g.sort_values("week_start")
        x = g["week_start"].map(idx).to_numpy(float)
        y = g["competency"].to_numpy(float)
        slope = float(np.polyfit(x, y, 1)[0]) if len(g) >= 2 and x.max() > x.min() else np.nan
        rows.append({"player_id": pid, "skill": skill, "n_coach_weeks": len(g), "slope": slope,
                     "start": float(y[:2].mean()), "end": float(y[-2:].mean())})
    cs = pd.DataFrame(rows, columns=["player_id", "skill", "n_coach_weeks", "slope", "start", "end"])
    stats = hours.merge(cs, on=["player_id", "skill"], how="outer")
    if stats.empty:
        return pd.DataFrame(columns=cols + ["headroom", "norm_slope", "position", "core"])
    stats["headroom"] = (CFG.scale_max - stats["start"]).clip(lower=0.5)
    stats["norm_slope"] = stats["slope"] / stats["headroom"]
    pos = dict(zip(players.player_id, players.position))
    stats["position"] = stats["player_id"].map(pos)
    stats["core"] = [CFG.is_core(p, s) if isinstance(p, str) else False
                     for p, s in zip(stats["position"], stats["skill"])]
    stats["n_coach_weeks"] = stats["n_coach_weeks"].fillna(0).astype(int)
    stats["weeks_submitted"] = stats["weeks_submitted"].fillna(0).astype(int)
    return stats


# ---------------------------------------------------------------- rules

def _hours_vs_gain(stats: pd.DataFrame, d: dict) -> list[Flag]:
    flags = []
    eligible = stats[(stats.n_coach_weeks >= d["min_coach_weeks"]) &
                     (stats.weeks_submitted >= d["min_report_weeks"]) &
                     stats.hours_per_week.notna() & stats.norm_slope.notna()]
    team_rate = eligible[eligible.core].groupby("skill")["norm_slope"].median().to_dict()
    for skill, s in eligible.groupby("skill"):
        group = s[s.core]
        if len(group) < 3:
            continue
        med_h = float(group.hours_per_week.median())
        med_rate = float(group.norm_slope.median())
        if med_rate <= 0:
            continue
        for r in s.itertuples():
            h = float(r.hours_per_week)
            if h < d["min_hours_per_week"]:
                continue
            excess = _clip((h - med_h) / (med_h + 1))
            flat = _clip(1 - r.norm_slope / med_rate)
            if excess < 0.25 or flat < 0.5:
                continue
            sev = excess * flat
            others = eligible[(eligible.player_id == r.player_id) & (eligible.skill != skill) & eligible.core]
            ratios = [o.norm_slope / team_rate[o.skill] for o in others.itertuples() if team_rate.get(o.skill, 0) > 0]
            control = float(np.mean(ratios)) if ratios else None
            if control is not None and control < 0.5:
                sev *= 0.5
                ctrl = "Other skills are also flat — could be a plateau or a rating issue rather than false reports."
            elif control is not None:
                ctrl = f"Other skills improving normally ({control:.0%} of team rate)."
            else:
                ctrl = "No other rated skills to compare against."
            pct = float((group.hours_per_week < h).mean() * 100)
            flags.append(Flag(
                player_id=int(r.player_id), rule="hours_vs_gain", skill=skill, severity=round(sev, 2),
                message=(f"{label(skill)}: {h:.1f} h/week reported (team median {med_h:.1f}, {ordinal(pct)} percentile) "
                         f"but competency only {r.start:.1f} → {r.end:.1f}. {ctrl}"),
                evidence={"hours_per_week": round(h, 1), "team_median_hours": round(med_h, 1),
                          "hours_percentile": round(pct), "competency_start": round(r.start, 1),
                          "competency_end": round(r.end, 1), "slope_per_week": round(r.slope, 3),
                          "relative_progress": round(r.norm_slope / med_rate, 2),
                          "other_skills_vs_team": None if control is None else round(control, 2),
                          "coach_weeks": int(r.n_coach_weeks), "weeks_submitted": int(r.weeks_submitted)}))
    return flags


def _effort_gap(rep: pd.DataFrame, coach: pd.DataFrame) -> list[Flag]:
    r = rep[(rep.hours > 0) & rep.effort.notna()][["player_id", "week_start", "skill", "effort"]]
    c = (coach.dropna(subset=["effort"])[["player_id", "week_start", "skill", "effort"]]
         .rename(columns={"effort": "coach_effort"}))
    m = r.merge(c, on=["player_id", "week_start", "skill"])
    if m.empty:
        return []
    m["gap"] = m["effort"] - m["coach_effort"]
    g = m.groupby("player_id")["gap"].agg(["mean", "count"]).reset_index()
    g = g[g["count"] >= 4]
    if len(g) < 4:
        return []
    med = float(g["mean"].median())
    mad = float((g["mean"] - med).abs().median())
    scale = 1.4826 * mad + 0.15
    flags = []
    for pid, gap, n in zip(g["player_id"], g["mean"], g["count"]):
        z = (gap - med) / scale
        if z > 2.5 and gap - med > 1.0:
            flags.append(Flag(
                player_id=int(pid), rule="effort_gap", severity=round(_clip(0.4 + (z - 2.5) / 4, 0.4), 2),
                message=f"Self-rated effort averages {gap:+.1f} vs. the coaches' rating; the team's typical gap is {med:+.1f}.",
                evidence={"player_gap": round(gap, 2), "team_median_gap": round(med, 2),
                          "robust_z": round(z, 1), "pairs_compared": int(n)}))
    return flags


def _plausibility(rep: pd.DataFrame, d: dict) -> list[Flag]:
    soft = d["soft_total_hours_week"]
    flags = []
    for pid, g in q.weekly_totals(rep).groupby("player_id"):
        g = g.sort_values("week_start")
        sev, parts, ev = 0.0, [], {}
        over = g[g.total_hours > soft]
        if len(over):
            mx = float(over.total_hours.max())
            sev = _clip(0.5 + (mx - soft) / 30, 0.5)
            parts.append(f"{len(over)} week(s) over {soft} h total (max {mx:.1f} h)")
            ev["weeks_over_limit"] = dict(zip(over.week_start, over.total_hours.round(1)))
        vals, weeks = g.total_hours.tolist(), g.week_start.tolist()
        jumps = []
        for i in range(3, len(vals)):
            med = float(np.median(vals[:i]))
            if vals[i] > 2.5 * med and vals[i] - med > 8:
                jumps.append((weeks[i], round(vals[i], 1), round(med, 1)))
        if jumps:
            sev = max(sev, 0.5)
            parts.append("sudden jump: " + "; ".join(f"week of {w} {v} h vs usual {m} h" for w, v, m in jumps))
            ev["jumps"] = jumps
        if sev:
            flags.append(Flag(player_id=int(pid), rule="plausibility", severity=round(sev, 2),
                              message="Weekly hours: " + "; ".join(parts) + ".", evidence=ev))
    return flags


def _patterns(rep: pd.DataFrame) -> list[Flag]:
    flags = []
    for pid, g in rep.groupby("player_id"):
        trained = g[g.hours > 0]
        if len(trained) < 8:
            continue
        sev, parts = 0.0, []
        eff = trained.effort.dropna()
        if len(eff) and eff.nunique() == 1:
            sev = 0.6
            parts.append(f"same effort ({eff.iloc[0]:.1f}) on all {len(eff)} entries")
        elif len(eff) and (eff >= CFG.scale_max).mean() >= 0.9:
            sev = 0.5
            parts.append(f"effort {CFG.scale_max:.1f} on {(eff >= CFG.scale_max).mean():.0%} of entries")
        per_skill = trained.groupby("skill")["hours"].agg(["nunique", "count"])
        multi = per_skill[per_skill["count"] >= 3]
        same = multi[multi["nunique"] == 1]
        if len(multi) >= 2 and len(same) == len(multi):
            sev = max(sev, 0.4)
            parts.append("identical hours every week for " + ", ".join(label(s) for s in same.index))
        if sev:
            flags.append(Flag(player_id=int(pid), rule="patterns", severity=sev,
                              message="Data looks too regular: " + "; ".join(parts) + ".",
                              evidence={"entries": int(len(trained))}))
    return flags


def _backfill(subs_win: pd.DataFrame, d: dict) -> list[Flag]:
    if subs_win.empty:
        return []
    s = subs_win.copy()
    week_end = pd.to_datetime(s["week_start"]) + pd.Timedelta(days=7)
    s["days_late"] = (pd.to_datetime(s["received_at"]) - week_end).dt.days
    flags = []
    for pid, g in s.groupby("player_id"):
        late = g[g.days_late > d["late_grace_days"]]
        per_file = g.groupby("ingest_file_id")["week_start"].nunique()
        multi = per_file[per_file >= 3]
        if late.empty and multi.empty:
            continue
        sev = 0.8 if (len(late) and len(multi)) else (0.5 if len(multi) else 0.3)
        parts = []
        if len(late):
            parts.append(f"{len(late)} week(s) received more than {d['late_grace_days']} days after the week ended "
                         f"(up to {int(late.days_late.max())} days)")
        if len(multi):
            files = g[g.ingest_file_id.isin(multi.index)]["file"].unique()
            parts.append(f"{int(multi.max())} weeks submitted in one file ({', '.join(files)})")
        flags.append(Flag(player_id=int(pid), rule="backfill", severity=sev, message="; ".join(parts).capitalize() + ".",
                          evidence={"late_weeks": dict(zip(late.week_start, late.days_late.astype(int)))}))
    return flags


def _copy(rep: pd.DataFrame, names: dict) -> list[Flag]:
    hits: dict[int, list[tuple[str, list[int]]]] = {}
    for week, g in rep[rep.hours > 0].groupby("week_start"):
        sigs: dict[tuple, list[int]] = {}
        for pid, h in g.groupby("player_id"):
            if len(h) < 3:
                continue
            key = tuple(sorted(zip(h.skill, h.hours, h.effort.fillna(-1))))
            sigs.setdefault(key, []).append(int(pid))
        for pids in sigs.values():
            if len(pids) > 1:
                for pid in pids:
                    hits.setdefault(pid, []).append((week, [p for p in pids if p != pid]))
    flags = []
    for pid, items in hits.items():
        desc = "; ".join(f"week of {w} identical to {', '.join(names.get(o, str(o)) for o in others)}"
                         for w, others in items)
        flags.append(Flag(player_id=pid, rule="copy", severity=0.9 if len(items) >= 2 else 0.6,
                          message=f"Report matches a teammate's exactly: {desc}. Can't tell who copied whom — check both.",
                          evidence={"matches": items}))
    return flags


def _resubmission(rep_all: pd.DataFrame, subs_win: pd.DataFrame) -> list[Flag]:
    sup = subs_win[subs_win.superseded_by.notna()]
    if sup.empty:
        return []
    totals = rep_all.groupby("submission_id")["hours"].sum().to_dict()
    hits: dict[int, list] = {}
    for r in sup.itertuples():
        old, new = totals.get(r.submission_id, 0), totals.get(int(r.superseded_by), 0)
        if new > old * 1.2 and new - old >= 3:
            hits.setdefault(int(r.player_id), []).append((r.week_start, round(old, 1), round(new, 1)))
    return [Flag(player_id=pid, rule="resubmission", severity=0.5 if len(items) >= 2 else 0.35,
                 message="Re-submitted with more hours: " + "; ".join(f"week of {w} {o} h → {n} h" for w, o, n in items) + ".",
                 evidence={"changes": items})
            for pid, items in hits.items()]


# ---------------------------------------------------------------- orchestration

def run_detection(conn: sqlite3.Connection, as_of: str | None = None) -> DetectionResult:
    d = CFG.detection
    players = q.players_df(conn)
    weeks = q.weeks_available(conn)
    if as_of is None and weeks:
        as_of = weeks[-1]
    window = [w for w in weeks if as_of and w <= as_of][-d["window_weeks"]:]

    rep_all = q.reports_df(conn, include_superseded=True)
    rep = rep_all[rep_all.superseded_by.isna() & rep_all.week_start.isin(window)]
    coach = q.coach_skill_df(conn)
    coach = coach[coach.week_start.isin(window)]
    subs = q.submissions_df(conn)
    subs_win = subs[subs.week_start.isin(window)]
    names = dict(zip(players.player_id, players.name))

    stats = skill_stats(rep, coach, window, players)
    flags: list[Flag] = []
    if window:
        flags += _hours_vs_gain(stats, d)
        flags += _effort_gap(rep, coach)
        flags += _plausibility(rep, d)
        flags += _patterns(rep)
        flags += _backfill(subs_win, d)
        flags += _copy(rep, names)
        flags += _resubmission(rep_all, subs_win)

    active = set(players.player_id)
    dismissed = {r[0] for r in conn.execute("SELECT fingerprint FROM flag_dismissals")}
    flags = [f for f in flags if f.player_id in active]
    for f in flags:
        f.weight = float(d["weights"].get(f.rule, 0.5))
        f.fingerprint = f"{f.rule}|{f.player_id}|{f.skill or '-'}|{window[0]}|{window[-1]}"
        f.dismissed = f.fingerprint in dismissed
    flags.sort(key=lambda f: -f.weight * f.severity)

    rated = set(zip(coach.dropna(subset=["competency"]).player_id, coach.dropna(subset=["competency"]).skill))
    th = d["thresholds"]
    rows = []
    for p in players.itertuples():
        pf = [f for f in flags if f.player_id == p.player_id and not f.dismissed]
        score = min(1.0, sum(f.contribution for f in pf))
        prep = rep[rep.player_id == p.player_id]
        n_weeks = prep.week_start.nunique()
        total = float(prep.hours.sum())
        unverified = float(prep[[(p.player_id, s) not in rated for s in prep.skill]].hours.sum()) if len(prep) else 0.0
        status = "red" if score >= th["red"] else "yellow" if score >= th["yellow"] else "green"
        if status == "green" and n_weeks < d["min_report_weeks"]:
            status = "gray"
        rows.append({"player_id": p.player_id, "score": round(score, 2), "status": status, "n_flags": len(pf),
                     "weeks_submitted": n_weeks, "window_hours": round(total, 1),
                     "unverifiable_share": round(unverified / total, 2) if total else 0.0})
    status_df = pd.DataFrame(rows, columns=["player_id", "score", "status", "n_flags", "weeks_submitted",
                                            "window_hours", "unverifiable_share"])
    return DetectionResult(as_of=as_of, window=window, flags=flags, status=status_df, skill_stats=stats)


def dismiss(conn: sqlite3.Connection, flag: Flag, note: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO flag_dismissals (fingerprint, player_id, rule, note, dismissed_at)"
                     " VALUES (?,?,?,?,?)", (flag.fingerprint, flag.player_id, flag.rule, note.strip(), now_iso()))


def restore(conn: sqlite3.Connection, fingerprint: str) -> None:
    with conn:
        conn.execute("DELETE FROM flag_dismissals WHERE fingerprint=?", (fingerprint,))


def dismissals_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("""SELECT d.fingerprint, p.name, d.rule, d.note, d.dismissed_at
                          FROM flag_dismissals d LEFT JOIN players p ON p.id = d.player_id
                          ORDER BY d.dismissed_at DESC""", conn)
