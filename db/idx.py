# idx.py —— 增量词级索引(daily):对每日新 md 追加建索引,输出每词的 首现/末现/出现天数/近30日
# 用法: python3 idx.py            # 只对"最新一天"增量追加(若已建过则跳过)
#       python3 idx.py --force     # 全量重建(只在需要时)
import glob, os, re, sqlite3, sys
import jieba
from seed_map import MAP

# 把资产映射词表注入 jieba 用户词典,确保 "稀土/水权/十五五/先进封装/HBM" 整词切出
for row in MAP:
    for w in row["words"]:
        jieba.add_word(w, freq=9999)
# 高频人名也整词
for w in ["习近平","赵乐际","王沪宁","李强","何立峰","丁薛祥","李书磊","蔡奇","李希"]:
    jieba.add_word(w, freq=9999)

NEWS_DIR = os.path.join(os.path.dirname(__file__), "..", "news")
DB_PATH = os.path.join(os.path.dirname(__file__), "kw.db")

# 停用词:不参与词频索引的联播固定框架词
STOP = set("""的 了 在 是 和 与 及 或 被 把 将 为 从 以 于 对 而 其 这 那 也 就 都
很 更 最 并 但 却 又 还 已 经 中 上 下 大 小 个 月 日 时 里 些 年 会 到 说 成 一
二 三 我们 我国 中国 记者 今天 日前 报道 消息 新华社 继续 表示 强调 指出 要求 坚持
新闻联播 节目 本期节目主要内容 查看原文 查看 原文 新闻摘要 详细新闻 更新时间戳 央视网消息 国内联播快讯 国际联播快讯
同比 增长 今年 目前 此外 近日 日上午 日下午""".split())

def words_of(text):
    # 用 jieba 切词(非滑窗),过滤停用词、纯标点、单字
    out = []
    for w in jieba.cut(text):
        if len(w) < 2:
            continue
        if w in STOP:
            continue
        if not re.search(r"[一-龥]", w):
            continue
        out.append(w)
    return out

def build(db, force=False):
    files = sorted(glob.glob(os.path.join(NEWS_DIR, "2*.md")))
    dates = [os.path.basename(f)[:8] for f in files]
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS kw(date TEXT, word TEXT, n INT, PRIMARY KEY(date,word))")
    last = cur.execute("SELECT MAX(date) FROM kw").fetchone()[0]
    target = dates[-1]
    print(f"已索引到 {last}, 最新日期 {target}")
    if not force and last and last >= target:
        print("最新一天已索引,跳过(增量)。--force 可重建")
        con.close(); return
    # 只算新增的日期(比 last 新)
    todo = [d for d in dates if not last or d > last]
    print(f"本次增量处理 {len(todo)} 天: {todo[:5]}...")
    for d in todo:
        f = os.path.join(NEWS_DIR, d + ".md")
        if not os.path.exists(f): continue
        text = open(f, encoding="utf-8", errors="ignore").read()
        counter = {}
        for w in words_of(text):
            counter[w] = counter.get(w, 0) + 1
        cur.executemany("INSERT INTO kw VALUES(?,?,?)",
                        [(d, w, n) for w, n in counter.items()])
        print(f"  {d}: {len(counter)} 个词")
    con.commit()
    con.close()
    print("索引完成")

if __name__ == "__main__":
    force = "--force" in sys.argv
    build(DB_PATH, force)
