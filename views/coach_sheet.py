from datetime import date, timedelta

import pandas as pd
import streamlit as st

from tracker import queries as q
from tracker.coach import coaches, prefill, save_week_sheet
from tracker.config import CFG, label
from tracker.ui import fmt_week, get_conn

conn = get_conn()
st.title("Weekly coach sheet")
st.caption("Fill this in once at the end of the week. Each coach saves their own sheet; the app averages "
           "across coaches. Leave a cell blank if you didn't see that skill — blank means not observed, not low.")

players = q.players_df(conn)
if players.empty:
    st.info("Add players on Roster setup first.")
    st.stop()

this_monday = q.monday(date.today())
week_opts = sorted({(this_monday - timedelta(weeks=i)).isoformat() for i in range(0, 12)} |
                   set(q.weeks_available(conn)), reverse=True)
c1, c2 = st.columns(2)
week = c1.selectbox("Week", week_opts, format_func=fmt_week, key="cs_week",
                    index=week_opts.index(st.session_state.get("_cs_week", this_monday.isoformat()))
                    if st.session_state.get("_cs_week", this_monday.isoformat()) in week_opts else 0)
st.session_state["_cs_week"] = week
known = coaches(conn)
coach = c2.text_input("Coach", value=st.session_state.get("_coach", known[0] if known else ""),
                      placeholder="Coach Smith", key="cs_coach")
st.session_state["_coach"] = coach
if known:
    c2.caption("Coaches so far: " + ", ".join(known))
if not coach.strip():
    st.warning("Enter your name so your ratings are kept separate from other coaches'.")
    st.stop()

skills = st.multiselect("Skills worked on this week", CFG.skills, default=CFG.skills, format_func=label,
                        key="cs_skills", help="Only these skills appear in the sheet. Others stay unrated this week.")
ratings, trait_vals, note = prefill(conn, week, coach.strip())
st.info(note, icon=":material/info:")

# ---- skills sheet: one row per player x skill (long format keeps 16+ columns off the screen)
grid = pd.DataFrame([(p.player_id, p.name, p.position, s) for p in players.itertuples() for s in skills],
                    columns=["player_id", "Player", "Position", "skill"])
grid = grid.merge(ratings, on=["player_id", "skill"], how="left")
grid["Skill"] = grid.skill.map(label)
grid["Core"] = [CFG.is_core(pos, s) for pos, s in zip(grid.Position, grid.skill)]
only_core = st.toggle("Only show each player's core skills", value=True, key="cs_core")
view_grid = grid[grid.Core] if only_core else grid

score_col = dict(min_value=CFG.scale_min, max_value=CFG.scale_max, step=0.1, format="%.1f")
with st.form("coach_sheet"):
    st.markdown("**Skills**")
    edited = st.data_editor(
        view_grid[["player_id", "skill", "Player", "Position", "Skill", "competency", "effort"]],
        hide_index=True, width="stretch", key=f"cs_grid_{week}_{coach}",
        disabled=["Player", "Position", "Skill"],
        column_order=["Player", "Position", "Skill", "competency", "effort"],
        column_config={"competency": st.column_config.NumberColumn("Competency", **score_col),
                       "effort": st.column_config.NumberColumn("Effort", **score_col)},
        height=min(38 * len(view_grid) + 40, 560))

    st.markdown(":material/lock: **Coach-only traits** (never shown to players, not used in cheat detection)")
    tgrid = players[["player_id", "name", "position"]].rename(columns={"name": "Player", "position": "Position"})
    if len(trait_vals):
        tgrid = tgrid.merge(trait_vals.pivot(index="player_id", columns="trait", values="score").reset_index(),
                            on="player_id", how="left")
    for t in CFG.coach_traits:
        if t not in tgrid:
            tgrid[t] = None
    tedited = st.data_editor(
        tgrid[["player_id", "Player", "Position"] + CFG.coach_traits], hide_index=True, width="stretch",
        key=f"cs_traits_{week}_{coach}", disabled=["Player", "Position"],
        column_order=["Player", "Position"] + CFG.coach_traits,
        column_config={t: st.column_config.NumberColumn(label(t), **score_col) for t in CFG.coach_traits})
    submitted = st.form_submit_button("Save sheet", type="primary", icon=":material/save:")

if submitted:
    skill_rows = [{"player_id": int(r.player_id), "skill": r.skill, "competency": r.competency, "effort": r.effort}
                  for r in edited.itertuples()]
    trait_rows = [{"player_id": int(r["player_id"]), "trait": t, "score": r[t]}
                  for _, r in tedited.iterrows() for t in CFG.coach_traits]
    try:
        n = save_week_sheet(conn, week, coach, skill_rows, trait_rows)
        st.success(f"Saved {fmt_week(week).lower()}: {n['ratings']} skill ratings and {n['traits']} trait ratings "
                   "changed. Earlier values are kept in history.", icon=":material/check:")
    except ValueError as e:
        st.error(str(e))
