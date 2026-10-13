import numpy as np
import pandas as pd
import streamlit as st

from tracker import charts
from tracker import queries as q
from tracker.config import CFG, label
from tracker.detection import run_detection
from tracker.ui import get_conn, select_player, short_week, status_badge, time_controls

conn = get_conn()
view = time_controls(conn)
st.title("Team charts")
if view is None:
    st.info("No data yet.")
    st.stop()

players = q.players_df(conn)
res = run_detection(conn, view.as_of)
status = dict(zip(res.status.player_id, res.status.status))
rep = q.reports_df(conn)
coach = q.coach_skill_df(conn)
traits = q.coach_traits_df(conn)

METRICS = {
    "Coach competency": ("coach", "competency"),
    "Coach effort": ("coach", "effort"),
    "Self-reported hours": ("rep", "hours"),
    "Self-rated effort": ("rep", "effort"),
    "Total hours": ("total", "hours"),
    "Coach-only traits": ("traits", "score"),
}

c1, c2, c3 = st.columns([2, 2, 2])
metric = c1.selectbox("Metric", list(METRICS), key="team_metric")
src, col = METRICS[metric]
positions = c2.multiselect("Positions", list(CFG.positions), default=list(CFG.positions), key="team_pos",
                           format_func=str.capitalize)
include_zero = False
if src in ("rep",) and col == "hours":
    include_zero = c3.toggle("Include 0-hour entries", value=False, key="team_zero",
                             help="0 hours = didn't train that skill. Hidden by default so untrained players "
                                  "don't drag the box down.")

pl = players[players.position.isin(positions)]
pos_of = dict(zip(players.player_id, players.position))
name_of = dict(zip(players.player_id, players.name))

# ---- choose what each box represents
if view.mode == "week":
    week_list = view.weeks
    if src == "total":
        dim, groups = "position", [p for p in CFG.positions if p in positions]
        group_labels = [p.capitalize() for p in groups]
    elif src == "traits":
        dim, groups = "trait", list(CFG.coach_traits)
        group_labels = [label(t) for t in groups]
    else:
        chosen = c3.multiselect("Skills", CFG.skills, default=CFG.skills, key="team_skills", format_func=label) \
            if not (src == "rep" and col == "hours") else CFG.skills
        dim, groups = "skill", [s for s in CFG.skills if s in chosen]
        group_labels = [label(s) for s in groups]
else:
    week_list = view.weeks
    dim, groups, group_labels = "week_start", week_list, [short_week(w) for w in week_list]
    if src == "traits":
        focus = st.selectbox("Trait", CFG.coach_traits, key="team_trait", format_func=label)
    elif src != "total":
        focus = st.selectbox("Skill", CFG.skills, key="team_skill_ov", format_func=label)

# ---- build one row per dot
if src == "coach":
    d = coach[coach.week_start.isin(week_list)].dropna(subset=[col]).rename(columns={col: "value"})
    if view.mode == "overview":
        d = d[d.skill == focus]
    d["hover"] = d.player_id.map(pos_of).str.capitalize() + " · " + d.week_start.map(short_week) + \
        " · " + d.n_coaches.astype(str) + " coach(es)"
elif src == "rep":
    d = rep[rep.week_start.isin(week_list)].copy()
    if col == "hours" and not include_zero:
        hidden = int((d.hours == 0).sum()) if view.mode == "week" else int(((d.hours == 0) & (d.skill == focus)).sum())
        d = d[d.hours > 0]
    if col == "effort":
        d = d.dropna(subset=["effort"])
    if view.mode == "overview":
        d = d[d.skill == focus]
    d = d.rename(columns={col: "value"})
    d["hover"] = d.player_id.map(pos_of).str.capitalize() + " · " + d.week_start.map(short_week) + \
        " · " + d.file + " row " + d.source_row.fillna(0).astype(int).astype(str)
elif src == "total":
    d = q.weekly_totals(rep[rep.week_start.isin(week_list)]).rename(columns={"total_hours": "value"})
    d["position"] = d.player_id.map(pos_of)
    d["value"] = d["value"].round(1)
    d["hover"] = d.position.str.capitalize() + " · " + d.week_start.map(short_week)
