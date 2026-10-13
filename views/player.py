import pandas as pd
import streamlit as st

from tracker import charts
from tracker import queries as q
from tracker.config import CFG, label
from tracker.detection import RULE_LABELS, run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

conn = get_conn()
view = time_controls(conn)
players = q.players_df(conn)
if players.empty or view is None:
    st.title("Player")
    st.info("No data yet.")
    st.stop()

ids = players.player_id.tolist()
name_of = dict(zip(players.player_id, players.name))
cur = st.session_state.get("_player", ids[0])
if cur not in ids:
    cur = ids[0]
pid = st.selectbox("Player", ids, index=ids.index(cur), format_func=lambda i: name_of[i], key=f"player_pick_{cur}")
select_player(pid)
p = players[players.player_id == pid].iloc[0]

res = run_detection(conn, view.as_of)
srow = res.status[res.status.player_id == pid].iloc[0]

st.title(p["name"])
h = st.columns([2, 1, 1, 1])
with h[0]:
    status_badge(srow.status, srow.score)
h[1].metric("Position", p.position.capitalize())
h[2].metric("Weeks submitted (window)", f"{srow.weeks_submitted}/{len(res.window)}")
h[3].metric("Unverifiable hours", f"{srow.unverifiable_share:.0%}",
            help="Share of reported hours in skills no coach rated in the window.")

# trend range: overview -> chosen range; week mode -> history up to the chosen week
weeks = view.weeks if view.mode == "overview" else [w for w in view.all_weeks if w <= view.as_of][-12:]
st.caption(f"Charts: {short_week(weeks[0])} – {short_week(weeks[-1])}. "
           f"Flags: window {short_week(res.window[0])} – {short_week(res.window[-1])}.")

flags = res.player_flags(pid, include_dismissed=True)
with st.expander(f"Flags ({sum(not f.dismissed for f in flags)} active)", expanded=srow.status in ("red", "yellow")):
    if not flags:
        st.caption("No rules fired in this window.")
    for f in flags:
        tag = " · dismissed" if f.dismissed else f" · adds {f.contribution:.2f} to score"
        st.markdown(f"**{RULE_LABELS[f.rule]}**{tag}  \n{f.message}")
        with st.popover("Evidence"):
            st.json(f.evidence)
    st.page_link("views/flags.py", label="Review or dismiss flags", icon=":material/flag:")

rep = q.reports_df(conn)
coach = q.coach_skill_df(conn)
traits = q.coach_traits_df(conn)
prep = rep[rep.player_id == pid]
_miss = q.missing_reports(conn, weeks)
missing = set(_miss[_miss.player_id == pid].week_start)

skill = st.selectbox("Skill", CFG.skills, index=CFG.skills.index(st.session_state.get("_skill", CFG.skills[0])),
                     format_func=label, key="player_skill")
st.session_state["_skill"] = skill
core = CFG.is_core(p.position, skill)
if not core:
    st.caption(f"{label(skill)} is off-profile for a {p.position} — allowed, compared against core players, never penalised.")

c1, c2 = st.columns(2)
with c1:
    st.markdown(f"**{label(skill)} competency vs. team**")
    team_pos = players[[CFG.is_core(pp, skill) for pp in players.position]].player_id
    team = coach[(coach.skill == skill) & coach.player_id.isin(set(team_pos) | {pid})].rename(columns={"competency": "value"})
    st.plotly_chart(charts.trend_band(team.dropna(subset=["value"]), pid, weeks, "Competency", p["name"], (1, 10)),
                    width="stretch")
    st.caption("Band = middle 50% of players with this as a core skill.")
with c2:
    st.markdown("**Hours vs. competency**")
    hrs = prep[prep.skill == skill][["week_start", "hours"]]
    comp = coach[(coach.player_id == pid) & (coach.skill == skill)][["week_start", "competency"]]
    st.plotly_chart(charts.hours_and_competency(hrs, comp, weeks, label(skill), missing), width="stretch")

c3, c4 = st.columns(2)
with c3:
    st.markdown(f"**{label(skill)} effort: self vs. coach**")
    se = prep[(prep.skill == skill) & (prep.hours > 0)][["week_start", "effort"]]
    ce = coach[(coach.player_id == pid) & (coach.skill == skill)][["week_start", "effort"]]
    st.plotly_chart(charts.effort_lines(se, ce, weeks), width="stretch")
with c4:
    st.markdown(":material/lock: **Coach-only traits**")
    tr = traits[(traits.player_id == pid) & traits.week_start.isin(weeks)]
    if tr.empty:
        st.caption("No trait ratings yet.")
    else:
        st.plotly_chart(charts.traits_lines(tr, weeks), width="stretch")

st.subheader(f"Hours by skill — week of {short_week(view.as_of)}")
wk = prep[prep.week_start == view.as_of]
if wk.empty:
    st.caption("No report for this week." if view.as_of in q.weeks_available(conn) else "")
else:
    rated = set(coach[(coach.player_id == pid) & coach.week_start.isin(res.window)].dropna(subset=["competency"]).skill)
    bd = wk[wk.hours > 0].assign(verified=lambda d: d.skill.isin(rated), skill=lambda d: d.skill.map(label))
    st.markdown(f"Total **{wk.hours.sum():.1f} h** · not trained: "
                + (", ".join(label(s) for s in wk[wk.hours == 0].skill) or "none"))
    if len(bd):
        st.plotly_chart(charts.hours_breakdown(bd[["skill", "hours", "verified"]]), width="stretch")
        st.caption("Gray = no coach rating for that skill in the window, so those hours can't be cross-checked.")

st.subheader("Source data")
st.caption("Every number traces back to an imported file and row, or to a coach's sheet.")
t1, t2, t3 = st.tabs(["Player reports", "Coach ratings", "Submission history"])
with t1:
    show = prep[prep.week_start.isin(weeks)].sort_values(["week_start", "skill"], ascending=[False, True])
    st.dataframe(show[["week_start", "skill", "hours", "effort", "file", "source_row", "filled_missing", "received_at"]],
                 hide_index=True, width="stretch",
                 column_config={"week_start": "Week", "source_row": "CSV row",
                                "filled_missing": st.column_config.CheckboxColumn("Row missing → 0"),
                                "received_at": "Received"})
with t2:
    raw = pd.read_sql("""SELECT week_start, skill, coach, competency/10.0 AS competency, effort/10.0 AS effort,
                                created_at, superseded_by IS NOT NULL AS replaced
                         FROM coach_ratings WHERE player_id=? ORDER BY week_start DESC, skill, coach""",
                      conn, params=(int(pid),))
    st.dataframe(raw[raw.week_start.isin(weeks)], hide_index=True, width="stretch",
                 column_config={"week_start": "Week", "replaced": st.column_config.CheckboxColumn("Replaced")})
with t3:
    subs = q.submissions_df(conn)
    subs = subs[subs.player_id == pid].sort_values("received_at", ascending=False)
    subs["status"] = subs.superseded_by.map(lambda x: "Replaced" if pd.notna(x) else "Current")
    st.dataframe(subs[["week_start", "file", "received_at", "status"]], hide_index=True, width="stretch")
