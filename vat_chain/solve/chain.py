"""Chain transaction classifier for the Rotkehl SAP extract (legacy ECC + S/4HANA). Usage: python chain.py <extract_dir> <out_csv>

Reconstructs chains across three booking regimes (ECC document flow, S/4 document flow with migrated numbers, chain cockpit
IDs incl. hand-typed references), determines the moving supply and the treatment of every chain document per the group's
tax position memo, and writes one row per chain billing document."""
import io
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

EU = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
      "PL", "PT", "RO", "SE", "SI", "SK"}
RATE = {"DE": Decimal("0.19"), "AT": Decimal("0.20")}
SELLER_TERMS = {"CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"}
NEG = {"O", "6", "N"}
COLS = ["CHAIN_ID", "LEG", "SELLER", "BUYER", "BILLING_DOC", "DOC_TYPE", "BILLING_DATE", "DELIVERY", "SHIP_FROM", "SHIP_TO", "INCOTERM",
        "TRANSPORT_BY", "MOVING", "CORRECT_TREATMENT", "JURISDICTION", "NET_EUR", "VAT_EUR", "FILED_TREATMENT", "COMMENT"]
GERMAN_NUM = r"-?\d{1,3}(\.\d{3})*,\d+-?|-?\d+,\d+-?"


# ---------- reading the mixed export formats ----------
def _decode(raw):
    raw = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _se16(text):
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
    """Dates YYYYMMDD, numbers with '.' decimals and leading minus, MANDT dropped; everything str."""
    df = df.drop(columns=[c for c in ("MANDT",) if c in df.columns])
    for c in df.columns:
        v = df[c].astype(str).str.strip()
        ne = v[v != ""].head(5000)
        if not len(ne):
            df[c] = v
            continue
        if ne.str.fullmatch(r"\d{2}\.\d{2}\.\d{4}").all():
            v = v.where(v == "", v.str[6:10] + v.str[3:5] + v.str[0:2])
        elif ne.str.contains(",", regex=False).any() and ne.str.fullmatch(GERMAN_NUM).all():
            neg = v.str.endswith("-") | v.str.startswith("-")
            n = v.str.strip("-").str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
            v = n.where(~neg | (n == ""), "-" + n)
        df[c] = v
    return df


def reader(folder):
    """rd(table) over a folder of SAP exports (<T>.txt/.csv/.xlsx, <T>_<part>.*); None if absent."""
    cache = {}
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def rd(t):
        if t in cache:
            return cache[t]
        fs = [f for f in names if re.fullmatch(rf"{t}(_[^.]*)?\.(txt|csv|xlsx)", f, re.I)]
        if not fs:
            cache[t] = None
            return None
        parts = []
        for f in fs:
            p = os.path.join(folder, f)
            if f.lower().endswith(".xlsx"):
                parts.append(_xlsx(p))
                continue
            text = _decode(open(p, "rb").read())
            if f.lower().endswith(".txt") or text.lstrip().startswith(("|", "-")) or "Dynamische Listenausgabe" in text[:300]:
                parts.append(_se16(text))
            else:
                sep = ";" if text[:2000].count(";") >= text[:2000].count(",") else ","
                parts.append(pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False))
        cache[t] = normalise(pd.concat(parts, ignore_index=True))
        return cache[t]
    return rd


def cents(s):
    return int((Decimal(s or "0") * 100).quantize(Decimal(1), ROUND_HALF_UP))


def q(x):
    return int(Decimal(x).quantize(Decimal(1), ROUND_HALF_UP))


def chain_key(s):
    """Chain cockpit ID from a field or a hand-typed reference (RKC-2024-000123, rkc2024000123, RKC-2024-123, ...)."""
    m = re.search(r"RKC\W*(\d{4})\W*0*(\d{1,6})(?!\d)", str(s), re.I)
    return f"RKC-{m.group(1)}-{int(m.group(2)):06d}" if m else ""


