import pandas as pd
import streamlit as st

from tracker import queries as q
from tracker.detection import STATUS_LABELS, run_detection
from tracker.ui import fmt_week, get_conn, select_player, short_week, time_controls

conn = get_conn()
view = time_controls(conn)
st.title("Roster")

players = q.players_df(conn)
if players.empty:
    st.info("No players yet. Add them on **Roster setup**, or run `python scripts/make_demo.py` to load a demo season.")
    st.page_link("views/setup.py", label="Go to Roster setup", icon=":material/settings:")
    st.stop()
if view is None:
    st.info("No weekly reports or coach sheets yet. Import reports on **Player reports** or fill in a **Weekly coach sheet**.")
    st.stop()

res = run_detection(conn, view.as_of)
rep = q.reports_df(conn)
rep_view = rep[rep.week_start.isin(view.weeks)]
missing = q.missing_reports(conn, view.weeks)

scope = fmt_week(view.weeks[0]) if view.mode == "week" else f"{short_week(view.weeks[0])} – {short_week(view.weeks[-1])}"
st.caption(f"{scope} · flags from the {len(res.window)}-week window "
           f"{short_week(res.window[0])} – {short_week(res.window[-1])}" if res.window else scope)

c = st.columns(4)
c[0].metric("Active players", len(players))
c[1].metric("Flagged", int((res.status.status == "red").sum()))
c[2].metric("To review", int((res.status.status == "yellow").sum()))
c[3].metric("Missing reports", len(missing))

hours = rep_view.groupby("player_id")["hours"].sum()
submitted = rep_view.groupby("player_id")["week_start"].nunique()
top_reason = {}
for f in res.flags:
    if not f.dismissed and f.player_id not in top_reason:
        top_reason[f.player_id] = f.message

tbl = players.merge(res.status, on="player_id", how="left")
tbl["Status"] = tbl["status"].map(STATUS_LABELS)
tbl["Hours"] = tbl["player_id"].map(hours).fillna(0).round(1)
if view.mode == "week":
    tbl["Report"] = ["Submitted" if submitted.get(pid) else "Missing" for pid in tbl.player_id]
else:
    tbl["Report"] = [f"{int(submitted.get(pid, 0))}/{len(view.weeks)} weeks" for pid in tbl.player_id]
tbl["Top reason"] = tbl["player_id"].map(top_reason).fillna("")
tbl["Unverifiable"] = tbl["unverifiable_share"]
order = {"red": 0, "yellow": 1, "gray": 2, "green": 3}
tbl = tbl.sort_values(["status", "score", "name"], key=lambda s: s.map(order) if s.name == "status" else s,
                      ascending=[True, False, True]).reset_index(drop=True)

show = tbl[["Status", "name", "number", "position", "score", "n_flags", "Top reason", "Hours", "Report", "Unverifiable"]]
row_bg = {"red": "background-color: rgba(208,59,59,0.20)", "yellow": "background-color: rgba(250,178,25,0.20)"}
styled = show.style.apply(lambda r: [row_bg.get(tbl.loc[r.name, "status"], "")] * len(r), axis=1)

st.subheader("Players")
st.caption("Click a row to open that player. Red rows have a score at or above the red threshold — a reason to "
           "look closer and talk to the player, not proof of cheating.")
event = st.dataframe(
    styled, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    column_config={
        "name": "Name", "number": st.column_config.NumberColumn("#", format="%d"), "position": "Position",
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=1, format="%.2f"),
        "n_flags": st.column_config.NumberColumn("Flags"),
        "Top reason": st.column_config.TextColumn("Top reason", width="large"),
        "Hours": st.column_config.NumberColumn("Hours", format="%.1f"),
        "Unverifiable": st.column_config.ProgressColumn(
            "Unverifiable hrs", min_value=0, max_value=1, format="percent",
            help="Share of reported hours in skills the coaches haven't rated in the window. Not a flag — "
                 "a hint to watch that skill at practice."),
    })
if event.selection.rows:
    pid = int(tbl.loc[event.selection.rows[0], "player_id"])
    select_player(pid)
    st.switch_page("views/player.py")

if len(missing):
    st.subheader("Missing reports")
    m = missing.assign(Week=missing.week_start.map(short_week))
    st.dataframe(m.groupby("name")["Week"].apply(", ".join).reset_index().rename(columns={"name": "Player", "Week": "Weeks missing"}),
                 hide_index=True, width="stretch")
