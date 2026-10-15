"""
Cheat detection.

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

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import queries
from .config import CFG, display_label
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

# Scale factor that turns a median absolute deviation into a standard-deviation estimate.
MAD_TO_STD = 1.4826


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
        """How much this flag adds to the player's score."""
        return 0.0 if self.dismissed else self.weight * self.severity


@dataclass
class DetectionResult:
    as_of: str | None
    window: list[str]
    flags: list[Flag]
    status: pd.DataFrame
    skill_stats: pd.DataFrame

    def player_flags(self, player_id: int, include_dismissed: bool = False) -> list[Flag]:
        return [flag for flag in self.flags
                if flag.player_id == player_id and (include_dismissed or not flag.dismissed)]

    def status_of(self, player_id: int) -> str:
        player_row = self.status[self.status.player_id == player_id]
        return player_row.status.iloc[0] if len(player_row) else "gray"


def _clamp(value, low=0.0, high=1.0) -> float:
    return float(min(high, max(low, value)))


def ordinal(number: float) -> str:
    """3 -> '3rd', 11 -> '11th', 22 -> '22nd'."""
    number = int(round(number))
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


# ---------------------------------------------------------------- per player/skill stats

def skill_stats(reports: pd.DataFrame, coach_ratings: pd.DataFrame, window: list[str],
                players: pd.DataFrame) -> pd.DataFrame:
    """One row per player / skill over the window: reported hours and the trend in coach competency.

    `slope` is competency change per week; `norm_slope` divides it by the room left to improve
    (headroom), so a player starting at 9.0 isn't expected to gain as fast as one starting at 4.0.
    """
    week_index = {week: position for position, week in enumerate(window)}
    columns = ["player_id", "skill", "hours", "weeks_submitted", "hours_per_week",
               "n_coach_weeks", "slope", "start", "end"]
    weeks_submitted = reports.groupby("player_id")["week_start"].nunique()
    hours = reports.groupby(["player_id", "skill"], as_index=False)["hours"].sum()
    hours["weeks_submitted"] = hours["player_id"].map(weeks_submitted)
    hours["hours_per_week"] = hours["hours"] / hours["weeks_submitted"]

    trend_rows = []
    rated = coach_ratings.dropna(subset=["competency"])
    for (player_id, skill), ratings in rated.groupby(["player_id", "skill"]):
        ratings = ratings.sort_values("week_start")
        week_numbers = ratings["week_start"].map(week_index).to_numpy(float)
        competency = ratings["competency"].to_numpy(float)
        has_spread = len(ratings) >= 2 and week_numbers.max() > week_numbers.min()
        slope = float(np.polyfit(week_numbers, competency, 1)[0]) if has_spread else np.nan
        trend_rows.append({"player_id": player_id, "skill": skill, "n_coach_weeks": len(ratings), "slope": slope,
                           "start": float(competency[:2].mean()), "end": float(competency[-2:].mean())})
    trends = pd.DataFrame(trend_rows, columns=["player_id", "skill", "n_coach_weeks", "slope", "start", "end"])

    stats = hours.merge(trends, on=["player_id", "skill"], how="outer")
    if stats.empty:
        return pd.DataFrame(columns=columns + ["headroom", "norm_slope", "position", "core"])
    stats["headroom"] = (CFG.scale_max - stats["start"]).clip(lower=0.5)
    stats["norm_slope"] = stats["slope"] / stats["headroom"]
    position_of = dict(zip(players.player_id, players.position))
    stats["position"] = stats["player_id"].map(position_of)
    stats["core"] = [CFG.is_core(position, skill) if isinstance(position, str) else False
                     for position, skill in zip(stats["position"], stats["skill"])]
    stats["n_coach_weeks"] = stats["n_coach_weeks"].fillna(0).astype(int)
    stats["weeks_submitted"] = stats["weeks_submitted"].fillna(0).astype(int)
    return stats


# ---------------------------------------------------------------- rules

