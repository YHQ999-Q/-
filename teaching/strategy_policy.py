# -*- coding: utf-8 -*-
"""
L2：策略选择（UCB）
L3：下一步动作（受限动作集）

边界：
  - 人定候选策略集、人定评价指标（二次正确率）、人定动作边界。
  - AI 只在这些候选里选，并给出可解释理由；不开放式乱试。
"""
import math

import teach_db as db


def _stats(course_id, error_type):
    """按课程 + 错因统计每个策略的历史表现。
    只算有 session 的 live attempts，不把冷启动种子数据混进来。"""
    with db._conn() as c:
        rows = c.execute('''
            SELECT a.strategy, COUNT(*) n, SUM(a.correct) w
            FROM attempts a
            JOIN sessions s ON a.session_id = s.id
            WHERE s.course_id = ? AND a.error_type = ?
            GROUP BY a.strategy
        ''', (course_id, error_type)).fetchall()
    return {r['strategy']: {'n': r['n'], 'wins': r['w'] or 0} for r in rows}


def _candidates(course_id, error_type, fallback=None):
    # 人定的候选集可以按课程不同；英语课不该用“数形结合”
    if course_id == 'english':
        cs = ['拆成两步的变式题', '先给思路不给答案', '检查清单']
    else:
        cs = list(db.ALT_STRATEGY.get(error_type, []))
    if fallback and fallback not in cs:
        cs.insert(0, fallback)
    if not cs:
        cs = [fallback or '反例优先', '拆成两步的变式题', '先给思路不给答案']
    return cs


def choose_strategy(course_id, error_type, fallback=None):
    """UCB1：样本少的策略先探索；样本够了看胜率。
    没有历史数据时用规则建议，不硬编一个“AI 选择”。"""
    cs = _candidates(course_id, error_type, fallback)
    stats = _stats(course_id, error_type)
    total = sum(v['n'] for v in stats.values())

    best, best_score = cs[0], -1.0
    breakdown = []
    for s in cs:
        st = stats.get(s, {'n': 0, 'wins': 0})
        if total == 0:
            score = 0.0
        elif st['n'] == 0:
            score = float('inf')          # 没试过 -> 先探索
        else:
            mean = st['wins'] / st['n']
            score = mean + math.sqrt(2 * math.log(total) / st['n'])
        breakdown.append({'strategy': s, 'n': st['n'], 'wins': st['wins'],
                          'rate': round(st['wins'] / st['n'], 2) if st['n'] else None,
                          'ucb': None if score == float('inf') else round(score, 3)})
        if score > best_score:
            best, best_score = s, score

    if total == 0:
        chosen = fallback or cs[0]
        reason = ('这门课还没有真实作答记录，先用规则建议的讲法；'
                  '交卷后开始按 UCB 在候选策略里选')
    else:
        st = stats.get(best, {'n': 0, 'wins': 0})
        rate = f"{st['wins'] / st['n']:.0%}" if st['n'] else '未尝试'
        reason = (f"UCB 在 {len(cs)} 个候选里选「{best}」："
                  f"历史 {st['n']} 次、胜率 {rate}；"
                  f"样本少的策略会被优先探索")

    return {'strategy': chosen if total == 0 else best,
            'method': 'ucb', 'reason': reason,
            'candidates': cs, 'breakdown': breakdown,
            'course_id': course_id, 'error_type': error_type}


def snapshot(course_id, error_type, fallback=None):
    return choose_strategy(course_id, error_type, fallback)


def next_action(course_id, latest=None, graph=None):
    """L3-lite：在 5 个允许动作里选一个，不开放式自创经验。"""
    latest = latest or {}
    nodes = (graph or {}).get('nodes') or []

    if latest and latest.get('verification') and latest['verification'] != 'PASS':
        return {'action': 'capture_more', 'label': '重新采集',
                'reason': '上一次证据没过校验，先别写记忆；重拍板书或把问题说清楚。',
                'course_id': course_id, 'target': 'capture'}

    if latest and not latest.get('total'):
        return {'action': 'probe_quiz', 'label': '先做 3 道自测',
                'reason': '刚生成的 3 道题还没做，先拿到“会不会”的信号，再决定下一步。',
                'course_id': course_id, 'target': 'quiz'}

    conflict = [n for n in nodes if n.get('status') == 'conflict']
    if conflict:
        return {'action': 'capture_more', 'label': '补一条新证据',
                'reason': f"子图里「{conflict[0].get('label', '')}」还是冲突状态；"
                          '再拍一张/说一句，让 Verifier 判断要不要改。',
                'course_id': course_id, 'target': 'capture'}

    low = [n for n in nodes if (n.get('conf') or 0) < 0.6 and n.get('id') != 'root']
    if low:
        return {'action': 'review_prereq', 'label': '补前置',
                'reason': f"「{low[0].get('label', '')}」置信度只有 {low[0].get('conf', 0):.2f}，"
                          '先把上游知识点补稳，再继续。',
                'course_id': course_id, 'target': low[0].get('id'),
                'target_label': low[0].get('label')}

    # 策略样本太少 -> 主动要一次诊断题
    et = latest.get('error_type') or '概念混淆'
    st = _stats(course_id, et)
    total = sum(v['n'] for v in st.values())
    if total < 5:
        return {'action': 'probe_quiz', 'label': '再测一次',
                'reason': f'这个错因目前只有 {total} 次作答，样本不足；'
                          '用新讲法再测 3 题，策略选择才可信。',
                'course_id': course_id, 'target': 'quiz'}

    return {'action': 'new_lecture', 'label': '进入下一节',
            'reason': '当前课程的知识点置信度和策略样本都够了，可以进入下一节课，'
                      '或者换一门课继续采集。',
            'course_id': course_id, 'target': 'capture'}
