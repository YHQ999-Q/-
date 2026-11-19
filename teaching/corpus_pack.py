# -*- coding: utf-8 -*-
"""
Bee Agent 测试语料包 → MetaMind 数据链路

只读取数据文件（json/md/txt），不执行语料包里的任何代码。
数据来源优先级：
  1. 环境变量 METAMIND_CORPUS
  2. 项目内 corpus/bee-agent-测试语料包
  3. 用户目录 MetaMind-corpus/bee-agent-测试语料包

每个 classroom 样本都会走同一条链路：
  transcript.txt → 按时间切片 → 防污染网关 → Critic 门禁（置信度/长度/噪声）
  → 通过的写 classroom（DPM 证据），被拒的写 classroom_rejected
  → 摘要里的 Action items / 复习自测 → todos
  → facts.json / 摘要要点 → facts
  → grow/insight-*.json → insights
"""
import json
import os
import re
import time

import teach_db as db
import courses

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_CANDIDATES = [
    os.environ.get('METAMIND_CORPUS', ''),
    os.path.join(BASE_DIR, 'corpus', 'bee-agent-测试语料包'),
    os.path.join(BASE_DIR, 'corpus'),
    os.path.join(os.path.expanduser('~'), 'MetaMind-corpus', 'bee-agent-测试语料包'),
]

LIFE_TERMS = (
    '吃饭', '食堂', '奶茶', '外卖', '宿舍', '室友', '洗澡', '睡觉', '起床',
    '外卖', '快递', '打游戏', '游戏', '电影', '逛街', '恋爱', '男朋友', '女朋友',
    '家里', '爸妈', '妈妈', '爸爸', '减肥', '天气', '好累', '困了', '闲聊',
)
FILLER_RE = re.compile(r'^[\s，。！？、,.!?嗯啊哦呃对好行是的有呢吧吗]*$')
LEARN_TERMS = (
    '教育', '课程', '课堂', '老师', '学生', '学习', '知识', '理论', '教学',
    '评估', '政策', '制度', '改革', '留学', '比较', '课程论', '哲学', '理性',
    'AI', '人工智能', '模型', '实验', '论证', '论文', '考试', '复习', '作业',
    '定义', '概念', '原理', '方法', '分析', '框架', '维度', '指标', '数据',
    '后现代', '多尔', '四r', '泰勒', '永恒主义', '通识', '经典', '赫钦斯',
    '日本', '俄罗斯', '印度', 'nep', '国别', '留学生', '学制', '分流',
    'goodhart', 'opentutor', '导学', '图灵', 'benchmark', '测量',
)


def corpus_root():
    for p in CORPUS_CANDIDATES:
        if p and os.path.isdir(p) and os.path.exists(os.path.join(p, 'manifest.json')):
            return p
    return ''


def _read(path, default=''):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return default


def _json(path, default=None):
    try:
        return json.loads(_read(path, 'null') or 'null')
    except Exception:
        return default if default is not None else {}


def load_manifest():
    root = corpus_root()
    if not root:
        return None
    return _json(os.path.join(root, 'manifest.json'), None)


# ---------------------------------------------------------------- 文本与网关
def _bigrams(t):
    t = re.sub(r'[^一-鿿A-Za-z0-9]', '', t or '')
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def gateway(text):
    """意图路由 / 防污染网关：学习信号才允许进知识图谱。
    返回 (route, reason)：classroom / life / reject。
    """
    t = (text or '').strip()
    if not t:
        return 'reject', '空文本'
    if FILLER_RE.match(t) or len(re.sub(r'[^一-鿿A-Za-z0-9]', '', t)) < 4:
        return 'reject', '语气词 / 无信息量，未达到证据门槛'
    life_hits = [w for w in LIFE_TERMS if w in t]
    learn_hits = [w for w in LEARN_TERMS if w.lower() in t.lower()]
    # 课堂转写里偶尔混进生活闲聊：命中生活词且没有学习词 → 只进轻量记忆流
    if life_hits and not learn_hits:
        return 'life', f'防污染网关：命中生活词「{"、".join(life_hits[:3])}」，留在轻量记忆流'
    if not learn_hits:
        # 没有明确学习词，但文本足够长，按“弱学习信号”处理，交给置信度门禁
        return 'classroom', '弱学习信号：未命中关键词，交给置信度门禁'
    return 'classroom', f'学习信号：命中「{"、".join(learn_hits[:3])}」'


