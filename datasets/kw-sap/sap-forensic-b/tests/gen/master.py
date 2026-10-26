"""Master data: company codes, GL, employees, vendors, materials, FX."""
import csv
import os
import unicodedata

import numpy as np
import pandas as pd

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed")

BUKRS = {
    "AT01": dict(name="Rotkehl Fahrradwerke Austria GmbH", city="Wien", land="AT", werks="AT10", tax=0.20, mwskz="V2"),
    "DE01": dict(name="Rotkehl Fahrradwerke GmbH", city="Schweinfurt", land="DE", werks="DE10", tax=0.19, mwskz="V1"),
    "SK01": dict(name="Rotkehl Bicycles Slovakia s.r.o.", city="Trnava", land="SK", werks="SK10", tax=0.20, mwskz="V3"),
}
COM_GL = ["0000400000", "0000400100", "0000403000", "0000405000"]  # cost of materials
GL = [  # SAKNR, XBILK, DE text, EN text
    ("0000021000", "X", "Technische Anlagen und Maschinen", "Plant and machinery"),
    ("0000113100", "X", "Bank Hauptkonto", "Bank main account"),
    ("0000140000", "X", "Forderungen aus Lieferungen und Leistungen", "Trade receivables"),
    ("0000145000", "X", "Rueckforderungsansprueche gegen Lieferanten", "Claims against vendors"),
    ("0000146000", "X", "Forderungen gegen Mitarbeiter", "Receivables from employees"),
    ("0000150000", "X", "Sonstige Vermoegensgegenstaende", "Other assets"),
    ("0000154000", "X", "Vorsteuer", "Input tax"),
    ("0000160000", "X", "Verbindlichkeiten aus Lieferungen und Leistungen", "Trade payables"),
    ("0000175000", "X", "Umsatzsteuer", "Output tax"),
    ("0000176000", "X", "Verbindlichkeiten Lohn und Gehalt", "Payroll liabilities"),
    ("0000191100", "X", "WE/RE-Verrechnung", "GR/IR clearing"),
    ("0000230000", "", "Kursverluste realisiert", "Realized FX losses"),
    ("0000231000", "", "Zahlungsdifferenzen", "Payment differences"),
    ("0000257000", "", "Aufwand aus Schadensfaellen", "Losses from fraud and damage cases"),
    ("0000258000", "", "Aufwand Zufuehrung Rueckstellungen", "Additions to provisions"),
    ("0000299000", "X", "Sonstige Rueckstellungen", "Other provisions"),
    ("0000280000", "", "Kursgewinne realisiert", "Realized FX gains"),
    ("0000400000", "", "Materialaufwand Rohstoffe", "Cost of materials - raw materials"),
    ("0000400100", "", "Materialaufwand Zukaufteile", "Cost of materials - purchased parts"),
    ("0000403000", "", "Materialaufwand Hilfs- und Betriebsstoffe", "Cost of materials - consumables"),
    ("0000405000", "", "Materialaufwand Verpackung", "Cost of materials - packaging"),
    ("0000417000", "", "Fremdleistungen", "Purchased services"),
    ("0000430000", "", "Loehne", "Wages"),
    ("0000435000", "", "Gehaelter", "Salaries"),
    ("0000470000", "", "Miete und Leasing", "Rent and leasing"),
    ("0000472000", "", "Energie", "Energy"),
    ("0000474000", "", "Reisekosten Mitarbeiter", "Employee travel expenses"),
    ("0000475000", "", "IT-Kosten", "IT costs"),
    ("0000476000", "", "Ausgangsfrachten", "Outbound freight"),
    ("0000800000", "", "Umsatzerloese", "Revenue"),
    ("0000800100", "", "Umsatzerloese verbundene Unternehmen", "Intercompany revenue"),
]
BRANCHES = ["Metallbau", "Kunststofftechnik", "Antriebstechnik", "Industriebedarf", "Verpackungen", "Stahlhandel",
            "Elektronik", "Komponenten", "Zweiradteile", "Praezisionsteile", "Oberflaechentechnik", "Gummiwerke",
            "Lagertechnik", "Kabelwerk", "Aluminiumguss", "Schraubenwerk", "Logistik", "Spedition", "Haustechnik",
            "IT-Services", "Energievertrieb", "Facility Services", "Werkzeugbau", "Lackierwerk", "Batterietechnik"]
