"""Plotly figures. Each dot carries customdata = [player_id, name, extra hover text] so
Streamlit's click selection can open that player."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

STATUS_COLORS = {"green": "#0ca30c", "yellow": "#fab219", "red": "#d03b3b", "gray": "#9a9893"}
STATUS_ORDER = ["red", "yellow", "green", "gray"]
STATUS_NAMES = {"green": "No flags", "yellow": "Review", "red": "Flagged", "gray": "Not enough data"}
BLUE, ORANGE, NEUTRAL = "#2a78d6", "#eb6834", "#888780"


def _base(fig: go.Figure, height: int = 420, y_title: str | None = None) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      hoverlabel=dict(align="left"))
    if y_title:
        fig.update_yaxes(title_text=y_title)
    return fig


def _jitter(pid: int, spread: float = 0.16) -> float:
    return ((int(pid) * 37) % 11 - 5) / 5 * spread


def box_with_dots(points: pd.DataFrame, groups: list[str], labels: list[str], y_title: str,
                  y_range: tuple[float, float] | None = None, selected: int | None = None) -> go.Figure:
    """points: player_id, name, status, group, value, hover. One box per group (if n >= 3) with every player as a dot."""
    fig = go.Figure()
    gi = {g: i for i, g in enumerate(groups)}
    pts = points[points["group"].isin(groups)].copy()
    pts["gx"] = pts["group"].map(gi) + pts["player_id"].map(_jitter)
    counts = pts.groupby("group").size().to_dict()

    for g in groups:
        d = pts[pts["group"] == g]
        if len(d) >= 3:
            fig.add_trace(go.Box(x=[gi[g]] * len(d), y=d["value"], boxpoints=False, width=0.5,
                                 marker_color=NEUTRAL, fillcolor="rgba(136,135,128,0.10)", line=dict(width=1.5),
                                 hoverinfo="y", showlegend=False, name=labels[gi[g]]))
    for status in STATUS_ORDER:
        d = pts[pts["status"] == status]
        if d.empty:
            continue
        is_sel = d["player_id"] == selected
        fig.add_trace(go.Scatter(
            x=d["gx"], y=d["value"], mode="markers", name=STATUS_NAMES[status],
            marker=dict(color=STATUS_COLORS[status], size=np.where(is_sel, 16, 10),
                        line=dict(color=np.where(is_sel, "#0b0b0b", "white").tolist(), width=np.where(is_sel, 2.5, 1)),
                        opacity=1.0 if selected is None else np.where(is_sel, 1.0, 0.45)),
            customdata=np.stack([d["player_id"], d["name"], d["hover"]], axis=-1),
            hovertemplate="<b>%{customdata[1]}</b><br>%{y}<br>%{customdata[2]}<extra></extra>"))
    fig.update_xaxes(tickvals=list(range(len(groups))), range=[-0.6, len(groups) - 0.4],
                     ticktext=[f"{lab}<br><span style='font-size:11px'>n={counts.get(g, 0)}</span>"
                               for g, lab in zip(groups, labels)])
    if y_range:
        fig.update_yaxes(range=y_range)
    fig.update_layout(clickmode="event+select", dragmode=False)
    return _base(fig, 440, y_title)


def trend_band(team: pd.DataFrame, player_id: int, weeks: list[str], y_title: str, player_name: str,
               y_range: tuple[float, float] | None = None) -> go.Figure:
    """team: player_id, week_start, value. Player's line over the team's middle-50% band + median."""
    team = team[team.week_start.isin(weeks)]
    agg = team.groupby("week_start")["value"].quantile([0.25, 0.5, 0.75]).unstack().reindex(weeks)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=weeks, y=agg[0.75], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=weeks, y=agg[0.25], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(136,135,128,0.18)", name="Team middle 50%", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=weeks, y=agg[0.5], mode="lines", line=dict(color=NEUTRAL, dash="dash", width=2),
                             name="Team median"))
    p = team[team.player_id == player_id].set_index("week_start").reindex(weeks)
    fig.add_trace(go.Scatter(x=weeks, y=p["value"], mode="lines+markers", connectgaps=False,
                             line=dict(color=BLUE, width=2.5), marker=dict(size=8), name=player_name))
    if y_range:
        fig.update_yaxes(range=y_range)
    fig.update_xaxes(type="category")
    return _base(fig, 340, y_title)


def hours_and_competency(hours: pd.DataFrame, comp: pd.DataFrame, weeks: list[str], skill: str,
                         missing_weeks: set[str]) -> go.Figure:
    """Two stacked panels sharing the week axis (no dual y-axis): self-reported hours, coach competency."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.45, 0.55],
                        subplot_titles=(f"{skill}: self-reported hours", f"{skill}: coach competency (weekly avg)"))
    h = hours.set_index("week_start").reindex(weeks)
    fig.add_trace(go.Bar(x=weeks, y=h["hours"], marker_color=ORANGE, name="Hours", width=0.5,
                         hovertemplate="%{x}<br>%{y} h<extra></extra>"), row=1, col=1)
    for w in missing_weeks & set(weeks):
        fig.add_annotation(x=w, y=0, text="missing", showarrow=False, yshift=10, font=dict(size=11, color=NEUTRAL),
                           row=1, col=1)
    c = comp.set_index("week_start").reindex(weeks)
    fig.add_trace(go.Scatter(x=weeks, y=c["competency"], mode="lines+markers", line=dict(color=BLUE, width=2.5),
                             marker=dict(size=8), name="Competency", connectgaps=True), row=2, col=1)
    fig.update_yaxes(range=[1, 10], row=2, col=1)
    fig.update_xaxes(type="category")
    fig.update_layout(showlegend=False)
    return _base(fig, 460)


