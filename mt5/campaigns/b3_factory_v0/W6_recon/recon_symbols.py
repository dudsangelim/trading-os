"""
W6 - Recon de simbolos MT5 XP. Read-only, nenhuma ordem.
Roda: python recon_symbols.py
"""
import MetaTrader5 as mt5
import csv
import re
import time
from datetime import datetime, timezone

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0\W6_recon"
TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

CANDIDATES_OF_INTEREST = [
    "WIN", "WDO", "IND", "DOL", "DI1", "BGI", "CCM", "ICF", "BIT", "ETH",
    "SFI", "IBOV", "SOJ", "CRA", "IB1",
]

def classify(symbol_name, path):
    s = symbol_name.upper()
    p = (path or "").upper()
    if re.match(r"^(WIN|IND)", s):
        return "indices_win"
    if re.match(r"^(WDO|DOL)", s):
        return "moedas_dolar"
    if re.match(r"^DI1", s):
        return "di_juros"
    if re.match(r"^(BGI|CCM|ICF|SFI|SOJ|CRA)", s):
        return "commodities"
    if re.match(r"^(BIT|ETH|BTC)", s):
        return "cripto"
    if "AÇÕES" in p or "ACOES" in p or "STOCKS" in p or "BOVESPA" in p:
        return "acoes"
    if "FOREX" in p or "CURRENC" in p:
        return "forex"
    if "INDICE" in p or "INDEX" in p:
        return "indices_outros"
    return "outros"

def main():
    ok = mt5.initialize(path=TERMINAL_PATH)
    if not ok:
        print("FALHA initialize:", mt5.last_error())
        return

    info = mt5.account_info()
    print("Conta:", info.login if info else None, "server:", info.server if info else None)

    symbols = mt5.symbols_get()
    print(f"Total simbolos no servidor: {len(symbols)}")

    groups = {}
    all_rows = []
    for s in symbols:
        grp = classify(s.name, s.path)
        groups.setdefault(grp, []).append(s.name)
        all_rows.append({
            "symbol": s.name,
            "description": s.description,
            "path": s.path,
            "group_classified": grp,
        })

    # salva inventario completo
    with open(f"{OUT_DIR}\\inventory_all_symbols.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "description", "path", "group_classified"])
        w.writeheader()
        w.writerows(all_rows)

    print("\n=== Contagem por grupo ===")
    for g, names in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"{g}: {len(names)}")

    # identificar simbolos de interesse (contains candidate prefix), ate 15 alem de WIN/WDO
    found_candidates = []
    all_names = [s.name for s in symbols]
    for cand in CANDIDATES_OF_INTEREST:
        matches = [n for n in all_names if n.upper().startswith(cand)]
        for m in matches:
            if m not in found_candidates:
                found_candidates.append(m)

    # foco: serie continua '$N' de cada prefixo relevante (representativa, evita
    # estourar limite de 20 com vencimentos individuais como WINQ26, DI1F27 etc)
    priority_suffixed = [
        "WIN$N", "WDO$N", "IND$N", "DOL$N", "DI1$N", "BGI$N", "CCM$N",
        "ICF$N", "BIT$N",
    ]
    target_symbols = [n for n in priority_suffixed if n in all_names]
    # completa com outros candidatos achados que ainda nao estao na lista
    for n in found_candidates:
        if len(target_symbols) >= 17:
            break
        if n not in target_symbols:
            target_symbols.append(n)

    print(f"\n=== Checando profundidade de historico para {len(target_symbols)} simbolos ===")
    depth_rows = []
    for sym in target_symbols:
        if not mt5.symbol_select(sym, True):
            print(f"{sym}: falha ao selecionar")
            continue
        time.sleep(1.0)
        desc = None
        info_sym = mt5.symbol_info(sym)
        if info_sym:
            desc = info_sym.description

        row = {"symbol": sym, "descricao": desc, "n_d1": 0, "primeiro_d1": None,
               "n_h1": 0, "primeiro_h1": None}

        # copy_rates_from_pos com count >= 100000 retorna erro "Invalid params" no
        # terminal MT5; usar 20000 (teto seguro, MT5 corta no que tiver disponivel)
        d1 = None
        for _ in range(3):
            d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 20000)
            if d1 is not None and len(d1) > 0:
                break
            time.sleep(0.5)
        if d1 is not None and len(d1) > 0:
            row["n_d1"] = len(d1)
            row["primeiro_d1"] = datetime.fromtimestamp(int(d1[0]["time"]), tz=timezone.utc).strftime("%Y-%m-%d")

        h1 = None
        for _ in range(3):
            h1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 20000)
            if h1 is not None and len(h1) > 0:
                break
            time.sleep(0.5)
        if h1 is not None and len(h1) > 0:
            row["n_h1"] = len(h1)
            row["primeiro_h1"] = datetime.fromtimestamp(int(h1[0]["time"]), tz=timezone.utc).strftime("%Y-%m-%d")

        depth_rows.append(row)
        print(f"{sym}: D1 n={row['n_d1']} desde={row['primeiro_d1']} | H1 n={row['n_h1']} desde={row['primeiro_h1']}")

    with open(f"{OUT_DIR}\\depth_check.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "descricao", "n_d1", "primeiro_d1", "n_h1", "primeiro_h1"])
        w.writeheader()
        w.writerows(depth_rows)

    # o que NAO existe
    not_found = [c for c in CANDIDATES_OF_INTEREST if not any(n.upper().startswith(c) for n in all_names)]
    print("\n=== Candidatos NAO encontrados no servidor ===")
    for nf in not_found:
        print(nf)

    with open(f"{OUT_DIR}\\not_found.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["candidato_nao_encontrado"])
        for nf in not_found:
            w.writerow([nf])

    mt5.shutdown()
    print("\nOK - CSVs salvos em", OUT_DIR)

if __name__ == "__main__":
    main()
