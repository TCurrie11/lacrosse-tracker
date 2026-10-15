"""
Plotly figures.

Each clickable dot carries customdata = [player_id, name, extra hover text] so Streamlit's click
selection can open that player.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

STATUS_COLORS = {"green": "#0ca30c", "yellow": "#fab219", "red": "#d03b3b", "gray": "#9a9893"}
STATUS_ORDER = ["red", "yellow", "green", "gray"]
STATUS_NAMES = {"green": "No flags", "yellow": "Review", "red": "Flagged", "gray": "Not enough data"}
BLUE, ORANGE, NEUTRAL = "#2a78d6", "#eb6834", "#888780"


def _apply_base_layout(fig: go.Figure, height: int = 420, y_title: str | None = None) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      hoverlabel=dict(align="left"))
    if y_title:
        fig.update_yaxes(title_text=y_title)
    return fig


def _jitter(player_id: int, spread: float = 0.16) -> float:
    """A fixed horizontal offset per player, so dots don't stack and don't move between reruns."""
    return ((int(player_id) * 37) % 11 - 5) / 5 * spread


def box_with_dots(points: pd.DataFrame, groups: list[str], labels: list[str], y_title: str,
                  y_range: tuple[float, float] | None = None, selected: int | None = None) -> go.Figure:
    """points: player_id, name, status, group, value, hover. One box per group (if n >= 3) with every player as a dot."""
    fig = go.Figure()
    group_index = {group: position for position, group in enumerate(groups)}
    points = points[points["group"].isin(groups)].copy()
    points["x_position"] = points["group"].map(group_index) + points["player_id"].map(_jitter)
    count_by_group = points.groupby("group").size().to_dict()

    for group in groups:
        group_points = points[points["group"] == group]
        if len(group_points) >= 3:
            fig.add_trace(go.Box(x=[group_index[group]] * len(group_points), y=group_points["value"],
                                 boxpoints=False, width=0.5, marker_color=NEUTRAL,
                                 fillcolor="rgba(136,135,128,0.10)", line=dict(width=1.5),
                                 hoverinfo="y", showlegend=False, name=labels[group_index[group]]))
    for status in STATUS_ORDER:
        status_points = points[points["status"] == status]
        if status_points.empty:
            continue
        is_selected = status_points["player_id"] == selected
        fig.add_trace(go.Scatter(
            x=status_points["x_position"], y=status_points["value"], mode="markers", name=STATUS_NAMES[status],
            marker=dict(color=STATUS_COLORS[status], size=np.where(is_selected, 16, 10),
                        line=dict(color=np.where(is_selected, "#0b0b0b", "white").tolist(),
                                  width=np.where(is_selected, 2.5, 1)),
                        opacity=1.0 if selected is None else np.where(is_selected, 1.0, 0.45)),
            customdata=np.stack([status_points["player_id"], status_points["name"], status_points["hover"]], axis=-1),
            hovertemplate="<b>%{customdata[1]}</b><br>%{y}<br>%{customdata[2]}<extra></extra>"))
    fig.update_xaxes(tickvals=list(range(len(groups))), range=[-0.6, len(groups) - 0.4],
                     ticktext=[f"{label}<br><span style='font-size:11px'>n={count_by_group.get(group, 0)}</span>"
                               for group, label in zip(groups, labels)])
    if y_range:
        fig.update_yaxes(range=y_range)
    fig.update_layout(clickmode="event+select", dragmode=False)
    return _apply_base_layout(fig, 440, y_title)


def trend_band(team: pd.DataFrame, player_id: int, weeks: list[str], y_title: str, player_name: str,
               y_range: tuple[float, float] | None = None) -> go.Figure:
    """team: player_id, week_start, value. Player's line over the team's middle-50% band + median."""
    team = team[team.week_start.isin(weeks)]
    team_quartiles = team.groupby("week_start")["value"].quantile([0.25, 0.5, 0.75]).unstack().reindex(weeks)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=weeks, y=team_quartiles[0.75], mode="lines", line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=weeks, y=team_quartiles[0.25], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(136,135,128,0.18)", name="Team middle 50%", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=weeks, y=team_quartiles[0.5], mode="lines",
                             line=dict(color=NEUTRAL, dash="dash", width=2), name="Team median"))
    player_values = team[team.player_id == player_id].set_index("week_start").reindex(weeks)
    fig.add_trace(go.Scatter(x=weeks, y=player_values["value"], mode="lines+markers", connectgaps=False,
                             line=dict(color=BLUE, width=2.5), marker=dict(size=8), name=player_name))
    if y_range:
        fig.update_yaxes(range=y_range)
    fig.update_xaxes(type="category")
    return _apply_base_layout(fig, 340, y_title)


def hours_and_competency(hours: pd.DataFrame, competency: pd.DataFrame, weeks: list[str], skill: str,
                         missing_weeks: set[str]) -> go.Figure:
    """Two stacked panels sharing the week axis (no dual y-axis): self-reported hours, coach competency."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.45, 0.55],
                        subplot_titles=(f"{skill}: self-reported hours", f"{skill}: coach competency (weekly avg)"))
    hours_by_week = hours.set_index("week_start").reindex(weeks)
    fig.add_trace(go.Bar(x=weeks, y=hours_by_week["hours"], marker_color=ORANGE, name="Hours", width=0.5,
                         hovertemplate="%{x}<br>%{y} h<extra></extra>"), row=1, col=1)
    for week in missing_weeks & set(weeks):
        fig.add_annotation(x=week, y=0, text="missing", showarrow=False, yshift=10,
                           font=dict(size=11, color=NEUTRAL), row=1, col=1)
    competency_by_week = competency.set_index("week_start").reindex(weeks)
    fig.add_trace(go.Scatter(x=weeks, y=competency_by_week["competency"], mode="lines+markers",
                             line=dict(color=BLUE, width=2.5), marker=dict(size=8), name="Competency",
                             connectgaps=True), row=2, col=1)
    fig.update_yaxes(range=[1, 10], row=2, col=1)
    fig.update_xaxes(type="category")
    fig.update_layout(showlegend=False)
    return _apply_base_layout(fig, 460)


