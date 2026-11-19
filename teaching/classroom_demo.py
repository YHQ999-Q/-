# -*- coding: utf-8 -*-
"""
两轮演示：同一个 10 步导学闭环，变的是 Agent 拿来判断的东西。

  第一轮（未进化）：只有静态大纲 + 本次这一句回答。
  第二轮（已进化）：子图谱里带着本节课的课堂时序证据
                   （板书 OCR / 课堂语音 ASR / 振动打点 + 时间戳）。

这里只做“把证据翻译成判断”，不碰数据库读写细节（那在 teach_db）。
每门课一张子图谱，课堂证据按 course_id 隔离，互不串味。
"""
import teach_db as db
import courses

# 课堂里每一处“打点”的原文。真实设备接进来时，走同一个 ingest 接口写进来即可。
COURSE_DEMO = {
    'math': [
        {'kp_id': 'integral', 'kp_label': '积分', 'kind': 'ocr', 't_sec': 160, 'weight': 1,
         'text': '板书：∫ x·cos x dx；老师写了「换元？」又划掉'},
        {'kp_id': 'integral', 'kp_label': '积分', 'kind': 'vibe', 't_sec': 245, 'weight': 3,
         'text': '此处停顿 47 秒，连续 3 次振动打点'},
        {'kp_id': 'integral', 'kp_label': '积分', 'kind': 'asr', 't_sec': 330, 'weight': 2,
         'text': '「这里为什么不能直接凑微分？」'},
        {'kp_id': 'equiv', 'kp_label': '等价无穷小', 'kind': 'asr', 't_sec': 400, 'weight': 2,
         'text': '「等价无穷小上节课我就没太懂」'},
        {'kp_id': 'equiv', 'kp_label': '等价无穷小', 'kind': 'ocr', 't_sec': 430, 'weight': 1,
         'text': '板书角落：sin x ~ x（只能乘除用），被圈了两次'},
        {'kp_id': 'limit', 'kp_label': '极限', 'kind': 'vibe', 't_sec': 455, 'weight': 1,
         'text': '提到极限时又停顿 22 秒，1 次打点'},
    ],
    'circuit': [
        {'kp_id': 'pullup', 'kp_label': '上拉电阻 R1→VCC', 'kind': 'ocr', 't_sec': 120, 'weight': 1,
         'text': '板书：R1 一端接 VCC，另一端写了一半又擦掉'},
        {'kp_id': 'pullup', 'kp_label': '上拉电阻 R1→VCC', 'kind': 'vibe', 't_sec': 200, 'weight': 2,
         'text': '停顿 35 秒，2 次振动打点'},
        {'kp_id': 'pullup', 'kp_label': '上拉电阻 R1→VCC', 'kind': 'asr', 't_sec': 260, 'weight': 2,
         'text': '「上拉电阻不是接电源吗，为什么 I2C 还要开漏？」'},
        {'kp_id': 'i2c', 'kp_label': 'I2C 总线时序', 'kind': 'asr', 't_sec': 300, 'weight': 2,
         'text': '「开漏到底是什么意思」'},
        {'kp_id': 'i2c', 'kp_label': 'I2C 总线时序', 'kind': 'vibe', 't_sec': 340, 'weight': 1,
         'text': '停顿 18 秒，1 次打点'},
        {'kp_id': 'i2c', 'kp_label': 'I2C 总线时序', 'kind': 'ocr', 't_sec': 380, 'weight': 1,
         'text': '板书：SDA/SCL 必须上拉；漏极开路只能拉低'},
    ],
    'english': [
        {'kp_id': 'tense', 'kp_label': '时态', 'kind': 'ocr', 't_sec': 90, 'weight': 1,
         'text': '板书：If I ___ (be) you, I would…；学生填了 was'},
        {'kp_id': 'tense', 'kp_label': '时态', 'kind': 'vibe', 't_sec': 150, 'weight': 2,
         'text': '停顿 25 秒，2 次振动打点'},
        {'kp_id': 'tense', 'kp_label': '时态', 'kind': 'asr', 't_sec': 210, 'weight': 2,
         'text': '「虚拟语气和时态到底怎么分」'},
        {'kp_id': 'clause', 'kp_label': '从句', 'kind': 'asr', 't_sec': 260, 'weight': 1,
         'text': '「从句里的时态要跟着主句变吗」'},
        {'kp_id': 'clause', 'kp_label': '从句', 'kind': 'vibe', 't_sec': 300, 'weight': 1,
         'text': '停顿 15 秒，1 次打点'},
        {'kp_id': 'clause', 'kp_label': '从句', 'kind': 'ocr', 't_sec': 330, 'weight': 1,
         'text': '板书：主将从现（时间 / 条件状语从句）'},
    ],
    'physics': [
        {'kp_id': 'newton', 'kp_label': '牛顿定律', 'kind': 'ocr', 't_sec': 100, 'weight': 1,
         'text': '板书：F=ma；斜面上物体受力图只画了重力'},
        {'kp_id': 'newton', 'kp_label': '牛顿定律', 'kind': 'vibe', 't_sec': 180, 'weight': 2,
         'text': '停顿 30 秒，2 次振动打点'},
        {'kp_id': 'newton', 'kp_label': '牛顿定律', 'kind': 'asr', 't_sec': 240, 'weight': 2,
         'text': '「斜面上到底要不要分解重力」'},
        {'kp_id': 'mech', 'kp_label': '力学', 'kind': 'asr', 't_sec': 280, 'weight': 1,
         'text': '「受力分析总漏摩擦力」'},
        {'kp_id': 'mech', 'kp_label': '力学', 'kind': 'vibe', 't_sec': 320, 'weight': 1,
         'text': '停顿 12 秒，1 次打点'},
        {'kp_id': 'mech', 'kp_label': '力学', 'kind': 'ocr', 't_sec': 350, 'weight': 1,
         'text': '板书：先画重力 → 支持力 → 摩擦力，顺序不能乱'},
    ],
}

