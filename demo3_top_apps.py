import json
from pathlib import Path

path = Path("data/Top Apps.json")
obj = json.loads(path.read_text(encoding="utf-8"))

for key in ["day", "week", "month"]:
    rows = obj.get(key, []) if isinstance(obj, dict) else []
    print("Top Apps", key)
    print("  rows:", len(rows))
    if rows:
        app = rows[0].get("app") if isinstance(rows[0].get("app"), dict) else {}
        print("  sample keys:", list(rows[0].keys()))
        print("  sample rank/title/tokens:", rows[0].get("rank"), app.get("title"), rows[0].get("total_tokens"))