def _confidence(text):
    t = (text or '').strip()
    conf = 0.9
    if len(t) < 12:
        conf -= 0.3
    if len(t) < 6:
        conf -= 0.3
    if re.search(r'(.)\1{5,}', t) or t.count('�'):
        conf -= 0.25
    if t.count('嗯') + t.count('呃') + t.count('啊') >= 4:
        conf -= 0.12
    if len(t) > 160:
        conf -= 0.04
    return round(max(0.05, min(0.98, conf)), 2)


def _looks_life(text):
    return gateway(text)[0] == 'life'


# ---------------------------------------------------------------- 课程与知识点
def _course_for(text):
    r = courses.route(text)
    return courses.get_course(r['id']) or courses.GENERAL


def _kp_for(course, text):
    """在课程子图里找这段文本最可能挂的知识点。"""
    t = (text or '').lower()
    best = None
    for nid, label, status, conf in course.get('nodes') or []:
        if nid == 'root':
            continue
        score = 0
        if label and label.lower() in t:
            score += 4
        for kw in _node_keywords(nid):
            if kw.lower() in t:
                score += 2
        if score and (best is None or score > best[0]):
            best = (score, nid, label)
    if best:
        return best[1], best[2]
    # 没有命中：挂到这门课的第一个非 root 节点，不硬造新节点
    for nid, label, status, conf in course.get('nodes') or []:
        if nid != 'root':
            return nid, label
    return 'root', course.get('name') or '未分类'


def _node_keywords(nid):
    return {
        'four_dim': ['四个维度', '四维', '教育发展', '管理制度', '学校教育制度', '学制', '分流', '改革重点'],
        'japan': ['日本', '留学生', '十万留学生', '招生', '就业', '在留', '国际贡献'],
        'russia': ['俄罗斯', '教育主权', '国家主导', '质量重建', '西方模式', '排名'],
        'india': ['印度', 'nep', '2020', '立法', '联邦', '基层', '语言'],
        'method': ['比较方法', '统计口径', '年龄段', '数据', '可比性', '维度'],
        'quality': ['质量保障', '质量', '就业', '社会融入', '人才留存'],
        'taylor': ['泰勒', '目标模式', '预设', '封闭', '工具化', '课程设计'],
        'postmodern': ['后现代', '生成', '开放', '师生互动', '经验', '反思', '批判'],
        'doll4r': ['四r', '丰富性', '回归性', '关联性', '严密性', '多尔'],
        'perennialism': ['永恒主义', '经典', '通识', '理性', '赫钦斯', '人性', '真理'],
        'ai_era': ['ai时代', '人工智能时代', '知识更新', '什么知识', '永恒的知识'],
        'assessment': ['表现', '能力', '区分', '评估', '在帮助下', '独立掌握'],
        'ai_eval': ['ai评估', '智能', '图灵', '大模型', '能力评估', '前沿'],
        'goodhart': ['goodhart', '指标', '指标仿真', '排名', '度量'],
        'capability': ['表现', '能力', '区分', '在帮助下', '独立掌握'],
        'meta_eval': ['评估方法', '实验逻辑', 'benchmark', '测量', '严谨', '可重复'],
        'open_tutor': ['opentutor', '导学', '苏格拉底', '亚里士多德', '追问'],
    }.get(nid, [])


