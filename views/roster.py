"""
Roster page (home).

Every active player with status, score, top reason, hours, report status and the share of hours
the coaches can't verify. Flagged players sort to the top; clicking a row opens that player.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import streamlit as st

from tracker import queries
from tracker.detection import STATUS_LABELS, run_detection
from tracker.ui import fmt_week, get_conn, select_player, short_week, time_controls

STATUS_SORT_ORDER = {"red": 0, "yellow": 1, "gray": 2, "green": 3}
ROW_BACKGROUNDS = {"red": "background-color: rgba(208,59,59,0.20)",
                   "yellow": "background-color: rgba(250,178,25,0.20)"}

conn = get_conn()
view = time_controls(conn)
st.title("Roster")

players = queries.players_df(conn)
if players.empty:
    st.info("No players yet. Add them on **Roster setup**, or run `python scripts/make_demo.py` to load a demo season.")
    st.page_link("views/setup.py", label="Go to Roster setup", icon=":material/settings:")
    st.stop()
if view is None:
    st.info("No weekly reports or coach sheets yet. Import reports on **Player reports** or fill in a "
            "**Weekly coach sheet**.")
    st.stop()

detection = run_detection(conn, view.as_of)
reports = queries.reports_df(conn)
reports_in_view = reports[reports.week_start.isin(view.weeks)]
missing = queries.missing_reports(conn, view.weeks)

if view.mode == "week":
    scope = fmt_week(view.weeks[0])
else:
    scope = f"{short_week(view.weeks[0])} – {short_week(view.weeks[-1])}"
st.caption(f"{scope} · flags from the {len(detection.window)}-week window "
           f"{short_week(detection.window[0])} – {short_week(detection.window[-1])}" if detection.window else scope)

active_column, flagged_column, review_column, missing_column = st.columns(4)
active_column.metric("Active players", len(players))
flagged_column.metric("Flagged", int((detection.status.status == "red").sum()))
review_column.metric("To review", int((detection.status.status == "yellow").sum()))
missing_column.metric("Missing reports", len(missing))

# ---- roster table
hours_by_player = reports_in_view.groupby("player_id")["hours"].sum()
weeks_submitted_by_player = reports_in_view.groupby("player_id")["week_start"].nunique()
top_reason = {}  # flags are sorted by contribution, so the first active one per player is the top reason
for flag in detection.flags:
    if not flag.dismissed and flag.player_id not in top_reason:
        top_reason[flag.player_id] = flag.message

table = players.merge(detection.status, on="player_id", how="left")
table["Status"] = table["status"].map(STATUS_LABELS)
table["Hours"] = table["player_id"].map(hours_by_player).fillna(0).round(1)
if view.mode == "week":
    table["Report"] = ["Submitted" if weeks_submitted_by_player.get(player_id) else "Missing"
                       for player_id in table.player_id]
else:
    table["Report"] = [f"{int(weeks_submitted_by_player.get(player_id, 0))}/{len(view.weeks)} weeks"
                       for player_id in table.player_id]
table["Top reason"] = table["player_id"].map(top_reason).fillna("")
table["Unverifiable"] = table["unverifiable_share"]
table = table.sort_values(["status", "score", "name"],
                          key=lambda column: column.map(STATUS_SORT_ORDER) if column.name == "status" else column,
                          ascending=[True, False, True]).reset_index(drop=True)

shown_columns = table[["Status", "name", "number", "position", "score", "n_flags", "Top reason", "Hours", "Report",
                       "Unverifiable"]]
styled_table = shown_columns.style.apply(
    lambda row: [ROW_BACKGROUNDS.get(table.loc[row.name, "status"], "")] * len(row), axis=1)

st.subheader("Players")
st.caption("Click a row to open that player. Red rows have a score at or above the red threshold — a reason to "
           "look closer and talk to the player, not proof of cheating.")
selection_event = st.dataframe(
    styled_table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
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
if selection_event.selection.rows:
    selected_player_id = int(table.loc[selection_event.selection.rows[0], "player_id"])
    select_player(selected_player_id)
    st.switch_page("views/player.py")

if len(missing):
    st.subheader("Missing reports")
    missing_with_labels = missing.assign(Week=missing.week_start.map(short_week))
    missing_by_player = (missing_with_labels.groupby("name")["Week"].apply(", ".join).reset_index()
                         .rename(columns={"name": "Player", "Week": "Weeks missing"}))
    st.dataframe(missing_by_player, hide_index=True, width="stretch")
