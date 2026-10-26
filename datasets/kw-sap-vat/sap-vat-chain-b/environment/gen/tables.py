"""SAP tables of the legacy ECC system (2021-06/2022) and S/4HANA (from 07/2022), written the way IT delivers them."""
import os
from datetime import date, timedelta

import pandas as pd

import export
from gen import BIKES, CO, COMPONENTS, CUTOVER, TAXCODES, WIEN, add_bd, kalsm, m_rate

export.DATE_COLS |= {"FKDAT", "AUDAT", "WADAT_IST", "LFDAT", "MIG_DATE"}
export.EXTRACT_DATE = "16.01.2026"
VKORG = {"DE01": "DE10", "AT01": "AT10", "CH01": "CH10", "HK01": "HK10"}
PLANT_LAND = {"DE10": "DE", "AT10": "AT", "CH10": "CH"}
MAT = {m: t for m, t, _ in BIKES + COMPONENTS + WIEN}
IC_KUNNR = {CO[b]["kunnr"] for b in CO if CO[b]["kunnr"]} | {CO[b]["ship"] for b in CO if CO[b].get("ship")}


def ymd(d):
    return int(d.strftime("%Y%m%d")) if d else ""


def s4(d):
    return d >= CUTOVER


# ---------- numbering ----------
def number(W, P):
    """Document numbers per system; migrated orders/deliveries get a second (S/4) number."""
    bills = sorted(W["bills"], key=lambda b: (b["fkdat"], b["id"]))
    ne = ns = 0
    for b in bills:
        if s4(b["fkdat"]):
            ns += 1
            b["vbeln"], b["sys"] = f"{95000000 + ns:010d}", "S4"
        else:
            ne += 1
            b["vbeln"], b["sys"] = f"{90000000 + ne:010d}", "ECC"
    by = {b["id"]: b for b in W["bills"]}
    dsys = {}
    for b in W["bills"]:
        if b["ref"] and b["ref"][0] == "J":
            dsys.setdefault(b["ref"][1], set()).add(b["sys"])
    ne = ns = nm = 0
    for d in sorted(W["dels"], key=lambda x: (x["gi"], x["id"])):
        if s4(d["gi"]):
            ns += 1
            d["vbeln"], d["sys"], d["new"] = f"{81000000 + ns:010d}", "S4", None
        else:
            ne += 1
            d["vbeln"], d["sys"], d["new"] = f"{80000000 + ne:010d}", "ECC", None
            if "S4" in dsys.get(d["id"], ()):
                nm += 1
                d["new"] = f"{84000000 + nm:010d}"
    dl_by = {d["id"]: d for d in W["dels"]}
    first_gi = {}
    for d in W["dels"]:
        first_gi[d["order"]] = min(first_gi.get(d["order"], d["gi"]), d["gi"])
    for b in W["bills"]:
        if b["ref"] and b["ref"][0] == "C":
            first_gi.setdefault(b["ref"][1], b["fkdat"])
    ne = ns = nm = 0
    for o in sorted(W["orders"], key=lambda x: (x["odate"], x["id"])):
        if s4(o["odate"]):
            ns += 1
            o["vbeln"], o["sys"], o["new"] = f"{10000000 + ns:010d}", "S4", None
        else:
            ne += 1
            o["vbeln"], o["sys"], o["new"] = f"{100000 + ne:010d}", "ECC", None
            if s4(first_gi.get(o["id"], o["odate"])):
                nm += 1
                o["new"] = f"{19000000 + nm:010d}"
    W["dl_by"], W["bill_by"], W["ord_by"] = dl_by, by, {o["id"]: o for o in W["orders"]}
    # FI document numbers (ECC: billing number = FI number; S/4: own ranges per company code and year)
    cnt = {}

    def nxt(sys_, bukrs, year, base):
        k = (sys_, bukrs, year, base)
        cnt[k] = cnt.get(k, 0) + 1
        return f"{base + cnt[k]:010d}"

    docs = [("bill", b, b["fkdat"]) for b in W["bills"]] + [("fi", f, f["budat"]) for f in W["fi"]] + [("vend", v, v["budat"]) for v in W["vend"]]
    docs.sort(key=lambda x: (x[2], x[1]["id"]))
    for kind, r, d in docs:
        sys_ = "S4" if s4(d) else "ECC"
        r["fsys"], r["gjahr"] = sys_, d.year
        if kind == "bill":
            r["belnr"] = r["vbeln"] if sys_ == "ECC" else nxt(sys_, r["bukrs"], d.year, 1400000000)
        elif kind == "fi":
            r["belnr"] = nxt(sys_, r["bukrs"], d.year, 99000000 if r["kind"] == "zm" else 1800000000)
            if r["kind"] == "zm":
                r["fsys"] = "S4"
        else:
            r["belnr"] = nxt(sys_, r["bukrs"], d.year, 5100000000 if r["kind"] == "ic" else 1900000000)
    for v in W["vend"]:
        if v["kind"] == "ic":
            src = by[v["src"]]
            v["xblnr"] = src["vbeln"].lstrip("0") if v["id"] % 3 else src["vbeln"]
    for f in W["fi"]:
        if f["kind"] in ("zm", "repost"):
            old = by[f["src"]]
            f["old"] = old
            v = int(old["vbeln"])
            if f["kind"] == "zm":
                f["xblnr"] = "" if f["noref"] else old["vbeln"]
                f["sgtxt"] = "OP-Übernahme Altsystem" if f["noref"] else f"OP {old['bukrs']} {old['vbeln']}"
            else:
                f["xblnr"] = [f"{v}", f"RE {v}", f"{v:010d}", f"ECC {v}", f"Re.-Nr. {v}"][f["fmt"]]
                f["sgtxt"] = f"Rechnung {v} (Altsystem)" if f["fmt"] in (0, 2) else "Erlöse Fahrräder"