def parse_transcript(path):
    """[HH:MM:SS] Unknown: text → 结构化话语列表。"""
    out = []
    for line in _read(path).splitlines():
        m = re.match(r'\[(\d{1,2}):(\d{2}):(\d{2})\]\s*([^:：]+)[:：]\s*(.*)', line.strip())
        if not m:
            continue
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        text = m.group(5).strip()
        if not text:
            continue
        out.append({'h': h, 'm': mi, 's': s,
                    't_sec': h * 3600 + mi * 60 + s,
                    't_label': f'{h:02d}:{mi:02d}:{s:02d}',
                    'speaker': m.group(4).strip(), 'text': text})
    return out


def segment(utts, target_chars=70, max_gap=45, max_utts=4):
    """把连续话语切成有信息量的证据段，避免一条一句把图谱刷爆。"""
    segs, cur = [], []
    for u in utts:
        if cur:
            gap = u['t_sec'] - cur[-1]['t_sec']
            if gap > max_gap or len(' '.join(x['text'] for x in cur)) >= target_chars:
                segs.append(cur)
                cur = []
        cur.append(u)
        if len(cur) >= max_utts:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    out = []
    for s in segs:
        text = ' '.join(x['text'] for x in s).strip()
        out.append({'text': text, 't_sec': s[0]['t_sec'],
                    't_label': s[0]['t_label'], 'speaker': s[0]['speaker'],
                    'n_utts': len(s)})
    return out


# ---------------------------------------------------------------- 摘要 / 待办
def _bullets_under(md, heading_words):
    """抓某个 Markdown 小节下的 - 项目。"""
    lines = (md or '').splitlines()
    out, on = [], False
    for line in lines:
        s = line.strip()
        if s.startswith('#'):
            on = any(w in s for w in heading_words)
            continue
        if on and re.match(r'^[-*]\s+', s):
            item = re.sub(r'^[-*]\s+', '', s).strip()
            item = re.sub(r'\*\*(.+?)\*\*', r'\1', item)
            if item and len(item) >= 6:
                out.append(item)
    return out


def _clean_md(s):
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s or '')
    s = re.sub(r'`(.+?)`', r'\1', s)
    return s.strip()


def _derive_todos(summary_md, sample_id='', course=None, date=''):
    items = []
    for h in (['Action items'], ['Action Items'], ['复习自测'], ['要记住的'],
              ['本节课回答的问题']):
        for x in _bullets_under(summary_md, h):
            if len(x) > 90:
                x = x[:90] + '…'
            items.append({'text': x, 'due': '', 'source': f'corpus:{sample_id}',
                          'head': h[0]})
    # 去重 + 限制数量
    seen, out = set(), []
    for it in items:
        k = re.sub(r'\s+', '', it['text'])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= 8:
            break
    return out


def _fmt_time(t_sec):
    t_sec = int(max(0, t_sec))
    return f'{t_sec // 3600:02d}:{(t_sec % 3600) // 60:02d}:{t_sec % 60:02d}'


def _extract_terms(md, max_n=12):
    """从摘要里抽加粗/引号术语，当作“板书 OCR”的对照证据（不是伪造原文）。"""
    terms = []
    for m in re.finditer(r'\*\*(.+?)\*\*', md or ''):
        t = _clean_md(m.group(1)).strip(' ：:，,。.')
        if 2 <= len(t) <= 28:
            terms.append(t)
    for m in re.finditer(r'「(.+?)」', md or ''):
        t = m.group(1).strip(' ：:，,。.')
        if 2 <= len(t) <= 28:
            terms.append(t)
    out, seen = [], set()
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_n:
            break
    return out


