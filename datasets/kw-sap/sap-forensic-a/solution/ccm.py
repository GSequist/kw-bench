"""Continuous control monitoring: P2P irregularities from an SAP extract. Usage: python ccm.py <extract_dir> <out_csv>"""
import io
import os
import re
import sys

import numpy as np
import pandas as pd

K3 = ["BUKRS", "BELNR", "GJAHR"]
VAT_GL = "0000154000"


GERMAN_NUM = r"-?\d{1,3}(\.\d{3})*,\d+-?|-?\d+,\d+-?"


def _decode(raw):
    raw = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _se16(text):
    """SE16N list download: '|'-framed rows, header repeated per page, title/separator lines in between."""
    rows, hdr = [], None
    for ln in text.splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if hdr is None:
            hdr = cells
        elif cells != hdr:
            rows.append(cells)
    return pd.DataFrame(rows, columns=hdr)


def _xlsx(path):
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    data = list(ws.iter_rows(values_only=True))
    cell = lambda v: "" if v is None else v.strftime("%Y%m%d") if hasattr(v, "strftime") else str(v)
    return pd.DataFrame([[cell(v) for v in r] for r in data[1:]], columns=[str(c) for c in data[0]])


def normalise(df):
    """Canonical strings: dates YYYYMMDD, times HHMMSS, numbers with '.' decimals and leading minus; MANDT dropped."""
    df = df.drop(columns=[c for c in ("MANDT",) if c in df.columns])
    for c in df.columns:
        v = df[c].astype(str).str.strip()
        ne = v[v != ""].head(5000)
        if not len(ne):
            df[c] = v
            continue
        if ne.str.fullmatch(r"\d{2}\.\d{2}\.\d{4}").all():
            v = v.where(v == "", v.str[6:10] + v.str[3:5] + v.str[0:2])
        elif ne.str.fullmatch(r"\d{2}:\d{2}:\d{2}").all():
            v = v.str.replace(":", "", regex=False)
        elif ne.str.contains(",", regex=False).any() and ne.str.fullmatch(GERMAN_NUM).all():
            neg = v.str.endswith("-") | v.str.startswith("-")
            n = v.str.strip("-").str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
            v = n.where(~neg | (n == ""), "-" + n)
        df[c] = v
    return df


def reader(sap):
    """rd(table, cols) over a folder of mixed SAP exports (<T>.txt/.csv/.xlsx, <T>_<part>.*); all values as str."""
    cache = {}

    def files(t):
        fs = sorted(f for f in os.listdir(sap) if re.fullmatch(rf"{t}(_[^.]*)?\.(txt|csv|xlsx)", f, re.I))
        if not fs:
            raise FileNotFoundError(f"{t} not found in {sap}")
        return fs

    def load(t, cols):
        parts = []
        for f in files(t):
            p = os.path.join(sap, f)
            if f.lower().endswith(".xlsx"):
                parts.append(_xlsx(p))
                continue
            text = _decode(open(p, "rb").read())
            if f.lower().endswith(".txt") or text.lstrip().startswith(("|", "-")):
                parts.append(_se16(text))
            else:
                sep = ";" if text[:2000].count(";") >= text[:2000].count(",") else ","
                use = (lambda c: c in cols) if cols else None
                parts.append(pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, usecols=use))
        return normalise(pd.concat(parts, ignore_index=True))

    def rd(t, cols=None):
        key = (t, tuple(cols) if cols else None)
        if key not in cache:
            cache[key] = load(t, set(cols) if cols else None)
        d = cache[key]
        return d[cols].copy() if cols else d.copy()
    return rd


def norm_iban(s):
    return s.str.replace(r"\s+", "", regex=True).str.upper()


def ref_key(x):
    """Reference reduced to its digits (O misread as 0, separators and leading zeros dropped)."""
    x = re.sub(r"(?<=\d)O|O(?=\d)", "0", str(x).upper())
    return re.sub(r"\D", "", x).lstrip("0")