# ---------- table builders ----------
def chain_ref(b, rng_):
    """Reference text: S/4 cockpit documents carry the chain ID, manual ones a hand-typed variant."""
    if not b["zzchain"] and not b["manual"]:
        return b["vbeln"]
    zz = b["zzchain"] or b["_zz"]
    y, n = zz.split("-")[1], int(zz.split("-")[2])
    if not b["manual"]:
        return zz
    return [f"RKC{y}{n:06d}", f"rkc-{y}-{n:06d}", f"RKC-{y}-{n}", f"RKC {y} {n:06d}"][b["id"] % 4]


def sd_tables(W, P, sys_):
    ords, dls, bls = W["ord_by"], W["dl_by"], W["bill_by"]
    vbak, vbap, likp, lips, vbrk, vbrp, vbfa = [], [], [], [], [], [], []

    def onum(o):
        if o is None:
            return ""
        return o["vbeln"] if sys_ == o["sys"] else (o["new"] or "") if sys_ == "S4" else ""

    def dnum(d):
        return d["vbeln"] if sys_ == d["sys"] else (d["new"] or "") if sys_ == "S4" else ""

    for o in sorted(W["orders"], key=lambda x: x["id"]):
        num = onum(o)
        if not num:
            continue
        mig = sys_ == "S4" and o["sys"] == "ECC"
        r = dict(VBELN=num, AUART=o["auart"], VBTYP="C", AUDAT=ymd(o["odate"]), ERDAT=ymd(CUTOVER if mig else o["odate"]),
                 ERNAM="MIGRATION" if mig else ("CHAIN_COCKPIT" if o["auart"] == "ZKO" else f"SD_{o['bukrs'][:2]}{o['id'] % 7 + 1:02d}"),
                 VKORG=VKORG[o["bukrs"]], VTWEG="10", SPART="00", KUNNR=o["kunnr"], BSTNK=o["bstnk"], BUKRS_VF=o["bukrs"],
                 WAERK=o["waerk"], INCO1=o["inco1"], INCO2=o["inco2"])
        if sys_ == "S4":
            r["ZZCHAIN"] = o["zzchain"]
        vbak.append(r)
        for i, (m, qty) in enumerate(o["items"], 1):
            vbap.append(dict(VBELN=num, POSNR=f"{i * 10:06d}", MATNR=m, ARKTX=MAT[m], KWMENG=float(qty), VRKME="ST", WERKS=o["plant"]))
    for d in sorted(W["dels"], key=lambda x: x["id"]):
        num = dnum(d)
        if not num:
            continue
        o = ords[d["order"]]
        mig = sys_ == "S4" and d["sys"] == "ECC"
        run = W["migrun"].get(("LIKP", d["id"]), 0) if mig else 0
        likp.append(dict(VBELN=num, LFART=d["lfart"], VSTEL=d["vstel"], VKORG=VKORG[o["bukrs"]], KUNNR=d["kunnr"], KUNAG=d["kunag"],
                         WADAT_IST=ymd(d["gi"]), LFDAT=ymd(d["gi"]), INCO1=d["inco1"], INCO2=d["inco2"],
                         ROUTE=f"R{d['vstel'][:2]}{PLANT_LAND.get(d['vstel'], 'XX')}", LIFEX=d["vbeln"].lstrip("0") if run >= 2 else "",
                         ERDAT=ymd(CUTOVER if mig else d["gi"])))
        on = onum(o)
        for i, (m, qty) in enumerate(d["items"], 1):
            lips.append(dict(VBELN=num, POSNR=f"{i * 10:06d}", MATNR=m, ARKTX=MAT[m], LFIMG=float(qty), VRKME="ST", WERKS=d["vstel"],
                             VGBEL=on, VGPOS=f"{i * 10:06d}" if on else ""))
            if on and (not mig or run in (1, 2)):
                vbfa.append(dict(VBELV=on, POSNV=f"{i * 10:06d}", VBTYP_V="C", VBELN=num, POSNN=f"{i * 10:06d}", VBTYP_N="J",
                                 RFMNG=float(qty), ERDAT=ymd(CUTOVER if mig else d["gi"])))
    for b in sorted(W["bills"], key=lambda x: x["id"]):
        if b["sys"] != sys_:
            continue
        o = ords.get(b["aubel"]) if b["aubel"] else None
        ref, vgbel, vgtyp, zuonr = b["ref"], "", "", ""
        if ref:
            if ref[0] == "J":
                vgbel, vgtyp = dnum(dls[ref[1]]), "J"
            elif ref[0] == "C":
                vgbel, vgtyp = onum(ords[ref[1]]), "C"
            else:
                tb = bls[ref[1]]
                if tb["sys"] == sys_:
                    vgbel, vgtyp = tb["vbeln"], tb["vbtyp"]
                else:
                    zuonr = tb["vbeln"].lstrip("0")
        sfakn = bls[b["orig"]]["vbeln"] if b["kind"] == "cancel" else ""
        kurrf = "1.00000" if b["waerk"] in ("EUR", CO[b["bukrs"]]["waers"]) else f"{m_rate(W['F'], b['waerk'], b['fkdat']):.5f}"
        r = dict(VBELN=b["vbeln"], FKART=b["fkart"], VBTYP=b["vbtyp"], WAERK=b["waerk"], VKORG=VKORG[b["bukrs"]], VTWEG="10",
                 FKDAT=ymd(b["fkdat"]), BUKRS=b["bukrs"], KURRF=kurrf, NETWK=b["net"] / 100, MWSBK=b["fwste"] / 100,
                 KUNAG=b["kunag"], KUNRG=b["kunag"], INCO1=b["inco1"], INCO2=b["inco2"], LAND1=b["land1"], STCEG=b["stceg"],
                 XBLNR=b["vbeln"] if sys_ == "ECC" else "", ZUONR=zuonr, FKSTO="X" if b["cancelled"] else "", SFAKN=sfakn,
                 ERNAM="VF_BATCH" if not b["manual"] else f"SD_{b['bukrs'][:2]}0{b['id'] % 5 + 1}", ERDAT=ymd(b["cpudt"]), RFBSK="C")
        if sys_ == "S4":
            r["ZZCHAIN"] = b["zzchain"]
        vbrk.append(r)
        rate = TAXCODES[(kalsm(b["bukrs"]), b["code"])][2]
        for i, (m, qty, net) in enumerate(b["items"], 1):
            vbrp.append(dict(VBELN=b["vbeln"], POSNR=f"{i * 10:06d}", FKIMG=float(qty), VRKME="ST", MATNR=m, ARKTX=MAT[m],
                             NETWR=net / 100, MWSBP=round(net * float(rate) / 100) / 100, MWSKZ=b["code"],
                             WERKS=(dls[ref[1]]["vstel"] if ref and ref[0] == "J" else "DE10" if b["bukrs"] == "DE01" else CO[b["bukrs"]]["plant"]),
                             VGBEL=vgbel, VGPOS=f"{i * 10:06d}" if vgbel else "", VGTYP=vgtyp, AUBEL=onum(o) if o else "",
                             AUPOS=f"{i * 10:06d}" if o and onum(o) else ""))
            if vgbel and vgtyp in ("J", "C", "M", "5"):
                vbfa.append(dict(VBELV=vgbel, POSNV=f"{i * 10:06d}", VBTYP_V=vgtyp, VBELN=b["vbeln"], POSNN=f"{i * 10:06d}",
                                 VBTYP_N=b["vbtyp"], RFMNG=float(qty), ERDAT=ymd(b["cpudt"])))
    out = dict(VBAK=pd.DataFrame(vbak), VBAP=pd.DataFrame(vbap), LIKP=pd.DataFrame(likp), LIPS=pd.DataFrame(lips),
               VBRK=pd.DataFrame(vbrk), VBRP=pd.DataFrame(vbrp), VBFA=pd.DataFrame(vbfa))
    return out


