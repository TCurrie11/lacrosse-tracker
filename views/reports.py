import streamlit as st

from tracker import queries as q
from tracker.config import CFG
from tracker.ingest import ingest_file, ingest_inbox, template_csv
from tracker.ui import get_conn

conn = get_conn()
st.title("Player reports")
st.caption(f"Players send one CSV per week. Drop the files here, or copy them into `{CFG.inbox}` and click "
           "Import inbox. Each file is checked, the original is archived untouched, and bad files are rejected "
           "whole with a reason.")

c1, c2 = st.columns([3, 2])
with c1:
    files = st.file_uploader("Weekly report CSVs", type=["csv"], accept_multiple_files=True, key="up")
    if st.button("Import", type="primary", disabled=not files, icon=":material/upload:"):
        results = []
        for f in files:
            dest = CFG.inbox / f.name
            dest.write_bytes(f.getvalue())
            results.append(ingest_file(conn, dest))
        st.session_state["_last_import"] = results
    pending = sorted(CFG.inbox.glob("*.csv"))
    if st.button(f"Import inbox ({len(pending)} file{'s' if len(pending) != 1 else ''})", disabled=not pending,
                 icon=":material/inbox:"):
        st.session_state["_last_import"] = ingest_inbox(conn)

with c2:
    st.markdown("**Template for players**")
    st.code(template_csv("K7Q2", "2026-10-05"), language="csv")
    st.download_button("Download template", template_csv(), "weekly_report_template.csv", "text/csv",
                       icon=":material/download:")
    st.caption("Give each player their submit code (Roster setup). `week_start` is that week's Monday. "
               "Put 0 hours for skills they didn't train and leave effort blank.")

for r in st.session_state.pop("_last_import", []):
    if r.status == "imported":
        st.success(f"**{r.file}** — {r.player}, {len(r.weeks)} week(s), {r.rows} rows imported.", icon=":material/check:")
        for w in r.warnings:
            st.caption(f"⚠ {w}")
    else:
        st.error(f"**{r.file}** — {r.status}:\n\n" + "\n".join(f"- {e}" for e in r.errors[:15]))

st.subheader("Import log")
log = q.ingest_log_df(conn)
if log.empty:
    st.caption("Nothing imported yet.")
else:
    status_filter = st.segmented_control("Show", ["All", "Imported", "Rejected"], default="All", key="log_filter")
    if status_filter == "Imported":
        log = log[log.status == "imported"]
    elif status_filter == "Rejected":
        log = log[log.status != "imported"]
    st.dataframe(log[["received_at", "file", "status", "player", "weeks", "rows", "message", "sha256", "stored_path"]],
                 hide_index=True, width="stretch",
                 column_config={"received_at": "Received", "message": st.column_config.TextColumn("Notes", width="large"),
                                "sha256": st.column_config.TextColumn("SHA-256", width="small"),
                                "stored_path": st.column_config.TextColumn("Stored copy", width="small")})