# ---------- loading ----------
def both(E, S, t, cols):
    out = []
    for sys_, rd in (("ECC", E), ("S4", S)):
        d = rd(t)
        if d is None:
            continue
        d = d.copy()
        for c in cols:
            if c not in d.columns:
                d[c] = ""
        out.append(d[cols].assign(SYS=sys_))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols + ["SYS"])


def load(sap):
    E, S = reader(os.path.join(sap, "ecc")), reader(os.path.join(sap, "s4"))
    D = dict(E=E, S=S)
    D["vbrk"] = both(E, S, "VBRK", ["VBELN", "FKART", "VBTYP", "WAERK", "FKDAT", "BUKRS", "NETWK", "KURRF", "KUNAG", "INCO1", "INCO2",
                                    "LAND1", "STCEG", "ZUONR", "SFAKN", "ZZCHAIN"])
    D["vbrp"] = both(E, S, "VBRP", ["VBELN", "POSNR", "VGBEL", "VGTYP", "AUBEL", "WERKS", "MWSKZ"])
    D["likp"] = both(E, S, "LIKP", ["VBELN", "VSTEL", "KUNNR", "WADAT_IST", "INCO1", "INCO2", "LIFEX"])
    D["lips"] = both(E, S, "LIPS", ["VBELN", "POSNR", "WERKS", "VGBEL"])
    D["vbak"] = both(E, S, "VBAK", ["VBELN", "AUART", "KUNNR", "INCO1", "INCO2", "ZZCHAIN"])
    kna1 = both(E, S, "KNA1", ["KUNNR", "NAME1", "LAND1", "STCEG", "KTOKD"])
    D["kna1"] = kna1.sort_values("SYS").drop_duplicates("KUNNR", keep="last").set_index("KUNNR")  # S/4 master wins
    t001w = both(E, S, "T001W", ["WERKS", "LAND1", "BWKEY"]).drop_duplicates("WERKS")
    t001k = both(E, S, "T001K", ["BWKEY", "BUKRS"]).drop_duplicates("BWKEY")
    pl = t001w.merge(t001k, on="BWKEY", how="left")
    D["plant_land"], D["plant_bukrs"] = dict(zip(pl.WERKS, pl.LAND1)), dict(zip(pl.WERKS, pl.BUKRS))
    t001 = both(E, S, "T001", ["BUKRS", "LAND1"]).drop_duplicates("BUKRS")
    D["co_land"] = dict(zip(t001.BUKRS, t001.LAND1))
    D["bkpf"] = both(E, S, "BKPF", ["BUKRS", "BELNR", "GJAHR", "BLART", "BUDAT", "XBLNR", "BKTXT", "AWTYP", "AWKEY"])
    zm = S("ZMIG_DOCMAP")
    D["zmig"] = zm if zm is not None else pd.DataFrame(columns=["OBJTYPE", "OLD_KEY", "NEW_KEY"])
    tc = S("TCURR") if S("TCURR") is not None else E("TCURR")
    u = tc[tc.KURST == "UST"]
    D["ust"] = {(r.FCURR, str(99999999 - int(r.GDATU))[:6]): Decimal(r.UKURS) for r in u.itertuples()}
    t7 = both(E, S, "T007A", ["KALSM", "MWSKZ", "TEXT1", "LSTML"]).drop_duplicates(["KALSM", "MWSKZ"])
    tk = both(E, S, "T007K", ["KALSM", "MWSKZ", "KZ_BASIS"]).drop_duplicates(["KALSM", "MWSKZ"])
    D["code_treat"] = code_treatments(t7, tk)
    co = both(E, S, "T001", ["BUKRS", "KALSM"]).drop_duplicates("BUKRS")
    D["kalsm"] = dict(zip(co.BUKRS, co.KALSM))
    return D