def fi_tables(W, P, sys_):
    """BKPF + line items (ECC: BSEG, S/4: ACDOCA) + BSET."""
    F = W["F"]
    hdr, lines, bset = [], [], []

    def kursf(ccy, bukrs, d):
        return 1.0 if ccy in ("EUR", CO[bukrs]["waers"]) else float(m_rate(F, ccy, d))

    def put(r, blart, tcode, d, ccy, xblnr, bktxt, awtyp, awkey, items, cpudt=None, usnam="FI_BATCH"):
        """items: [(koart, shkzg, hkont, dm, wr, mwskz, ktosl, kunnr, lifnr, matnr, zuonr, sgtxt)]"""
        k = kursf(ccy, r["bukrs"], d)
        hdr.append(dict(BUKRS=r["bukrs"], BELNR=r["belnr"], GJAHR=d.year, BLART=blart, BLDAT=ymd(d), BUDAT=ymd(d), MONAT=d.month,
                        CPUDT=ymd(cpudt or d), CPUTM=f"{8 + r['id'] % 10:02d}{r['id'] * 7 % 60:02d}{r['id'] * 13 % 60:02d}", USNAM=usnam,
                        TCODE=tcode, XBLNR=xblnr, BKTXT=bktxt, WAERS=ccy, KURSF=f"{k:.5f}", TXKRS=r.get("txkrs", ""),
                        AWTYP=awtyp, AWKEY=awkey, STBLG=""))
        for n, (koart, sh, hk, dm, wr, mw, kt, ku, li, ma, zu, sg) in enumerate(items, 1):
            if sys_ == "ECC":
                lines.append(dict(BUKRS=r["bukrs"], BELNR=r["belnr"], GJAHR=d.year, BUZEI=f"{n:03d}", KOART=koart, SHKZG=sh,
                                  DMBTR=dm / 100, WRBTR=wr / 100, HKONT=hk, KUNNR=ku, LIFNR=li, MWSKZ=mw, ZUONR=zu, SGTXT=sg,
                                  VBELN=awkey if awtyp == "VBRK" else "", MATNR=ma))
            else:
                sg_ = 1 if sh == "S" else -1
                lines.append(dict(RLDNR="0L", RBUKRS=r["bukrs"], GJAHR=d.year, BELNR=r["belnr"], DOCLN=f"{n:06d}", BUDAT=ymd(d),
                                  POPER=f"{d.month:03d}", BLART=blart, RACCT=hk, DRCRK=sh, HSL=sg_ * dm / 100, WSL=sg_ * wr / 100,
                                  RWCUR=ccy, RHCUR=CO[r["bukrs"]]["waers"], MWSKZ=mw, KTOSL=kt, KUNNR=ku, LIFNR=li, MATNR=ma,
                                  AWTYP=awtyp, AWREF=awkey if awtyp == "VBRK" else r["belnr"], ZUONR=zu, SGTXT=sg))

    def tax(r, out_sh, d, ktosl="MWS", buzei=None):
        if r["code"] == "":
            return
        bset.append(dict(BUKRS=r["bukrs"], BELNR=r["belnr"], GJAHR=d.year, BUZEI=buzei or "001", MWSKZ=r["code"], KTOSL=ktosl,
                         SHKZG=out_sh, HWBAS=r["hwbas"] / 100, HWSTE=r["hwste"] / 100, FWBAS=r["fwbas"] / 100, FWSTE=r["fwste"] / 100,
                         LSTML=r["lstml"]))

    for b in sorted(W["bills"], key=lambda x: x["id"]):
        if b["fsys"] != sys_:
            continue
        neg = b["vbtyp"] in ("O", "6", "N")
        d, ccy = b["fkdat"], b["waerk"]
        k = kursf(ccy, b["bukrs"], d)
        rev = "S" if neg else "H"
        cust = "H" if neg else "S"
        ic = b["kunag"] in IC_KUNNR
        its = []
        loc_sum = 0
        for m, qty, net in b["items"]:
            loc = round(net * k)
            loc_sum += loc
            its.append(("S", rev, "0000800100" if ic else "0000800000", loc, net, b["code"], "", "", "", m, "", MAT[m][:50]))
        loc_tax = b["hwste"]
        items = [("D", cust, "0000140000", loc_sum + loc_tax, b["net"] + b["fwste"], "", "", b["kunag"], "", "", b["vbeln"].lstrip("0"), "")]
        items += its
        if b["hwste"]:
            items.append(("S", rev, "0000175000", loc_tax, b["fwste"], b["code"], "MWS", "", "", "", "", ""))
        xb = chain_ref(b, None) if b["sys"] == "S4" else b["vbeln"]
        put(b, "RV", "VF01", d, ccy, xb, "", "VBRK", b["vbeln"], items, cpudt=b["cpudt"],
            usnam="VF_BATCH" if not b["manual"] else f"SD_{b['bukrs'][:2]}0{b['id'] % 5 + 1}")
        tax(b, "S" if neg else "H", d, "MWS" if b["hwste"] else "")
    for f in sorted(W["fi"], key=lambda x: x["id"]):
        if f["fsys"] != sys_:
            continue
        d = f["budat"]
        if f["kind"] == "zm":
            old = f["old"]
            gross_loc = round(old["net"] * kursf(old["waerk"], old["bukrs"], old["fkdat"])) + old["hwste"]
            items = [("D", "S", "0000140000", gross_loc, old["net"] + old["fwste"], "", "", old["kunag"], "", "", f["xblnr"].lstrip("0"), f["sgtxt"]),
                     ("S", "H", "0000199900", gross_loc, old["net"] + old["fwste"], "", "", "", "", "", "", "Migration Verrechnung OP")]
            put(f, "ZM", "LTMC", d, old["waerk"], f["xblnr"], f["bktxt"], "BKPF", "", items, cpudt=f["cpudt"], usnam="MIGRATION")
            continue
        items = [("D", "S", "0000140000", f["net"] + f["hwste"], f["net"] + f["fwste"], "", "", f["kunnr"], "", "", "", f["sgtxt"]),
                 ("S", "H", "0000800100" if f["kunnr"] in IC_KUNNR else "0000809000" if f["kind"] == "scrap" else "0000800000",
                  f["net"], f["net"], f["code"], "", "", "", "", "", f["sgtxt"])]
        if f["hwste"]:
            items.append(("S", "H", "0000175000", f["hwste"], f["fwste"], f["code"], "MWS", "", "", "", "", ""))
        put(f, "DR", "FB70", d, "EUR", f["xblnr"], f["bktxt"], "BKPF", "", items, cpudt=f["cpudt"],
            usnam="AR_DE02" if f["kind"] == "repost" else "AR_DE01")
        tax(f, "H", d, "MWS" if f["hwste"] else "")
    for v in sorted(W["vend"], key=lambda x: x["id"]):
        if v["fsys"] != sys_:
            continue
        d, neg = v["budat"], v["sign"] < 0
        exp = "S" if not neg else "H"
        vd = "H" if not neg else "S"
        k = kursf(v["waers"], v["bukrs"], d)
        loc = round(v["net"] * k)
        code = v["code"]
        acct = "0000400100" if v["kind"] in ("ic", "tw", "eu") else "0000417000"
        items = [("K", vd, "0000160000", loc + (v["hwste"] if code in ("V1", "GV") else 0), v["net"] + (v["fwste"] if code in ("V1", "GV") else 0),
                  "", "", "", v["lifnr"], "", v["xblnr"], ""),
                 ("S", exp, acct, loc, v["net"], code, "", "", "", "", "", "Wareneingang Konzern" if v["kind"] == "ic" else "")]
        if code in ("V1", "GV"):
            items.append(("S", exp, "0000154000", v["hwste"], v["fwste"], code, "VST", "", "", "", "", ""))
            tax(v, exp, d, "VST")
        elif code == "E1":
            items.append(("S", exp, "0000154000", v["hwste"], v["fwste"], code, "ESE", "", "", "", "", ""))
            items.append(("S", vd, "0000176500", v["hwste"], v["fwste"], code, "ESA", "", "", "", "", ""))
            tax(v, exp, d, "ESE", "002")
            tax(v, vd, d, "ESA", "003")
        else:
            tax(v, exp, d, "")
        put(v, "RE" if v["kind"] == "ic" else "KR", "MIRO" if v["kind"] == "ic" else "FB60", d, v["waers"], v["xblnr"], "", "BKPF", "",
            items, usnam="EDI_IDOC" if v["kind"] == "ic" else f"AP_{v['bukrs'][:2]}0{v['id'] % 6 + 1}")
    bk = pd.DataFrame(hdr).sort_values(["BUKRS", "GJAHR", "BELNR"], kind="stable")
    li = pd.DataFrame(lines)
    li = li.sort_values(["BUKRS", "GJAHR", "BELNR", "BUZEI"] if sys_ == "ECC" else ["RBUKRS", "GJAHR", "BELNR", "DOCLN"], kind="stable")
    bs = pd.DataFrame(bset).sort_values(["BUKRS", "GJAHR", "BELNR", "BUZEI"], kind="stable")
    return {"BKPF": bk.reset_index(drop=True), ("BSEG" if sys_ == "ECC" else "ACDOCA"): li.reset_index(drop=True), "BSET": bs.reset_index(drop=True)}