def _hours_vs_gain(stats: pd.DataFrame, settings: dict) -> list[Flag]:
    flags = []
    eligible = stats[(stats.n_coach_weeks >= settings["min_coach_weeks"]) &
                     (stats.weeks_submitted >= settings["min_report_weeks"]) &
                     stats.hours_per_week.notna() & stats.norm_slope.notna()]
    team_rate_by_skill = eligible[eligible.core].groupby("skill")["norm_slope"].median().to_dict()

    for skill, skill_rows in eligible.groupby("skill"):
        core_players = skill_rows[skill_rows.core]
        if len(core_players) < 3:
            continue
        team_median_hours = float(core_players.hours_per_week.median())
        team_median_rate = float(core_players.norm_slope.median())
        if team_median_rate <= 0:
            continue

        for row in skill_rows.itertuples():
            hours_per_week = float(row.hours_per_week)
            if hours_per_week < settings["min_hours_per_week"]:
                continue
            excess_hours = _clamp((hours_per_week - team_median_hours) / (team_median_hours + 1))
            flatness = _clamp(1 - row.norm_slope / team_median_rate)
            if excess_hours < 0.25 or flatness < 0.5:
                continue
            severity = excess_hours * flatness

            # Control check: are the player's *other* core skills also flat?
            other_skills = eligible[(eligible.player_id == row.player_id) & (eligible.skill != skill) & eligible.core]
            ratios = [other.norm_slope / team_rate_by_skill[other.skill] for other in other_skills.itertuples()
                      if team_rate_by_skill.get(other.skill, 0) > 0]
            other_skills_rate = float(np.mean(ratios)) if ratios else None
            if other_skills_rate is not None and other_skills_rate < 0.5:
                severity *= 0.5
                control_note = ("Other skills are also flat — could be a plateau or a rating issue "
                                "rather than false reports.")
            elif other_skills_rate is not None:
                control_note = f"Other skills improving normally ({other_skills_rate:.0%} of team rate)."
            else:
                control_note = "No other rated skills to compare against."

            hours_percentile = float((core_players.hours_per_week < hours_per_week).mean() * 100)
            flags.append(Flag(
                player_id=int(row.player_id), rule="hours_vs_gain", skill=skill, severity=round(severity, 2),
                message=(f"{display_label(skill)}: {hours_per_week:.1f} h/week reported "
                         f"(team median {team_median_hours:.1f}, {ordinal(hours_percentile)} percentile) "
                         f"but competency only {row.start:.1f} → {row.end:.1f}. {control_note}"),
                evidence={"hours_per_week": round(hours_per_week, 1),
                          "team_median_hours": round(team_median_hours, 1),
                          "hours_percentile": round(hours_percentile),
                          "competency_start": round(row.start, 1),
                          "competency_end": round(row.end, 1),
                          "slope_per_week": round(row.slope, 3),
                          "relative_progress": round(row.norm_slope / team_median_rate, 2),
                          "other_skills_vs_team": None if other_skills_rate is None else round(other_skills_rate, 2),
                          "coach_weeks": int(row.n_coach_weeks),
                          "weeks_submitted": int(row.weeks_submitted)}))
    return flags