def load(sap):
    """Vendor invoices/credit memos with vendor line, net EUR and clearing info."""
    rd = reader(sap)
    bkpf = rd("BKPF", K3 + ["BLART", "BUDAT", "CPUDT", "CPUTM", "USNAM", "XBLNR", "BKTXT", "WAERS", "KURSF", "AWTYP", "AWKEY", "STBLG"])
    docs = bkpf[bkpf.BLART.isin(["RE", "KR", "KG"])]
    bseg = rd("BSEG", K3 + ["BUZEI", "KOART", "SHKZG", "DMBTR", "WRBTR", "HKONT", "LIFNR", "MATNR", "MENGE", "EBELN", "EBELP", "AUGBL", "AUGDT", "SGTXT"])
    bseg = bseg.merge(docs[K3], on=K3)
    for c in ("DMBTR", "WRBTR"):
        bseg[c] = bseg[c].astype(float)
    bseg["sg"] = np.where(bseg.SHKZG == "S", 1.0, -1.0)
    vend = bseg[bseg.KOART == "K"][K3 + ["LIFNR", "WRBTR", "DMBTR", "SHKZG", "AUGBL"]].rename(
        columns={"WRBTR": "gross_wr", "DMBTR": "gross_eur", "SHKZG": "vshkzg"})
    other = bseg[bseg.KOART != "K"]
    net = (other[other.HKONT != VAT_GL].assign(x=lambda d: d.DMBTR * d.sg)).groupby(K3).x.sum().rename("net_eur").reset_index()
    tax = (other[other.HKONT == VAT_GL].assign(x=lambda d: d.DMBTR * d.sg)).groupby(K3).x.sum().rename("tax_eur").reset_index()
    inv = docs.merge(vend, on=K3).merge(net, on=K3, how="left").merge(tax, on=K3, how="left").fillna({"net_eur": 0.0, "tax_eur": 0.0})
    return rd, inv, bseg


def ghost(rd, inv):
    """Trade vendor whose bank account is an employee's account."""
    lfa1, lfbk, pa9 = rd("LFA1", ["LIFNR", "KTOKK"]), rd("LFBK", ["LIFNR", "IBAN"]), rd("PA0009", ["PERNR", "IBAN"])
    lfbk["ib"], pa9["ib"] = norm_iban(lfbk.IBAN), norm_iban(pa9.IBAN)
    hit = lfbk[lfbk.ib != ""].merge(pa9[["PERNR", "ib"]], on="ib").merge(lfa1, on="LIFNR")
    hit = hit[hit.KTOKK != "MITA"]
    f = inv[inv.LIFNR.isin(hit.LIFNR) & (inv.vshkzg == "H") & (inv.STBLG == "")]
    return f.assign(AMOUNT_EUR=f.net_eur, REASON_CODE="GHOST_VENDOR"), hit


def duplicates(inv):
    """Same vendor, same gross amount, same normalised reference posted more than once (later postings)."""
    d = inv[(inv.vshkzg == "H") & (inv.STBLG == "")].copy()
    d["ref"] = d.XBLNR.map(ref_key)
    d = d[d.ref != ""].sort_values(["CPUDT", "CPUTM", "BELNR"])
    d["n"] = d.groupby(["LIFNR", "ref", "gross_wr"]).cumcount()
    return d[d.n > 0].assign(AMOUNT_EUR=lambda x: x.net_eur, REASON_CODE="DUPLICATE_PAYMENT")


def dup_credits(inv, dups):
    """Supplier credit notes that refer to a duplicated invoice reference."""
    if dups.empty:
        return inv.iloc[0:0]
    c = inv[(inv.vshkzg == "S") & (inv.STBLG == "") & inv.LIFNR.isin(dups.LIFNR)].copy()
    c["ref"] = c.BKTXT.map(lambda t: ref_key(t.split()[-1]) if t.strip() else "")
    m = c.merge(dups[["LIFNR", "ref", "BUKRS", "BELNR", "GJAHR"]].rename(columns={"BUKRS": "d_bukrs", "BELNR": "d_belnr", "GJAHR": "d_gjahr"}),
                on=["LIFNR", "ref"])
    return m


def price_lines(rd, bseg):
    """Invoice lines priced above the PO price beyond tolerance: excess in EUR per line."""
    ekpo = rd("EKPO", ["EBELN", "EBELP", "NETPR", "PEINH"])
    x = bseg[(bseg.EBELN != "") & (bseg.KOART == "S") & (bseg.HKONT != VAT_GL)].merge(ekpo, on=["EBELN", "EBELP"])
    q = x.MENGE.astype(float)
    x = x[(q > 0) & (x.SHKZG == "S")].copy()
    q = x.MENGE.astype(float)
    po = q * x.NETPR.astype(float) / x.PEINH.astype(float)
    rate = np.where(x.WRBTR > 0, x.DMBTR / x.WRBTR.where(x.WRBTR > 0, 1), 1.0)
    x["dev"] = x.WRBTR / po - 1
    x["excess"] = np.round((x.WRBTR - po) * rate, 2)
    return x[(x.dev > 0.02) & (x.excess > 0)]


