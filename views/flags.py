import streamlit as st

from tracker import queries as q
from tracker.config import CFG
from tracker.detection import RULE_LABELS, dismiss, dismissals_df, restore, run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

conn = get_conn()
view = time_controls(conn)
st.title("Flags")
if view is None:
    st.info("No data yet.")
    st.stop()

res = run_detection(conn, view.as_of)
players = q.players_df(conn)
name_of = dict(zip(players.player_id, players.name))
th = CFG.detection["thresholds"]
if res.window:
    st.caption(f"Window {short_week(res.window[0])} – {short_week(res.window[-1])}. Score = sum of rule weight × severity "
               f"(capped at 1). Review at {th['yellow']:.2f}, flagged at {th['red']:.2f}. Tune weights and thresholds "
               "in config.yaml.")

active = [f for f in res.flags if not f.dismissed]
if not active:
    st.success("No active flags in this window.", icon=":material/check:")

by_player: dict[int, list] = {}
for f in active:
    by_player.setdefault(f.player_id, []).append(f)
order = sorted(by_player, key=lambda pid: -float(res.status.set_index("player_id").score.get(pid, 0)))

for pid in order:
    srow = res.status[res.status.player_id == pid].iloc[0]
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.markdown(f"### {name_of[pid]}")
        with b:
            status_badge(srow.status, srow.score)
        for f in by_player[pid]:
            st.markdown(f"**{RULE_LABELS[f.rule]}** · severity {f.severity:.2f} × weight {f.weight:.1f} = "
                        f"{f.contribution:.2f}  \n{f.message}")
            e1, e2 = st.columns([1, 3])
            with e1.popover("Evidence"):
                st.json(f.evidence)
            with e2.popover("Dismiss", icon=":material/do_not_disturb_on:"):
                with st.form(f"dismiss_{f.fingerprint}"):
                    note = st.text_input("Why? (kept in the log)", placeholder="Talked to him — logs wall ball as passing")
                    if st.form_submit_button("Dismiss flag"):
                        if not note.strip():
                            st.error("Add a short note so you remember why.")
                        else:
                            dismiss(conn, f, note)
                            st.rerun()
        if st.button("Open player", key=f"open_{pid}", icon=":material/person:"):
            select_player(pid)
            st.switch_page("views/player.py")

st.subheader("Dismissed")
st.caption("A dismissal applies to that rule, player and window. If the pattern continues into a new window, it can fire again.")
dis = dismissals_df(conn)
if dis.empty:
    st.caption("None.")
for r in dis.itertuples():
    c1, c2 = st.columns([5, 1])
    c1.markdown(f"**{r.name}** · {RULE_LABELS.get(r.rule, r.rule)} · {r.dismissed_at[:10]}  \n_{r.note}_")
    if c2.button("Restore", key=f"restore_{r.fingerprint}"):
        restore(conn, r.fingerprint)
        st.rerun()
