"""Write tables the way IT delivers them: SE16N list downloads, German/English CSV, Excel; split large tables by year."""
import io
import os
import re
import zipfile

import numpy as np
import pandas as pd

EXTRACT_DATE = "12.01.2026"
DATE_COLS = {"BUDAT", "BLDAT", "CPUDT", "AUGDT", "ZFBDT", "BEDAT", "AEDAT", "ERDAT", "BEGDA", "ENDDA", "GBDAT", "UDATE", "LAUFD",
             "ZALDT", "GLTGV", "GLTGB", "TRDAT", "FROM_DAT", "TO_DAT", "DATBI", "ERSDA", "DATLB"}
TIME_COLS = {"CPUTM", "UTIME", "LTIME"}
UMLAUT = [("Muenchen", "München"), ("Nuernberg", "Nürnberg"), ("Wuerzburg", "Würzburg"), ("Praezisions", "Präzisions"),
          ("Oberflaechen", "Oberflächen"), ("Industriestr.", "Industriestraße"), ("Bahnhofstr.", "Bahnhofstraße"),
          ("Hauptstr.", "Hauptstraße"), ("Robert-Bosch-Str.", "Robert-Bosch-Straße"), ("Oesterreich", "Österreich")]
# table -> (format, options). se16: pipe list download (cp1252, DD.MM.YYYY, 1.234,56-); csv_de: ';' + decimal comma;
# csv: ';' + decimal point; xlsx: Excel export. split: one file per fiscal year.
FORMATS = {
    "LFA1": ("se16", {}), "LFB1": ("se16", {}), "LFBK": ("se16", {}), "KNA1": ("se16", {}), "CDHDR": ("se16", {}),
    "CDPOS": ("se16", {}), "REGUH": ("se16", {}), "EKKO": ("se16", {}), "PA0001": ("se16", {}), "PA0002": ("se16", {}),
    "PA0009": ("se16", {}), "PA0105": ("se16", {}),
    "BSEG": ("csv_de", dict(split="GJAHR", mandt=True)), "REGUP": ("csv_de", {}), "ACDOCP": ("csv_de", dict(trailing_minus=True)),
    "BKPF": ("csv", dict(split="GJAHR")), "RBKP": ("csv", dict(mandt=True)), "MSEG": ("csv", dict(reorder=True)),
    "EKPO": ("csv", dict(reorder=True)),
    **{t: ("xlsx", {}) for t in ("T001", "SKA1", "SKAT", "CSKS", "CEPC", "T001W", "T024", "T052", "T003", "TBSL", "T005", "T007A",
                                "T042Z", "T077K", "TKA01", "T880", "T513S", "TVARVC", "AGR_USERS", "USR02")},
}


def fix_umlauts(s):
    for a, b in UMLAUT:
        s = s.replace(a, b)
    return s


def strs(df):
    """All columns as display strings (floats keep 2 decimals, MENGE 3)."""
    out = {}
    for c in df.columns:
        v = df[c]
        if v.dtype.kind == "f":
            dec = 3 if c in ("MENGE", "MSL") else 2
            out[c] = [("" if np.isnan(x) else f"{x:.{dec}f}") for x in v.values]
        else:
            out[c] = ["" if (x is None or (isinstance(x, float) and np.isnan(x))) else str(x) for x in v.values]
    return pd.DataFrame(out, columns=df.columns)


def de_num(s, thousands=False, trailing=False):
    """'-1234.56' -> '1.234,56-' (thousands, trailing minus) or '-1234,56'."""
    if s == "":
        return s
    neg = s.startswith("-")
    a = s.lstrip("-")
    i, _, d = a.partition(".")
    if thousands:
        i = f"{int(i):,}".replace(",", ".")
    r = i + ("," + d if d else "")
    return (r + "-" if trailing else "-" + r) if neg else r


def de_date(s):
    return f"{s[6:8]}.{s[4:6]}.{s[:4]}" if re.fullmatch(r"\d{8}", s) else s