def mm_tables(rng, W, sys_):
    """Goods issues for every delivery; receipts at AT10/CH10 for intra-group stock deliveries; assembly in Vienna."""
    rows = []
    for d in sorted(W["dels"], key=lambda x: x["id"]):
        for i, (m, qty) in enumerate(d["items"], 1):
            rows.append((d["gi"], "601", m, d["vstel"], qty, d["kunnr"], "", d["vbeln"], ""))
    for p in sorted(W["procs"], key=lambda x: x["id"]):
        dl = W["dl_by"][p["dl"]]
        gr, prod = add_bd(dl["gi"], 2), add_bd(dl["gi"], 5)
        aufnr = f"{1000000 + p['id']:012d}"
        for m, qty, _ in p["comps"]:
            rows.append((gr, "101", m, "AT10", qty, "", CO["DE01"]["lifnr"], dl["vbeln"], ""))
            rows.append((prod, "261", m, "AT10", qty, "", "", "", aufnr))
        if p.get("bike"):
            rows.append((add_bd(prod, 1), "101", p["bike"][0], "AT10", p["bike"][1], "", "", "", aufnr))
    for d in sorted(W["dels"], key=lambda x: x["id"]):
        if d["kunnr"] == CO["CH01"]["ship"]:
            for m, qty in d["items"]:
                rows.append((add_bd(d["gi"], 2), "101", m, "CH10", qty, "", CO["DE01"]["lifnr"], d["vbeln"], ""))
    rows = [r for r in rows if s4(r[0]) == (sys_ == "S4")]
    rows.sort(key=lambda r: (r[0], r[7], r[1]))
    docs, out, last = 0, [], None
    for r in rows:
        key = (r[0], r[7] or r[8], r[1])
        if key != last:
            docs += 1
            last, z = key, 0
        z += 1
        out.append(dict(MBLNR=f"{4900000000 + docs:010d}", MJAHR=r[0].year, ZEILE=f"{z:04d}", BWART=r[1], MATNR=r[2], WERKS=r[3],
                        MENGE=float(r[4]), MEINS="ST", BUDAT=ymd(r[0]), KUNNR=r[5], LIFNR=r[6], VBELN_IM=r[7], AUFNR=r[8]))
    df = pd.DataFrame(out)
    if sys_ == "S4":
        return {"MATDOC": df}
    mk = df.drop_duplicates("MBLNR")[["MBLNR", "MJAHR", "BUDAT"]].assign(BLDAT=lambda x: x.BUDAT, USNAM="WM_BATCH", XBLNR="")
    return {"MKPF": mk.reset_index(drop=True), "MSEG": df.drop(columns=["BUDAT"])}


