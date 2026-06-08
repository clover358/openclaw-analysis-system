import json
from pathlib import Path

from src.agents.analyst.agent import _leaderboard_to_dataframe

FILES = [
    "data/day_leaderboard.json",
    "data/week_leaderboard.json",
    "data/month_leaderboard.json",
]

for file in FILES:
    rows = json.loads(Path(file).read_text(encoding="utf-8"))
    df = _leaderboard_to_dataframe(rows)
    print("\n" + file)
    print(df[["date", "model_permaslug", "analysis_tokens", "rank"]].head(10).to_string(index=False))