def code_treatments(t7, tk):
    """Tax code -> treatment it implies (via the return line mapping, else the code text)."""
    kz = {"81": "DOM_TAX", "022": "DOM_TAX", "41": "ICS", "017": "ICS", "43": "EXPORT", "011": "EXPORT", "42": "TRIANGULAR", "DREIECK": "TRIANGULAR"}
    out = {}
    for r in t7.itertuples():
        m = tk[(tk.KALSM == r.KALSM) & (tk.MWSKZ == r.MWSKZ)]
        t = kz.get(m.KZ_BASIS.iloc[0], "") if len(m) else ""
        if not t:
            txt = r.TEXT1.lower()
            t = "EXPORT" if "ausfuhr" in txt else "DEST_SUPPLY" if ("ausland" in txt or "mwst" in txt) else ""
        out[(r.KALSM, r.MWSKZ)] = t
    return out


def to_eur(D, ccy, c, d):
    """Net cents in document currency -> EUR cents at the VAT rate (type UST) of the billing month."""
    if ccy == "EUR":
        return c
    return q(Decimal(c) / abs(D["ust"][(ccy, d[:6])]))


# ---------- reconstruction ----------
def build(sap):
    """Billing documents with physical facts, chain membership, moving supply and treatment."""
    D = load(sap)
    b = D["vbrk"].copy()
    assert b.VBELN.is_unique, "billing numbers not unique across systems"
    it = D["vbrp"].sort_values(["VBELN", "POSNR"]).drop_duplicates("VBELN")
    b = b.merge(it[["VBELN", "VGBEL", "VGTYP", "AUBEL", "WERKS", "MWSKZ"]], on="VBELN", how="left").fillna("")
    # deliveries: migrated S/4 numbers -> legacy number (migration log, else external ID on the delivery)
    mig = {}
    z = D["zmig"]
    for r in z[z.OBJTYPE == "LIKP"].itertuples():
        mig[r.NEW_KEY.zfill(10)] = r.OLD_KEY.zfill(10)
    lk = D["likp"]
    for r in lk[(lk.SYS == "S4") & (lk.LIFEX != "")].itertuples():
        mig.setdefault(r.VBELN, r.LIFEX.zfill(10))
    canon = lambda v: mig.get(v, v)
    lp = D["lips"].sort_values(["VBELN", "POSNR"]).drop_duplicates("VBELN")
    dwerks = dict(zip(lp.VBELN.map(canon), lp.WERKS))
    dl = lk.assign(C=lk.VBELN.map(canon)).sort_values("SYS").drop_duplicates("C")  # ECC original where available
    dfacts = {r.C: dict(vstel=r.VSTEL, shipto=r.KUNNR, gi=r.WADAT_IST, inco=r.INCO1, werks=dwerks.get(r.C, r.VSTEL)) for r in dl.itertuples()}
    ord2dl = {}
    for r in D["lips"].itertuples():
        if r.VGBEL:
            ord2dl.setdefault(r.VGBEL, canon(r.VBELN))
    vbak = D["vbak"].drop_duplicates("VBELN").set_index("VBELN")

    def delivery(r):
        if r.VGTYP == "J":
            return canon(r.VGBEL)
        if r.VGTYP == "C":
            return ord2dl.get(r.VGBEL, "")
        return ""
    b["DL"] = [delivery(r) for r in b.itertuples()]
    # original document of credit memos and cancellations (cross-system credits carry the legacy number in ZUONR)
    nums = set(b.VBELN)

    def orig(r):
        if r.VBTYP == "N":
            return r.SFAKN
        if r.VBTYP in ("O", "6"):
            if r.VGBEL and r.VGTYP in ("M", "5"):
                return r.VGBEL
            z_ = re.sub(r"\D", "", r.ZUONR).zfill(10)
            return z_ if z_ in nums else ""
        return ""
    b["ORIG"] = [orig(r) for r in b.itertuples()]
    root = dict(zip(b.VBELN, b.ORIG))

    def find_root(v):
        seen = 0
        while root.get(v) and seen < 5:
            v, seen = root[v], seen + 1
        return v
    b["ROOT"] = [find_root(v) for v in b.VBELN]
    # chain IDs: custom field, else the FI reference of the billing document (hand-typed variants)
    bk = D["bkpf"]
    ref = bk[(bk.AWTYP == "VBRK")].drop_duplicates("AWKEY")
    xb = dict(zip(ref.AWKEY, ref.XBLNR))
    b["ZZ"] = [chain_key(r.ZZCHAIN) or chain_key(xb.get(r.VBELN, "")) for r in b.itertuples()]
    rootrow = b.set_index("VBELN")
    b["RDL"] = [rootrow.DL.get(v, "") for v in b.ROOT]
    b["RZZ"] = [rootrow.ZZ.get(v, "") for v in b.ROOT]
    kna1 = D["kna1"]
    land = lambda k: kna1.LAND1.get(k, "")
    external = lambda k: k in kna1.index and kna1.KTOKD.get(k, "") != "KONZ"
    inv = b[b.VBTYP.isin(["M", "5"])]
    chains = {}

    def add_chain(key, g, dlv):
        f = dfacts.get(dlv)
        ic, cu = g[g.VBTYP == "5"], g[g.VBTYP == "M"]
        if f is None or not len(ic) or not len(cu):
            return
        a, bb = ic.BUKRS.iloc[0], cu.BUKRS.iloc[0]
        if a == bb or not external(f["shipto"]) or D["plant_bukrs"].get(f["werks"]) != a:
            return  # goods not shipped from the first supplier's own location directly to an external customer: no chain
        chains[key] = dict(key=key, A=a, B=bb, C=f["shipto"], dl=dlv, ic=list(ic.VBELN), cu=list(cu.VBELN), f=f)
    for dlv, g in inv[inv.DL != ""].groupby("DL"):
        add_chain(f"D{dlv}", g, dlv)
    for zz, g in inv[inv.ZZ != ""].groupby("ZZ"):
        dls = [d for d in g[g.VBTYP == "M"].DL if d]
        if dls and f"D{dls[0]}" not in chains:
            add_chain(zz, g, dls[0])
    member = {}
    for k, c in chains.items():
        for v in c["ic"]:
            member[v] = (k, 1)
        for v in c["cu"]:
            member[v] = (k, 2)
    for k, c in chains.items():
        legs = b[b.VBELN.isin(c["ic"] + c["cu"]) & ~b.VBTYP.isin(list(NEG))]
        l1 = legs[legs.VBTYP == "5"].iloc[0]
        l2 = legs[legs.VBTYP == "M"].iloc[0]
        i1 = l1.INCO1 or (vbak.INCO1.get(l1.AUBEL, "") if l1.AUBEL in vbak.index else "") or \
            (vbak.INCO1.get(l1.VGBEL, "") if l1.VGTYP == "C" and l1.VGBEL in vbak.index else "")
        i2 = l2.INCO1 or c["f"]["inco"]
        tby = "A" if i1 in SELLER_TERMS else "B" if i2 in SELLER_TERMS else "C"
        dep, arr = D["plant_land"].get(c["f"]["werks"], ""), land(c["C"])
        c_id = kna1.STCEG.get(c["C"], "") or l2.STCEG
        moving, treat = rules(dep, arr, tby, l1.STCEG, D["co_land"].get(c["B"], ""), bool(c_id) and c_id[:2] == arr)
        c.update(i1=i1, i2=i2, tby=tby, dep=dep, arr=arr, moving=moving, treat=treat, quoted=l1.STCEG)
    tre, mov, ck, lg = [], [], [], []
    for r in b.itertuples():
        m = member.get(r.ROOT)
        if m:
            c = chains[m[0]]
            tre.append(c["treat"][m[1]])
            mov.append(c["moving"] == m[1])
            ck.append(m[0])
            lg.append(m[1])
            continue
        f = dfacts.get(r.RDL)
        if f is None:
            tre.append(("", ""))
        else:
            dep, arr = D["plant_land"].get(f["werks"], ""), land(f["shipto"])
            tre.append(standalone(dep, arr))
        mov.append(False)
        ck.append("")
        lg.append(0)
    b["TREAT"], b["JURIS"] = [t for t, _ in tre], [j for _, j in tre]
    b["MOVING"], b["CHAIN"], b["LEG"] = mov, ck, lg
    b["SIGN"] = [-1 if v in NEG else 1 for v in b.VBTYP]
    b["NETC"] = [cents(x) for x in b.NETWK]
    b["EURC"] = [r.SIGN * to_eur(D, r.WAERK, r.NETC, r.FKDAT) for r in b.itertuples()]
    b["VATC"] = [q(Decimal(r.EURC) * RATE[r.JURIS]) if r.TREAT == "DOM_TAX" and r.JURIS in RATE else 0 for r in b.itertuples()]
    b["ARR"] = [chains[r.CHAIN]["arr"] if r.CHAIN else land(dfacts[r.RDL]["shipto"]) if r.RDL in dfacts else "" for r in b.itertuples()]
    b["FILED"] = [D["code_treat"].get((D["kalsm"].get(r.BUKRS, ""), r.MWSKZ), "") for r in b.itertuples()]
    return D, b, chains