def master_tables(W, P, M, sys_):
    F = W["F"]
    cust = M["cust"] + [M["scrap"]]
    ic = [dict(KUNNR=CO["AT01"]["kunnr"], NAME1=CO["AT01"]["name"], LAND1="AT", ORT01="Wien", PSTLZ="1230", STCEG=CO["AT01"]["vat"]),
          dict(KUNNR=CO["AT01"]["ship"], NAME1=CO["AT01"]["name"] + " Werk Wien", LAND1="AT", ORT01="Wien", PSTLZ="1230", STCEG=CO["AT01"]["vat"]),
          dict(KUNNR=CO["CH01"]["kunnr"], NAME1=CO["CH01"]["name"], LAND1="CH", ORT01="Basel", PSTLZ="4057", STCEG=CO["CH01"]["vat"]),
          dict(KUNNR=CO["CH01"]["ship"], NAME1=CO["CH01"]["name"] + " Lager Basel", LAND1="CH", ORT01="Basel", PSTLZ="4057", STCEG=CO["CH01"]["vat"]),
          dict(KUNNR=CO["HK01"]["kunnr"], NAME1=CO["HK01"]["name"], LAND1="HK", ORT01="Hong Kong", PSTLZ="",
               STCEG=CO["HK01"]["vat_de"] if sys_ == "S4" else "")]
    for c in ic:
        c.update(KTOKD="KONZ", ERDAT="20120101", VBUND=c["LAND1"] + "1000")
    kna1 = pd.DataFrame([dict(KUNNR=c["KUNNR"], NAME1=c["NAME1"], LAND1=c["LAND1"], ORT01=c["ORT01"], PSTLZ=c["PSTLZ"], STCEG=c["STCEG"],
                              KTOKD=c["KTOKD"], VBUND=c.get("VBUND", ""), ERDAT=int(c["ERDAT"])) for c in cust + ic]).sort_values("KUNNR")
    knas = [dict(KUNNR=CO["AT01"]["kunnr"], LAND1="AT", STCEG=CO["AT01"]["vat"]), dict(KUNNR=CO["AT01"]["kunnr"], LAND1="DE", STCEG=CO["AT01"]["vat_de"])]
    if sys_ == "S4":
        knas.append(dict(KUNNR=CO["HK01"]["kunnr"], LAND1="DE", STCEG=CO["HK01"]["vat_de"]))
    vend = W["vendors"]["dom"] + W["vendors"]["eu"] + W["vendors"]["tw"]
    vend += [dict(LIFNR=CO["DE01"]["lifnr"], NAME1=CO["DE01"]["name"], LAND1="DE", STCEG=CO["DE01"]["vat"], KTOKK="KONZ")]
    lfa1 = pd.DataFrame(vend)[["LIFNR", "NAME1", "LAND1", "STCEG", "KTOKK"]].sort_values("LIFNR")
    t001 = pd.DataFrame([dict(BUKRS=b, BUTXT=c["name"], ORT01=c["city"], LAND1=c["land"], WAERS=c["waers"], KTOPL="INT",
                              STCEG=c["vat"].split(" ")[0] if c["vat"] else "", KALSM=c["kalsm"]) for b, c in sorted(CO.items())])
    t001w = pd.DataFrame([("CH10", "Lager Basel", "CH10", "CH", "Basel"), ("DE10", "Werk Schweinfurt", "DE10", "DE", "Schweinfurt"),
                          ("AT10", "Werk Wien (Montage)", "AT10", "AT", "Wien")], columns=["WERKS", "NAME1", "BWKEY", "LAND1", "ORT01"]).sort_values("WERKS")
    t001k = pd.DataFrame([("AT10", "AT01"), ("CH10", "CH01"), ("DE10", "DE01")], columns=["BWKEY", "BUKRS"])
    tvko = pd.DataFrame([("AT10", "Vertrieb Österreich", "AT01"), ("CH10", "Vertrieb Schweiz", "CH01"), ("DE10", "Vertrieb Deutschland", "DE01"),
                         ("HK10", "Sales Asia Pacific / Export Hub", "HK01")], columns=["VKORG", "VTEXT", "BUKRS"])
    tvst = pd.DataFrame([("AT10", "Versandstelle Wien", "AT", "Wien"), ("CH10", "Versandstelle Basel", "CH", "Basel"),
                         ("DE10", "Versandstelle Schweinfurt", "DE", "Schweinfurt")], columns=["VSTEL", "VTEXT", "LAND1", "ORT01"])
    t007a = pd.DataFrame([dict(KALSM=k, MWSKZ=c, MWART=v[1], LSTML=v[0], KBETR=f"{float(v[2]) * 10:.3f}", TEXT1=v[5])
                          for (k, c), v in sorted(TAXCODES.items()) if sys_ == "S4" or not c.startswith("K")])
    t007k = pd.DataFrame(T007K, columns=["LAND1", "VERSI", "KALSM", "MWSKZ", "KTOSL", "KZ_BASIS", "KZ_STEUER"])
    if sys_ == "ECC":
        t007k = t007k[~t007k.MWSKZ.str.startswith("K")]
    tinc = pd.DataFrame([("EXW", "Ab Werk"), ("FCA", "Frei Frachtführer"), ("FAS", "Frei Längsseite Schiff"), ("FOB", "Frei an Bord"),
                         ("CPT", "Frachtfrei"), ("CIP", "Frachtfrei versichert"), ("CFR", "Kosten und Fracht"), ("CIF", "Kosten, Versicherung, Fracht"),
                         ("DAP", "Geliefert benannter Ort"), ("DPU", "Geliefert benannter Ort entladen"), ("DDP", "Geliefert verzollt")],
                        columns=["INCO1", "BEZEI"])
    mats = BIKES + COMPONENTS + WIEN
    mara = pd.DataFrame([dict(MATNR=m, MTART="HALB" if (m, t, p) in COMPONENTS else "FERT", MATKL=m.split("-")[1], MEINS="ST") for m, t, p in mats])
    makt = pd.DataFrame([dict(MATNR=m, SPRAS="D", MAKTX=t) for m, t, p in mats])
    rows = []
    end = date(2022, 6, 30) if sys_ == "ECC" else date(2025, 12, 31)
    for ccy in sorted(F["daily"]):
        days, vals = F["daily"][ccy]
        for dd, v in zip(days, vals):
            if dd <= end:
                rows.append(dict(KURST="M", FCURR=ccy, TCURR="EUR", GDATU=99999999 - ymd(dd), UKURS=f"{v:.5f}", FFACT=1, TFACT=1))
        for (y, m), v in sorted(F["ust"][ccy].items()):
            if date(y, m, 1) <= end:
                rows.append(dict(KURST="UST", FCURR=ccy, TCURR="EUR", GDATU=99999999 - (y * 10000 + m * 100 + 1), UKURS=f"-{v:.5f}", FFACT=1, TFACT=1))
    tcurr = pd.DataFrame(rows)
    return dict(KNA1=kna1.reset_index(drop=True), KNAS=pd.DataFrame(knas), LFA1=lfa1.reset_index(drop=True), T001=t001, T001W=t001w,
                T001K=t001k, TVKO=tvko, TVST=tvst, T007A=t007a, T007K=t007k, TINC=tinc, MARA=mara, MAKT=makt, TCURR=tcurr)


