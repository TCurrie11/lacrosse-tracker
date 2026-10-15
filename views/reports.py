"""
Player reports page.

Upload weekly report CSVs (or import whatever is in the inbox folder), download the blank
template, and browse the import log of every file received, accepted or rejected.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import streamlit as st

from tracker import queries
from tracker.config import CFG
from tracker.ingest import ingest_file, ingest_inbox, template_csv
from tracker.ui import get_conn

conn = get_conn()
st.title("Player reports")
st.caption(f"Players send one CSV per week. Drop the files here, or copy them into `{CFG.inbox_dir}` and click "
           "Import inbox. Each file is checked, the original is archived untouched, and bad files are rejected "
           "whole with a reason.")

upload_column, template_column = st.columns([3, 2])
with upload_column:
    uploaded_files = st.file_uploader("Weekly report CSVs", type=["csv"], accept_multiple_files=True, key="up")
    if st.button("Import", type="primary", disabled=not uploaded_files, icon=":material/upload:"):
        import_results = []
        for uploaded_file in uploaded_files:
            inbox_path = CFG.inbox_dir / uploaded_file.name
            inbox_path.write_bytes(uploaded_file.getvalue())
            import_results.append(ingest_file(conn, inbox_path))
        st.session_state["_last_import"] = import_results
    pending_files = sorted(CFG.inbox_dir.glob("*.csv"))
    plural = "s" if len(pending_files) != 1 else ""
    if st.button(f"Import inbox ({len(pending_files)} file{plural})", disabled=not pending_files,
                 icon=":material/inbox:"):
        st.session_state["_last_import"] = ingest_inbox(conn)

with template_column:
    st.markdown("**Template for players**")
    st.code(template_csv("K7Q2", "2026-10-05"), language="csv")
    st.download_button("Download template", template_csv(), "weekly_report_template.csv", "text/csv",
                       icon=":material/download:")
    st.caption("Give each player their submit code (Roster setup). `week_start` is that week's Monday. "
               "Put 0 hours for skills they didn't train and leave effort blank.")

for result in st.session_state.pop("_last_import", []):
    if result.status == "imported":
        st.success(f"**{result.file}** — {result.player}, {len(result.weeks)} week(s), {result.rows} rows imported.",
                   icon=":material/check:")
        for warning in result.warnings:
            st.caption(f"⚠ {warning}")
    else:
        st.error(f"**{result.file}** — {result.status}:\n\n" + "\n".join(f"- {error}" for error in result.errors[:15]))

st.subheader("Import log")
import_log = queries.ingest_log_df(conn)
if import_log.empty:
    st.caption("Nothing imported yet.")
else:
    status_filter = st.segmented_control("Show", ["All", "Imported", "Rejected"], default="All", key="log_filter")
    if status_filter == "Imported":
        import_log = import_log[import_log.status == "imported"]
    elif status_filter == "Rejected":
        import_log = import_log[import_log.status != "imported"]
    st.dataframe(import_log[["received_at", "file", "status", "player", "weeks", "rows", "message", "sha256",
                             "stored_path"]],
                 hide_index=True, width="stretch",
                 column_config={"received_at": "Received",
                                "message": st.column_config.TextColumn("Notes", width="large"),
                                "sha256": st.column_config.TextColumn("SHA-256", width="small"),
                                "stored_path": st.column_config.TextColumn("Stored copy", width="small")})
