"""Harbor trial dir -> viz trace: python viz/timeline.py <trial_dir> <out.json> --harness NAME --cost USD [--model M]."""
import argparse, glob, json, re
from datetime import datetime

ROOTS = ("sap/", "tax/", "docs/", "templates/", "inbox/", "out/", "scripts/", "work/")
PATH = re.compile(r"(/(?:app|tmp)/[\w./-]+|(?<![\w/])(?:%s|ecc/|s4/)[\w./-]+)" % "|".join(ROOTS))
WRITE = re.compile(r"(?:>\s*|to_excel\(|to_csv\(|save\(|open\([^)]*['\"]w|savefig\(|SimpleDocTemplate\()\s*['\"]?([\w./-]+)")
ERR = re.compile(r"Traceback|Error:|\bERR\b|No such file|command not found|exit code [1-9]")


KEEP = tuple("/app/" + r for r in ROOTS) + ("/tmp/",)


def norm(p, cwd):
    """Absolute /app path for a raw path token, or None if it is not a task file."""
    p = p.rstrip(".,;:'\")/")
    if not p.startswith("/"):
        p = "/app/sap/" + p if p.startswith(("ecc/", "s4/")) else "/app/" + p
    return p if p.startswith(KEEP) and p.count("/") >= 3 else None


def cmd_of(name, inp):
    return inp.get("command") or inp.get("code") or inp.get("content") or json.dumps(inp)[:400] if name != "todo_tool_" else "todo: update task list"


def microcc(trial):
    """Events from micro-cc's messages.jsonl, with per-turn tokens rescaled to tokens.json totals."""
    proj = glob.glob(f"{trial}/agent/microcc-projects/*/")[0]
    msgs = [json.loads(l) for l in open(proj + "messages.jsonl")]
    tok = json.load(open(proj + "tokens.json"))
    results = {b["tool_use_id"]: b for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"}
    t0 = datetime.fromisoformat(msgs[0]["ts"])
    ctx, ev = 0, []
    for m in msgs:
        c = m["content"]
        size = len(json.dumps(c)) if isinstance(c, list) else len(c)
        if m["role"] == "assistant" and isinstance(c, list):
            out = sum(len(b.get("thinking") or b.get("text") or json.dumps(b.get("input", ""))) for b in c)
            for b in (b for b in c if b["type"] == "tool_use"):
                cmd = cmd_of(b["name"], b["input"])
                cwd = (re.match(r"\s*cd\s+(\S+)", cmd) or [None, "/app"])[1]
                paths = {n for p in PATH.findall(cmd) if (n := norm(p, cwd))}
                writes = {n for p in WRITE.findall(cmd) if (n := norm(p, cwd))} | {p for p in paths if p.startswith(("/app/out", "/app/scripts"))
                                                                     and re.search(r"cat\s*>|write|save|to_excel|mkdir", cmd)}
                res = results.get(b["id"], {}).get("content", "")
                res = res if isinstance(res, str) else json.dumps(res)
                ev.append({"t": (datetime.fromisoformat(m["ts"]) - t0).total_seconds(), "tool": "python" if "python" in cmd[:60] else "bash",
                           "cmd": cmd, "reads": sorted(paths - writes), "writes": sorted(writes), "out_bytes": len(res),
                           "in_tok": ctx, "out_tok": out, "error": bool(ERR.search(res[:4000]))})
        ctx += size
    for k, total in (("in_tok", tok["total_input"]), ("out_tok", tok["output"])):  # chars -> real token totals
        s = sum(e[k] for e in ev) or 1
        for e in ev:
            e[k] = int(e[k] * total / s)
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trial"), ap.add_argument("out")
    ap.add_argument("--harness", required=True), ap.add_argument("--cost", type=float, required=True)
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    a = ap.parse_args()
    ev = microcc(a.trial)
    rw = json.load(open(f"{a.trial}/verifier/reward.json"))
    cfg = json.load(open(f"{a.trial}/config.json"))
    task = cfg.get("task", {}).get("path", "").rstrip("/").split("/")[-1] or "sap-vat-chain-b"
    score = {k: rw.get(src, 0) for k, src in [("register", "register"), ("recon", "recon"), ("correction", "correction"),
                                              ("tool", "ccm"), ("memo", "memo"), ("traps", "traps"), ("format", "format")]}
    tr = {"harness": a.harness, "model": a.model, "task": task, "wall_s": ev[-1]["t"] + 30, "cost_usd": a.cost,
          "score": score, "total": rw["reward"], "events": ev}
    json.dump(tr, open(a.out, "w"), indent=1)
    print(f"{len(ev)} events, {tr['wall_s'] / 60:.0f} min, errors {sum(e['error'] for e in ev)}, "
          f"files {len({p for e in ev for p in e['reads'] + e['writes']})}")


if __name__ == "__main__":
    main()
