from src.agents.analyst.agent import _parse_date, _date_text

SAMPLES = [
    "2025-06-09",
    "2025-7-28",
    "2026-06-02",
    "2026-06-08 00:00:00",
    "20260608",
    "2026/06/08",
]

for sample in SAMPLES:
    parsed = _parse_date(sample)
    print(sample, "=>", parsed, "=>", _date_text(parsed) if parsed is not None else None)