else:
    d = traits[traits.week_start.isin(week_list)].rename(columns={"score": "value"})
    if view.mode == "overview":
        d = d[d.trait == focus]
    d["hover"] = d.player_id.map(pos_of).str.capitalize() + " · " + d.week_start.map(short_week) + " · coach only"

d = d[d.player_id.isin(pl.player_id)].copy()
d["group"] = d[dim]
d["name"] = d.player_id.map(name_of)
d["status"] = d.player_id.map(status).fillna("gray")

y_title = {"hours": "Hours per week"}.get(col, f"{metric} (1–10)")
y_range = None if col == "hours" else [0.5, 10.5]
fig = charts.box_with_dots(d, groups, group_labels, y_title, y_range, st.session_state.get("_player"))
event = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="team_box")

notes = []
if src == "rep" and col == "hours" and not include_zero:
    notes.append(f"{hidden} zero-hour entries hidden")
miss = q.missing_reports(conn, week_list)
miss = miss[miss.player_id.isin(pl.player_id)]
if src in ("rep", "total") and len(miss):
    notes.append(f"missing reports excluded ({len(miss)})")
if src == "total" and view.mode == "week":
    notes.append("total = all skills summed; split by position so defenders are compared with defenders")
if notes:
    st.caption(" · ".join(notes).capitalize() + ". Click a dot to open that player.")

if event and event.selection and event.selection.get("points"):
    pt = next((p for p in event.selection["points"] if p.get("customdata")), None)
    if pt:
        select_player(int(float(pt["customdata"][0])))
        gi = int(round(float(pt.get("x", 0))))
        if dim == "skill" and 0 <= gi < len(groups):
            st.session_state["_skill"] = groups[gi]

# ---- player panel
pid = st.session_state.get("_player")
with st.container(border=True):
    if pid is None or pid not in name_of:
        st.caption("Click a dot to see that player here.")
    else:
        week = view.as_of
        srow = res.status[res.status.player_id == pid]
        h1, h2 = st.columns([3, 1])
        h1.markdown(f"**{name_of[pid]}** · {pos_of[pid]} · week of {short_week(week)}")
        with h2:
            status_badge(status.get(pid, "gray"), float(srow.score.iloc[0]) if len(srow) else None)
        pflags = res.player_flags(pid)
        for f in pflags[:3]:
            st.markdown(f"- {f.message}")
        if not pflags:
            st.caption("No flags in the current window.")
        default_skill = st.session_state.get("_skill", CFG.skills[0])
        skill = st.selectbox("Skill", CFG.skills, index=CFG.skills.index(default_skill), format_func=label,
                             key=f"panel_skill_{pid}")
        st.session_state["_skill"] = skill
        cr = coach[(coach.player_id == pid) & (coach.week_start == week) & (coach.skill == skill)]
        rr = rep[(rep.player_id == pid) & (rep.week_start == week) & (rep.skill == skill)]
        submitted = not rep[(rep.player_id == pid) & (rep.week_start == week)].empty
        m = st.columns(4)
        m[0].metric("Coach competency", f"{cr.competency.iloc[0]:.1f}" if len(cr) and pd.notna(cr.competency.iloc[0]) else "—")
        m[1].metric("Coach effort", f"{cr.effort.iloc[0]:.1f}" if len(cr) and pd.notna(cr.effort.iloc[0]) else "—")
        if not submitted:
            hrs, eff = "Missing", "—"
        elif rr.hours.iloc[0] == 0:
            hrs, eff = "Not trained", "—"
        else:
            hrs, eff = f"{rr.hours.iloc[0]:.1f} h", f"{rr.effort.iloc[0]:.1f}"
        m[2].metric("Self hours", hrs)
        m[3].metric("Self effort", eff)
        if submitted and not CFG.is_core(pos_of[pid], skill) and len(rr) and rr.hours.iloc[0] > 0:
            st.caption("Off-profile skill for this position — allowed, compared against core players, never penalised.")
        tr = traits[(traits.player_id == pid) & (traits.week_start == week)]
        if len(tr):
            st.caption(":material/lock: Coach only")
            t = st.columns(len(CFG.coach_traits))
            for i, trait in enumerate(CFG.coach_traits):
                v = tr[tr.trait == trait].score
                t[i].metric(label(trait), f"{v.iloc[0]:.1f}" if len(v) else "—")
        st.page_link("views/player.py", label="Open full player page", icon=":material/arrow_forward:")