T007K = [("DE", "UVA", "TAXD", "A1", "MWS", "81", "81_STEUER"), ("DE", "UVA", "TAXD", "A2", "", "41", ""), ("DE", "UVA", "TAXD", "A0", "", "43", ""),
         ("DE", "UVA", "TAXD", "K1", "", "41", ""), ("DE", "UVA", "TAXD", "K3", "", "43", ""), ("DE", "UVA", "TAXD", "E1", "ESA", "89", "89_STEUER"),
         ("DE", "UVA", "TAXAT", "G1", "MWS", "81", "81_STEUER"), ("DE", "UVA", "TAXAT", "G2", "", "41", ""), ("DE", "UVA", "TAXAT", "G3", "", "43", ""),
         ("DE", "UVA", "TAXAT", "K1", "", "41", ""), ("AT", "UVA", "TAXAT", "B1", "MWS", "022", "022_UST"), ("AT", "UVA", "TAXAT", "B2", "", "017", ""),
         ("AT", "UVA", "TAXAT", "B3", "", "011", ""), ("AT", "UVA", "TAXAT", "B4", "", "DREIECK", ""), ("AT", "UVA", "TAXAT", "K2", "", "DREIECK", ""),
         ("AT", "UVA", "TAXAT", "E1", "ESA", "070", "072_UST")]