def review_updates(course_id):
    """Reviewer 层：不信任采集侧的自述，直接拿绑定证据核对每条记忆结论。
    门槛：同一知识点 ≥2 条独立证据，或权重 ≥3 的强信号。
    不达标的结论只留证据，不进 Dw（薄弱点），避免“虚假进化”。"""
    summary = db.classroom_summary(course_id)
    items = []
    for it in summary['by_kp']:
        passed = db.support_passed(it)
        items.append({
            'kp_id': it['kp_id'], 'kp_label': it['kp_label'],
            'verdict': 'PASS' if passed else 'WEAK',
            'reason': (f'{it["count"]} 条证据、权重 {round(it["weight"], 1)}，达到支撑门槛'
                       if passed else
                       f'仅 {it["count"]} 条证据、权重 {round(it["weight"], 1)}，'
                       f'低于支撑门槛（≥2 条或权重≥3），不进入薄弱点'),
            'support': [x['evidence_source'] for x in it['items']],
        })
    return {
        'passed': len([x for x in items if x['verdict'] == 'PASS']),
        'weak': len([x for x in items if x['verdict'] == 'WEAK']),
        'items': items,
    }


STEP_NAMES = ['进入会话', '确定目标', '诊断前置检查', '问答交互', '按表现分流',
              '白板图形辅助', '记录帮助，换题独立练习', '更新技能图掌握度',
              '课后存档', '下次接续']
STEP_NO = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']


def sample_events(course_id):
    return COURSE_DEMO.get(course_id, [])


def _label_map(course):
    return {n[0]: n[1] for n in course['nodes']}


def _parent_map(course):
    parents = {}
    for a, b in course['edges']:
        parents.setdefault(b, []).append(a)
    return parents


def _ancestors(course, node_id):
    parents = _parent_map(course)
    seen, stack = set(), [node_id]
    while stack:
        x = stack.pop()
        for p in parents.get(x, []):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def _pick_focus(course, summary):
    """课堂打点最密的知识点 → 今天先解决它。"""
    label_of = _label_map(course)
    for item in summary.get('by_kp', []):
        if item['kp_id'] in label_of:
            return item['kp_id'], item['kp_label'], item
    if summary.get('by_kp'):
        it = summary['by_kp'][0]
        return it['kp_id'], it['kp_label'], it
    return None, None, None


def _pick_prereq(course, focus_id):
    """沿子图谱的边往上找前置；优先挑状态为「冲突」的那个。"""
    anc = _ancestors(course, focus_id)
    nodes = [n for n in course['nodes'] if n[0] in anc and n[0] != 'root']
    if not nodes:
        return None
    for n in nodes:                        # conflict 优先
        if n[2] == 'conflict':
            return {'id': n[0], 'label': n[1], 'status': n[2], 'conf': n[3]}
    n = nodes[0]
    return {'id': n[0], 'label': n[1], 'status': n[2], 'conf': n[3]}