def price_dev(rd, inv, bseg):
    pl = price_lines(rd, bseg)
    pe = pl.groupby(K3).excess.sum().round(2).rename("AMOUNT_EUR").reset_index()
    f = inv[(inv.STBLG == "") & (inv.vshkzg == "H")].merge(pe, on=K3)
    return f.assign(REASON_CODE="PRICE_DEVIATION"), pl


def bank_history(rd):
    """IBAN change events per vendor from change documents."""
    h = rd("CDHDR", ["OBJECTCLAS", "OBJECTID", "CHANGENR", "USERNAME", "UDATE", "UTIME"])
    p = rd("CDPOS", ["OBJECTCLAS", "OBJECTID", "CHANGENR", "TABNAME", "FNAME", "VALUE_NEW", "VALUE_OLD"])
    p = p[(p.TABNAME == "LFBK") & (p.FNAME == "IBAN")].merge(h, on=["OBJECTCLAS", "OBJECTID", "CHANGENR"])
    p["new"], p["old"] = norm_iban(p.VALUE_NEW), norm_iban(p.VALUE_OLD)
    p["ts"] = p.UDATE + p.UTIME.str.zfill(6)
    return p.rename(columns={"OBJECTID": "LIFNR"}).sort_values(["LIFNR", "ts", "CHANGENR"])


def diversion(rd, emp_ibans):
    """Payments to a bank account that was set temporarily (changed and reverted) or that belongs to an employee."""
    hist = bank_history(rd)
    reguh = rd("REGUH", ["LAUFD", "LAUFI", "ZBUKR", "LIFNR", "VBLNR", "ZIBAN", "RBETR"])
    reguh["ib"] = norm_iban(reguh.ZIBAN)
    lfa1, lfbk = rd("LFA1", ["LIFNR", "KTOKK"]), rd("LFBK", ["LIFNR", "IBAN"])
    cur = dict(zip(lfbk.LIFNR, norm_iban(lfbk.IBAN)))
    mita = set(lfa1.LIFNR[lfa1.KTOKK == "MITA"])
    hits, events = set(), []
    for l, g in hist.groupby("LIFNR"):
        ev = g.to_dict("records")
        for i, a in enumerate(ev):
            for b in ev[i + 1:]:
                if b["old"] == a["new"] and b["new"] == a["old"] and int(b["UDATE"]) - int(a["UDATE"]) <= 100:
                    r = reguh[(reguh.LIFNR == l) & (reguh.ib == a["new"]) & (reguh.LAUFD >= a["UDATE"]) & (reguh.LAUFD <= b["UDATE"])]
                    if len(r):
                        hits |= set(r.VBLNR + "|" + r.ZBUKR + "|" + r.LAUFD)
                        events.append(dict(LIFNR=l, change=a, revert=b))
                    break
    emp = reguh[reguh.ib.isin(emp_ibans) & ~reguh.LIFNR.isin(mita) & (reguh.ib != reguh.LIFNR.map(cur).fillna(""))]
    hits |= set(emp.VBLNR + "|" + emp.ZBUKR + "|" + emp.LAUFD)
    regup = rd("REGUP", ["LAUFD", "ZBUKR", "LIFNR", "VBLNR", "BUKRS", "BELNR", "GJAHR", "SHKZG", "DMBTR"])
    regup = regup[(regup.VBLNR + "|" + regup.ZBUKR + "|" + regup.LAUFD).isin(hits) & (regup.SHKZG == "H")]
    f = regup.assign(AMOUNT_EUR=regup.DMBTR.astype(float), REASON_CODE="BANK_DIVERSION", AUGBL=regup.VBLNR)
    return f, events


def detect(sap):
    rd, inv, bseg = load(sap)
    pa9 = rd("PA0009", ["PERNR", "IBAN"])
    g, ghost_hit = ghost(rd, inv)
    d = duplicates(inv)
    p, pl = price_dev(rd, inv, bseg)
    v, div_events = diversion(rd, set(norm_iban(pa9.IBAN)) - {""})
    cols = K3 + ["LIFNR", "AMOUNT_EUR", "REASON_CODE", "AUGBL"]
    f = pd.concat([x[cols] for x in (g, d, p, v)], ignore_index=True).drop_duplicates(K3)
    f["AMOUNT_EUR"] = f.AMOUNT_EUR.astype(float).round(2)
    ctx = dict(rd=rd, inv=inv, bseg=bseg, ghost_hit=ghost_hit, dups=d, dup_credits=dup_credits(inv, d), price_lines=pl,
               div_events=div_events)
    return f, ctx


def main(sap, out):
    f, _ = detect(sap)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    f.to_csv(out, sep=";", index=False)
    print(f"{len(f)} findings -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