def effort_lines(self_effort: pd.DataFrame, coach_effort: pd.DataFrame, weeks: list[str]) -> go.Figure:
    self_by_week = self_effort.set_index("week_start").reindex(weeks)
    coach_by_week = coach_effort.set_index("week_start").reindex(weeks)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=weeks, y=coach_by_week["effort"], mode="lines+markers", name="Coach effort",
                             line=dict(color=BLUE, width=2.5), connectgaps=True))
    fig.add_trace(go.Scatter(x=weeks, y=self_by_week["effort"], mode="lines+markers", name="Self-rated effort",
                             line=dict(color=ORANGE, width=2.5, dash="dot"), marker=dict(symbol="diamond"),
                             connectgaps=True, fill="tonexty", fillcolor="rgba(235,104,52,0.10)"))
    fig.update_yaxes(range=[1, 10.3])
    fig.update_xaxes(type="category")
    return _apply_base_layout(fig, 300, "Effort (1–10)")


def effort_dumbbell(efforts: pd.DataFrame) -> go.Figure:
    """efforts: player_id, name, status, self_effort, coach_effort, gap (window averages)."""
    efforts = efforts.sort_values("gap")
    fig = go.Figure()
    for player in efforts.itertuples():
        fig.add_trace(go.Scatter(x=[player.coach_effort, player.self_effort], y=[player.name, player.name],
                                 mode="lines", line=dict(color=STATUS_COLORS[player.status], width=3),
                                 showlegend=False, hoverinfo="skip"))
    hover_data = np.stack([efforts.player_id, efforts.name, efforts.gap], -1)
    fig.add_trace(go.Scatter(x=efforts.coach_effort, y=efforts.name, mode="markers", name="Coach",
                             marker=dict(color=BLUE, size=11), customdata=hover_data,
                             hovertemplate="%{customdata[1]}<br>Coach %{x:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=efforts.self_effort, y=efforts.name, mode="markers", name="Self",
                             marker=dict(color=ORANGE, size=11, symbol="diamond"), customdata=hover_data,
                             hovertemplate="%{customdata[1]}<br>Self %{x:.1f} (gap %{customdata[2]:+.1f})<extra></extra>"))
    fig.update_xaxes(range=[1, 10.3], title_text="Average effort over window")
    return _apply_base_layout(fig, max(300, 28 * len(efforts) + 80))


