"""Streamlit helpers shared by every page: DB connection, the week/overview control, status badges."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from . import queries as q
from .db import connect
from .detection import STATUS_LABELS

BADGE_COLORS = {"green": "green", "yellow": "orange", "red": "red", "gray": "gray"}
BADGE_ICONS = {"green": ":material/check_circle:", "yellow": ":material/visibility:",
               "red": ":material/flag:", "gray": ":material/hourglass_empty:"}


@st.cache_resource
def get_conn():
    return connect(check_same_thread=False)


def fmt_week(w: str) -> str:
    d = date.fromisoformat(w)
    return f"Week of {d.strftime('%b')} {d.day}, {d.year}"


def short_week(w: str) -> str:
    d = date.fromisoformat(w)
    return f"{d.strftime('%b')} {d.day}"


@dataclass
class View:
    mode: str          # "week" | "overview"
    as_of: str         # detection runs on the window ending here
    weeks: list[str]   # weeks shown in charts
    all_weeks: list[str]


def _persist(src: str, dst: str) -> None:
    st.session_state[dst] = st.session_state[src]


def _step(weeks: list[str], delta: int) -> None:
    cur = st.session_state.get("_week", weeks[-1])
    i = weeks.index(cur) if cur in weeks else len(weeks) - 1
    new = weeks[max(0, min(len(weeks) - 1, i + delta))]
    st.session_state["_week"] = new
    st.session_state["w_week"] = new


def time_controls(conn) -> View | None:
    """Sidebar control shared by all pages. Choices survive page switches."""
    weeks = q.weeks_available(conn)
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
            saved = st.session_state.get("_week")
            if saved not in weeks:
                saved = weeks[-1]
                st.session_state["_week"] = saved
            options = weeks[::-1]
            if st.session_state.get("w_week") not in options:
                st.session_state["w_week"] = saved
            week = st.selectbox("Week", options, format_func=fmt_week, key="w_week",
                                on_change=_persist, args=("w_week", "_week"))
            c1, c2 = st.columns(2)
            c1.button("Previous", icon=":material/chevron_left:", width="stretch",
                      on_click=_step, args=(weeks, -1), disabled=week == weeks[0])
            c2.button("Next", icon=":material/chevron_right:", width="stretch",
                      on_click=_step, args=(weeks, 1), disabled=week == weeks[-1])
            st.caption("Flags use the rolling window ending this week.")
            return View("week", week, [week], weeks)
        saved = st.session_state.get("_range", (weeks[0], weeks[-1]))
        if saved[0] not in weeks or saved[1] not in weeks:
            saved = (weeks[0], weeks[-1])
        if st.session_state.get("w_range") is None or any(w not in weeks for w in st.session_state["w_range"]):
            st.session_state["w_range"] = saved
        rng = st.select_slider("Weeks", options=weeks, format_func=short_week, key="w_range",
                               on_change=_persist, args=("w_range", "_range"))
        in_view = [w for w in weeks if rng[0] <= w <= rng[1]]
        st.caption("Flags use the rolling window ending at the last week shown.")
        return View("overview", rng[1], in_view, weeks)


def status_badge(status: str, score: float | None = None) -> None:
    text = STATUS_LABELS[status] + (f" · score {score:.2f}" if score is not None else "")
    st.badge(text, color=BADGE_COLORS[status], icon=BADGE_ICONS[status])


def select_player(player_id: int) -> None:
    st.session_state["_player"] = int(player_id)
