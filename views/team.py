"""
Team charts page.

Box plots with every player as a clickable dot, for any metric, plus a panel for the clicked
player and three extra team views: a players x skills heatmap, hours vs. improvement, and the
self vs. coach effort gap.

Week mode puts one box per skill (or position / trait); overview mode puts one box per week.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import pandas as pd
import streamlit as st

from tracker import charts, queries
from tracker.config import CFG, display_label
from tracker.detection import run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

# Metric name -> (data source, column). Sources: coach ratings, player reports, weekly totals, coach traits.
METRICS = {
    "Coach competency": ("coach", "competency"),
    "Coach effort": ("coach", "effort"),
    "Self-reported hours": ("reports", "hours"),
    "Self-rated effort": ("reports", "effort"),
    "Total hours": ("total", "hours"),
    "Coach-only traits": ("traits", "score"),
}

conn = get_conn()
view = time_controls(conn)
st.title("Team charts")
if view is None:
    st.info("No data yet.")
    st.stop()

players = queries.players_df(conn)
detection = run_detection(conn, view.as_of)
status_of = dict(zip(detection.status.player_id, detection.status.status))
reports = queries.reports_df(conn)
coach_ratings = queries.coach_skill_df(conn)
traits = queries.coach_traits_df(conn)

metric_column, positions_column, options_column = st.columns([2, 2, 2])
metric = metric_column.selectbox("Metric", list(METRICS), key="team_metric")
source, value_column = METRICS[metric]
is_self_reported_hours = source == "reports" and value_column == "hours"
chosen_positions = positions_column.multiselect("Positions", list(CFG.positions), default=list(CFG.positions),
                                                key="team_pos", format_func=str.capitalize)
include_zero_hours = False
if is_self_reported_hours:
    include_zero_hours = options_column.toggle(
        "Include 0-hour entries", value=False, key="team_zero",
        help="0 hours = didn't train that skill. Hidden by default so untrained players don't drag the box down.")

shown_players = players[players.position.isin(chosen_positions)]
position_of = dict(zip(players.player_id, players.position))
name_of = dict(zip(players.player_id, players.name))

# ---- choose what each box represents
weeks_shown = view.weeks
if view.mode == "week":
    if source == "total":
        group_by, groups = "position", [position for position in CFG.positions if position in chosen_positions]
        group_labels = [position.capitalize() for position in groups]
    elif source == "traits":
        group_by, groups = "trait", list(CFG.coach_traits)
        group_labels = [display_label(trait) for trait in groups]
    else:
        if is_self_reported_hours:
            chosen_skills = CFG.skills
        else:
            chosen_skills = options_column.multiselect("Skills", CFG.skills, default=CFG.skills, key="team_skills",
                                                       format_func=display_label)
        group_by, groups = "skill", [skill for skill in CFG.skills if skill in chosen_skills]
        group_labels = [display_label(skill) for skill in groups]
else:
    group_by, groups, group_labels = "week_start", weeks_shown, [short_week(week) for week in weeks_shown]
    if source == "traits":
        focus = st.selectbox("Trait", CFG.coach_traits, key="team_trait", format_func=display_label)
    elif source != "total":
        focus = st.selectbox("Skill", CFG.skills, key="team_skill_ov", format_func=display_label)

# ---- build one row per dot
if source == "coach":
    dots = coach_ratings[coach_ratings.week_start.isin(weeks_shown)].dropna(subset=[value_column])
    dots = dots.rename(columns={value_column: "value"})
    if view.mode == "overview":
        dots = dots[dots.skill == focus]
    dots["hover"] = (dots.player_id.map(position_of).str.capitalize() + " · " + dots.week_start.map(short_week)
                     + " · " + dots.n_coaches.astype(str) + " coach(es)")
elif source == "reports":
    dots = reports[reports.week_start.isin(weeks_shown)].copy()
    if is_self_reported_hours and not include_zero_hours:
        if view.mode == "week":
            hidden_zero_count = int((dots.hours == 0).sum())
        else:
            hidden_zero_count = int(((dots.hours == 0) & (dots.skill == focus)).sum())
        dots = dots[dots.hours > 0]
    if value_column == "effort":
        dots = dots.dropna(subset=["effort"])
    if view.mode == "overview":
        dots = dots[dots.skill == focus]
    dots = dots.rename(columns={value_column: "value"})
    dots["hover"] = (dots.player_id.map(position_of).str.capitalize() + " · " + dots.week_start.map(short_week)
                     + " · " + dots.file + " row " + dots.source_row.fillna(0).astype(int).astype(str))
elif source == "total":
    dots = queries.weekly_totals(reports[reports.week_start.isin(weeks_shown)]).rename(columns={"total_hours": "value"})
    dots["position"] = dots.player_id.map(position_of)
    dots["value"] = dots["value"].round(1)
    dots["hover"] = dots.position.str.capitalize() + " · " + dots.week_start.map(short_week)
else:
    dots = traits[traits.week_start.isin(weeks_shown)].rename(columns={"score": "value"})
    if view.mode == "overview":
        dots = dots[dots.trait == focus]
    dots["hover"] = (dots.player_id.map(position_of).str.capitalize() + " · " + dots.week_start.map(short_week)
                     + " · coach only")

dots = dots[dots.player_id.isin(shown_players.player_id)].copy()
dots["group"] = dots[group_by]
dots["name"] = dots.player_id.map(name_of)
dots["status"] = dots.player_id.map(status_of).fillna("gray")

y_title = {"hours": "Hours per week"}.get(value_column, f"{metric} (1–10)")
y_range = None if value_column == "hours" else [0.5, 10.5]
box_figure = charts.box_with_dots(dots, groups, group_labels, y_title, y_range, st.session_state.get("_player"))
box_event = st.plotly_chart(box_figure, width="stretch", on_select="rerun", selection_mode="points", key="team_box")

chart_notes = []
if is_self_reported_hours and not include_zero_hours:
    chart_notes.append(f"{hidden_zero_count} zero-hour entries hidden")
missing = queries.missing_reports(conn, weeks_shown)
missing = missing[missing.player_id.isin(shown_players.player_id)]
if source in ("reports", "total") and len(missing):
    chart_notes.append(f"missing reports excluded ({len(missing)})")
if source == "total" and view.mode == "week":
    chart_notes.append("total = all skills summed; split by position so defenders are compared with defenders")
if chart_notes:
    st.caption(" · ".join(chart_notes).capitalize() + ". Click a dot to open that player.")

if box_event and box_event.selection and box_event.selection.get("points"):
    clicked_point = next((point for point in box_event.selection["points"] if point.get("customdata")), None)
    if clicked_point:
        select_player(int(float(clicked_point["customdata"][0])))
        clicked_group = int(round(float(clicked_point.get("x", 0))))
        if group_by == "skill" and 0 <= clicked_group < len(groups):
            st.session_state["_skill"] = groups[clicked_group]

# ---- player panel
selected_player_id = st.session_state.get("_player")
with st.container(border=True):
    if selected_player_id is None or selected_player_id not in name_of:
        st.caption("Click a dot to see that player here.")
    else:
        week = view.as_of
        player_status = detection.status[detection.status.player_id == selected_player_id]
        name_column, badge_column = st.columns([3, 1])
        name_column.markdown(f"**{name_of[selected_player_id]}** · {position_of[selected_player_id]} · "
                             f"week of {short_week(week)}")
        with badge_column:
            status_badge(status_of.get(selected_player_id, "gray"),
                         float(player_status.score.iloc[0]) if len(player_status) else None)
        player_flags = detection.player_flags(selected_player_id)
        for flag in player_flags[:3]:
            st.markdown(f"- {flag.message}")
        if not player_flags:
            st.caption("No flags in the current window.")

        default_skill = st.session_state.get("_skill", CFG.skills[0])
        skill = st.selectbox("Skill", CFG.skills, index=CFG.skills.index(default_skill), format_func=display_label,
                             key=f"panel_skill_{selected_player_id}")
        st.session_state["_skill"] = skill
        is_player = coach_ratings.player_id == selected_player_id
        skill_rating = coach_ratings[is_player & (coach_ratings.week_start == week) & (coach_ratings.skill == skill)]
        player_week_reports = reports[(reports.player_id == selected_player_id) & (reports.week_start == week)]
        skill_report = player_week_reports[player_week_reports.skill == skill]
        submitted = not player_week_reports.empty

        competency_column, coach_effort_column, hours_column, self_effort_column = st.columns(4)
        has_competency = len(skill_rating) and pd.notna(skill_rating.competency.iloc[0])
        has_coach_effort = len(skill_rating) and pd.notna(skill_rating.effort.iloc[0])
        competency_column.metric("Coach competency",
                                 f"{skill_rating.competency.iloc[0]:.1f}" if has_competency else "—")
        coach_effort_column.metric("Coach effort", f"{skill_rating.effort.iloc[0]:.1f}" if has_coach_effort else "—")
        if not submitted:
            hours_text, effort_text = "Missing", "—"
        elif skill_report.hours.iloc[0] == 0:
            hours_text, effort_text = "Not trained", "—"
        else:
            hours_text, effort_text = f"{skill_report.hours.iloc[0]:.1f} h", f"{skill_report.effort.iloc[0]:.1f}"
        hours_column.metric("Self hours", hours_text)
        self_effort_column.metric("Self effort", effort_text)
        is_off_profile = not CFG.is_core(position_of[selected_player_id], skill)
        if submitted and is_off_profile and len(skill_report) and skill_report.hours.iloc[0] > 0:
            st.caption("Off-profile skill for this position — allowed, compared against core players, never penalised.")

        week_traits = traits[(traits.player_id == selected_player_id) & (traits.week_start == week)]
        if len(week_traits):
            st.caption(":material/lock: Coach only")
            trait_columns = st.columns(len(CFG.coach_traits))
            for trait_column, trait in zip(trait_columns, CFG.coach_traits):
                trait_score = week_traits[week_traits.trait == trait].score
                trait_column.metric(display_label(trait), f"{trait_score.iloc[0]:.1f}" if len(trait_score) else "—")
        st.page_link("views/player.py", label="Open full player page", icon=":material/arrow_forward:")

# ---- other team views
st.subheader("More team views")
heatmap_tab, hours_gain_tab, effort_gap_tab = st.tabs(["Heatmap", "Hours vs. improvement", "Effort gap"])

with heatmap_tab:
    if view.mode == "week":
        grid = (coach_ratings[coach_ratings.week_start == view.as_of]
                .pivot_table(index="player_id", columns="skill", values="competency"))
        colorbar_title, diverging = "Competency", False
    else:
        rated_in_range = (coach_ratings[coach_ratings.week_start.isin(view.weeks)]
                          .dropna(subset=["competency"]).sort_values("week_start"))
        first_rating = rated_in_range.groupby(["player_id", "skill"]).competency.first()
        last_rating = rated_in_range.groupby(["player_id", "skill"]).competency.last()
        grid = (last_rating - first_rating).unstack()
        colorbar_title, diverging = "Change", True
    grid = grid.reindex(columns=[skill for skill in CFG.skills if skill in grid.columns])
    grid = grid[grid.index.isin(shown_players.player_id)]
    grid.index = [name_of[player_id] for player_id in grid.index]
    grid.columns = [display_label(skill) for skill in grid.columns]
    if grid.empty:
        st.caption("No coach ratings in this range.")
    else:
        st.caption("Competency this week." if view.mode == "week" else
                   "Change in competency from first to last rated week in the range (blue = improved).")
        st.plotly_chart(charts.heatmap(grid.sort_index(), colorbar_title, diverging), width="stretch")

with hours_gain_tab:
    scatter_skill = st.selectbox("Skill", CFG.skills, key="hvg_skill", format_func=display_label)
    stats = detection.skill_stats
    stats = stats[(stats.skill == scatter_skill) & stats.hours_per_week.notna() & stats.slope.notna()
                  & stats.player_id.isin(shown_players.player_id)].copy()
    stats["gain"] = (stats["end"] - stats["start"]).round(1)
    stats["name"] = stats.player_id.map(name_of)
    stats["status"] = stats.player_id.map(status_of).fillna("gray")
    stats["position"] = stats.position.str.capitalize()
    st.caption(f"Window {short_week(detection.window[0])} – {short_week(detection.window[-1])}. Each dot is a player; "
               "the bottom-right corner (lots of hours, little improvement) is what the hours-vs-improvement rule "
               "looks for." if detection.window else "")
    if stats.empty:
        st.caption("Not enough data for this skill yet.")
    else:
        scatter_event = st.plotly_chart(charts.hours_gain_scatter(stats, display_label(scatter_skill)),
                                        width="stretch", on_select="rerun", selection_mode="points", key="hvg")
        if scatter_event and scatter_event.selection and scatter_event.selection.get("points"):
            clicked_point = next((point for point in scatter_event.selection["points"] if point.get("customdata")),
                                 None)
            if clicked_point:
                select_player(int(float(clicked_point["customdata"][0])))
                st.session_state["_skill"] = scatter_skill

with effort_gap_tab:
    window = detection.window
    self_effort = reports[reports.week_start.isin(window) & (reports.hours > 0)].dropna(subset=["effort"])
    coach_effort = coach_ratings[coach_ratings.week_start.isin(window)].dropna(subset=["effort"])
    paired = self_effort.merge(coach_effort[["player_id", "week_start", "skill", "effort"]],
                               on=["player_id", "week_start", "skill"], suffixes=("_self", "_coach"))
    effort_by_player = (paired.groupby("player_id")
                        .agg(self_effort=("effort_self", "mean"), coach_effort=("effort_coach", "mean"))
                        .reset_index())
    effort_by_player = effort_by_player[effort_by_player.player_id.isin(shown_players.player_id)]
    if effort_by_player.empty:
        st.caption("No matching self/coach effort ratings in the window.")
    else:
        effort_by_player["gap"] = (effort_by_player.self_effort - effort_by_player.coach_effort).round(1)
        effort_by_player["name"] = effort_by_player.player_id.map(name_of)
        effort_by_player["status"] = effort_by_player.player_id.map(status_of).fillna("gray")
        st.caption("Average effort per player over the detection window, only where both the player and a coach rated "
                   f"the same skill and week. Most players rate themselves a bit higher — the team's median gap is "
                   f"{effort_by_player.gap.median():+.1f}.")
        st.plotly_chart(charts.effort_dumbbell(effort_by_player), width="stretch")