LEGAL = {"DE": ["GmbH", "GmbH & Co. KG", "AG", "KG"], "AT": ["GmbH", "Ges.m.b.H.", "AG"], "SK": ["s.r.o.", "a.s."],
         "CZ": ["s.r.o.", "a.s."], "IT": ["S.r.l.", "S.p.A."], "NL": ["B.V."], "US": ["Inc.", "LLC", "Corp."]}
CITIES = {"DE": ["Muenchen", "Stuttgart", "Nuernberg", "Leipzig", "Dortmund", "Bielefeld", "Augsburg", "Wuerzburg", "Kassel"],
          "AT": ["Wien", "Linz", "Graz", "Salzburg", "Wels", "Steyr"], "SK": ["Bratislava", "Trnava", "Zilina", "Nitra"],
          "CZ": ["Brno", "Ostrava", "Plzen", "Praha"], "IT": ["Milano", "Brescia", "Vicenza"], "NL": ["Eindhoven", "Utrecht"],
          "US": ["Chicago", "Portland", "Denver", "Austin"]}
COMPONENTS = [  # suffix, text, share of bike price, mtart, GL
    ("FRM", "Rahmen", 0.28, "HALB", "0000400100"), ("FRK", "Federgabel", 0.12, "HALB", "0000400100"),
    ("WHL", "Laufradsatz", 0.10, "HALB", "0000400100"), ("DRV", "Schaltgruppe", 0.09, "HALB", "0000400100"),
    ("BRK", "Bremsanlage", 0.05, "HALB", "0000400100"), ("SAD", "Sattel", 0.015, "HALB", "0000400100"),
    ("HBR", "Lenker", 0.012, "HALB", "0000400100"), ("TYR", "Reifen", 0.01, "HALB", "0000400100"),
]
RAW = [("ROH-AL6061", "Aluminiumrohr 6061 (kg)", 6.2, "KG", "0000400000"), ("ROH-AL7005", "Aluminiumrohr 7005 (kg)", 7.4, "KG", "0000400000"),
       ("ROH-CRMO", "CrMo-Stahlrohr (kg)", 4.1, "KG", "0000400000"), ("ROH-CFK", "Carbon-Prepreg (m2)", 38.0, "M2", "0000400000"),
       ("ROH-PULV", "Pulverlack (kg)", 9.5, "KG", "0000400000"), ("ROH-GUMMI", "Gummimischung (kg)", 3.2, "KG", "0000400000"),
       ("ROH-ZELL", "Li-Ion Zellen 21700", 3.9, "ST", "0000400000"), ("ROH-CU", "Kupferdraht (kg)", 11.0, "KG", "0000400000")]
SMALL = [("HIB-SCHR-M5", "Schraube M5x12 Edelstahl", 0.06), ("HIB-SCHR-M6", "Schraube M6x16 Edelstahl", 0.08),
         ("HIB-MUT-M5", "Mutter M5 selbstsichernd", 0.04), ("HIB-KABEL", "Kabelbinder 200mm", 0.03),
         ("HIB-FETT", "Lagerfett (Tube)", 2.4), ("HIB-KLEB", "Schraubensicherung (Flasche)", 6.8),
         ("HIB-HANDS", "Arbeitshandschuhe (Paar)", 1.9), ("VERP-KART", "Versandkarton Fahrrad", 4.6),
         ("VERP-FOLIE", "Stretchfolie (Rolle)", 14.0), ("VERP-SCHAUM", "Schaumschutz-Set", 2.2)]
