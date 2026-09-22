# dispatch.py —— 每日增量报告引擎(核心)
# 读 kw.db(词级索引),对某一天输出:首现词/末现词 → 映射到资产维度 → 方向
# 用法: python3 dispatch.py 20260920
import sqlite3, sys, os
from seed_map import MAP

DB = os.path.join(os.path.dirname(__file__), "kw.db")
NEWS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "news"))

# 窗口口径(唯一事实源):所有"近N日"判断与展示都用它,避免标签与实现不一致
WINDOW = 15

def get_first_last(con, date, window=WINDOW):
    # 用纯 python 集合差,避免 SQL 日期边界误判。
    # 今日词集 vs 近 window 天(不含今日)词集:
    #   突现 = 今日有 且 近N天无   (今天新冒头)
    #   撤下 = 近N天有 且 今日无   (近N天常说、今天停了)
    rows = con.execute("SELECT word FROM kw WHERE date=?", (date,)).fetchall()
    today = {r[0] for r in rows}
    # 近 window 天的日期列表(取 SQL 里 date <= 今天且按日期倒序前 window)
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM kw WHERE date<=? ORDER BY date DESC LIMIT ?",
        (date, window)).fetchall()]
    close = set()
    for d in dates:
        if d != date:
            close |= {r[0] for r in con.execute("SELECT word FROM kw WHERE date=?", (d,)).fetchall()}
    first = {w for w in today if w not in close}   # 近N天无 → 突现
    last = {w for w in close if w not in today}    # 近N天有、今天停 → 撤下
    return first, last

def news_segments(md_text):
    # 按 ### 标题切开每则,返回 [(title, body_text)];body 去 HTML 标签
    import re
    parts = re.split(r'^### ', md_text, flags=re.M)
    segs = []
    for p in parts[1:]:
        title = p.split('\n')[0].strip()
        body = re.sub(r'<[^>]+>', ' ', p)
        body = re.sub(r'\[查看原文\].*$', '', body, flags=re.S)
        body = re.sub(r'\s+', ' ', body).strip()
        segs.append((title, body))
    return segs

def find_segment_for_word(segments, word):
    """找到包含该词的那则(title, body 上下文)"""
    for title, body in segments:
        if word in body or word in title:
            return title, body
    return None, None

# 通用实体库(可扩展):点名才标实体;无点名则记泛词
ENTITIES = ["DeepSeek","Claude","盘古","通义","文心","Kimi","智谱","可灵",
            "宁德","比亚迪","华为","台积电","阿斯麦","礼来","恒瑞","百济",
            "信达","石药","茅台","中石油","中石化","稀土永磁","HBM","宁德时代"]

def named_entities_in(body):
    import re
    found = [e for e in ENTITIES if e in body]
    return found

def last_date_with(con, word):
    r = con.execute("SELECT MAX(date) FROM kw WHERE word=?", (word,)).fetchone()[0]
    return r

def map_to_assets(word):
    hits = []
    for row in MAP:
        if word in row["words"]:
            hits.append({"dim": row["dim"], "asset": row["asset"], "dir": row["dir"], "verify": row["verify"]})
    return hits

def window_dates(con, date, window):
    """该日期及之前的 window 个已索引日期(倒序)"""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM kw WHERE date<=? ORDER BY date DESC LIMIT ?",
        (date, window)).fetchall()]

def days_in_window(con, word, date, window=WINDOW):
    """该词在「近 window 天(含当日)」内出现的天数 → timeline 强度。
    修复:原实现 WHERE date<=? 无下界,返回的是全历史天数(与函数名/注释不符)。"""
    ds = window_dates(con, date, window)
    if not ds:
        return 0
    oldest = min(ds)
    n = con.execute(
        "SELECT COUNT(DISTINCT date) FROM kw WHERE word=? AND date<=? AND date>=?",
        (word, date, oldest)).fetchone()[0]
    return n

def build_dimension_objects(con, date, first, last, segments=None, window=WINDOW):
    """聚合:每个资产维度 → 一个主题对象(突现/撤下/近WINDOW日强度/点名实体)"""
    # 维度 → { 突现词, 撤下词, 实体 }
    dim = {}
    for w in first:
        for h in map_to_assets(w):
            key = h["dim"]
            b = dim.setdefault(key, {"sudden": [], "dropped": [], "entities": [], "who": []})
            b["sudden"].append(w)
            b["assets"] = h["asset"]
            b["dir"] = h["dir"]
    for w in last:
        for h in map_to_assets(w):
            key = h["dim"]
            b = dim.setdefault(key, {"sudden": [], "dropped": [], "entities": []})
            b["dropped"].append(w)
            b["assets"] = h["asset"]
            b["dir"] = h["dir"]
    # 实体:收集该维度相关词的上下文里点名的实体(segments 由调用方传入,避免重复读盘)
    if segments is None:
        md = open(os.path.join(NEWS_DIR, date + ".md"), encoding="utf-8", errors="ignore").read() if os.path.exists(os.path.join(NEWS_DIR, date + ".md")) else ""
        segments = news_segments(md)
    segs = segments
    for key, b in dim.items():
        for w in b["sudden"] + b["dropped"]:
            title, body = find_segment_for_word(segs, w)
            if not body:
                lastd = last_date_with(con, w)
                if lastd:
                    op = os.path.join(NEWS_DIR, lastd + ".md")
                    if os.path.exists(op):
                        title, body = find_segment_for_word(news_segments(open(op, encoding="utf-8", errors="ignore").read()), w)
            if body:
                for e in named_entities_in(body):
                    if e not in b["entities"]:
                        b["entities"].append(e)
    return dim

def report(date):
    con = sqlite3.connect(DB)
    first, last = get_first_last(con, date)
    md = open(os.path.join(NEWS_DIR, date + ".md"), encoding="utf-8", errors="ignore").read() if os.path.exists(os.path.join(NEWS_DIR, date + ".md")) else ""
    segments = news_segments(md)
    objs = build_dimension_objects(con, date, first, last, segments=segments)
    print(f"===== {date} · 每日联播主题对象(话语→资产) =====")
    print(f"首现词 {len(first)} · 撤下词 {len(last)} · 聚合维度 {len(objs)} · 窗口 {WINDOW} 日\n")
    # 有动作的维度排前
    for dim, b in sorted(objs.items(), key=lambda kv: -(len(kv[1]['sudden']) + len(kv[1]['dropped']))):
        ent_txt = ("点名: " + "/".join(b["entities"])) if b["entities"] else "(未点名)"
        act = f"→ {b['assets']} ({b['dir']})"
        print(f"## {dim}  {act}")
        print(f"   点名实体: {ent_txt}")
        if b["sudden"]:
            print(f"   今日突现(近{WINDOW}日无,今天先说): {', '.join(b['sudden'][:8])}")
        if b["dropped"]:
            print(f"   今日撤下(近{WINDOW}日有,今天停提): {', '.join(b['dropped'][:8])}")
        print()
    con.close()

if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "20260920"
    report(d)
