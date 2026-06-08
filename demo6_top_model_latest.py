import json
from pathlib import Path

from src.agents.analyst.agent import _timeseries_to_dataframes

payload = json.loads(Path("data/Top Model.json").read_text(encoding="utf-8"))
series = payload.get("data", payload) if isinstance(payload, dict) else payload
_, latest = _timeseries_to_dataframes(series, entity_col="model")

print(latest[["period", "model", "tokens", "rank", "share_pct"]].head(15).to_string(index=False))