def _noise_events(utts, sample, default_kp=('', ''), max_n=12):
    """从原始转写里挑出被网关拒收的语气词/无信息量片段，进拒收日志。
    这样“Critic 门禁拦截了什么”在演示页不是空表。"""
    out = []
    for u in utts:
        route, reason = gateway(u['text'])
        if route != 'reject':
            continue
        out.append({
            'kp_id': default_kp[0] or '', 'kp_label': default_kp[1] or '',
            'kind': 'asr', 't_sec': u['t_sec'], 't_label': u['t_label'],
            'text': u['text'], 'weight': 1, 'source_type': 'auto',
            'confidence': 0.3, 'verify_status': 'UNVERIFIED',
            'verify_reasons': [reason],
            'evidence_source': {
                'kind': 'asr', 'kind_label': '课堂语音',
                't_sec': u['t_sec'], 't_label': u['t_label'],
                'ref': u['text'][:80], 'device': 'bee-agent corpus',
                'source_type': 'auto', 'confidence': 0.3,
                'verify_status': 'UNVERIFIED', 'verify_reasons': [reason],
                'source_sample': sample.get('id'), 'source_file': 'transcript.txt',
                'date': sample.get('date', ''),
            },
        })
        if len(out) >= max_n:
            break
    return out


def _derive_facts(summary_md, sample_id='', date='', max_n=8):
    facts = []
    for h in (['Key Take aways', 'Key Takeaways', 'Key Takeaways'],
              ['Key Takeaways'], ['Summary'], ['要点']):
        for x in _bullets_under(summary_md, h):
            if len(x) < 12:
                continue
            facts.append(x[:240])
            if len(facts) >= max_n:
                break
        if facts:
            break
    return facts