def hours_gain_scatter(players: pd.DataFrame, skill: str) -> go.Figure:
    """players: player_id, name, status, position, hours_per_week, gain."""
    fig = go.Figure()
    for status in STATUS_ORDER:
        status_players = players[players.status == status]
        if status_players.empty:
            continue
        fig.add_trace(go.Scatter(
            x=status_players.hours_per_week, y=status_players.gain, mode="markers", name=STATUS_NAMES[status],
            marker=dict(color=STATUS_COLORS[status], size=12, line=dict(color="white", width=1)),
            customdata=np.stack([status_players.player_id, status_players.name, status_players.position], -1),
            hovertemplate="<b>%{customdata[1]}</b> (%{customdata[2]})<br>%{x:.1f} h/week<br>"
                          "competency %{y:+.1f}<extra></extra>"))
    if len(players):
        fig.add_hline(y=float(players.gain.median()), line=dict(color=NEUTRAL, dash="dot", width=1))
        fig.add_vline(x=float(players.hours_per_week.median()), line=dict(color=NEUTRAL, dash="dot", width=1))
    fig.update_xaxes(title_text=f"{skill}: self-reported hours per week", rangemode="tozero")
    fig.update_layout(clickmode="event+select", dragmode=False)
    return _apply_base_layout(fig, 400, "Competency change over window")


def heatmap(pivot: pd.DataFrame, title: str, diverging: bool = False) -> go.Figure:
    """Players x skills grid. `diverging` centres the colour scale on zero (for changes)."""
    if diverging:
        limit = max(0.5, float(np.nanmax(np.abs(pivot.values)))) if pivot.size else 1
        color_settings = dict(colorscale=[[0, "#d03b3b"], [0.5, "#f0efec"], [1, BLUE]], zmin=-limit, zmax=limit)
    else:
        color_settings = dict(colorscale=[[0, "#eaf2fb"], [1, "#0c447c"]], zmin=1, zmax=10)
    fig = go.Figure(go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index), hoverongaps=False,
                               texttemplate="%{z:.1f}", colorbar=dict(title=title, thickness=12), xgap=2, ygap=2,
                               **color_settings))
    fig.update_yaxes(autorange="reversed")
    return _apply_base_layout(fig, max(320, 30 * len(pivot) + 100))


def traits_lines(traits: pd.DataFrame, weeks: list[str]) -> go.Figure:
    colors = [BLUE, ORANGE, "#1baf7a", "#6250d6", "#e87ba4"]
    dashes = ["solid", "dot", "dash", "dashdot", "longdash"]
    fig = go.Figure()
    for trait_number, (trait, trait_scores) in enumerate(traits.groupby("trait")):
        scores_by_week = trait_scores.set_index("week_start").reindex(weeks)
        fig.add_trace(go.Scatter(x=weeks, y=scores_by_week["score"], mode="lines+markers", name=trait.capitalize(),
                                 line=dict(color=colors[trait_number % 5], dash=dashes[trait_number % 5], width=2),
                                 connectgaps=True))
    fig.update_yaxes(range=[1, 10.3])
    fig.update_xaxes(type="category")
    return _apply_base_layout(fig, 300, "Coach rating (1–10)")


def hours_breakdown(hours: pd.DataFrame) -> go.Figure:
    """hours: skill, hours, verified (bool) — verified = coaches rated this skill in the window."""
    hours = hours.sort_values("hours")
    fig = go.Figure(go.Bar(
        x=hours.hours, y=hours.skill, orientation="h",
        marker_color=[BLUE if verified else "#b4b2a9" for verified in hours.verified],
        text=[f"{skill_hours:.1f} h" + ("" if verified else " · unverifiable")
              for skill_hours, verified in zip(hours.hours, hours.verified)],
        textposition="outside", hovertemplate="%{y}: %{x:.1f} h<extra></extra>"))
    fig.update_xaxes(title_text="Hours", rangemode="tozero")
    return _apply_base_layout(fig, max(240, 32 * len(hours) + 60))
