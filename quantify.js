// quantify.js —— 把新闻联播当日全文量化为结构性数据
// 纯 Node 标准库实现,零新依赖。读 news/YYYYMMDD.md → 写 stats/YYYYMMDD.json
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const NEWS_DIR = path.join(__dirname, 'news');
const STATS_DIR = path.join(__dirname, 'stats');

// 停用词：不参与词频的常见词/虚词/联播固定措辞
const STOP = new Set(
  "的 了 在 是 和 与 及 或 被 把 将 为 从 以 于 对 而 其 这 那 也 就 都 很 更 最 并 但 却 又 还 已 经 中 上 下 大 小 个 月 日 时 里 些 年 会 到 说 成 这 一 二 三 我们 我国 中国 记者 今天 日前 报道 消息 新华社 继续 表示 强调 指出 要求 坚持 同 央视网消息 央视网记者 新闻联播 节目 本期节目主要内容 查看原文 查看 原文 联合 播快讯 新闻摘要 详细新闻"
    .split(/\\s+/)
);

// 人物库：领导人/政治人物（出现即记人物热度）。可扩充。
const FIGURES = [
  '习近平', '赵乐际', '王沪宁', '蔡奇', '丁薛祥', '李强', '何立峰', '李希',
  '李干杰', '李书磊', '吴政隆', '谌贻琴', '王毅', '刘国中', '王小洪',
];

// 主题 → 触发词词典（按主导主题打标）
const TOPIC_RULES = [
  { topic: '经济',    words: ['经济','GDP','增长','投资','消费','贸易','产业','企业','外贸','汇率','就业','税收','改革','开放','市场','价格','制造业','高技术'] },
  { topic: '外交',    words: ['会谈','会见','访问','外交','大使','使馆','双边','合作','友好','国际','峰会','多边','元首'] },
  { topic: '党建政治', words: ['党员','党建','基层','干部','整治','纪律','作风','反腐','监察','全会','会议','政策','治国'] },
  { topic: '科技',    words: ['科技','创新','研发','芯片','航天','卫星','发射','算力','人工智能','量子','装备','技术','实验室'] },
  { topic: '民生'  ,  words: ['民生','医疗','医保','教育','养老','住房','就业','社保','救助','生活','食品','安全','交通'] },
  { topic: '生态'  ,  words: ['生态','环境','环保','碳','减排','水','森林','草原','污染','绿色','治理','保护','生物'] },
  { topic: '国防安全', words: ['军队','国防','军事','演习','巡航','安全','边境','海警','反恐','导弹','海军','空军'] },
  { topic: '文化体育', words: ['文化','文艺','体育','赛事','奥运','亚运','比赛','金牌','电影节','旅游','博物馆','非遗'] },
  { topic: '农业',    words: ['农业','粮食','农田','乡村振兴','丰收','农产品','种植','养殖','农村','农民'] },
];

// 剥 HTML 标签 & 实体 → 纯文本
function stripHtml(html) {
  return (html || '')
    .replace(/<[^>]+>/g, ' ')   // 去标签
    .replace(/&nbsp;|&amp;|&lt;|&gt;|&quot;/g, ' ') // 去实体
    .replace(/\s+/g, ' ')
    .trim();
}

// 简易中文切词：优先整词匹配词典(主题词/人物)，未覆盖的连续中文再按「定长非重叠」切长词
// —— 不引入 jieba：词典词整词计数，未知文本取 2-4 字且不重叠，避免同一段被切出成串碎片
function tokenize(text, topicWords) {
  const tokens = [];
  const dict = [...new Set(topicWords.concat(FIGURES))].sort((a,b)=>b.length-a.length);
  let rest = text;
  // 第一遍：整词命中词典（含人物），命中处用空格占位标记，避免二次滑窗重复切
  for (const w of dict) {
    const re = new RegExp(w, 'g');
    let m;
    const matched = new Set();
    while ((m = re.exec(rest))) {
      const idx = m.index;
      if (!matched.has(idx)) {
        matched.add(idx);
        tokens.push(w);
        // 把这段换成空格，防滑窗重切
        rest = rest.slice(0, idx) + ''.padEnd(w.length, ' ') + rest.slice(idx + w.length);
        re.lastIndex = idx + w.length; // 保持继续向后
      }
    }
  }
  // 第二遍：不滑窗——未知文本不强行切词，避免碎片污染热词。
  // 词频完全由「有意义词典」驱动，信号更干净（省 token 且可解释）。
  return tokens.filter(t => t.length > 0 && !STOP.has(t));
}

function quantify() {
  if (!fs.existsSync(STATS_DIR)) fs.mkdirSync(STATS_DIR, { recursive: true });

  // 取最新的 news/ 文件
  const files = fs.readdirSync(NEWS_DIR).filter(f => /^\d{8}\.md$/.test(f)).sort();
  const latest = files[files.length - 1];
  if (!latest) { console.error('无 news 文件'); return; }
  const date = latest.replace('.md', '');
  let md = fs.readFileSync(path.join(NEWS_DIR, latest), 'utf8');

  // 分则新闻（每一则以 ### 开头）
  const items = md.split(/^### /m).map(s => s.trim()).filter(Boolean);
  const newsItems = [];
  const plainItems = [];
  let abstract = '';
  const am = md.match(/## 新闻摘要\n\n([\s\S]*?)\n\n## 详细新闻/);
  if (am) abstract = stripHtml(am[1]);

  for (const item of items) {
    const title = item.split('\n')[0].trim();
    const body = item.slice(title.length).split('[查看原文]')[0];
    const text = stripHtml(body);
    plainItems.push({ title, text });
  }

  // 词频（跨全文）
  const allTopicWords = TOPIC_RULES.flatMap(r => r.words);
  const fullText = stripHtml(md);
  const docFreq = {};
  for (const t of tokenize(fullText, allTopicWords)) docFreq[t] = (docFreq[t] || 0) + 1;
  const topWords = Object.entries(docFreq).sort((a,b)=>b[1]-a[1]).slice(0, 40)
    .map(([w, n]) => ({ word: w, count: n }));

  // 人物热度
  const figureCount = {};
  for (const f of FIGURES) {
    const n = (fullText.match(new RegExp(f, 'g')) || []).length;
    if (n > 0) figureCount[f] = n;
  }
  const figures = Object.entries(figureCount).sort((a,b)=>b[1]-a[1]).map(([n, c]) => ({ name: n, mentions: c }));

  // 主题分布：每一则的主标题命中哪个主题，做「则数占比」
  const topicCount = {};
  for (const item of plainItems) {
    for (const r of TOPIC_RULES) {
      if (r.words.some(w => item.title.includes(w))) {
        topicCount[r.topic] = (topicCount[r.topic] || 0) + 1;
        break;
      }
    }
  }
  const topics = Object.entries(topicCount)
    // 没打中的也记一个「未分类」
    .sort((a,b)=>b[1]-a[1])
    .map(([t, n]) => ({ topic: t, items: n }));

  const stat = { date, abstract, itemsCount: plainItems.length, topWords, figures, topics };
  fs.writeFileSync(path.join(STATS_DIR, `${date}.json`), JSON.stringify(stat, null, 2));
  console.log(`STATS 已生成: stats/${date}.json (${plainItems.length} 则, ${topWords.length} 个热词)`);
}

quantify();
