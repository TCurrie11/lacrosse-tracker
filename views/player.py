"""
Player page.

One player in depth: status and flags, competency against the team band, hours vs. competency,
self vs. coach effort, coach-only traits, this week's hours by skill, and the source rows behind
every number.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import pandas as pd
import streamlit as st

from tracker import charts, queries
from tracker.config import CFG, display_label
from tracker.detection import RULE_LABELS, run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

conn = get_conn()
view = time_controls(conn)
players = queries.players_df(conn)
if players.empty or view is None:
    st.title("Player")
    st.info("No data yet.")
    st.stop()

# ---- player picker
player_ids = players.player_id.tolist()
name_of = dict(zip(players.player_id, players.name))
current_player_id = st.session_state.get("_player", player_ids[0])
if current_player_id not in player_ids:
    current_player_id = player_ids[0]
player_id = st.selectbox("Player", player_ids, index=player_ids.index(current_player_id),
                         format_func=lambda option: name_of[option], key=f"player_pick_{current_player_id}")
select_player(player_id)
player = players[players.player_id == player_id].iloc[0]

detection = run_detection(conn, view.as_of)
player_status = detection.status[detection.status.player_id == player_id].iloc[0]

# ---- header
st.title(player["name"])
badge_column, position_column, weeks_column, unverifiable_column = st.columns([2, 1, 1, 1])
with badge_column:
    status_badge(player_status.status, player_status.score)
position_column.metric("Position", player.position.capitalize())
weeks_column.metric("Weeks submitted (window)", f"{player_status.weeks_submitted}/{len(detection.window)}")
unverifiable_column.metric("Unverifiable hours", f"{player_status.unverifiable_share:.0%}",
                           help="Share of reported hours in skills no coach rated in the window.")

# trend range: overview -> chosen range; week mode -> history up to the chosen week
chart_weeks = (view.weeks if view.mode == "overview"
               else [week for week in view.all_weeks if week <= view.as_of][-12:])
st.caption(f"Charts: {short_week(chart_weeks[0])} – {short_week(chart_weeks[-1])}. "
           f"Flags: window {short_week(detection.window[0])} – {short_week(detection.window[-1])}.")

# ---- flags
player_flags = detection.player_flags(player_id, include_dismissed=True)
active_count = sum(not flag.dismissed for flag in player_flags)
with st.expander(f"Flags ({active_count} active)", expanded=player_status.status in ("red", "yellow")):
    if not player_flags:
        st.caption("No rules fired in this window.")
    for flag in player_flags:
        flag_note = " · dismissed" if flag.dismissed else f" · adds {flag.contribution:.2f} to score"
        st.markdown(f"**{RULE_LABELS[flag.rule]}**{flag_note}  \n{flag.message}")
        with st.popover("Evidence"):
            st.json(flag.evidence)
    st.page_link("views/flags.py", label="Review or dismiss flags", icon=":material/flag:")

reports = queries.reports_df(conn)
coach_ratings = queries.coach_skill_df(conn)
traits = queries.coach_traits_df(conn)
player_reports = reports[reports.player_id == player_id]
missing = queries.missing_reports(conn, chart_weeks)
missing_weeks = set(missing[missing.player_id == player_id].week_start)

# ---- per-skill charts
skill = st.selectbox("Skill", CFG.skills, index=CFG.skills.index(st.session_state.get("_skill", CFG.skills[0])),
                     format_func=display_label, key="player_skill")
st.session_state["_skill"] = skill
if not CFG.is_core(player.position, skill):
    st.caption(f"{display_label(skill)} is off-profile for a {player.position} — allowed, compared against core "
               "players, never penalised.")
player_skill_ratings = coach_ratings[(coach_ratings.player_id == player_id) & (coach_ratings.skill == skill)]
player_skill_reports = player_reports[player_reports.skill == skill]

competency_column, hours_column = st.columns(2)
with competency_column:
    st.markdown(f"**{display_label(skill)} competency vs. team**")
    core_player_ids = players[[CFG.is_core(position, skill) for position in players.position]].player_id
    comparison_ids = set(core_player_ids) | {player_id}
    team_competency = (coach_ratings[(coach_ratings.skill == skill) & coach_ratings.player_id.isin(comparison_ids)]
                       .rename(columns={"competency": "value"}))
    st.plotly_chart(charts.trend_band(team_competency.dropna(subset=["value"]), player_id, chart_weeks,
                                      "Competency", player["name"], (1, 10)),
                    width="stretch")
    st.caption("Band = middle 50% of players with this as a core skill.")
with hours_column:
    st.markdown("**Hours vs. competency**")
    st.plotly_chart(charts.hours_and_competency(player_skill_reports[["week_start", "hours"]],
                                                player_skill_ratings[["week_start", "competency"]],
                                                chart_weeks, display_label(skill), missing_weeks),
                    width="stretch")

effort_column, traits_column = st.columns(2)
with effort_column:
    st.markdown(f"**{display_label(skill)} effort: self vs. coach**")
    self_effort = player_skill_reports[player_skill_reports.hours > 0][["week_start", "effort"]]
    st.plotly_chart(charts.effort_lines(self_effort, player_skill_ratings[["week_start", "effort"]], chart_weeks),
                    width="stretch")
with traits_column:
    st.markdown(":material/lock: **Coach-only traits**")
    player_traits = traits[(traits.player_id == player_id) & traits.week_start.isin(chart_weeks)]
    if player_traits.empty:
        st.caption("No trait ratings yet.")
    else:
        st.plotly_chart(charts.traits_lines(player_traits, chart_weeks), width="stretch")

# ---- this week's hours by skill
st.subheader(f"Hours by skill — week of {short_week(view.as_of)}")
week_reports = player_reports[player_reports.week_start == view.as_of]
if week_reports.empty:
    st.caption("No report for this week." if view.as_of in queries.weeks_available(conn) else "")
else:
    window_ratings = coach_ratings[(coach_ratings.player_id == player_id) & coach_ratings.week_start.isin(detection.window)]
    rated_skills = set(window_ratings.dropna(subset=["competency"]).skill)
    trained = week_reports[week_reports.hours > 0].assign(
        verified=lambda rows: rows.skill.isin(rated_skills), skill=lambda rows: rows.skill.map(display_label))
    untrained_skills = ", ".join(display_label(skill) for skill in week_reports[week_reports.hours == 0].skill)
    st.markdown(f"Total **{week_reports.hours.sum():.1f} h** · not trained: " + (untrained_skills or "none"))
    if len(trained):
        st.plotly_chart(charts.hours_breakdown(trained[["skill", "hours", "verified"]]), width="stretch")
        st.caption("Gray = no coach rating for that skill in the window, so those hours can't be cross-checked.")

# ---- source data
st.subheader("Source data")
st.caption("Every number traces back to an imported file and row, or to a coach's sheet.")
reports_tab, ratings_tab, history_tab = st.tabs(["Player reports", "Coach ratings", "Submission history"])
with reports_tab:
    shown_reports = (player_reports[player_reports.week_start.isin(chart_weeks)]
                     .sort_values(["week_start", "skill"], ascending=[False, True]))
    st.dataframe(shown_reports[["week_start", "skill", "hours", "effort", "file", "source_row", "filled_missing",
                                "received_at"]],
                 hide_index=True, width="stretch",
                 column_config={"week_start": "Week", "source_row": "CSV row",
                                "filled_missing": st.column_config.CheckboxColumn("Row missing → 0"),
                                "received_at": "Received"})
with ratings_tab:
    raw_ratings = pd.read_sql("""SELECT week_start, skill, coach, competency/10.0 AS competency, effort/10.0 AS effort,
                                        created_at, superseded_by IS NOT NULL AS replaced
                                 FROM coach_ratings WHERE player_id=? ORDER BY week_start DESC, skill, coach""",
                              conn, params=(int(player_id),))
    st.dataframe(raw_ratings[raw_ratings.week_start.isin(chart_weeks)], hide_index=True, width="stretch",
                 column_config={"week_start": "Week", "replaced": st.column_config.CheckboxColumn("Replaced")})
with history_tab:
    submissions = queries.submissions_df(conn)
    submissions = submissions[submissions.player_id == player_id].sort_values("received_at", ascending=False)
    submissions["status"] = submissions.superseded_by.map(
        lambda replaced_by: "Replaced" if pd.notna(replaced_by) else "Current")
    st.dataframe(submissions[["week_start", "file", "received_at", "status"]], hide_index=True, width="stretch")