def _effort_gap(reports: pd.DataFrame, coach_ratings: pd.DataFrame) -> list[Flag]:
    self_effort = reports[(reports.hours > 0) & reports.effort.notna()][["player_id", "week_start", "skill", "effort"]]
    coach_effort = (coach_ratings.dropna(subset=["effort"])[["player_id", "week_start", "skill", "effort"]]
                    .rename(columns={"effort": "coach_effort"}))
    paired = self_effort.merge(coach_effort, on=["player_id", "week_start", "skill"])
    if paired.empty:
        return []
    paired["gap"] = paired["effort"] - paired["coach_effort"]
    gap_by_player = paired.groupby("player_id")["gap"].agg(["mean", "count"]).reset_index()
    gap_by_player = gap_by_player[gap_by_player["count"] >= 4]
    if len(gap_by_player) < 4:
        return []

    # Robust z-score: median and MAD instead of mean and std, so one extreme player can't hide another.
    team_median_gap = float(gap_by_player["mean"].median())
    median_abs_deviation = float((gap_by_player["mean"] - team_median_gap).abs().median())
    spread = MAD_TO_STD * median_abs_deviation + 0.15
    flags = []
    for player_id, player_gap, pairs_compared in zip(gap_by_player["player_id"], gap_by_player["mean"],
                                                     gap_by_player["count"]):
        robust_z = (player_gap - team_median_gap) / spread
        if robust_z > 2.5 and player_gap - team_median_gap > 1.0:
            flags.append(Flag(
                player_id=int(player_id), rule="effort_gap",
                severity=round(_clamp(0.4 + (robust_z - 2.5) / 4, 0.4), 2),
                message=(f"Self-rated effort averages {player_gap:+.1f} vs. the coaches' rating; "
                         f"the team's typical gap is {team_median_gap:+.1f}."),
                evidence={"player_gap": round(player_gap, 2), "team_median_gap": round(team_median_gap, 2),
                          "robust_z": round(robust_z, 1), "pairs_compared": int(pairs_compared)}))
    return flags


def _plausibility(reports: pd.DataFrame, settings: dict) -> list[Flag]:
    soft_limit = settings["soft_total_hours_week"]
    flags = []
    for player_id, totals in queries.weekly_totals(reports).groupby("player_id"):
        totals = totals.sort_values("week_start")
        severity, findings, evidence = 0.0, [], {}

        weeks_over = totals[totals.total_hours > soft_limit]
        if len(weeks_over):
            highest_total = float(weeks_over.total_hours.max())
            severity = _clamp(0.5 + (highest_total - soft_limit) / 30, 0.5)
            findings.append(f"{len(weeks_over)} week(s) over {soft_limit} h total (max {highest_total:.1f} h)")
            evidence["weeks_over_limit"] = dict(zip(weeks_over.week_start, weeks_over.total_hours.round(1)))

        # A jump is judged against the median of all earlier weeks, once there are at least three.
        weekly_hours, weeks = totals.total_hours.tolist(), totals.week_start.tolist()
        jumps = []
        for week_number in range(3, len(weekly_hours)):
            usual_hours = float(np.median(weekly_hours[:week_number]))
            this_week = weekly_hours[week_number]
            if this_week > 2.5 * usual_hours and this_week - usual_hours > 8:
                jumps.append((weeks[week_number], round(this_week, 1), round(usual_hours, 1)))
        if jumps:
            severity = max(severity, 0.5)
            findings.append("sudden jump: " + "; ".join(f"week of {week} {hours} h vs usual {usual} h"
                                                        for week, hours, usual in jumps))
            evidence["jumps"] = jumps

        if severity:
            flags.append(Flag(player_id=int(player_id), rule="plausibility", severity=round(severity, 2),
                              message="Weekly hours: " + "; ".join(findings) + ".", evidence=evidence))
    return flags


def _patterns(reports: pd.DataFrame) -> list[Flag]:
    flags = []
    for player_id, player_reports in reports.groupby("player_id"):
        trained = player_reports[player_reports.hours > 0]
        if len(trained) < 8:
            continue
        severity, findings = 0.0, []

        efforts = trained.effort.dropna()
        share_at_max = (efforts >= CFG.scale_max).mean()
        if len(efforts) and efforts.nunique() == 1:
            severity = 0.6
            findings.append(f"same effort ({efforts.iloc[0]:.1f}) on all {len(efforts)} entries")
        elif len(efforts) and share_at_max >= 0.9:
            severity = 0.5
            findings.append(f"effort {CFG.scale_max:.1f} on {share_at_max:.0%} of entries")

        hours_per_skill = trained.groupby("skill")["hours"].agg(["nunique", "count"])
        repeated_skills = hours_per_skill[hours_per_skill["count"] >= 3]
        constant_skills = repeated_skills[repeated_skills["nunique"] == 1]
        if len(repeated_skills) >= 2 and len(constant_skills) == len(repeated_skills):
            severity = max(severity, 0.4)
            findings.append("identical hours every week for "
                            + ", ".join(display_label(skill) for skill in constant_skills.index))

        if severity:
            flags.append(Flag(player_id=int(player_id), rule="patterns", severity=severity,
                              message="Data looks too regular: " + "; ".join(findings) + ".",
                              evidence={"entries": int(len(trained))}))
    return flags


