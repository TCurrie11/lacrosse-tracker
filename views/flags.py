"""
Flags page.

Every active flag in the detection window, grouped by player and sorted by score, with the
evidence behind it. Flags can be dismissed with a note and restored later.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header, neutral placeholder text.
"""
import streamlit as st

from tracker import queries
from tracker.config import CFG
from tracker.detection import RULE_LABELS, dismiss, dismissals_df, restore, run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

conn = get_conn()
view = time_controls(conn)
st.title("Flags")
if view is None:
    st.info("No data yet.")
    st.stop()

detection = run_detection(conn, view.as_of)
players = queries.players_df(conn)
name_of = dict(zip(players.player_id, players.name))
thresholds = CFG.detection["thresholds"]
if detection.window:
    st.caption(f"Window {short_week(detection.window[0])} – {short_week(detection.window[-1])}. "
               "Score = sum of rule weight × severity "
               f"(capped at 1). Review at {thresholds['yellow']:.2f}, flagged at {thresholds['red']:.2f}. "
               "Tune weights and thresholds in config.yaml.")

active_flags = [flag for flag in detection.flags if not flag.dismissed]
if not active_flags:
    st.success("No active flags in this window.", icon=":material/check:")

flags_by_player: dict[int, list] = {}
for flag in active_flags:
    flags_by_player.setdefault(flag.player_id, []).append(flag)
score_by_player = detection.status.set_index("player_id").score
players_by_score = sorted(flags_by_player, key=lambda player_id: -float(score_by_player.get(player_id, 0)))

for player_id in players_by_score:
    player_status = detection.status[detection.status.player_id == player_id].iloc[0]
    with st.container(border=True):
        name_column, badge_column = st.columns([3, 1])
        name_column.markdown(f"### {name_of[player_id]}")
        with badge_column:
            status_badge(player_status.status, player_status.score)
        for flag in flags_by_player[player_id]:
            st.markdown(f"**{RULE_LABELS[flag.rule]}** · severity {flag.severity:.2f} × weight {flag.weight:.1f} = "
                        f"{flag.contribution:.2f}  \n{flag.message}")
            evidence_column, dismiss_column = st.columns([1, 3])
            with evidence_column.popover("Evidence"):
                st.json(flag.evidence)
            with dismiss_column.popover("Dismiss", icon=":material/do_not_disturb_on:"):
                with st.form(f"dismiss_{flag.fingerprint}"):
                    note = st.text_input("Why? (kept in the log)",
                                         placeholder="Talked to the player — logs wall ball as passing")
                    if st.form_submit_button("Dismiss flag"):
                        if not note.strip():
                            st.error("Add a short note so you remember why.")
                        else:
                            dismiss(conn, flag, note)
                            st.rerun()
        if st.button("Open player", key=f"open_{player_id}", icon=":material/person:"):
            select_player(player_id)
            st.switch_page("views/player.py")

st.subheader("Dismissed")
st.caption("A dismissal applies to that rule, player and window. If the pattern continues into a new window, "
           "it can fire again.")
dismissals = dismissals_df(conn)
if dismissals.empty:
    st.caption("None.")
for dismissal in dismissals.itertuples():
    details_column, restore_column = st.columns([5, 1])
    details_column.markdown(f"**{dismissal.name}** · {RULE_LABELS.get(dismissal.rule, dismissal.rule)} · "
                            f"{dismissal.dismissed_at[:10]}  \n_{dismissal.note}_")
    if restore_column.button("Restore", key=f"restore_{dismissal.fingerprint}"):
        restore(conn, dismissal.fingerprint)
        st.rerun()