def _evidence_of(summary, kp_id):
    for item in summary.get('by_kp', []):
        if item['kp_id'] == kp_id:
            return item
    return None


def _fmt_items(items, n=2):
    return '；'.join(f'{i["t_label"]} {i["kind_label"]}「{i["text"]}」' for i in items[:n])


def build_compare(course_id='math', focus_kp=''):
    course = courses.get_course(course_id) or courses.GENERAL
    syllabus = [n[1] for n in course['nodes'] if n[0] != 'root']
    summary = db.classroom_summary(course['id'])
    evolved = summary['total'] > 0
    dpm = db.learner_profile(course['id'])      # DPM：动态学习者记忆

    focus_id, focus_label, focus_item = _pick_focus(course, summary)
    if focus_kp:
        for n in course['nodes']:
            if n[0] == focus_kp or n[1] == focus_kp:
                focus_id, focus_label = n[0], n[1]
                focus_item = _evidence_of(summary, n[0])
                break
    if not focus_id:                       # 没有证据时，第一轮按大纲起步
        first = next((n for n in course['nodes'] if n[0] != 'root'), None)
        focus_id, focus_label = (first[0], first[1]) if first else ('', '未分类')

    prereq = _pick_prereq(course, focus_id) if evolved else None
    hist = db.kp_history(focus_label)
    hist_txt = (f'这个知识点历史作答 {hist["n"]} 次、错 {hist["wrong"]} 次'
                + (f'，错得最多是「{hist["types"][0][0]}」' if hist['types'] else ''))

    ev_items = (focus_item or {}).get('items', [])
    pre_item = _evidence_of(summary, prereq['id']) if prereq else None

    # ---------- 第一轮：静态大纲 ----------
    r1 = [
        ('同一入口开始；会话只有这一轮的内容',
         'SKG · 静态大纲'),
        (f'按课程大纲顺序：{" → ".join(syllabus[:4])}；今天从「{syllabus[0] if syllabus else "—"}」开始',
         'SKG · 大纲顺序'),
        ('跑固定诊断卷：3 道通用题（定义 / 常规计算 / 应用题），所有学生同一套',
         'SKG · 通用诊断'),
        ('同一套讲解模板：定义 → 例题 → 练习，不引用课堂原话', 'SKG · 通用模板'),
        ('只看这一题对不对：错 → 再讲一遍 + 换数字；对 → 下一题', '仅本次回答'),
        ('现场重新生成示意图，不引用课堂原板书', '现场生成'),
        ('记录这次给了什么帮助，换一道同知识点新题独立做', '同知识点换题'),
        ('只标「会 / 不会」，没有时间、没有来源', 'SKG · 会/不会'),
        ('存档：技能状态 + 本次结论', 'SKG · 技能快照'),
        ('下次从最后一道题往后翻', '按题号接续'),
    ]

    # ---------- 第二轮：带着课堂时序证据 ----------
    if evolved:
        vibe_n = summary['by_kind'].get('vibe', 0)
        ev_desc = (f'「{focus_label}」被打点 {focus_item["count"] if focus_item else 0} 次'
                   f'（{vibe_n} 次振动、权重 {int((focus_item or {}).get("weight", 0))}）'
                   if focus_item else f'「{focus_label}」')
        if prereq:
            diag = (f'先查子图谱的前置链：{focus_label} ← … ← 「{prereq["label"]}」'
                    f'（状态 {prereq["status"]}，置信度 {prereq["conf"]}）'
                    f'→ 只出 1 道前置诊断，不刷通用卷')
        else:
            diag = '查子图谱：没有更上游的缺口 → 诊断只考本知识点的一个混淆点'
        related = [it for it in summary.get('by_kp', [])
                   if it['kp_id'] not in (focus_id, prereq['id'] if prereq else '')]
        rel_txt = ''
        if related:
            it = related[0]
            rel_txt = (f'；课堂里还提到「{it["kp_label"]}」，它不在前置链上，'
                       f'判成概念混淆，连它的原证据一起调：{_fmt_items(it["items"], 1)}')
        branch = (f'错误先查历史（{hist_txt}）；判为前置缺口 → 退回补「{prereq["label"] if prereq else "上游知识点"}」，'
                  f'并调出课堂原证据：{_fmt_items(pre_item["items"] if pre_item else ev_items)}{rel_txt}')
        board = (f'直接调出课堂板书，不再重新生成：{_fmt_items([i for i in ev_items if i["kind"] == "ocr"] or ev_items, 1)}')
        r2 = [
            ('同一入口开始；DPM 已冻结，第二轮只读不写；会话里多带一份本节课课堂时序索引', '同一闭环'),
            (f'课堂打点最密的是 {ev_desc} → 今天直接从「{focus_label}」开始，不按大纲顺序',
             'DPM · 课堂时序'),
            (diag, 'SKG + DPM · 前置链'),
            (f'用课堂原话做锚点：{_fmt_items([i for i in ev_items if i["kind"] == "asr"] or ev_items, 1)} → 讲解接着这句话',
             'DPM · 课堂原话'),
            (branch, 'SKG + DPM · 历史 + 证据'),
            (board, 'DPM · 课堂板书'),
            (f'按前置关系换题：先 1 道「{prereq["label"] if prereq else "上游"}」确认，再回「{focus_label}」做变式',
             'SKG · 前置关系'),
            (f'把 {summary["total"]} 条课堂证据（板书 {summary["by_kind"]["ocr"]} / 语音 {summary["by_kind"]["asr"]} / '
             f'振动 {summary["by_kind"]["vibe"]}）连同时间戳挂到节点上，子图 v{course.get("version", 1)} → v{course.get("version", 1) + 1}',
             'DPM → SKG 入图'),
            (f'存档包：技能状态 + 结论 + 课堂时序索引（{_fmt_items([i for i in ev_items], 2)}）',
             'DPM · 带证据存档'),
            (f'下次直接读这份索引，跳过重复诊断，从「{focus_label}」+ 前置「{prereq["label"] if prereq else "—"}」继续',
             'DPM · 时序接续'),
        ]
    else:
        # 没注入证据时，右列就和左列一模一样——这正是“没进化”的样子
        r2 = [(t, '同左 · DPM 为空') for t, _ in r1]

    steps = []
    for i, (t1, src1) in enumerate(r1):
        t2, src2 = r2[i]
        steps.append({
            'no': STEP_NO[i], 'name': STEP_NAMES[i],
            'r1': {'text': t1, 'src': src1},
            'r2': {'text': t2, 'src': src2},
            'changed': (t1 != t2),
        })

    # ---------- 子图谱快照（证明按学科隔离） ----------
    ev_count = {it['kp_id']: it['count'] for it in summary.get('by_kp', [])}
    graph = {
        'course_id': course['id'], 'course_name': course['name'],
        'version': course.get('version', 1),
        'nodes': [{'id': n[0], 'label': n[1], 'status': n[2], 'conf': n[3],
                   'evidence': ev_count.get(n[0], 0)} for n in course['nodes']],
        'edges': [list(e) for e in course['edges']],
        'evidence_nodes': [n[1] for n in course['nodes'] if ev_count.get(n[0])],
    }

    return {
        'course': {'id': course['id'], 'name': course['name'], 'type': course['type']},
        'evolved': evolved,
        'frozen': db.is_frozen(course['id']),
        'dpm': dpm,
        'review': review_updates(course['id']),
        'focus': {'kp_id': focus_id, 'kp_label': focus_label,
                  'prereq': prereq,
                  'history': {'n': hist['n'], 'wrong': hist['wrong'],
                              'types': [t for t, _ in hist['types'][:3]]}},
        'steps': steps,
        'evidence': summary.get('events', []),
        'evidence_summary': summary.get('by_kp', []),
        'graph': graph,
        'next_action': ('第二轮：直接走进化后的判断' if evolved
                        else '第一轮：先注入本节课采集结果，再重放同一条闭环'),
    }