def _backfill(window_submissions: pd.DataFrame, settings: dict) -> list[Flag]:
    if window_submissions.empty:
        return []
    submissions = window_submissions.copy()
    week_end = pd.to_datetime(submissions["week_start"]) + pd.Timedelta(days=7)
    submissions["days_late"] = (pd.to_datetime(submissions["received_at"]) - week_end).dt.days
    grace_days = settings["late_grace_days"]

    flags = []
    for player_id, player_submissions in submissions.groupby("player_id"):
        late = player_submissions[player_submissions.days_late > grace_days]
        weeks_per_file = player_submissions.groupby("ingest_file_id")["week_start"].nunique()
        multi_week_files = weeks_per_file[weeks_per_file >= 3]
        if late.empty and multi_week_files.empty:
            continue
        if len(late) and len(multi_week_files):
            severity = 0.8
        elif len(multi_week_files):
            severity = 0.5
        else:
            severity = 0.3

        findings = []
        if len(late):
            findings.append(f"{len(late)} week(s) received more than {grace_days} days after the week ended "
                            f"(up to {int(late.days_late.max())} days)")
        if len(multi_week_files):
            file_names = player_submissions[player_submissions.ingest_file_id.isin(multi_week_files.index)]["file"].unique()
            findings.append(f"{int(multi_week_files.max())} weeks submitted in one file ({', '.join(file_names)})")
        flags.append(Flag(player_id=int(player_id), rule="backfill", severity=severity,
                          message="; ".join(findings).capitalize() + ".",
                          evidence={"late_weeks": dict(zip(late.week_start, late.days_late.astype(int)))}))
    return flags


def _copy(reports: pd.DataFrame, name_of: dict) -> list[Flag]:
    # A report's signature is its (skill, hours, effort) rows; identical signatures in one week are a match.
    matches_by_player: dict[int, list[tuple[str, list[int]]]] = {}
    for week, week_reports in reports[reports.hours > 0].groupby("week_start"):
        players_by_signature: dict[tuple, list[int]] = {}
        for player_id, player_rows in week_reports.groupby("player_id"):
            if len(player_rows) < 3:
                continue
            signature = tuple(sorted(zip(player_rows.skill, player_rows.hours, player_rows.effort.fillna(-1))))
            players_by_signature.setdefault(signature, []).append(int(player_id))
        for matching_players in players_by_signature.values():
            if len(matching_players) > 1:
                for player_id in matching_players:
                    teammates = [other for other in matching_players if other != player_id]
                    matches_by_player.setdefault(player_id, []).append((week, teammates))

    flags = []
    for player_id, matches in matches_by_player.items():
        description = "; ".join(
            f"week of {week} identical to {', '.join(name_of.get(other, str(other)) for other in teammates)}"
            for week, teammates in matches)
        flags.append(Flag(player_id=player_id, rule="copy", severity=0.9 if len(matches) >= 2 else 0.6,
                          message=(f"Report matches a teammate's exactly: {description}. "
                                   "Can't tell who copied whom — check both."),
                          evidence={"matches": matches}))
    return flags


def _resubmission(all_reports: pd.DataFrame, window_submissions: pd.DataFrame) -> list[Flag]:
    replaced = window_submissions[window_submissions.superseded_by.notna()]
    if replaced.empty:
        return []
    total_hours_by_submission = all_reports.groupby("submission_id")["hours"].sum().to_dict()
    changes_by_player: dict[int, list] = {}
    for submission in replaced.itertuples():
        old_total = total_hours_by_submission.get(submission.submission_id, 0)
        new_total = total_hours_by_submission.get(int(submission.superseded_by), 0)
        if new_total > old_total * 1.2 and new_total - old_total >= 3:
            changes_by_player.setdefault(int(submission.player_id), []).append(
                (submission.week_start, round(old_total, 1), round(new_total, 1)))
    return [Flag(player_id=player_id, rule="resubmission", severity=0.5 if len(changes) >= 2 else 0.35,
                 message="Re-submitted with more hours: "
                         + "; ".join(f"week of {week} {old} h → {new} h" for week, old, new in changes) + ".",
                 evidence={"changes": changes})
            for player_id, changes in changes_by_player.items()]