def write_se16(df, path, name):
    d = strs(df)
    d.insert(0, "MANDT", "100")
    num = [c for c in df.columns if df[c].dtype.kind == "f"]
    for c in d.columns:
        if c in DATE_COLS:
            d[c] = [de_date(x) for x in d[c]]
        elif c in TIME_COLS:
            d[c] = [f"{x[:2]}:{x[2:4]}:{x[4:6]}" if re.fullmatch(r"\d{6}", x) else x for x in d[c]]
        elif c in num:
            d[c] = [de_num(x, thousands=True, trailing=True) for x in d[c]]
        elif c in ("NAME1", "ORT01", "STRAS", "ZNME1", "KOINH"):
            d[c] = [fix_umlauts(x) for x in d[c]]
    w = [max(len(c), max((len(x) for x in d[c]), default=0)) for c in d.columns]
    just = [str.rjust if c in num else str.ljust for c in d.columns]
    line = lambda vals: "|" + "|".join(j(v, n) for v, n, j in zip(vals, w, just)) + "|"
    head = "|" + "|".join(c.ljust(n) for c, n in zip(d.columns, w)) + "|"
    sep = "-" * len(head)
    rows = [line(r) for r in d.itertuples(index=False)]
    page, out = 1000, io.StringIO()
    for p, i in enumerate(range(0, max(len(rows), 1), page), 1):
        title = f"{EXTRACT_DATE}" + "Dynamische Listenausgabe".center(max(len(head) - 20, 30)) + f"{p:>10}"
        out.write(f"{title}\n\nTabelle:          {name}\nAngezeigte Felder: {len(d.columns)} von {len(d.columns)}  "
                  f"Feststehende Führungsspalten: 2  Listbreite {len(head):04d}\n\n{sep}\n{head}\n{sep}\n")
        out.write("\n".join(rows[i:i + page]) + ("\n" if rows[i:i + page] else ""))
        out.write(f"{sep}\n\n")
    with open(path, "w", encoding="cp1252", newline="\r\n") as f:
        f.write(out.getvalue())


def write_csv(df, path, german=False, mandt=False, reorder=False, trailing_minus=False):
    d = strs(df)
    if german:
        for c in df.columns:
            if df[c].dtype.kind == "f":
                d[c] = [de_num(x, trailing=trailing_minus) for x in d[c]]
    if reorder:
        key = list(d.columns[:2])
        d = d[key + sorted(c for c in d.columns if c not in key)]
    if mandt:
        d.insert(0, "MANDT", "100")
    d.to_csv(path, sep=";", index=False, lineterminator="\n", encoding="utf-8-sig" if german else "utf-8")


def write_xlsx(df, path, name):
    from datetime import datetime
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = name
    ws.append(list(df.columns))
    for c in ws[1]:
        c.font = Font(bold=True)
    d = strs(df)
    for r_df, r in zip(df.itertuples(index=False), d.itertuples(index=False)):
        row = []
        for c, raw, s in zip(df.columns, r_df, r):
            if c in DATE_COLS and re.fullmatch(r"\d{8}", s):
                row.append(datetime(int(s[:4]), int(s[4:6]), int(s[6:])))
            elif df[c].dtype.kind == "f" and s != "":
                row.append(float(raw))
            else:
                row.append(s)
        ws.append(row)
    for col in ws.iter_cols(min_row=2):
        for cell in col:
            if isinstance(cell.value, datetime):
                cell.number_format = "DD.MM.YYYY"
    save_xlsx(wb, path)


def save_xlsx(wb, path):
    """Save with fixed zip timestamps and document properties (byte-reproducible)."""
    from datetime import datetime
    wb.properties.created = wb.properties.modified = datetime(2026, 1, 12, 7, 30, 0)
    wb.properties.creator = wb.properties.lastModifiedBy = "Rotkehl IT"
    buf = io.BytesIO()
    wb.save(buf)
    zin = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in sorted(zin.infolist(), key=lambda i: zin.namelist().index(i.filename)):
            data = zin.read(it.filename)
            if it.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-01-12T07:30:00Z", data)
            zi = zipfile.ZipInfo(it.filename, date_time=(2026, 1, 12, 7, 30, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)


def write_tables(tables, sap, descr):
    """Write every table in its delivery format; returns README file list lines."""
    files = []
    for name in sorted(tables):
        df = tables[name]
        fmt, opt = FORMATS.get(name, ("csv", {}))
        parts = [(f"_{y}", df[df[opt["split"]] == y]) for y in sorted(df[opt["split"]].unique())] if "split" in opt else [("", df)]
        for suf, part in parts:
            part = part.reset_index(drop=True)
            if fmt == "se16":
                fn = f"{name}{suf}.txt"
                write_se16(part, os.path.join(sap, fn), name)
            elif fmt == "xlsx":
                fn = f"{name}{suf}.xlsx"
                write_xlsx(part, os.path.join(sap, fn), name)
            else:
                fn = f"{name}{suf}.csv"
                write_csv(part, os.path.join(sap, fn), german=fmt == "csv_de", mandt=opt.get("mandt", False),
                          reorder=opt.get("reorder", False), trailing_minus=opt.get("trailing_minus", False))
            files.append(f"  {fn:<22}{name:<10}{descr.get(name, '')}")
    return files