EMP_ROLES = [  # STELL, text, per company
    ("50000101", "Sachbearbeiter Kreditorenbuchhaltung", {"DE01": 9, "AT01": 4, "SK01": 4}),
    ("50000102", "Teamleiter Kreditorenbuchhaltung", {"DE01": 1, "AT01": 1, "SK01": 1}),
    ("50000110", "Stammdatenmanagement", {"DE01": 3, "AT01": 0, "SK01": 0}),
    ("50000120", "Einkaeufer", {"DE01": 10, "AT01": 4, "SK01": 4}),
    ("50000121", "Leiter Einkauf", {"DE01": 1, "AT01": 1, "SK01": 0}),
    ("50000130", "Lagerist Wareneingang", {"DE01": 8, "AT01": 4, "SK01": 5}),
    ("50000140", "Controller", {"DE01": 4, "AT01": 2, "SK01": 1}),
    ("50000150", "Treasury", {"DE01": 2, "AT01": 0, "SK01": 0}),
    ("50000160", "Werksleiter", {"DE01": 1, "AT01": 1, "SK01": 1}),
    ("50000170", "CFO", {"DE01": 1, "AT01": 0, "SK01": 0}),
    ("50000180", "Produktionsmitarbeiter", {"DE01": 230, "AT01": 120, "SK01": 110}),
    ("50000190", "Vertrieb", {"DE01": 20, "AT01": 10, "SK01": 4}),
    ("50000175", "Leiter Interne Revision", {"DE01": 1, "AT01": 0, "SK01": 0}),
]
BANKS = {"DE": ["50010517", "37040044", "70020270", "60050101", "10070000", "20040000", "76026000"],
         "AT": ["12000", "20111", "32000", "14000", "60000"], "SK": ["1100", "0900", "0200", "7500"],
         "CZ": ["0800", "0100", "2010"], "IT": ["0306909606"], "NL": ["ABNA", "INGB", "RABO"], "US": []}


