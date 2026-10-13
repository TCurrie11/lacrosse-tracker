import streamlit as st

from tracker import queries as q
from tracker.config import CFG
from tracker.roster import add_player, regenerate_code, set_active, update_player
from tracker.ui import get_conn

conn = get_conn()
st.title("Roster setup")
st.caption("No accounts — each player gets a submit code that must be in their CSV. It isn't a password, "
           "just a guard against submitting under a teammate's name. Skills, positions and traits live in config.yaml.")

with st.form("add_player", clear_on_submit=True):
    st.markdown("**Add a player**")
    c1, c2, c3 = st.columns([3, 1, 2])
    name = c1.text_input("Name")
    number = c2.number_input("Number", min_value=0, max_value=99, value=None, step=1)
    position = c3.selectbox("Position", list(CFG.positions), format_func=str.capitalize)
    if st.form_submit_button("Add player", type="primary", icon=":material/person_add:"):
        try:
            add_player(conn, name, position, int(number) if number is not None else None)
            st.success(f"Added {name}.")
        except ValueError as e:
            st.error(str(e))

players = q.players_df(conn, active_only=False)
if players.empty:
    st.info("No players yet.")
    st.stop()

st.markdown("**Players**")
edited = st.data_editor(
    players[["player_id", "name", "number", "position", "submit_code", "active"]],
    hide_index=True, width="stretch", disabled=["player_id", "submit_code"], key="roster_editor",
    column_config={"player_id": None, "name": "Name", "number": st.column_config.NumberColumn("#", format="%d"),
                   "position": st.column_config.SelectboxColumn("Position", options=list(CFG.positions)),
                   "submit_code": "Submit code", "active": st.column_config.CheckboxColumn("Active")})
c1, c2, c3 = st.columns(3)
if c1.button("Save changes", icon=":material/save:"):
    for r in edited.itertuples():
        update_player(conn, int(r.player_id), r.name, None if r.number != r.number else r.number, r.position)
        set_active(conn, int(r.player_id), bool(r.active))
    st.success("Saved.")
    st.rerun()
with c2.popover("New code for a player", icon=":material/key:"):
    pid = st.selectbox("Player", players.player_id, format_func=dict(zip(players.player_id, players.name)).get)
    if st.button("Generate new code"):
        st.success(f"New code: {regenerate_code(conn, int(pid))}")
c3.download_button("Download codes (CSV)", players[["name", "number", "submit_code"]].to_csv(index=False),
                   "submit_codes.csv", "text/csv", icon=":material/download:")