def migration_tables(rng, W):
    """ZMIG_DOCMAP: migration cockpit log (runs 3 and 4 were not logged)."""
    rows = []
    for o in sorted(W["orders"], key=lambda x: x["id"]):
        if o["new"]:
            run = W["migrun"][("VBAK", o["id"])]
            if run <= 2:
                rows.append(dict(MIG_RUN=run, OBJTYPE="VBAK", OLD_SYS="E11", OLD_KEY=o["vbeln"], NEW_KEY=o["new"], MIG_DATE=20220701))
    for d in sorted(W["dels"], key=lambda x: x["id"]):
        if d["new"]:
            run = W["migrun"][("LIKP", d["id"])]
            if run <= 2:
                rows.append(dict(MIG_RUN=run, OBJTYPE="LIKP", OLD_SYS="E11", OLD_KEY=d["vbeln"], NEW_KEY=d["new"], MIG_DATE=20220701))
    for f in sorted(W["fi"], key=lambda x: x["id"]):
        if f["kind"] == "zm" and f["logged"]:
            o = f["old"]
            rows.append(dict(MIG_RUN=1 + f["id"] % 2, OBJTYPE="BKPF", OLD_SYS="E11", OLD_KEY=f"{o['bukrs']}{o['belnr']}{o['gjahr']}",
                             NEW_KEY=f"{f['bukrs']}{f['belnr']}{f['gjahr']}", MIG_DATE=20220702))
    return pd.DataFrame(rows).sort_values(["MIG_RUN", "OBJTYPE", "OLD_KEY"], kind="stable").reset_index(drop=True)