def ascii_up(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def iban(land, bban):
    """Valid IBAN (mod-97 check digits) for a country BBAN."""
    num = "".join(str(int(c, 36)) for c in (bban + land + "00"))
    return f"{land}{98 - int(num) % 97:02d}{bban}"


def rand_iban(rng, land):
    bank = rng.choice(BANKS[land]) if BANKS.get(land) else ""
    if land == "DE":
        return iban("DE", bank + f"{rng.integers(10**9, 10**10):010d}"), bank
    if land == "AT":
        return iban("AT", bank + f"{rng.integers(10**10, 10**11):011d}"), bank
    if land in ("SK", "CZ"):
        return iban(land, bank + f"{rng.integers(0, 10**6):06d}" + f"{rng.integers(10**9, 10**10):010d}"), bank
    if land == "IT":
        return iban("IT", "X" + bank[:5] + bank[5:10] + f"{rng.integers(10**11, 10**12):012d}"), bank[:5]
    if land == "NL":
        return iban("NL", bank + f"{rng.integers(10**9, 10**10):010d}"), bank
    return "", f"{rng.integers(10**8, 10**9)}"  # US: ABA routing, no IBAN


def fmt_iban(s, style):
    """Render an IBAN in a given human format (0 compact, 1 spaced, 2 spaced lower, 3 compact lower)."""
    spaced = " ".join(s[i:i + 4] for i in range(0, len(s), 4))
    return [s, spaced, spaced.lower(), s.lower()][style]


def read_seed(name, sep=","):
    with open(os.path.join(SEED_DIR, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=sep))


def employees(rng):
    """PA0001/PA0002/PA0105/PA0009 frames plus a lookup frame `emp`."""
    names = read_seed("EmployeePersonalData.csv", ";")
    idx = rng.permutation(len(names))
    rows, k = [], 0
    for stell, stltx, per in EMP_ROLES:
        for bukrs, n in per.items():
            for _ in range(n):
                p = names[idx[k % len(names)]]
                k += 1
                rows.append(dict(PERNR=f"{10000 + k:08d}", BUKRS=bukrs, STELL=stell, STLTX=stltx,
                                 VORNA=p["FIRSTNAME"], NACHN=p["LASTNAME"], GESCH="1" if p["GENDER"] == "M" else "2",
                                 GBDAT=f"{int(p['YEAROFBIRTH']) + 20}{rng.integers(1, 13):02d}{rng.integers(1, 29):02d}"))
    emp = pd.DataFrame(rows)
    users, seen = [], set()
    for v, n in zip(emp.VORNA, emp.NACHN):
        u = (ascii_up(v)[:1] + "".join(c for c in ascii_up(n) if c.isalpha()))[:12]
        base, i = u, 1
        while u in seen:
            i += 1
            u = f"{base[:11]}{i}"
        seen.add(u)
        users.append(u)
    emp["USRID"] = users
    emp["BEGDA"] = [f"{y}{m:02d}01" for y, m in zip(rng.integers(2008, 2024, len(emp)), rng.integers(1, 13, len(emp)))]
    ib = [rand_iban(rng, BUKRS[b]["land"]) for b in emp.BUKRS]
    emp["IBAN"] = [i for i, _ in ib]
    emp["BANKL"] = [b for _, b in ib]
    emp["IBAN_FMT"] = [fmt_iban(i, s) for i, s in zip(emp.IBAN, rng.choice([0, 0, 0, 1], len(emp)))]
    return emp


def materials(rng):
    """MARA/MAKT-like frame with EUR base prices and GL."""
    prods = {r["PRODUCTID"]: r for r in read_seed("Products.csv")}
    texts = {r["PRODUCTID"]: r["SHORT_DESCR"] for r in read_seed("ProductTexts.csv")}
    rows = []
    for pid, p in prods.items():
        price = float(p["PRICE"]) / 1.08
        for suf, txt, share, mtart, gl in COMPONENTS:
            rows.append(dict(MATNR=f"{pid}-{suf}", MAKTX=f"{txt} {texts.get(pid, pid)}", MTART=mtart, MEINS="ST",
                             PRICE=round(price * share * rng.uniform(0.85, 1.15), 2), GL=gl, MATKL=f"BT-{suf}"))
    for m, t, pr, un, gl in RAW:
        rows.append(dict(MATNR=m, MAKTX=t, MTART="ROH", MEINS=un, PRICE=pr, GL=gl, MATKL=f"ROH-{un}"))
    for m, t, pr in SMALL:
        rows.append(dict(MATNR=m, MAKTX=t, MTART="VERP" if m.startswith("VERP") else "HIBE", MEINS="ST", PRICE=pr,
                         GL="0000405000" if m.startswith("VERP") else "0000403000", MATKL="VERP" if m.startswith("VERP") else "HIBE"))
    mat = pd.DataFrame(rows)
    mat["PEINH"] = np.where(mat.PRICE < 1, 100, 1)
    return mat


def vendor_names(rng, n, lands, surnames, used):
    out = []
    for land in lands:
        while True:
            nm = f"{rng.choice(surnames)} {rng.choice(BRANCHES)} {rng.choice(LEGAL[land])}"
            if nm not in used:
                used.add(nm)
                out.append(nm)
                break
    return out


def fx_rates(rng, plan_usd, h2_usd):
    """Daily EUR rates (business days 2023-12..2025-12): EUR per 1 USD / per 1 CZK."""
    days = pd.bdate_range("2023-12-01", "2025-12-31")
    n = len(days)
    t = np.arange(n)
    # EUR/USD (USD per EUR): around plan in 2024/H1-2025, drifting to h2 level in H2 2025
    drift = np.where(days < "2025-05-01", plan_usd, plan_usd + (h2_usd - plan_usd) * np.clip((t - np.searchsorted(days, pd.Timestamp("2025-05-01"))) / 60, 0, 1))
    usd = drift + np.cumsum(rng.normal(0, 0.0025, n)) * 0.6
    usd = usd - (usd - drift) * 0.8  # mean-revert noise
    czk = 25.0 + np.cumsum(rng.normal(0, 0.03, n)) * 0.3
    return pd.DataFrame({"date": days, "EUR_PER_USD": np.round(1 / usd, 5), "EUR_PER_CZK": np.round(1 / czk, 6)})
