import json

SRC = r"C:\Users\Notebook\AppData\Local\Temp\claude\C--Users-Notebook-Documents-Claude-Projects-Finan-as\df08f3ba-b083-4201-999d-785dc1e7b34a\tasks\w8bqtrjaz.output"
with open(SRC, encoding="utf-8") as f:
    data = json.load(f)

for r in data["result"][1:]:
    print("=" * 100)
    print("WORKER:", r.get("worker"), "| status:", r.get("status"))
    print("HEADLINE:", r.get("headline"))
    for c in r.get("cells", []):
        v = (c.get("verdict") or "")[:90]
        print(f"  {c.get('name')} | {c.get('period')} | net={c.get('net_ann_pct')} "
              f"shp={c.get('sharpe')} dd={c.get('maxdd_pct')} pf={c.get('pf')} "
              f"mens={c.get('monthly_mean_pct')} n={c.get('n_trades')} | {v}")
    print("NOTES:", (r.get("notes") or "")[:900])