def build(rng, W, P, M, F):
    W["F"] = F
    W["migrun"] = {}
    for o in sorted(W["orders"], key=lambda x: x["id"]):
        if o["new"]:
            W["migrun"][("VBAK", o["id"])] = int(rng.integers(1, 5))
    for d in sorted(W["dels"], key=lambda x: x["id"]):
        if d["new"]:
            W["migrun"][("LIKP", d["id"])] = int(rng.integers(1, 5))
    for b in W["bills"]:
        b["_zz"] = W["chains_by"][b["chain"]]["zz"] if b["chain"] is not None else b["zzchain"]
    out = {}
    for sys_ in ("ECC", "S4"):
        t = {}
        t.update(sd_tables(W, P, sys_))
        t.update(fi_tables(W, P, sys_))
        t.update(mm_tables(rng, W, sys_))
        t.update(master_tables(W, P, M, sys_))
        if sys_ == "S4":
            t["ZMIG_DOCMAP"] = migration_tables(rng, W)
        out[sys_] = t
    return out


ECC_FMT = {t: ("se16", {}) for t in ("VBAK", "VBAP", "LIKP", "LIPS", "VBRK", "VBRP", "VBFA", "KNA1", "KNAS", "BKPF", "BSEG", "BSET",
                                      "MKPF", "MSEG", "TCURR", "MARA", "MAKT", "LFA1")}
ECC_FMT.update({t: ("xlsx", {}) for t in ("T001", "T001W", "T001K", "TVKO", "TVST", "T007A", "T007K", "TINC")})
S4_FMT = {"VBRK": ("csv_de", {}), "VBRP": ("csv_de", dict(mandt=True)), "ACDOCA": ("csv_de", dict(split="GJAHR")), "BSET": ("csv_de", {}),
          "BKPF": ("csv", dict(split="GJAHR")), "VBAK": ("csv", {}), "VBAP": ("csv", dict(reorder=True)), "LIKP": ("csv", {}),
          "LIPS": ("csv", {}), "VBFA": ("csv", dict(mandt=True)), "MATDOC": ("csv", {}), "TCURR": ("csv", {}),
          "KNA1": ("se16", {}), "KNAS": ("se16", {}), "LFA1": ("se16", {}), "MARA": ("se16", {}), "MAKT": ("se16", {})}
S4_FMT.update({t: ("xlsx", {}) for t in ("T001", "T001W", "T001K", "TVKO", "TVST", "T007A", "T007K", "TINC", "ZMIG_DOCMAP")})
DESCR = dict(ACDOCA="Universal journal entry line items", BKPF="Accounting document header", BSEG="Accounting document segment",
             BSET="Tax data document segment", KNA1="Customer master (general data)", KNAS="Customer master (VAT registration numbers)",
             LFA1="Vendor master (general data)", LIKP="SD document: delivery header", LIPS="SD document: delivery item",
             MAKT="Material descriptions", MARA="General material data", MATDOC="Material documents", MKPF="Header: material document",
             MSEG="Document segment: material", T001="Company codes", T001K="Valuation area", T001W="Plants/branches",
             T007A="Tax keys", T007K="Tax keys: advance return lines", TCURR="Exchange rates", TINC="Incoterms",
             TVKO="Sales organizations", TVST="Shipping points", VBAK="Sales document: header", VBAP="Sales document: item",
             VBFA="Sales document flow", VBRK="Billing document: header", VBRP="Billing document: item",
             ZMIG_DOCMAP="Migration cockpit: legacy-to-S/4 number log (custom)")
README = {"ECC": """Rotkehl IT - legacy system E11 (SAP ECC 6.0), read-only since 01.07.2022 - extract for Group Tax (ticket INC-2026-00988)
SE16N list downloads (exporting user's settings) and Excel exports for customizing. Extracted {date}.
Scope: company codes DE01, AT01, CH01, HK01; documents up to 30.06.2022.

Files
  File                  Table     Description
{files}
""", "S4": """Rotkehl IT - SAP S/4HANA PRD (client 100) - extract for Group Tax (ticket INC-2026-00988). Extracted {date}.
S/4HANA go-live 01.07.2022 (new implementation). Migrated from E11: open sales orders, open deliveries and open
customer items (document type ZM). Legacy-to-new numbers are logged in ZMIG_DOCMAP (migration cockpit log).
Large tables are split by fiscal year. CSV export via report ZFI_TABLE_EXPORT (';'), some tables as SE16N downloads.

Files
  File                  Table     Description
{files}
"""}


def write(sap_tables, sap_dir):
    for sys_, sub in (("ECC", "ecc"), ("S4", "s4")):
        d = os.path.join(sap_dir, sub)
        os.makedirs(d, exist_ok=True)
        fmts = ECC_FMT if sys_ == "ECC" else S4_FMT
        saved, files = dict(export.FORMATS), []
        export.FORMATS.clear()
        export.FORMATS.update(fmts)
        try:
            files = export.write_tables(sap_tables[sys_], d, DESCR)
        finally:
            export.FORMATS.clear()
            export.FORMATS.update(saved)
        with open(os.path.join(d, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(README[sys_].format(date=export.EXTRACT_DATE, files="\n".join(files)))
