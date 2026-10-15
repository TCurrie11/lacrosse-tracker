"""
Lacrosse Tracker: local player-development tracking for a lacrosse team.

Package layout
--------------
config     Loads config.yaml (skills, positions, detection settings).
db         SQLite schema and connection.
roster     Adding and editing players.
ingest     Validating and importing weekly player CSV reports.
coach      Saving the weekly coach sheet.
queries    Read-side queries returning DataFrames.
detection  Rules that flag suspicious self-reported data.
charts     Plotly figures.
ui         Streamlit helpers shared by every page.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file headers, package version.
"""

__version__ = "1.1.0"
