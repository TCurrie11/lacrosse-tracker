"""
Roster setup page.

Add players, edit name / number / position / active status, issue new submit codes, and
download every player's code to hand out.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import streamlit as st

from tracker import queries
from tracker.config import CFG
from tracker.roster import add_player, regenerate_code, set_active, update_player
from tracker.ui import get_conn

conn = get_conn()
st.title("Roster setup")
st.caption("No accounts — each player gets a submit code that must be in their CSV. It isn't a password, "
           "just a guard against submitting under a teammate's name. Skills, positions and traits live in config.yaml.")

with st.form("add_player", clear_on_submit=True):
    st.markdown("**Add a player**")
    name_column, number_column, position_column = st.columns([3, 1, 2])
    name = name_column.text_input("Name")
    number = number_column.number_input("Number", min_value=0, max_value=99, value=None, step=1)
    position = position_column.selectbox("Position", list(CFG.positions), format_func=str.capitalize)
    if st.form_submit_button("Add player", type="primary", icon=":material/person_add:"):
        try:
            add_player(conn, name, position, int(number) if number is not None else None)
            st.success(f"Added {name}.")
        except ValueError as error:
            st.error(str(error))

players = queries.players_df(conn, active_only=False)
if players.empty:
    st.info("No players yet.")
    st.stop()

st.markdown("**Players**")
edited_players = st.data_editor(
    players[["player_id", "name", "number", "position", "submit_code", "active"]],
    hide_index=True, width="stretch", disabled=["player_id", "submit_code"], key="roster_editor",
    column_config={"player_id": None, "name": "Name", "number": st.column_config.NumberColumn("#", format="%d"),
                   "position": st.column_config.SelectboxColumn("Position", options=list(CFG.positions)),
                   "submit_code": "Submit code", "active": st.column_config.CheckboxColumn("Active")})

save_column, code_column, download_column = st.columns(3)
if save_column.button("Save changes", icon=":material/save:"):
    for edited in edited_players.itertuples():
        number_is_blank = edited.number != edited.number  # NaN is the only value not equal to itself
        update_player(conn, int(edited.player_id), edited.name, None if number_is_blank else edited.number,
                      edited.position)
        set_active(conn, int(edited.player_id), bool(edited.active))
    st.success("Saved.")
    st.rerun()
with code_column.popover("New code for a player", icon=":material/key:"):
    chosen_player_id = st.selectbox("Player", players.player_id,
                                    format_func=dict(zip(players.player_id, players.name)).get)
    if st.button("Generate new code"):
        st.success(f"New code: {regenerate_code(conn, int(chosen_player_id))}")
download_column.download_button("Download codes (CSV)", players[["name", "number", "submit_code"]].to_csv(index=False),
                                "submit_codes.csv", "text/csv", icon=":material/download:")