# ---- other team views
st.subheader("More team views")
tab1, tab2, tab3 = st.tabs(["Heatmap", "Hours vs. improvement", "Effort gap"])

with tab1:
    if view.mode == "week":
        hm = coach[coach.week_start == view.as_of].pivot_table(index="player_id", columns="skill", values="competency")
        title = "Competency"
        diverging = False
    else:
        cw = coach[coach.week_start.isin(view.weeks)].dropna(subset=["competency"]).sort_values("week_start")
        first = cw.groupby(["player_id", "skill"]).competency.first()
        last = cw.groupby(["player_id", "skill"]).competency.last()
        hm = (last - first).unstack()
        title, diverging = "Change", True
    hm = hm.reindex(columns=[s for s in CFG.skills if s in hm.columns])
    hm = hm[hm.index.isin(pl.player_id)]
    hm.index = [name_of[i] for i in hm.index]
    hm.columns = [label(s) for s in hm.columns]
    if hm.empty:
        st.caption("No coach ratings in this range.")
    else:
        st.caption("Competency this week." if view.mode == "week" else
                   "Change in competency from first to last rated week in the range (blue = improved).")
        st.plotly_chart(charts.heatmap(hm.sort_index(), title, diverging), width="stretch")

with tab2:
    sk = st.selectbox("Skill", CFG.skills, key="hvg_skill", format_func=label)
    s = res.skill_stats
    s = s[(s.skill == sk) & s.hours_per_week.notna() & s.slope.notna() & s.player_id.isin(pl.player_id)].copy()
    s["gain"] = (s["end"] - s["start"]).round(1)
    s["name"] = s.player_id.map(name_of)
    s["status"] = s.player_id.map(status).fillna("gray")
    s["position"] = s.position.str.capitalize()
    st.caption(f"Window {short_week(res.window[0])} – {short_week(res.window[-1])}. Each dot is a player; "
               "the bottom-right corner (lots of hours, little improvement) is what the hours-vs-improvement rule looks for."
               if res.window else "")
    if s.empty:
        st.caption("Not enough data for this skill yet.")
    else:
        ev = st.plotly_chart(charts.hours_gain_scatter(s, label(sk)), width="stretch",
                             on_select="rerun", selection_mode="points", key="hvg")
        if ev and ev.selection and ev.selection.get("points"):
            pt = next((p for p in ev.selection["points"] if p.get("customdata")), None)
            if pt:
                select_player(int(float(pt["customdata"][0])))
                st.session_state["_skill"] = sk

with tab3:
    w = res.window
    r = rep[rep.week_start.isin(w) & (rep.hours > 0)].dropna(subset=["effort"])
    c = coach[coach.week_start.isin(w)].dropna(subset=["effort"])
    m = r.merge(c[["player_id", "week_start", "skill", "effort"]], on=["player_id", "week_start", "skill"],
                suffixes=("_self", "_coach"))
    g = m.groupby("player_id").agg(self_effort=("effort_self", "mean"), coach_effort=("effort_coach", "mean")).reset_index()
    g = g[g.player_id.isin(pl.player_id)]
    if g.empty:
        st.caption("No matching self/coach effort ratings in the window.")
    else:
        g["gap"] = (g.self_effort - g.coach_effort).round(1)
        g["name"] = g.player_id.map(name_of)
        g["status"] = g.player_id.map(status).fillna("gray")
        st.caption("Average effort per player over the detection window, only where both the player and a coach rated "
                   f"the same skill and week. Most players rate themselves a bit higher — the team's median gap is "
                   f"{g.gap.median():+.1f}.")
        st.plotly_chart(charts.effort_dumbbell(g), width="stretch")
