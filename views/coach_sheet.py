"""
Weekly coach sheet page.

Each coach rates competency and effort per player and skill, plus the coach-only traits, once a
week. The sheet pre-fills from that coach's previous week so only changes need typing.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from tracker import queries
from tracker.coach import coaches, prefill, save_week_sheet
from tracker.config import CFG, display_label
from tracker.ui import fmt_week, get_conn

conn = get_conn()
st.title("Weekly coach sheet")
st.caption("Fill this in once at the end of the week. Each coach saves their own sheet; the app averages "
           "across coaches. Leave a cell blank if you didn't see that skill — blank means not observed, not low.")

players = queries.players_df(conn)
if players.empty:
    st.info("Add players on Roster setup first.")
    st.stop()

# ---- week and coach
this_monday = queries.monday(date.today())
recent_mondays = {(this_monday - timedelta(weeks=weeks_back)).isoformat() for weeks_back in range(0, 12)}
week_options = sorted(recent_mondays | set(queries.weeks_available(conn)), reverse=True)
saved_week = st.session_state.get("_cs_week", this_monday.isoformat())
week_column, coach_column = st.columns(2)
week = week_column.selectbox("Week", week_options, format_func=fmt_week, key="cs_week",
                             index=week_options.index(saved_week) if saved_week in week_options else 0)
st.session_state["_cs_week"] = week

known_coaches = coaches(conn)
coach = coach_column.text_input("Coach", value=st.session_state.get("_coach", known_coaches[0] if known_coaches else ""),
                                placeholder="Coach Smith", key="cs_coach")
st.session_state["_coach"] = coach
if known_coaches:
    coach_column.caption("Coaches so far: " + ", ".join(known_coaches))
if not coach.strip():
    st.warning("Enter your name so your ratings are kept separate from other coaches'.")
    st.stop()

skills = st.multiselect("Skills worked on this week", CFG.skills, default=CFG.skills, format_func=display_label,
                        key="cs_skills", help="Only these skills appear in the sheet. Others stay unrated this week.")
prefilled_ratings, prefilled_traits, prefill_note = prefill(conn, week, coach.strip())
st.info(prefill_note, icon=":material/info:")

# ---- skills sheet: one row per player x skill (long format keeps 16+ columns off the screen)
skill_grid = pd.DataFrame([(player.player_id, player.name, player.position, skill)
                           for player in players.itertuples() for skill in skills],
                          columns=["player_id", "Player", "Position", "skill"])
skill_grid = skill_grid.merge(prefilled_ratings, on=["player_id", "skill"], how="left")
skill_grid["Skill"] = skill_grid.skill.map(display_label)
skill_grid["Core"] = [CFG.is_core(position, skill) for position, skill in zip(skill_grid.Position, skill_grid.skill)]
only_core_skills = st.toggle("Only show each player's core skills", value=True, key="cs_core")
visible_skill_grid = skill_grid[skill_grid.Core] if only_core_skills else skill_grid

score_column_settings = dict(min_value=CFG.scale_min, max_value=CFG.scale_max, step=0.1, format="%.1f")
with st.form("coach_sheet"):
    st.markdown("**Skills**")
    edited_skills = st.data_editor(
        visible_skill_grid[["player_id", "skill", "Player", "Position", "Skill", "competency", "effort"]],
        hide_index=True, width="stretch", key=f"cs_grid_{week}_{coach}",
        disabled=["Player", "Position", "Skill"],
        column_order=["Player", "Position", "Skill", "competency", "effort"],
        column_config={"competency": st.column_config.NumberColumn("Competency", **score_column_settings),
                       "effort": st.column_config.NumberColumn("Effort", **score_column_settings)},
        height=min(38 * len(visible_skill_grid) + 40, 560))

    # ---- coach-only traits: one row per player, one column per trait
    st.markdown(":material/lock: **Coach-only traits** (never shown to players, not used in cheat detection)")
    trait_grid = players[["player_id", "name", "position"]].rename(columns={"name": "Player", "position": "Position"})
    if len(prefilled_traits):
        traits_wide = prefilled_traits.pivot(index="player_id", columns="trait", values="score").reset_index()
        trait_grid = trait_grid.merge(traits_wide, on="player_id", how="left")
    for trait in CFG.coach_traits:
        if trait not in trait_grid:
            trait_grid[trait] = None
    edited_traits = st.data_editor(
        trait_grid[["player_id", "Player", "Position"] + CFG.coach_traits], hide_index=True, width="stretch",
        key=f"cs_traits_{week}_{coach}", disabled=["Player", "Position"],
        column_order=["Player", "Position"] + CFG.coach_traits,
        column_config={trait: st.column_config.NumberColumn(display_label(trait), **score_column_settings)
                       for trait in CFG.coach_traits})
    submitted = st.form_submit_button("Save sheet", type="primary", icon=":material/save:")

if submitted:
    skill_rows = [{"player_id": int(row.player_id), "skill": row.skill,
                   "competency": row.competency, "effort": row.effort}
                  for row in edited_skills.itertuples()]
    trait_rows = [{"player_id": int(row["player_id"]), "trait": trait, "score": row[trait]}
                  for _, row in edited_traits.iterrows() for trait in CFG.coach_traits]
    try:
        changed = save_week_sheet(conn, week, coach, skill_rows, trait_rows)
        st.success(f"Saved {fmt_week(week).lower()}: {changed['ratings']} skill ratings and {changed['traits']} "
                   "trait ratings changed. Earlier values are kept in history.", icon=":material/check:")
    except ValueError as error:
        st.error(str(error))
