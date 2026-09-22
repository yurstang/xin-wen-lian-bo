# log.py —— 冻结前瞻登记(frozen forward log):每日信号带方法版本戳落库,只增不改
#
# 为什么需要它:
#   联播原文(news/*.md)本身是 append-only 的,短语/词级信号可以事后确定性重算;
#   真正会被"事后调参"污染的是词表与规则 —— 所以每条信号必须带 method_hash,
#   锁死"当时用的是哪一版方法",回溯重算才不构成 look-ahead bias。
#   同时落一份**随机负对照**(同数量、随机词 + 随机维度),否则"准"无法与运气区分。
#
# 用法:
#   python3 log.py                # 对最新一天落库(已存在则拒绝,保证只增不改)
#   python3 log.py 20260920       # 指定日期
#   python3 log.py 20260920 --force   # 覆盖(会记 revision,慎用)
import hashlib, json, os, random, sqlite3, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dispatch as D

SIGNALS_DIR = os.path.normpath(os.path.join(HERE, "..", "signals"))
METHOD_FILES = ["idx.py", "dispatch.py", "seed_map.py"]


def method_hash():
    """三个源码文件的联合哈希 = 这一版方法的指纹"""
    h = hashlib.sha256()
    for f in METHOD_FILES:
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    return h.hexdigest()[:16]


def negative_control(con, date, n, rng):
    """随机负对照:抽 n 个当日出现的词(与真实信号同量级),维度随机分配。
    用途:事件研究时与真实信号比较,排除"随便挑也能中"的运气成分。"""
    words = [r[0] for r in con.execute("SELECT word FROM kw WHERE date=?", (date,)).fetchall()]
    if not words:
        return []
    dims = sorted({row["dim"] for row in D.MAP})
    picked = rng.sample(words, min(n, len(words)))
    out = []
    for w in picked:
        dim = rng.choice(dims)
        row = next((r for r in D.MAP if r["dim"] == dim), None)
        out.append({"word": w, "dim": dim,
                    "assets": row["asset"] if row else "?",
                    "dir": "control"})
    return out


def build_record(date, force=False):
    con = sqlite3.connect(D.DB)
    first, last = D.get_first_last(con, date)
    md = ""
    p = os.path.join(D.NEWS_DIR, date + ".md")
    if os.path.exists(p):
        md = open(p, encoding="utf-8", errors="ignore").read()
    segments = D.news_segments(md)
    objs = D.build_dimension_objects(con, date, first, last, segments=segments)

    signals = []
    for dim, b in sorted(objs.items(), key=lambda kv: -(len(kv[1]["sudden"]) + len(kv[1]["dropped"]))):
        signals.append({
            "dim": dim, "assets": b.get("assets"), "dir": b.get("dir"),
            "sudden": sorted(b["sudden"])[:20], "dropped": sorted(b["dropped"])[:20],
            "entities": b["entities"],
            "n_sudden": len(b["sudden"]), "n_dropped": len(b["dropped"]),
        })

    # 负对照数量 = 真实"有动作"的信号数(至少 5),用日期做种子保证可复现
    rng = random.Random(int(date))
    n_active = sum(1 for s in signals if s["n_sudden"] or s["n_dropped"])
    control = negative_control(con, date, max(5, n_active), rng)

    rec = {
        "date": date,
        "method_hash": method_hash(),
        "window": D.WINDOW,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frozen": True,
        "n_sudden_total": len(first),
        "n_dropped_total": len(last),
        "signals": signals,
        "negative_control": control,
        "note": "只增不改。method_hash 锁定当时方法;输入 news/*.md 本身 append-only。",
    }
    con.close()
    return rec


def write_record(date, force=False):
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    path = os.path.join(SIGNALS_DIR, date + ".json")
    if os.path.exists(path) and not force:
        print(f"已存在,拒绝覆盖(只增不改): {path}")
        return False
    rec = build_record(date, force)
    if os.path.exists(path):  # force 覆盖 → 标注 revision
        try:
            old = json.load(open(path, encoding="utf-8"))
            rec["revision_of"] = old.get("method_hash")
            rec["revision_n"] = old.get("revision_n", 1) + 1
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"已落库: {path}  (method_hash={rec['method_hash']}, "
          f"信号 {len(rec['signals'])} 维, 负对照 {len(rec['negative_control'])} 条)")
    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if args:
        date = args[0]
    else:
        con = sqlite3.connect(D.DB)
        date = con.execute("SELECT MAX(date) FROM kw").fetchone()[0]
        con.close()
    write_record(date, force)
