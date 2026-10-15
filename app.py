"""
Lacrosse Tracker: Streamlit entry point and page list.

Run with:  streamlit run app.py

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: file header.
"""
import streamlit as st

st.set_page_config(page_title="Lacrosse Tracker", page_icon=":material/sports:", layout="wide")

pages = {
    "Team": [
        st.Page("views/roster.py", title="Roster", icon=":material/groups:", default=True),
        st.Page("views/team.py", title="Team charts", icon=":material/insights:"),
        st.Page("views/player.py", title="Player", icon=":material/person:"),
        st.Page("views/flags.py", title="Flags", icon=":material/flag:"),
    ],
    "Data entry": [
        st.Page("views/coach_sheet.py", title="Weekly coach sheet", icon=":material/edit_note:"),
        st.Page("views/reports.py", title="Player reports", icon=":material/upload_file:"),
        st.Page("views/setup.py", title="Roster setup", icon=":material/settings:"),
    ],
}
st.navigation(pages).run()
