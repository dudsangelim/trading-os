import json

SRC = r"C:\Users\Notebook\AppData\Local\Temp\claude\C--Users-Notebook-Documents-Claude-Projects-Finan-as\df08f3ba-b083-4201-999d-785dc1e7b34a\tasks\whd07ce0x.output"
with open(SRC, encoding="utf-8") as f:
    d = json.load(f)
r3 = d["result"]["analise"][1]
print("STATUS:", r3["status"])
print("HEADLINE:", r3["headline"])
for c in r3["cells"]:
    print(f"  {c.get('name')} | {c.get('period','')} | net={c.get('net_ann_pct')} "
          f"shp={c.get('sharpe')} dd={c.get('maxdd_pct')} pf={c.get('pf')} "
          f"mens={c.get('monthly_mean_pct')} | {(c.get('verdict') or '')[:110]}")
print("NOTES:", (r3.get("notes") or "")[:3000])
