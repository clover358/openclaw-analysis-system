import json
from pathlib import Path

FILES = [
    "data/day_leaderboard.json",
    "data/week_leaderboard.json",
    "data/month_leaderboard.json",
]


for file in FILES:
    rows = json.loads(Path(file).read_text(encoding="utf-8"))
    dates = [row.get("date") for row in rows if isinstance(row, dict) and row.get("date")]
    print(file)
    print("  rows:", len(rows))
    print("  min date:", min(dates) if dates else None)
    print("  max date:", max(dates) if dates else None)
    print("  last date:", dates[-1] if dates else None)