def effort_lines(self_eff: pd.DataFrame, coach_eff: pd.DataFrame, weeks: list[str]) -> go.Figure:
    s = self_eff.set_index("week_start").reindex(weeks)
    c = coach_eff.set_index("week_start").reindex(weeks)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=weeks, y=c["effort"], mode="lines+markers", name="Coach effort",
                             line=dict(color=BLUE, width=2.5), connectgaps=True))
    fig.add_trace(go.Scatter(x=weeks, y=s["effort"], mode="lines+markers", name="Self-rated effort",
                             line=dict(color=ORANGE, width=2.5, dash="dot"), marker=dict(symbol="diamond"),
                             connectgaps=True, fill="tonexty", fillcolor="rgba(235,104,52,0.10)"))
    fig.update_yaxes(range=[1, 10.3])
    fig.update_xaxes(type="category")
    return _base(fig, 300, "Effort (1–10)")


def effort_dumbbell(df: pd.DataFrame) -> go.Figure:
    """df: player_id, name, status, self_effort, coach_effort (window averages)."""
    df = df.sort_values("gap")
    fig = go.Figure()
    for r in df.itertuples():
        fig.add_trace(go.Scatter(x=[r.coach_effort, r.self_effort], y=[r.name, r.name], mode="lines",
                                 line=dict(color=STATUS_COLORS[r.status], width=3), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=df.coach_effort, y=df.name, mode="markers", name="Coach",
                             marker=dict(color=BLUE, size=11), customdata=np.stack([df.player_id, df.name, df.gap], -1),
                             hovertemplate="%{customdata[1]}<br>Coach %{x:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=df.self_effort, y=df.name, mode="markers", name="Self",
                             marker=dict(color=ORANGE, size=11, symbol="diamond"),
                             customdata=np.stack([df.player_id, df.name, df.gap], -1),
                             hovertemplate="%{customdata[1]}<br>Self %{x:.1f} (gap %{customdata[2]:+.1f})<extra></extra>"))
    fig.update_xaxes(range=[1, 10.3], title_text="Average effort over window")
    return _base(fig, max(300, 28 * len(df) + 80))


def hours_gain_scatter(df: pd.DataFrame, skill: str) -> go.Figure:
    """df: player_id, name, status, hours_per_week, gain."""
    fig = go.Figure()
    for status in STATUS_ORDER:
        d = df[df.status == status]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(x=d.hours_per_week, y=d.gain, mode="markers", name=STATUS_NAMES[status],
                                 marker=dict(color=STATUS_COLORS[status], size=12, line=dict(color="white", width=1)),
                                 customdata=np.stack([d.player_id, d.name, d.position], -1),
                                 hovertemplate="<b>%{customdata[1]}</b> (%{customdata[2]})<br>%{x:.1f} h/week<br>"
                                               "competency %{y:+.1f}<extra></extra>"))
    if len(df):
        fig.add_hline(y=float(df.gain.median()), line=dict(color=NEUTRAL, dash="dot", width=1))
        fig.add_vline(x=float(df.hours_per_week.median()), line=dict(color=NEUTRAL, dash="dot", width=1))
    fig.update_xaxes(title_text=f"{skill}: self-reported hours per week", rangemode="tozero")
    fig.update_layout(clickmode="event+select", dragmode=False)
    return _base(fig, 400, "Competency change over window")


def heatmap(pivot: pd.DataFrame, title: str, diverging: bool = False) -> go.Figure:
    if diverging:
        lim = max(0.5, float(np.nanmax(np.abs(pivot.values)))) if pivot.size else 1
        kw = dict(colorscale=[[0, "#d03b3b"], [0.5, "#f0efec"], [1, BLUE]], zmin=-lim, zmax=lim)
    else:
        kw = dict(colorscale=[[0, "#eaf2fb"], [1, "#0c447c"]], zmin=1, zmax=10)
    fig = go.Figure(go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index), hoverongaps=False,
                               texttemplate="%{z:.1f}", colorbar=dict(title=title, thickness=12), xgap=2, ygap=2, **kw))
    fig.update_yaxes(autorange="reversed")
    return _base(fig, max(320, 30 * len(pivot) + 100))


def traits_lines(df: pd.DataFrame, weeks: list[str]) -> go.Figure:
    colors = [BLUE, ORANGE, "#1baf7a", "#6250d6", "#e87ba4"]
    dashes = ["solid", "dot", "dash", "dashdot", "longdash"]
    fig = go.Figure()
    for i, (trait, g) in enumerate(df.groupby("trait")):
        g = g.set_index("week_start").reindex(weeks)
        fig.add_trace(go.Scatter(x=weeks, y=g["score"], mode="lines+markers", name=trait.capitalize(),
                                 line=dict(color=colors[i % 5], dash=dashes[i % 5], width=2), connectgaps=True))
    fig.update_yaxes(range=[1, 10.3])
    fig.update_xaxes(type="category")
    return _base(fig, 300, "Coach rating (1–10)")


def hours_breakdown(df: pd.DataFrame) -> go.Figure:
    """df: skill, hours, verified (bool) — verified = coaches rated this skill in the window."""
    df = df.sort_values("hours")
    fig = go.Figure(go.Bar(x=df.hours, y=df.skill, orientation="h",
                           marker_color=[BLUE if v else "#b4b2a9" for v in df.verified],
                           text=[f"{h:.1f} h" + ("" if v else " · unverifiable") for h, v in zip(df.hours, df.verified)],
                           textposition="outside", hovertemplate="%{y}: %{x:.1f} h<extra></extra>"))
    fig.update_xaxes(title_text="Hours", rangemode="tozero")
    return _base(fig, max(240, 32 * len(df) + 60))
