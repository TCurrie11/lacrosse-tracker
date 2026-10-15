"""
Streamlit helpers shared by every page: DB connection, the week/overview control, status badges.

Session-state keys starting with "_" hold choices that must survive page switches (Streamlit
clears widget keys when a page stops rendering the widget).

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from . import queries
from .db import connect
from .detection import STATUS_LABELS

BADGE_COLORS = {"green": "green", "yellow": "orange", "red": "red", "gray": "gray"}
BADGE_ICONS = {"green": ":material/check_circle:", "yellow": ":material/visibility:",
               "red": ":material/flag:", "gray": ":material/hourglass_empty:"}


@st.cache_resource
def get_conn():
    return connect(check_same_thread=False)


def fmt_week(week: str) -> str:
    """'2026-10-05' -> 'Week of Oct 5, 2026'"""
    day = date.fromisoformat(week)
    return f"Week of {day.strftime('%b')} {day.day}, {day.year}"


def short_week(week: str) -> str:
    """'2026-10-05' -> 'Oct 5'"""
    day = date.fromisoformat(week)
    return f"{day.strftime('%b')} {day.day}"


@dataclass
class View:
    mode: str          # "week" | "overview"
    as_of: str         # detection runs on the window ending here
    weeks: list[str]   # weeks shown in charts
    all_weeks: list[str]


def _persist(widget_key: str, saved_key: str) -> None:
    st.session_state[saved_key] = st.session_state[widget_key]


def _step_week(weeks: list[str], step: int) -> None:
    """Move the selected week `step` weeks forward (or back), staying inside `weeks`."""
    current_week = st.session_state.get("_week", weeks[-1])
    current_index = weeks.index(current_week) if current_week in weeks else len(weeks) - 1
    new_week = weeks[max(0, min(len(weeks) - 1, current_index + step))]
    st.session_state["_week"] = new_week
    st.session_state["w_week"] = new_week


def time_controls(conn) -> View | None:
    """Sidebar control shared by all pages. Choices survive page switches."""
    weeks = queries.weeks_available(conn)
    with st.sidebar:
        st.markdown("#### Time range")
        if not weeks:
            st.caption("No reports or coach sheets yet.")
            return None
        modes = ["Week", "Overview"]
        mode = st.segmented_control("View", modes, default=st.session_state.get("_mode", "Week"),
                                    key="w_mode", on_change=_persist, args=("w_mode", "_mode"),
                                    label_visibility="collapsed") or "Week"

        if mode == "Week":
            saved_week = st.session_state.get("_week")
            if saved_week not in weeks:
                saved_week = weeks[-1]
                st.session_state["_week"] = saved_week
            newest_first = weeks[::-1]
            if st.session_state.get("w_week") not in newest_first:
                st.session_state["w_week"] = saved_week
            week = st.selectbox("Week", newest_first, format_func=fmt_week, key="w_week",
                                on_change=_persist, args=("w_week", "_week"))
            previous_column, next_column = st.columns(2)
            previous_column.button("Previous", icon=":material/chevron_left:", width="stretch",
                                   on_click=_step_week, args=(weeks, -1), disabled=week == weeks[0])
            next_column.button("Next", icon=":material/chevron_right:", width="stretch",
                               on_click=_step_week, args=(weeks, 1), disabled=week == weeks[-1])
            st.caption("Flags use the rolling window ending this week.")
            return View("week", week, [week], weeks)

        saved_range = st.session_state.get("_range", (weeks[0], weeks[-1]))
        if saved_range[0] not in weeks or saved_range[1] not in weeks:
            saved_range = (weeks[0], weeks[-1])
        widget_range = st.session_state.get("w_range")
        if widget_range is None or any(week not in weeks for week in widget_range):
            st.session_state["w_range"] = saved_range
        first_week, last_week = st.select_slider("Weeks", options=weeks, format_func=short_week, key="w_range",
                                                 on_change=_persist, args=("w_range", "_range"))
        weeks_in_view = [week for week in weeks if first_week <= week <= last_week]
        st.caption("Flags use the rolling window ending at the last week shown.")
        return View("overview", last_week, weeks_in_view, weeks)


def status_badge(status: str, score: float | None = None) -> None:
    text = STATUS_LABELS[status] + (f" · score {score:.2f}" if score is not None else "")
    st.badge(text, color=BADGE_COLORS[status], icon=BADGE_ICONS[status])


def select_player(player_id: int) -> None:
    """Remember the player to show on the Player page and highlight in charts."""
    st.session_state["_player"] = int(player_id)
