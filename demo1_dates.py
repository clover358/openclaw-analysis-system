import json
from pathlib import Path

FILES = [
    "data/Market Share.json",
    "data/Top Model.json",
    "data/Categories.json",
    "data/Languages.json",
    "data/Programming.json",
    "data/Context Length.json",
]


def load_points(path: str):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, dict) and isinstance(obj.get("data"), list):
        return obj["data"]
    if isinstance(obj, list):
        return obj
    return []


for file in FILES:
    points = load_points(file)
    xs = [item.get("x") for item in points if isinstance(item, dict) and item.get("x")]
    print(file)
    print("  count:", len(xs))
    print("  min:", min(xs) if xs else None)
    print("  max:", max(xs) if xs else None)
    print("  last item x:", xs[-1] if xs else None)