def rules(dep, arr, tby, quoted, b_land, c_has_id):
    """Moving supply and treatment of both legs (tax position memo, section 3)."""
    qc = quoted[:2] if quoted[:2] in EU else ""
    moving = 1 if tby == "A" else 2 if tby == "C" else (2 if qc == dep else 1)
    if arr == dep:
        return moving, {1: ("DOM_TAX", dep), 2: ("DOM_TAX", dep)}
    if moving == 2:
        return moving, {1: ("DOM_TAX", dep), 2: ("ICS", dep) if arr in EU else ("EXPORT", dep)}
    t1 = (("ICS", dep) if qc and qc != dep else ("DOM_TAX", dep)) if arr in EU else ("EXPORT", dep)
    if arr in EU and qc and qc not in (dep, arr) and c_has_id:
        t2 = ("TRIANGULAR", qc)
    elif arr == b_land and arr in RATE:
        t2 = ("DOM_TAX", arr)
    else:
        t2 = ("DEST_SUPPLY", arr)
    return moving, {1: t1, 2: t2}


def standalone(dep, arr):
    return ("DOM_TAX", dep) if dep == arr else ("ICS", dep) if (dep in EU and arr in EU) else ("EXPORT", dep)


def register(b, chains, D):
    """Register rows: one per chain billing document."""
    tb = {"A": "FIRST_SUPPLIER", "B": "INTERMEDIARY", "C": "CUSTOMER"}
    rows = []
    for r in b[b.CHAIN != ""].sort_values(["CHAIN", "LEG", "FKDAT", "VBELN"]).itertuples():
        c = chains[r.CHAIN]
        rows.append(dict(CHAIN_ID=r.CHAIN, LEG=r.LEG, SELLER=r.BUKRS, BUYER=r.KUNAG, BILLING_DOC=r.VBELN,
                         DOC_TYPE="INVOICE" if r.VBTYP in ("M", "5") else "CREDIT" if r.VBTYP in ("O", "6") else "CANCEL",
                         BILLING_DATE=r.FKDAT, DELIVERY=c["dl"], SHIP_FROM=c["dep"], SHIP_TO=c["arr"],
                         INCOTERM=c["i1"] if r.LEG == 1 else c["i2"], TRANSPORT_BY=tb[c["tby"]], MOVING="Y" if r.MOVING else "N",
                         CORRECT_TREATMENT=r.TREAT, JURISDICTION=r.JURIS, NET_EUR=r.EURC / 100, VAT_EUR=r.VATC / 100,
                         FILED_TREATMENT=r.FILED, COMMENT="" if r.FILED in (r.TREAT, "") else f"posted {r.MWSKZ}: {r.FILED}"))
    return pd.DataFrame(rows, columns=COLS)


def main(sap, out):
    D, b, chains = build(sap)
    reg = register(b, chains, D)
    reg.to_csv(out, sep=";", index=False)
    print(f"{len(chains)} chains, {len(reg)} chain documents -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