# ---------------------------------------------------------------- orchestration

def run_detection(conn: sqlite3.Connection, as_of: str | None = None) -> DetectionResult:
    """Run every rule over the rolling window ending at `as_of` (default: the latest week)."""
    settings = CFG.detection
    players = queries.players_df(conn)
    weeks = queries.weeks_available(conn)
    if as_of is None and weeks:
        as_of = weeks[-1]
    window = [week for week in weeks if as_of and week <= as_of][-settings["window_weeks"]:]

    all_reports = queries.reports_df(conn, include_superseded=True)
    reports = all_reports[all_reports.superseded_by.isna() & all_reports.week_start.isin(window)]
    coach_ratings = queries.coach_skill_df(conn)
    coach_ratings = coach_ratings[coach_ratings.week_start.isin(window)]
    submissions = queries.submissions_df(conn)
    window_submissions = submissions[submissions.week_start.isin(window)]
    name_of = dict(zip(players.player_id, players.name))

    stats = skill_stats(reports, coach_ratings, window, players)
    flags: list[Flag] = []
    if window:
        flags += _hours_vs_gain(stats, settings)
        flags += _effort_gap(reports, coach_ratings)
        flags += _plausibility(reports, settings)
        flags += _patterns(reports)
        flags += _backfill(window_submissions, settings)
        flags += _copy(reports, name_of)
        flags += _resubmission(all_reports, window_submissions)

    active_player_ids = set(players.player_id)
    dismissed_fingerprints = {row[0] for row in conn.execute("SELECT fingerprint FROM flag_dismissals")}
    flags = [flag for flag in flags if flag.player_id in active_player_ids]
    for flag in flags:
        flag.weight = float(settings["weights"].get(flag.rule, 0.5))
        flag.fingerprint = f"{flag.rule}|{flag.player_id}|{flag.skill or '-'}|{window[0]}|{window[-1]}"
        flag.dismissed = flag.fingerprint in dismissed_fingerprints
    flags.sort(key=lambda flag: -flag.weight * flag.severity)

    rated_competency = coach_ratings.dropna(subset=["competency"])
    rated_skills = set(zip(rated_competency.player_id, rated_competency.skill))
    thresholds = settings["thresholds"]
    status_rows = []
    for player in players.itertuples():
        active_flags = [flag for flag in flags if flag.player_id == player.player_id and not flag.dismissed]
        score = min(1.0, sum(flag.contribution for flag in active_flags))
        player_reports = reports[reports.player_id == player.player_id]
        weeks_submitted = player_reports.week_start.nunique()
        window_hours = float(player_reports.hours.sum())
        unverifiable_hours = float(
            player_reports[[(player.player_id, skill) not in rated_skills for skill in player_reports.skill]].hours.sum()
        ) if len(player_reports) else 0.0

        if score >= thresholds["red"]:
            status = "red"
        elif score >= thresholds["yellow"]:
            status = "yellow"
        else:
            status = "green"
        if status == "green" and weeks_submitted < settings["min_report_weeks"]:
            status = "gray"

        status_rows.append({"player_id": player.player_id, "score": round(score, 2), "status": status,
                            "n_flags": len(active_flags), "weeks_submitted": weeks_submitted,
                            "window_hours": round(window_hours, 1),
                            "unverifiable_share": round(unverifiable_hours / window_hours, 2) if window_hours else 0.0})
    status_table = pd.DataFrame(status_rows, columns=["player_id", "score", "status", "n_flags", "weeks_submitted",
                                                      "window_hours", "unverifiable_share"])
    return DetectionResult(as_of=as_of, window=window, flags=flags, status=status_table, skill_stats=stats)


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