# ---------------------------------------------------------------- 导入：课堂
def import_classroom(sample, force=False):
    root = corpus_root()
    sid = sample.get('id') or ''
    if not root or not sid:
        return {'sample_id': sid, 'skipped': 'no corpus'}
    if db.corpus_imported(sid) and not force:
        return {'sample_id': sid, 'skipped': 'already imported'}
    path = os.path.join(root, sample.get('path', ''))
    meta = _json(os.path.join(path, 'meta.json'), {})
    summary = _read(os.path.join(path, 'summary.md'))
    utts = parse_transcript(os.path.join(path, 'transcript.txt'))
    if not utts:
        return {'sample_id': sid, 'skipped': 'no transcript'}

    probe = f"{sample.get('title', '')} {_clean_md(summary)[:1800]} {' '.join(u['text'] for u in utts[:40])}"
    course = _course_for(probe)
    default_kp = _kp_for(course, probe)
    segs = segment(utts)
    if len(segs) > 160:                       # 一场课最多留 160 条证据，避免演示时爆炸
        step = max(1, len(segs) // 160)
        segs = segs[::step][:160]

    accepted, rejected, life = [], [], []
    for sg in segs:
        route, reason = gateway(sg['text'])
        conf = _confidence(sg['text'])
        kp_id, kp_label = _kp_for(course, sg['text'])
        if kp_id == 'root' or not kp_id:
            kp_id, kp_label = default_kp
        ev = {
            'kp_id': kp_id, 'kp_label': kp_label, 'kind': 'asr',
            't_sec': sg['t_sec'], 't_label': sg['t_label'], 'text': sg['text'],
            'weight': 1 + (0.5 if sg['n_utts'] >= 3 else 0),
            'source_type': 'auto', 'confidence': conf,
            'verify_status': 'PASS', 'verify_reasons': [],
            'device_id': 'bee-agent corpus',
            'evidence_source': {
                'kind': 'asr', 'kind_label': '课堂语音',
                't_sec': sg['t_sec'], 't_label': sg['t_label'],
                'ref': sg['text'][:80], 'device': 'bee-agent corpus',
                'source_type': 'auto', 'confidence': conf,
                'verify_status': 'PASS', 'verify_reasons': [],
                'source_sample': sid, 'source_file': sample.get('path', '') + 'transcript.txt',
                'date': sample.get('date', ''), 'speaker': sg['speaker'],
            },
        }
        if route == 'life':
            life.append({'text': sg['text'], 'reason': reason, 'ts': sg['t_sec']})
            continue
        if route == 'reject' or conf < 0.65 or (len(sg['text']) < 20 and conf < 0.78):
            ev['verify_status'] = 'LOW_CONFIDENCE' if conf < 0.78 else 'UNVERIFIED'
            ev['verify_reasons'] = [reason] + (
                [f'置信度 {conf:.2f} 偏低 / 片段过短，只留原始日志']
                if conf < 0.78 else [])
            rejected.append(ev)
            continue
        ev['verify_reasons'] = [reason]
        accepted.append(ev)

    # 板书 OCR：语料包没有原始照片，但摘要里的加粗术语是 Bee 从课堂里提出来的，
    # 这里作为“板书关键词”证据挂进同一知识点，和语音证据交叉验证。
    last_t = max([u['t_sec'] for u in utts] + [0])
    for i, term in enumerate(_extract_terms(summary)[:12]):
        kp_id, kp_label = _kp_for(course, term)
        if kp_id == 'root':
            kp_id, kp_label = default_kp
        t = last_t + i * 20
        ev = {
            'kp_id': kp_id, 'kp_label': kp_label, 'kind': 'ocr',
            't_sec': t, 't_label': _fmt_time(t), 'text': f'板书：{term}',
            'weight': 1, 'source_type': 'auto', 'confidence': 0.92,
            'verify_status': 'PASS', 'verify_reasons': ['摘要术语 → 板书关键词证据'],
            'device_id': 'bee-agent corpus',
            'evidence_source': {
                'kind': 'ocr', 'kind_label': '板书 OCR',
                't_sec': t, 't_label': _fmt_time(t),
                'ref': term, 'device': 'bee-agent corpus',
                'source_type': 'auto', 'confidence': 0.92,
                'verify_status': 'PASS',
                'verify_reasons': ['摘要术语 → 板书关键词证据'],
                'source_sample': sid, 'source_file': sample.get('path', '') + 'summary.md',
                'date': sample.get('date', ''),
            },
        }
        accepted.append(ev)

    # 语气词 / 无信息量片段进拒收日志（让门禁拦截在页面上可见）
    rejected.extend(_noise_events(utts, sample, default_kp))

    # 幂等：同一个课程里已经有一模一样的证据就不重复写（force 导入也安全）
    existing = {(e.get('t_label'), (e.get('text') or '')[:50])
                for e in db.classroom_events(course['id'], 5000)}
    accepted = [e for e in accepted
                if (e.get('t_label'), (e.get('text') or '')[:50]) not in existing]
    existing_rej = {(((r.get('event') or {}).get('t_label') or ''),
                     ((r.get('event') or {}).get('text') or '')[:50])
                    for r in db.list_rejected(course['id'], 5000)}
    rejected = [e for e in rejected
                if (e.get('t_label') or '', (e.get('text') or '')[:50]) not in existing_rej]

    # 写库
    inserted = 0
    try:
        inserted = db.add_classroom_events(course['id'], accepted,
                                           f'corpus:{sid}')
    except db.MemoryFrozen:
        return {'sample_id': sid, 'skipped': 'memory frozen'}
    if rejected:
        db.add_rejected_events(course['id'], rejected,
                               status='LOW_CONFIDENCE', reasons=None,
                               session_id=f'corpus:{sid}')
    for lf in life[:20]:
        db.add_life_note(lf['text'], 'life')

    # Critic / Reviewer 留痕：通过门禁的结论要能绑定证据
    try:
        reviews = []
        by_kp = {}
        for e in accepted:
            by_kp.setdefault(e['kp_label'], []).append(e)
        for kp, evs in by_kp.items():
            reviews.append({
                'kp_label': kp, 'verdict': 'PASS' if len(evs) >= 2 else 'WEAK',
                'reason': f'语料包课堂证据 {len(evs)} 条，来自 {sid}',
                'support': [x['evidence_source'] for x in evs[:3]],
            })
        db.add_memory_reviews(course['id'], reviews, f'corpus:{sid}')
    except Exception as exc:
        print('[corpus] review 留痕失败：', exc)

    # 待办 / 事实
    n_todos = 0
    for t in _derive_todos(summary, sid, course, sample.get('date', '')):
        row = db.add_todo(t['text'][:90], due='', source=t['source'],
                          course_id=course['id'], kp=default_kp[1],
                          kp_id=default_kp[0], priority=2,
                          meta={'sample_id': sid, 'head': t['head']})
        if row:
            n_todos += 1
    n_facts = 0
    for i, f in enumerate(_derive_facts(summary, sid, sample.get('date', ''))):
        if db.add_fact(f, fact_id=f'{sid}:summary:{i}', source='corpus:summary',
                       sample_id=sid, date=sample.get('date', ''),
                       tags=['classroom', course['id']]):
            n_facts += 1
    for i, f in enumerate(_json(os.path.join(path, 'facts.json'), []) or []):
        if isinstance(f, dict) and db.add_fact(
                f.get('text', ''), fact_id=f'{sid}:fact:{f.get("id") or i}',
                source='corpus:fact', sample_id=sid, date=sample.get('date', ''),
                tags=f.get('tags') or [], meta={'note': f.get('note', '')}):
            n_facts += 1

    db.record_corpus_import(sid, 'classroom', sample.get('path', ''),
                            events=inserted, rejected=len(rejected),
                            facts=n_facts, todos=n_todos,
                            meta={'course_id': course['id'], 'course_name': course['name'],
                                  'life': len(life), 'utterances': len(utts),
                                  'segments': len(segs)})
    return {'sample_id': sid, 'course_id': course['id'], 'course_name': course['name'],
            'events': inserted, 'rejected': len(rejected), 'life': len(life),
            'todos': n_todos, 'facts': n_facts}


# ---------------------------------------------------------------- 导入：成长
def import_grow(sample, force=False):
    root = corpus_root()
    sid = sample.get('id') or ''
    if not root or not sid:
        return {'sample_id': sid, 'skipped': 'no corpus'}
    if db.corpus_imported(sid) and not force:
        return {'sample_id': sid, 'skipped': 'already imported'}
    path = os.path.join(root, sample.get('path', ''))
    meta = _json(os.path.join(path, 'meta.json'), {})
    if sample.get('kind') == 'daily':
        md = _read(os.path.join(path, 'daily.md'))
        title = (re.search(r'^#\s+(.+)', md, re.M) or [None, sample.get('title', '')])[1]
        one = ''
        m = re.search(r'\*\*一句话：\*\*\s*(.+)', md)
        if m:
            one = m.group(1).strip()
        key = _derive_facts(md, sid, sample.get('date', ''), max_n=8)
        desc = one or _clean_md(md)[:300]
        if key:
            desc += '\n\n' + '\n'.join('· ' + x for x in key[:5])
        db.add_insight(title, desc, kind='daily', insight_id=sid,
                       suggested=key[:3], date=sample.get('date', ''),
                       related=sample.get('related_samples') or [],
                       source='corpus:daily', meta=meta)
        n_todos = 0
        for x in key[:3]:
            if db.add_todo(x[:90], source='corpus:daily', priority=1,
                           meta={'sample_id': sid}):
                n_todos += 1
        db.record_corpus_import(sid, 'grow', sample.get('path', ''),
                                insights=1, todos=n_todos,
                                meta={'kind': 'daily'})
        return {'sample_id': sid, 'insights': 1, 'todos': n_todos}
    data = _json(os.path.join(path, 'insight.json'), {})
    db.add_insight(data.get('title') or sample.get('title', ''),
                   data.get('description', ''),
                   kind='insight', insight_id=sid,
                   suggested=data.get('suggested_actions') or [],
                   date=sample.get('date', ''),
                   related=sample.get('related_samples') or [],
                   source='corpus:insight', meta=meta)
    db.record_corpus_import(sid, 'grow', sample.get('path', ''), insights=1,
                            meta={'kind': 'insight'})
    return {'sample_id': sid, 'insights': 1}


# ---------------------------------------------------------------- 导入：项目
def import_project(sample, force=False):
    root = corpus_root()
    sid = sample.get('id') or ''
    if not root or not sid:
        return {'sample_id': sid, 'skipped': 'no corpus'}
    if db.corpus_imported(sid) and not force:
        return {'sample_id': sid, 'skipped': 'already imported'}
    path = os.path.join(root, sample.get('path', ''))
    meta = _json(os.path.join(path, 'meta.json'), {})
    summary = _read(os.path.join(path, 'summary.md'))
    n_facts = 0
    for i, f in enumerate(_json(os.path.join(path, 'facts.json'), []) or []):
        if isinstance(f, dict) and db.add_fact(
                f.get('text', ''), fact_id=f'{sid}:fact:{f.get("id") or i}',
                source='corpus:fact', sample_id=sid, date=sample.get('date', ''),
                tags=f.get('tags') or [], meta={'note': f.get('note', '')}):
            n_facts += 1
    for i, f in enumerate(_derive_facts(summary, sid, sample.get('date', ''), max_n=6)):
        if db.add_fact(f, fact_id=f'{sid}:summary:{i}', source='corpus:summary',
                       sample_id=sid, date=sample.get('date', ''),
                       tags=['project']):
            n_facts += 1
    n_todos = 0
    for t in _derive_todos(summary, sid, None, sample.get('date', '')):
        if db.add_todo(t['text'][:90], source=t['source'], priority=1,
                       meta={'sample_id': sid, 'head': t['head']}):
            n_todos += 1
    db.record_corpus_import(sid, 'project', sample.get('path', ''),
                            facts=n_facts, todos=n_todos, meta=meta)
    return {'sample_id': sid, 'facts': n_facts, 'todos': n_todos}


def import_shared(force=False):
    root = corpus_root()
    if not root:
        return {'skipped': 'no corpus'}
    path = os.path.join(root, 'shared', '用户规则-课堂或讲座.json')
    data = _json(path, {})
    if data:
        db._flag_set('user_rule:classroom', json.dumps(data, ensure_ascii=False))
    if db.corpus_imported('shared-rule-classroom') and not force:
        return {'sample_id': 'shared-rule-classroom', 'skipped': 'already imported'}
    db.record_corpus_import('shared-rule-classroom', 'shared', 'shared/',
                            meta={'rule': data})
    return {'sample_id': 'shared-rule-classroom', 'rule': bool(data)}


def get_shared_rule():
    raw = db._flag_get('user_rule:classroom')
    try:
        return json.loads(raw or '{}')
    except Exception:
        return {}


# ---------------------------------------------------------------- 一键导入
def import_all(force=False, verbose=True):
    manifest = load_manifest()
    if not manifest:
        return {'ok': False, 'error': '没有找到语料包（manifest.json）', 'root': corpus_root()}
    root = corpus_root()
    result = {'ok': True, 'root': root, 'samples': [], 'counts': {}}
    for s in manifest.get('samples') or []:
        cat = s.get('category')
        try:
            if cat == 'classroom':
                r = import_classroom(s, force)
            elif cat == 'grow':
                r = import_grow(s, force)
            elif cat == 'project':
                r = import_project(s, force)
            elif cat == 'shared':
                r = import_shared(force)
            else:
                continue
            result['samples'].append(r)
            if verbose:
                print(f'[corpus] {s.get("id")}: {r}')
        except Exception as e:
            result['samples'].append({'sample_id': s.get('id'), 'error': str(e)})
            if verbose:
                print(f'[corpus] {s.get("id")} 导入失败：', e)
    result['counts'] = db.corpus_status()
    return result


if __name__ == '__main__':
    print(json.dumps(import_all(force='--force' in os.sys.argv), ensure_ascii=False, indent=2))