def build_trace(course_id='math'):
    """把 DPM 整理成 T1/T2/T3 三层痕迹，供现场审计。

    T1 会话元信息：哪门课、哪几堂、时间跨度、是否冻结 —— 只描述，不下结论
    T2 导学任务：今天解决什么、先查哪条前置、每条结论绑了哪几条证据
    T3 原始证据：每一条都带 ev_id + 秒数 + 通道 + 原文，是谁说的就去查那一秒

    三层靠 kp_id / ev_id 互相指，所以第二轮策略改变能一路追回某条课堂证据，
    而不是“AI 觉得你弱”。
    """
    course = courses.get_course(course_id) or courses.GENERAL
    cid = course['id']
    summary = db.classroom_summary(cid)
    evs = summary.get('events', [])
    profile = db.learner_profile(cid)
    cmp = build_compare(cid)
    focus = cmp['focus']

    # ---- T3：原始证据层（最小可信单元） ----
    t3 = [{
        'ev_id': e.get('id'),
        'kp_id': e.get('kp_id'), 'kp_label': e.get('kp_label'),
        'kind': e.get('kind'), 'kind_label': e.get('kind_label'),
        't_sec': e.get('t_sec'), 't_label': e.get('t_label'),
        'text': e.get('text'), 'weight': e.get('weight'),
        'session_id': e.get('session_id') or '',
        'prov': _load_prov(e),
    } for e in evs]

    ev_by_kp = {}
    for e in t3:
        ev_by_kp.setdefault(e['kp_id'] or e['kp_label'], []).append(e['ev_id'])

    # ---- T1：会话元信息层 ----
    secs = [e['t_sec'] or 0 for e in evs]
    sessions = sorted({e.get('session_id') for e in evs if e.get('session_id')})
    t1 = {
        'layer': 'T1 · 会话元信息',
        'course_id': cid, 'course_name': course['name'],
        'sessions': sessions or ['(本批证据未绑定会话，来自手动回放或设备直传)'],
        'n_events': summary['total'],
        'window': ({'from': db._mmss(min(secs)), 'to': db._mmss(max(secs))}
                   if evs else None),
        'by_kind': summary['by_kind'],
        'frozen': db.is_frozen(cid),
        'rule': '这一层只回答“哪门课、哪一堂、什么时候”，不解释任何结论。',
    }

    # ---- T2：导学任务层（结论 + 支撑证据指针） ----
    claims = []
    for item in summary.get('by_kp', []):
        passed = db.support_passed(item)
        claims.append({
            'claim': f'「{item["kp_label"]}」是本节课的卡点'
                     + ('（达到支撑门槛）' if passed else '（证据不足，未进 Dw）'),
            'kp_id': item['kp_id'], 'kp_label': item['kp_label'],
            'verdict': 'PASS' if passed else 'WEAK',
            'weight': round(item['weight'], 1),
            'supports': ev_by_kp.get(item['kp_id'] or item['kp_label'], []),
            'why': f'{item["count"]} 条证据、权重 {round(item["weight"], 1)}'
                   f'（门槛：≥2 条或权重≥3）',
        })

    prereq = focus.get('prereq')
    t2 = {
        'layer': 'T2 · 导学任务',
        'goal': (f'今天先解决「{focus["kp_label"]}」' if focus.get('kp_label')
                 else '还没有课堂证据，按 SKG 大纲起步'),
        'focus': {'kp_id': focus.get('kp_id'), 'kp_label': focus.get('kp_label')},
        'prereq': prereq,
        'strategy': (profile.get('Dr') or {}).get('active') or '',
        'claims': claims,
        'rule': '这一层的每句话都带 supports（ev_id 列表），可以逐条回溯到 T3 的原文。',
    }

    # ---- 可审计链：一条结论 → 支撑证据 → 原文 ----
    chain = []
    for c in claims:
        if c['verdict'] != 'PASS':
            continue
        rows = [e for e in t3 if e['ev_id'] in c['supports']]
        chain.append({
            'from': c['claim'],
            'via': [f'T3 ev#{e["ev_id"]} · {e["t_label"]} {e["kind_label"]}「{e["text"]}」'
                    for e in rows],
            'to': (f'T2 任务：先查前置「{prereq["label"]}」再讲本知识点'
                   if prereq else 'T2 任务：只诊断本知识点'),
        })

    return {
        'course': {'id': cid, 'name': course['name'], 'type': course['type']},
        'frozen': db.is_frozen(cid),
        'T1': t1, 'T2': t2, 'T3': t3,
        'chain': chain,
        'note': ('T1/T2/T3 是痕迹存储的分层；产品与技术清单里的 L1/L2/L3 '
                 '是自进化层级，两者不是一回事。'),
    }


def _load_prov(e):
    """取出这条证据的溯源元数据；读不出来就用行内字段兜底。"""
    try:
        import json as _json
        prov = _json.loads(e.get('prov_json') or '') or {}
    except Exception:
        prov = {}
    if prov:
        return prov
    return {'kind': e.get('kind', ''), 'kind_label': e.get('kind_label', ''),
            't_sec': e.get('t_sec'), 't_label': e.get('t_label'),
            'ref': (e.get('text') or '')[:60]}
