# -*- coding: utf-8 -*-
"""
MetaMind 教学闭环 · 数据层（SQLite）

设计原则：
  1. 学习计划、错误指纹、策略版本，全部从 attempts（真实作答）算出来，
     不再返回写死的常量。冷启动时灌一份"历史学情基线"，之后每次作答都往上加。
  2. 一次采集 = 一条 session；讲解后立刻出 3 道自测题；
     作答结果写回 attempts，计划/画像/策略下一屏就变。
  3. 策略改变必须留下 diff 和理由，可回滚。

表：
  strategies  教学策略版本（v1/v3/v5/v7...）
  attempts    每一次作答（对/错、错因、知识点、当时用的策略）
  sessions    每一次"拍板书 + 提问"的完整记录
  state       杂项键值（当前 active 版本等）
"""
import json
import os
import random
import sqlite3
import time

import courses

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'metamind.db')

# 6 类错因固定，不自由命名（CONTRACT.md 的约定）
ERROR_TYPES = ['概念混淆', '步骤缺失', '计算失误', '读题理解', '记忆遗忘', '信心不足']
TYPE_KP = {
    '概念混淆': '极限',
    '步骤缺失': '中值定理',
    '计算失误': '积分',
    '读题理解': '导数',
    '记忆遗忘': '泰勒展开',
    '信心不足': '极限',
}
TYPE_NOTE = {
    '概念混淆': '等价无穷小、洛必达条件',
    '步骤缺失': '中值定理证明题',
    '计算失误': '分式化简',
    '读题理解': '条件最值',
    '记忆遗忘': '常用泰勒展开',
    '信心不足': '长期不做的题型',
}
TYPE_STRATEGY = {
    '概念混淆': '反例优先',
    '步骤缺失': '拆成两步的变式题',
    '计算失误': '检查清单',
    '读题理解': '先给思路不给答案',
    '记忆遗忘': '数形结合',
    '信心不足': '降难度成功体验',
}
ALT_STRATEGY = {
    '概念混淆': ['反例优先', '拆成两步的变式题', '数形结合'],
    '步骤缺失': ['拆成两步的变式题', '先给思路不给答案', '反例优先'],
    '计算失误': ['检查清单', '拆成两步的变式题', '先给思路不给答案'],
    '读题理解': ['先给思路不给答案', '反例优先', '拆成两步的变式题'],
    '记忆遗忘': ['数形结合', '反例优先', '拆成两步的变式题'],
    '信心不足': ['降难度成功体验', '先给思路不给答案', '拆成两步的变式题'],
}

# 冷启动策略：只有一条真实基线，不编历史成绩。
# 之后的版本必须由真实作答/真实采集触发，diff 和理由都可追溯。
SEED_VERSIONS = [
    {'id': 'v1', 'time': '冷启动', 'title': '人工规则集（尚无作答数据）',
     'status': 'active',
     'reason': '还没有人交过卷，先按规则路由运行；第一次自测作答后，才由真实正确率决定要不要升版。',
     'metrics': None,
     'diff': {'ctx': ['初始状态：按错因路由到教学策略'],
              'del': [],
              'add': ['概念混淆 → 反例优先',
                      '步骤缺失 → 拆成两步的变式题',
                      '记忆遗忘 → 数形结合']}},
]

# 冷启动基线：哪些错因、错了几次、总共做了几题、当时用的策略
# 数字与旧版页面上的画像一致；source='seed' 标明是历史数据，不是现场编的
SEED_BASE = [
    # type, wrong, total, strategy
    ('概念混淆', 12, 38, '反例优先'),
    ('步骤缺失', 8, 20, '拆成两步的变式题'),
    ('计算失误', 5, 18, '检查清单'),
    ('读题理解', 4, 12, '先给思路不给答案'),
    ('记忆遗忘', 3, 14, '数形结合'),
    ('信心不足', 2, 9, '降难度成功体验'),
]

# ---------------- 题库 ----------------
# 每个错因 3 道：第 1 道直击病根，第 2 道变式，第 3 道判断题
QUIZ_BANK = {
    '概念混淆': [
        {'q': '下面哪种替换是对的？',
         'options': ['sin x − x ~ 0', 'sin x ~ x（在 sin x / x 里）',
                     'tan x − x ~ 0', 'sin x + x ~ 2x'],
         'answer': 1,
         'why': '等价无穷小替换只在乘除因子中成立；加减法里直接换会吃掉主项。'},
        {'q': 'lim(x→0) (tan x − x) / x³ = ?',
         'options': ['0', '1/3', '1', '不存在'],
         'answer': 1,
         'why': 'tan x = x + x³/3 + o(x³)，减掉 x 后剩 x³/3。'},
        {'q': '判断：等价无穷小在加减法里可以直接替换。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。必须展开到能看见主项的阶数。'},
    ],
    '步骤缺失': [
        {'q': '罗尔定理不需要下面哪个条件？',
         'options': ['闭区间连续', '开区间可导', 'f(a)=f(b)', "f'(x)≠0"],
         'answer': 3,
         'why': "罗尔定理只要求连续、可导、两端函数值相等；f'(x)≠0 不是前提。"},
        {'q': 'f(a)≠f(b) 时，构造 F(x)=f(x)−[f(b)−f(a)]/(b−a)·(x−a) 的目的是？',
         'options': ['让 F(a)=F(b)', '让 F 连续', '让 F 可导', '让 F 单调'],
         'answer': 0,
         'why': '减去两端连线后，F(a)=F(b)，就能套罗尔定理。'},
        {'q': '判断：中值定理证明题，先构造辅助函数，再验证三个条件。',
         'options': ['对', '错'], 'answer': 0,
         'why': '对。构造是为了"变成能套的样子"，验证才是套定理。'},
    ],
    '计算失误': [
        {'q': '∫ x² dx = ?',
         'options': ['x³/3 + C', '2x + C', 'x³ + C', 'x²/2 + C'],
         'answer': 0, 'why': '幂函数积分：指数加 1，再除以新指数。'},
        {'q': '∫ sin x dx = ?',
         'options': ['cos x + C', '−cos x + C', '−sin x + C', 'sin x + C'],
         'answer': 1, 'why': 'sin 的原函数是 −cos，符号最容易丢。'},
        {'q': '判断：不定积分写完可以不加 +C。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。不定积分是函数族，必须写 +C。'},
    ],
    '读题理解': [
        {'q': '闭区间上求最值，第一步该看什么？',
         'options': ['只求驻点', '驻点 + 端点', '只求导数', '直接代 0'],
         'answer': 1, 'why': '闭区间最值 = 驻点与端点一起比。'},
        {'q': '"不等式恒成立"通常转化成？',
         'options': ['求最值', '求导数', '求积分', '求极限'],
         'answer': 0, 'why': '恒成立 ⇔ 左边最小值 ≥ 右边最大值（或相反）。'},
        {'q': '判断：看到"对任意 x 成立"，直接代 x=0 就能定参数。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。一个点只能给必要条件，还要验证全局。'},
    ],
    '记忆遗忘': [
        {'q': "(uv)' = ?",
         'options': ["u'v + uv'", "u'v'", "u'v − uv'", "uv"],
         'answer': 0, 'why': '乘积法则：前导后不导 + 前不导后导。'},
        {'q': "(x² sin x)' = ?",
         'options': ['2x sin x + x² cos x', '2x cos x', 'x² cos x',
                     '2x sin x − x² cos x'],
         'answer': 0, 'why': '直接套乘积法则，两项都不能丢。'},
        {'q': "判断：(uv)' = u'v'。",
         'options': ['对', '错'], 'answer': 1,
         'why': '错。这是最常见的记忆混淆。'},
    ],
    '信心不足': [
        {'q': '遇到长期不做的题型，第一步最该做什么？',
         'options': ['直接做压轴题', '先做一道同类型简单题',
                     '看答案', '跳过'],
         'answer': 1, 'why': '先建立一次成功体验，再回到原题。'},
        {'q': '做完一道难题后，最有效的巩固是？',
         'options': ['立刻做更难的', '复述刚才的关键一步',
                     '马上换科目', '只看答案'],
         'answer': 1, 'why': '复述能把"做出来"变成"讲得出来"。'},
        {'q': '判断：连续做错时，应该加量刷同类题。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。先降难度拿到一次成功，再恢复原难度。'},
    ],
}


# 按知识点出题：上传的是电路/几何，就出对应题，不能拿高数题糊弄
TOPIC_QUIZ = {
    '电路': [
        {'q': 'I2C 的 SDA/SCL 为什么要接上拉电阻？',
         'options': ['开漏输出只能拉低，高电平靠上拉', '为了提高电压',
                     '为了过滤噪声', '为了保护芯片'],
         'answer': 0,
         'why': 'I2C 是开漏结构，器件只能拉低；高电平必须由上拉电阻提供。'},
        {'q': '上拉电阻一端接 VCC，另一端接哪里？',
         'options': ['GND', 'GPIO4', 'SDA/SCL 信号线', 'VCC'],
         'answer': 2,
         'why': '上拉是"把信号线拉到高电平"，所以另一端接 SDA/SCL（或对应 GPIO）。'},
        {'q': '判断：上拉电阻应该接 GND。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。接 GND 是下拉，不是上拉。'},
    ],
    '几何': [
        {'q': '直角三角形两条直角边是 3 和 4，斜边是？',
         'options': ['5', '7', '12', '25'],
         'answer': 0, 'why': '3² + 4² = 25，斜边 = 5。'},
        {'q': '勾股定理适用于哪类三角形？',
         'options': ['所有三角形', '直角三角形', '等腰三角形', '等边三角形'],
         'answer': 1, 'why': 'a² + b² = c² 只对直角三角形成立。'},
        {'q': '判断：a² + b² = c² 对任意三角形成立。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。只有直角三角形，且 c 必须是斜边。'},
    ],
    '力学': [
        {'q': '牛顿第二定律 F = ma 里，a 的方向与什么一致？',
         'options': ['速度方向', '合外力方向', '位移方向', '摩擦方向'],
         'answer': 1, 'why': '加速度方向始终与合外力方向一致。'},
        {'q': '受力分析的第一步是？',
         'options': ['列方程', '选研究对象并画受力图', '代入数值', '求加速度'],
         'answer': 1, 'why': '先隔离研究对象，再画全所有外力。'},
        {'q': '判断：受力分析可以先分解力，再画受力图。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。先画全力，再按坐标系分解，否则容易漏力。'},
    ],
    '英语语法': [
        {'q': 'He ___ to school every day.',
         'options': ['go', 'goes', 'going', 'gone'],
         'answer': 1, 'why': '一般现在时，第三人称单数动词加 -es。'},
        {'q': 'If it ___ tomorrow, we will stay home.',
         'options': ['rains', 'rain', 'rained', 'raining'],
         'answer': 0, 'why': '主将从现：主句用 will，条件从句用一般现在时。'},
        {'q': '判断：定语从句里 that 和 which 在任何情况下都能互换。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。介词后、非限制性定语从句等情况不能用 that。'},
    ],
}


# 复测题库：和第一遍不同的题，否则“验证新策略”就是假的
TOPIC_RETRY = {
    '电路': [
        {'q': '上拉电阻从 10kΩ 改成 1kΩ，最可能的后果是？',
         'options': ['高电平更稳', '低电平拉不下去，功耗还变大', '没影响', '芯片立刻烧毁'],
         'answer': 1, 'why': '上拉太小会和开漏器件“打架”，低电平拉不低，静态电流也变大。'},
        {'q': 'I2C 总线上完全没有上拉电阻，SDA 会怎样？',
         'options': ['一直是低电平', '一直悬空，读不到稳定高电平', '完全正常', '变成时钟线'],
         'answer': 1, 'why': '开漏只能拉低，没有上拉就没有人把线拉高。'},
        {'q': '判断：多个 I2C 器件可以共用一组上拉电阻。',
         'options': ['对', '错'], 'answer': 0,
         'why': '对。总线是共享的，通常只放一组上拉。'},
    ],
    '几何': [
        {'q': '直角三角形斜边是 5，一条直角边是 3，另一条是？',
         'options': ['4', '2', '√34', '8'],
         'answer': 0, 'why': '5² − 3² = 16，另一条直角边 = 4。'},
        {'q': '判断：勾股定理里的 c 可以是任意一条边。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。c 必须是斜边，也就是直角所对的那条边。'},
        {'q': '等腰直角三角形直角边为 1，斜边是？',
         'options': ['1', '√2', '2', '√3'],
         'answer': 1, 'why': '1² + 1² = 2，斜边 = √2。'},
    ],
    '力学': [
        {'q': '物体做匀速直线运动时，合外力是？',
         'options': ['0', 'ma', '重力', '摩擦力'],
         'answer': 0, 'why': '匀速直线运动加速度为 0，所以合外力为 0。'},
        {'q': '斜面上的物体，重力通常分解成哪两个方向？',
         'options': ['水平 + 竖直', '沿斜面下滑 + 垂直斜面', '沿斜面向上 + 水平', '不需要分解'],
         'answer': 1, 'why': '沿斜面和垂直斜面分解，列方程最方便。'},
        {'q': '判断：摩擦力方向一定与运动方向相反。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。摩擦力阻碍的是相对运动/相对运动趋势，不一定与运动方向相反。'},
    ],
    '英语语法': [
        {'q': 'She ___ English for three years.',
         'options': ['learns', 'has learned', 'learned', 'learning'],
         'answer': 1, 'why': 'for + 时间段，用现在完成时 has learned。'},
        {'q': 'The book ___ I bought yesterday is very useful.',
         'options': ['who', 'which', 'where', 'when'],
         'answer': 1, 'why': '先行词是物，定语从句用 which/that。'},
        {'q': '判断：主句用过去时，宾语从句一定要用过去时。',
         'options': ['对', '错'], 'answer': 1,
         'why': '错。客观真理等例外仍用一般现在时。'},
    ],
}

QUIZ_RETRY = {
    '概念混淆': [
        {'q': 'lim(x→0) (sin x)/x = ?',
         'options': ['0', '1', '∞', '不存在'],
         'answer': 1, 'why': '这是等价无穷小的标准用法：乘除里 sin x ~ x。'},
        {'q': 'lim(x→0) (1 − cos x)/x² = ?',
         'options': ['0', '1/2', '1', '2'],
         'answer': 1, 'why': '1 − cos x ~ x²/2，所以极限是 1/2。'},
        {'q': 'lim(x→0) (x − sin x)/x³ = ?',
         'options': ['0', '1/6', '1/3', '1'],
         'answer': 1, 'why': 'sin x = x − x³/6 + o(x³)，减掉 x 后剩 −x³/6。'},
    ],
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS strategies (
            id TEXT PRIMARY KEY,
            time TEXT,
            title TEXT,
            status TEXT,
            reason TEXT,
            metrics_json TEXT,
            diff_json TEXT,
            error_type TEXT,
            strategy TEXT,
            created_ts REAL
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            session_id TEXT,
            kp TEXT,
            error_type TEXT,
            strategy TEXT,
            question TEXT,
            chosen INTEGER,
            correct INTEGER,
            source TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            ts REAL,
            asr TEXT,
            ocr TEXT,
            conclusion TEXT,
            steps_json TEXT,
            strategy TEXT,
            version TEXT,
            error_type TEXT,
            kp TEXT,
            correct INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            quiz_json TEXT,
            course_id TEXT DEFAULT '',
            course_name TEXT DEFAULT '',
            verification TEXT DEFAULT 'PASS'
        );
        CREATE TABLE IF NOT EXISTS state (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            title TEXT,
            reason TEXT,
            diff_json TEXT,
            node_id TEXT,
            session_id TEXT
        );
        -- 课堂时序证据：板书 OCR / 课堂语音 ASR / 振动打点，全部带时间戳，
        -- 按 course_id 挂在对应子图谱的知识点上（两轮演示的“进化原材料”）
        CREATE TABLE IF NOT EXISTS classroom (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT,
            kp_id TEXT,
            kp_label TEXT,
            kind TEXT,              -- ocr / asr / vibe / human
            t_sec REAL,             -- 课堂内第几秒
            t_label TEXT,           -- 00:04:05 这种给人看的时间
            text TEXT,
            weight REAL DEFAULT 1,  -- 振动打点一次算 1，停顿越久权重越高
            session_id TEXT,
            created_ts REAL,
            prov_json TEXT DEFAULT '',  -- 溯源元数据：这条记忆来自哪条原始证据
            source_type TEXT DEFAULT 'auto',   -- auto / human_input
            confidence REAL DEFAULT 1.0        -- 0-1，低于阈值只留日志
        );
        CREATE INDEX IF NOT EXISTS idx_classroom_course ON classroom(course_id);
        -- 记忆更新审查（Reviewer 层）：每条结论能否被绑定证据支撑
        CREATE TABLE IF NOT EXISTS memory_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            kp_label TEXT,
            verdict TEXT,           -- PASS / WEAK（证据不足）
            reason TEXT,
            support_json TEXT,      -- 绑定的 evidence_source 列表
            session_id TEXT
        );
        -- 校验未通过的课堂证据：只留原始日志，不进 DPM、不进决策上下文
        CREATE TABLE IF NOT EXISTS classroom_rejected (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            event_json TEXT,
            status TEXT,
            reasons TEXT,
            session_id TEXT
        );
        -- 记忆冲突：新证据和已有记忆矛盾时不自动覆盖，留两条等人确认
        CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            kp_id TEXT,
            kp_label TEXT,
            new_event_json TEXT,
            existing_json TEXT,
            reason TEXT,
            source_type TEXT,
            session_id TEXT,
            status TEXT DEFAULT 'pending'   -- pending / accepted / dismissed
        );
        -- 人工纠错样本：冲突确认时沉淀下来，回灌给 Verify / Critic 做 few-shot
        CREATE TABLE IF NOT EXISTS verifier_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            kp_label TEXT,
            text TEXT,
            action TEXT,          -- accept（人工确认采用）/ reject（人工拒绝）
            reason TEXT,
            session_id TEXT
        );
        -- 生活 / 灵感碎片：不进知识图谱，单独一条轻量记忆流
        CREATE TABLE IF NOT EXISTS life_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            text TEXT,
            tag TEXT DEFAULT 'life'   -- life / classroom_suggest
        );
        -- 学习证据：自测/问答的结果反哺到知识点与变更记录
        CREATE TABLE IF NOT EXISTS learning_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            node_id TEXT,
            kp TEXT,
            source TEXT,              -- quiz / chat
            correct INTEGER,
            total INTEGER,
            error_type TEXT,
            strategy_version TEXT,
            note TEXT,
            session_id TEXT
        );
        -- 统一策略库：5001 strategies 与 OpenTutor skill_versions 的物理合并读模型
        CREATE TABLE IF NOT EXISTS strategy_registry (
            id TEXT PRIMARY KEY,          -- teaching:v9 / opentutor:<uuid>
            source TEXT,                  -- teaching_5001 / opentutor
            name TEXT,
            version TEXT,
            status TEXT,
            strategy TEXT,
            change_summary TEXT,
            trigger_json TEXT,
            target_json TEXT,
            steps_json TEXT,
            metrics_json TEXT,
            source_evaluation_id TEXT,
            source_reflection_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        -- 待办：可以来自语料包、录音抽取、教学 Agent 自动生成
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            text TEXT,
            due TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            course_id TEXT DEFAULT '',
            kp TEXT DEFAULT '',
            kp_id TEXT DEFAULT '',
            status TEXT DEFAULT 'open',      -- open / done
            done_ts REAL,
            priority INTEGER DEFAULT 0,
            meta_json TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
        -- 我的事实：来自 project/sample-* 的 facts.json 与摘要 Action items
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            fact_id TEXT,
            text TEXT,
            source TEXT DEFAULT '',
            sample_id TEXT DEFAULT '',
            date TEXT DEFAULT '',
            tags_json TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',    -- active / duplicate
            meta_json TEXT DEFAULT ''
        );
        -- 成长洞察：来自 grow/insight-* 与每日回顾
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            insight_id TEXT,
            kind TEXT DEFAULT 'insight',     -- insight / daily
            title TEXT,
            description TEXT,
            suggested_json TEXT DEFAULT '[]',
            date TEXT DEFAULT '',
            related_json TEXT DEFAULT '[]',
            source TEXT DEFAULT 'corpus',
            status TEXT DEFAULT 'active',
            meta_json TEXT DEFAULT ''
        );
        -- 教学 Agent 会话：消息带上下文（哪条洞察 / 哪个薄弱点带进来的）
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            session_id TEXT,
            role TEXT,
            content TEXT,
            context_json TEXT DEFAULT '',
            strategy_level INTEGER DEFAULT 1,
            strategy_label TEXT DEFAULT '',
            meta_json TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agent_session ON agent_messages(session_id);
        -- 待办完成 → 回写 DPM：这条薄弱点已经复习过一次
        CREATE TABLE IF NOT EXISTS mastery_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            course_id TEXT,
            kp TEXT,
            kp_id TEXT DEFAULT '',
            source TEXT DEFAULT 'todo',
            todo_id INTEGER,
            note TEXT DEFAULT ''
        );
        -- 语料包导入记录（幂等：同一个 sample 不重复灌）
        CREATE TABLE IF NOT EXISTS corpus_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            sample_id TEXT UNIQUE,
            category TEXT,
            path TEXT,
            events INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            facts INTEGER DEFAULT 0,
            todos INTEGER DEFAULT 0,
            insights INTEGER DEFAULT 0,
            meta_json TEXT DEFAULT ''
        );
        ''')
        # 旧库迁移：给 sessions 补课程字段
        cols = [r['name'] for r in c.execute('PRAGMA table_info(sessions)').fetchall()]
        if 'course_id' not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN course_id TEXT DEFAULT ''")
        if 'course_name' not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN course_name TEXT DEFAULT ''")
        if 'verification' not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN verification TEXT DEFAULT 'PASS'")
        ccols = [r['name'] for r in c.execute('PRAGMA table_info(classroom)').fetchall()]
        if ccols and 'prov_json' not in ccols:
            c.execute("ALTER TABLE classroom ADD COLUMN prov_json TEXT DEFAULT ''")
        if ccols and 'source_type' not in ccols:
            c.execute("ALTER TABLE classroom ADD COLUMN source_type TEXT DEFAULT 'auto'")
        if ccols and 'confidence' not in ccols:
            c.execute("ALTER TABLE classroom ADD COLUMN confidence REAL DEFAULT 1.0")

        n = c.execute('SELECT COUNT(*) n FROM strategies').fetchone()['n']
        if n == 0:
            for i, v in enumerate(SEED_VERSIONS):
                c.execute('''INSERT INTO strategies
                    (id,time,title,status,reason,metrics_json,diff_json,error_type,strategy,created_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                          (v['id'], v['time'], v['title'], v['status'], v['reason'],
                           json.dumps(v['metrics'], ensure_ascii=False),
                           json.dumps(v['diff'], ensure_ascii=False),
                           '概念混淆', '反例优先', time.time() - (len(SEED_VERSIONS) - i) * 86400))

        # 迁移：旧版编造过 v3/v5/v7 和 48%→63% 这类数字，全部删掉，只留诚实基线
        old = {r['id']: r for r in c.execute('SELECT * FROM strategies').fetchall()}
        if any(x in old for x in ('v3', 'v5', 'v7')):
            c.execute("DELETE FROM strategies WHERE id IN ('v3','v5','v7')")
            if 'v1' in old:
                other_active = c.execute(
                    "SELECT COUNT(*) n FROM strategies WHERE status='active' AND id!='v1'"
                ).fetchone()['n']
                c.execute('''UPDATE strategies SET title=?, reason=?, metrics_json=NULL,
                             diff_json=?, time=?, status=? WHERE id='v1' ''',
                          ('人工规则集（尚无作答数据）',
                           '还没有人交过卷，先按规则路由运行；第一次自测作答后，才由真实正确率决定要不要升版。',
                           json.dumps(SEED_VERSIONS[0]['diff'], ensure_ascii=False),
                           '冷启动', 'retired' if other_active else 'active'))

        n = c.execute('SELECT COUNT(*) n FROM attempts').fetchone()['n']
        if n == 0:
            _seed_attempts(c)


def _seed_attempts(c):
    """灌一份近 30 天的历史作答。错题时间分布刻意做出趋势：
    概念混淆/步骤缺失/信心不足 在下降，读题理解 在上升。"""
    rng = random.Random(42)
    now = time.time()
    # 每个类型：(错误的时间分布(天前), 正确的时间分布)
    wrong_days = {
        '概念混淆': [1, 3, 6, 9, 12, 16, 20, 23, 26, 27, 28, 29],
        '步骤缺失': [2, 7, 11, 15, 19, 22, 25, 28],
        '计算失误': [4, 10, 18, 24, 27],
        '读题理解': [1, 2, 4, 9],
        '记忆遗忘': [5, 17, 26],
        '信心不足': [20, 29],
    }
    for typ, wrong, total, strategy in SEED_BASE:
        days = wrong_days[typ]
        for i in range(wrong):
            d = days[i % len(days)]
            c.execute('''INSERT INTO attempts
                (ts,session_id,kp,error_type,strategy,question,chosen,correct,source)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                      (now - d * 86400 - rng.randint(0, 8000), 'seed', TYPE_KP[typ],
                       typ, strategy, '（历史作答）', -1, 0, 'seed'))
        for i in range(total - wrong):
            d = rng.randint(0, 30)
            c.execute('''INSERT INTO attempts
                (ts,session_id,kp,error_type,strategy,question,chosen,correct,source)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                      (now - d * 86400 - rng.randint(0, 8000), 'seed', TYPE_KP[typ],
                       typ, strategy, '（历史作答）', -1, 1, 'seed'))


# ---------------- 读 ----------------
def _since(days):
    return time.time() - days * 86400


SOLO_LEVELS = {
    1: ('前结构', '回答偏离问题，或存在根本性理解错误'),
    2: ('单点结构', '能说出一个正确的点，但很片面'),
    3: ('多点结构', '知道很多点，但还没串起来'),
    4: ('关联结构', '能解释概念之间的关系，形成完整逻辑'),
    5: ('拓展抽象', '能迁移、归纳、创造，用到陌生场景'),
}


def _solo_level(rows):
    """SOLO 层级：按真实作答的对错结构估，不靠模型瞎猜。"""
    if not rows:
        return None
    n = len(rows)
    ok = len([r for r in rows if r['correct']])
    rate = ok / n
    if rate == 0:
        return 1
    if rate < 0.35:
        return 2
    if rate < 0.65:
        return 3
    if rate < 0.85:
        return 4
    return 5


def _solo_summary(rows):
    """取作答最多的一门课作为"当前层级"，并给出目标层级和证据。"""
    by_kp = {}
    for r in rows:
        by_kp.setdefault(r['kp'], []).append(r)
    if not by_kp:
        return {'current': 1, 'name': SOLO_LEVELS[1][0], 'desc': SOLO_LEVELS[1][1],
                'target': 2, 'target_name': SOLO_LEVELS[2][0],
                'evidence': '还没有作答记录，先做一次自测才能定位。',
                'per_kp': []}

    per_kp = []
    for kp, rs in by_kp.items():
        lv = _solo_level(rs)
        per_kp.append({'kp': kp, 'level': lv, 'n': len(rs),
                       'rate': round(len([r for r in rs if r['correct']]) / len(rs), 2)})
    per_kp.sort(key=lambda x: -x['n'])
    main = per_kp[0]
    lv = main['level'] or 1

    wrong = [r for r in rows if not r['correct']]
    top_err = None
    if wrong:
        counts = {}
        for r in wrong:
            counts[r['error_type']] = counts.get(r['error_type'], 0) + 1
        top_err = max(counts, key=counts.get)
    ev = f'「{main["kp"]}」最近 {main["n"]} 道自测，正确率 {int(main["rate"]*100)}%'
    if top_err:
        ev += f'；错得最多的是「{top_err}」'
    if lv == 2:
        ev += '。你能说出一个点，但换个问法就散。'
    elif lv == 3:
        ev += '。你知道好几步，但讲不清它们之间的先后和因果。'
    elif lv == 4:
        ev += '。你能串起来了，差的是迁移到没见过的新题。'
    elif lv == 5:
        ev += '。这一步你已经能自己造例子了。'
    else:
        ev += '。方向还没对，先回到最基础的定义。'

    target = min(5, lv + 1)
    return {
        'current': lv, 'name': SOLO_LEVELS[lv][0], 'desc': SOLO_LEVELS[lv][1],
        'target': target, 'target_name': SOLO_LEVELS[target][0],
        'target_desc': SOLO_LEVELS[target][1],
        'evidence': ev, 'per_kp': per_kp,
    }


def get_profile():
    with _conn() as c:
        live_rows = c.execute(
            "SELECT * FROM attempts WHERE ts >= ? AND source='live'", (_since(30),)
        ).fetchall()
        if live_rows:
            rows, baseline = live_rows, False      # 有真实作答就只算真实作答
        else:
            rows = c.execute('SELECT * FROM attempts WHERE ts >= ?', (_since(30),)).fetchall()
            baseline = True                        # 否则展示冷启动基线，前端会标注
    fp = []
    for typ in ERROR_TYPES:
        mine = [r for r in rows if r['error_type'] == typ]
        wrong = [r for r in mine if not r['correct']]
        last7 = len([r for r in wrong if r['ts'] >= _since(7)])
        prev7 = len([r for r in wrong if _since(14) <= r['ts'] < _since(7)])
        trend = 'up' if last7 > prev7 else ('down' if last7 < prev7 else 'flat')
        fp.append({'type': typ, 'count': len(wrong), 'trend': trend,
                   'note': TYPE_NOTE[typ]})
    mastery = []
    kps = []
    for r in rows:
        if r['kp'] and r['kp'] not in kps:
            kps.append(r['kp'])
    for kp in kps[:6]:
        mine = [r for r in rows if r['kp'] == kp]
        if not mine:
            continue
        correct = len([r for r in mine if r['correct']])
        mastery.append({'kp': kp, 'value': round(correct / len(mine), 2)})
    return {
        'student': {'name': '陈默', 'stage': '大四 · 考研数学', 'days_left': 37},
        'fingerprint': fp,
        'mastery': mastery,
        'solo': _solo_summary(rows),
        'baseline': baseline,      # True = 还全是冷启动演示数据，前端要标出来
    }


def get_plan():
    """从最近 14 天的错题里，挑复发最多的一个错因。"""
    cutoff = _since(14)
    with _conn() as c:
        live = c.execute("SELECT COUNT(*) n FROM attempts WHERE source='live'").fetchone()['n']
        cond = "AND source='live'" if live else ""
        rows = c.execute(f'''SELECT error_type, kp, strategy, COUNT(*) n
                            FROM attempts
                            WHERE ts >= ? AND correct = 0 {cond}
                            GROUP BY error_type, kp, strategy
                            ORDER BY n DESC LIMIT 1''', (cutoff,)).fetchone()
        total = c.execute(f'SELECT COUNT(*) n FROM attempts WHERE ts >= ? {cond}',
                          (cutoff,)).fetchone()['n']
    if not rows:
        if live:
            return {
                'title': '今天保持：最近 14 天没有新的同类错题',
                'reason': f'最近 14 天有 {total} 道真实作答，没有新的错题。继续按当前节奏，明天复测一次。',
                'duration_min': 8,
                'items': [{'kp': '复习', 'task': '把上次的 3 道复测题再做一遍'}],
                'baseline': False,
            }
        return {
            'title': '今天只做一件事：先测一次，让系统知道你在哪卡住',
            'reason': '还没有足够的作答记录。按一次手表按钮，拍一道题、说一句"哪里没懂"。',
            'duration_min': 10,
            'items': [{'kp': '待诊断', 'task': '完成一次采集 + 3 道自测'}],
            'baseline': True,
        }
    typ, kp, strategy, n = rows['error_type'], rows['kp'], rows['strategy'], rows['n']
    strategy = active_strategy_for(typ) or strategy      # 写现在线上用哪套，不写历史那套
    rate = _strategy_rate(typ, strategy, live_only=bool(live))
    pct = f'{round(rate * 100)}%' if rate is not None else '暂无'
    if live == 0:
        reason = (f'冷启动演示基线：{total} 条历史样本，其中 {n} 道同类错。'
                  f'你上传第一道题并交卷后，这里会换成你的真实数据。')
    else:
        reason = (f'最近 14 天做了 {total} 道自测，有 {n} 道栽在同一个坑；'
                  f'当前策略「{strategy}」的历史二次正确率 {pct}。')
    return {
        'title': f'今天只做一件事：把「{kp}」的{typ}压下去',
        'reason': reason,
        'duration_min': min(30, 10 + 4 * n),
        'items': [
            {'kp': kp, 'task': f'错解辨析 ×3（针对{typ}）'},
            {'kp': kp, 'task': '变式题 ×2（不是原题重做）'},
        ],
        'baseline': live == 0,
    }


def _strategy_rate(error_type, strategy, live_only=False):
    extra = " AND source='live'" if live_only else ""
    with _conn() as c:
        row = c.execute(f'''SELECT COUNT(*) n, SUM(correct) w FROM attempts
                           WHERE error_type=? AND strategy=?{extra}''',
                        (error_type, strategy)).fetchone()
    if not row or not row['n']:
        return None
    return (row['w'] or 0) / row['n']


def record_change(title, reason, diff, node_id='', session_id=''):
    with _conn() as c:
        c.execute('''INSERT INTO changes (ts,title,reason,diff_json,node_id,session_id)
                     VALUES (?,?,?,?,?,?)''',
                  (time.time(), title, reason,
                   json.dumps(diff, ensure_ascii=False), node_id, session_id))


def add_learning_evidence(course_id, node_id, kp, source, correct, total,
                          error_type='', strategy_version='', note='', session_id=''):
    """把一次自测/问答结果写成证据，并同步一条变更记录。

    这是“反哺”：知识图谱页能看到这条证据，变更记录能看到它为何写入。
    不直接改节点置信度——置信度仍由课堂证据/校验决定，避免一次考试就翻盘。
    """
    with _conn() as c:
        cur = c.execute('''INSERT INTO learning_evidence
            (ts,course_id,node_id,kp,source,correct,total,error_type,
             strategy_version,note,session_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (time.time(), course_id or '', node_id or '', kp or '',
                         source or 'quiz', int(correct or 0), int(total or 0),
                         error_type or '', strategy_version or '', note or '',
                         session_id or ''))
        evidence_id = cur.lastrowid
    record_change(
        f'学习证据写入「{kp or "知识点"}」',
        f'{source}：{correct}/{total}；错因 {error_type or "未标注"}；'
        f'策略 {strategy_version or "未记录"}',
        {'ctx': [f'course: {course_id}', f'node: {node_id}'],
         'add': [f'{kp or "知识点"} ← {source} {correct}/{total}'],
         'del': []},
        node_id=node_id or '', session_id=session_id or '')
    return evidence_id


def list_learning_evidence(course_id='', node_id='', limit=100):
    with _conn() as c:
        where, args = [], []
        if course_id:
            where.append('course_id=?')
            args.append(course_id)
        if node_id:
            where.append('node_id=?')
            args.append(node_id)
        sql = 'SELECT * FROM learning_evidence'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY ts DESC LIMIT ?'
        args.append(limit)
        rows = c.execute(sql, args).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item['time'] = time.strftime('%m-%d %H:%M', time.localtime(item.get('ts') or 0))
        out.append(item)
    return out


def list_strategies():
    with _conn() as c:
        rows = c.execute('SELECT * FROM strategies ORDER BY created_ts ASC').fetchall()
        chs = c.execute('SELECT * FROM changes ORDER BY ts ASC').fetchall()
    out = []
    for r in rows:
        out.append({
            'id': r['id'], 'time': r['time'], 'title': r['title'],
            'status': r['status'], 'reason': r['reason'],
            'metrics': json.loads(r['metrics_json']) if r['metrics_json'] else None,
            'diff': json.loads(r['diff_json']) if r['diff_json'] else
                    {'ctx': [], 'del': [], 'add': []},
            'error_type': r['error_type'], 'strategy': r['strategy'],
            'kind': 'strategy', '_sort': r['created_ts'] or 0,
        })
    for r in chs:
        out.append({
            'id': f"k{r['id']}", 'time': time.strftime('%m-%d %H:%M', time.localtime(r['ts'])),
            'title': r['title'], 'status': 'info', 'reason': r['reason'],
            'metrics': None,
            'diff': json.loads(r['diff_json']) if r['diff_json'] else
                    {'ctx': [], 'del': [], 'add': []},
            'error_type': '', 'strategy': '', 'node_id': r['node_id'],
            'kind': 'knowledge', '_sort': r['ts'] or 0,
        })
    out.sort(key=lambda x: x['_sort'])
    active = next((v['id'] for v in reversed(out)
                   if v.get('kind') == 'strategy' and v['status'] == 'active'), None)
    for v in out:
        v.pop('_sort', None)
    return {'versions': list(reversed(out)), 'active': active}


def rollback_strategy(vid):
    with _conn() as c:
        row = c.execute('SELECT * FROM strategies WHERE id=?', (vid,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE strategies SET status='retired' WHERE status='active'")
        c.execute("UPDATE strategies SET status='active' WHERE id=?", (vid,))
    return True


# ---------------- 写 ----------------
def save_session(sid, asr, ocr, conclusion, steps, strategy, version, error_type, kp, quiz,
                 course_id='', course_name='', verification='PASS'):
    with _conn() as c:
        c.execute('''INSERT OR REPLACE INTO sessions
            (id,ts,asr,ocr,conclusion,steps_json,strategy,version,error_type,kp,quiz_json,
             course_id,course_name,verification)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (sid, time.time(), asr, ocr, conclusion,
                   json.dumps(steps, ensure_ascii=False), strategy, version,
                   error_type, kp, json.dumps(quiz, ensure_ascii=False),
                   course_id, course_name, verification))


def list_sessions(limit=30):
    """历史：每次采集/分析都存了一条，这里按时间倒序给前端。"""
    with _conn() as c:
        rows = c.execute("SELECT id, ts, kp, error_type, conclusion, asr, ocr, "
                         "strategy, correct, total, steps_json, course_id, course_name, "
                         "verification FROM sessions ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['steps'] = json.loads(d.pop('steps_json') or '[]')[:4]
        except Exception:
            d['steps'] = []
        d['ocr'] = (d.get('ocr') or '')[:200]
        d['asr'] = (d.get('asr') or '')[:120]
        out.append(d)
    return out


def get_session(sid):
    with _conn() as c:
        r = c.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
    return dict(r) if r else None


def active_strategy_for(error_type):
    with _conn() as c:
        r = c.execute('''SELECT strategy FROM strategies
                         WHERE status='active' AND error_type=?
                         ORDER BY created_ts DESC LIMIT 1''', (error_type,)).fetchone()
    if r and r['strategy']:
        return r['strategy']
    return TYPE_STRATEGY.get(error_type, '反例优先')


def create_retry_session(src_sid, new_sid):
    """策略刚切换后，用同一道题/同一错因，按新策略再出 3 道复测题。"""
    s = get_session(src_sid)
    if not s:
        return None
    strategy = active_strategy_for(s['error_type'])
    quiz = make_quiz(s['error_type'], s['kp'], retry=True)
    save_session(new_sid, s['asr'], s['ocr'], '换一种讲法，再测一次',
                 json.loads(s['steps_json'] or '[]'), strategy, s['version'],
                 s['error_type'], s['kp'], quiz,
                 s.get('course_id', ''), s.get('course_name', ''),
                 s.get('verification', 'PASS'))
    return {'session_id': new_sid, 'error_type': s['error_type'], 'kp': s['kp'],
            'strategy': strategy, 'items': public_quiz(quiz)}


def make_quiz(error_type, kp, retry=False):
    # 先按知识点出题（电路图就出电路题）；复测优先用另一套题
    items = None
    if retry:
        items = TOPIC_RETRY.get(kp) or QUIZ_RETRY.get(error_type)
    items = items or TOPIC_QUIZ.get(kp) or QUIZ_BANK.get(error_type) or QUIZ_BANK['概念混淆']
    prefix = f'{kp}-复测' if retry else kp
    return [{'id': f'{prefix}-{i}', 'q': it['q'], 'options': it['options'],
             'answer': it['answer'], 'why': it['why'], 'kp': kp,
             'error_type': error_type} for i, it in enumerate(items)]


def public_quiz(items):
    return [{'id': it['id'], 'q': it['q'], 'options': it['options'],
             'kp': it['kp'], 'error_type': it['error_type']} for it in items]


def grade_quiz(sid, answers, node_id=''):
    """answers: [int,int,int]。返回逐题结果 + 更新后的计划/画像/策略。"""
    s = get_session(sid)
    if not s:
        return None
    items = json.loads(s['quiz_json'] or '[]')
    if not items:
        return None
    results, correct_n = [], 0
    with _conn() as c:
        for it, chosen in zip(items, answers):
            ok = int(chosen == it['answer'])
            correct_n += ok
            c.execute('''INSERT INTO attempts
                (ts,session_id,kp,error_type,strategy,question,chosen,correct,source)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                      (time.time(), sid, it['kp'], it['error_type'],
                       s['strategy'] or TYPE_STRATEGY[it['error_type']],
                       it['q'], chosen, ok, 'live'))
            results.append({'id': it['id'], 'chosen': chosen, 'answer': it['answer'],
                            'correct': bool(ok), 'why': it['why']})
        c.execute('UPDATE sessions SET correct=?, total=? WHERE id=?',
                  (correct_n, len(items), sid))
    evidence_id = add_learning_evidence(
        s.get('course_id') or '', node_id, s.get('kp') or '',
        'quiz', correct_n, len(items), s.get('error_type') or '',
        s.get('version') or '', f'课堂自测：{correct_n}/{len(items)}', sid)
    evolved = maybe_evolve(sid, s['error_type'], s['strategy'], correct_n, len(items))
    return {
        'ok': True,
        'correct': correct_n,
        'total': len(items),
        'results': results,
        'evolved': evolved,
        'evidence_id': evidence_id,
        'plan': get_plan(),
        'profile': get_profile(),
        'evolution': list_strategies(),
    }


def maybe_evolve(sid, error_type, strategy, correct, total):
    """一次自测正确率 <= 1/3，就认为当前策略对这个错因失效，
    切到下一条候选策略，生成新版本（可回滚）。样本小，所以理由是"先切，明天复测"。"""
    if total <= 0 or correct / total > 1 / 3:
        return None
    error_type = error_type or '概念混淆'
    strategy = strategy or TYPE_STRATEGY[error_type]
    alts = [x for x in ALT_STRATEGY.get(error_type, []) if x != strategy]
    if not alts:
        return None
    with _conn() as c:
        # 1 小时内同一错因已经切过，不重复刷版本
        last = c.execute('''SELECT created_ts FROM strategies
                            WHERE error_type=? ORDER BY created_ts DESC LIMIT 1''',
                         (error_type,)).fetchone()
        if last and time.time() - last['created_ts'] < 3600:
            return None
        row = c.execute('SELECT MAX(CAST(SUBSTR(id,2) AS INTEGER)) m FROM strategies').fetchone()
        next_n = (row['m'] or 7) + 1
        vid = f'v{next_n}'
        alt = alts[0]
        acc = round(correct / total, 2)
        c.execute("UPDATE strategies SET status='retired' WHERE status='active'")
        c.execute('''INSERT INTO strategies
            (id,time,title,status,reason,metrics_json,diff_json,error_type,strategy,created_ts)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (vid, time.strftime('%m-%d %H:%M'), f'「{error_type}」改用「{alt}」',
                   'active',
                   f'本次 {total} 道自测只对 {correct} 道，当前「{strategy}」对这个错因失效；'
                   f'先切到「{alt}」，明天用同一错因复测。',
                   json.dumps({'old': acc, 'new': None, 'n': total}, ensure_ascii=False),
                   json.dumps({'ctx': [f'error_type: {error_type}'],
                               'del': [f'策略：{strategy}'],
                               'add': [f'策略：{alt}（本场自测 {correct}/{total} 后切换）']},
                              ensure_ascii=False),
                   error_type, alt, time.time()))
    try:
        sync_local_strategies()
    except Exception:
        pass
    return vid


# =====================================================================
# 课堂时序证据（板书 OCR / 课堂语音 / 振动打点）—— 按课程子图谱隔离
# =====================================================================
KIND_LABEL = {'ocr': '板书识别', 'asr': '课堂语音', 'vibe': '振动打点',
              'human': '人工补录'}


def _mmss(sec):
    sec = int(sec or 0)
    return f'{sec // 60:02d}:{sec % 60:02d}'


class MemoryFrozen(Exception):
    """DPM 已冻结：第二轮只读，任何写入都会被拒绝（保证两轮对比可复现）。"""


def _flag_get(k):
    with _conn() as c:
        r = c.execute('SELECT v FROM state WHERE k=?', (k,)).fetchone()
    return r['v'] if r else None


def _flag_set(k, v):
    with _conn() as c:
        c.execute('INSERT OR REPLACE INTO state (k,v) VALUES (?,?)', (k, v))


def is_frozen(course_id):
    return _flag_get(f'dpm_frozen:{course_id}') == '1'


def freeze_info(course_id):
    at = _flag_get(f'dpm_frozen_at:{course_id}')
    return {'frozen': is_frozen(course_id), 'at': float(at) if at else None}


def freeze_memory(course_id, frozen=True):
    """冻结 = 把当前课堂证据拍一份快照存进 state，之后只读快照。
    这样第二轮导学哪怕继续聊天，也不会篡改用于对比的记忆。"""
    if frozen:
        rows = _classroom_rows(course_id, 10 ** 6)
        for r in rows:                     # 快照里带上中文来源名
            r['kind_label'] = KIND_LABEL.get(r['kind'], r['kind'])
        _flag_set(f'dpm_snapshot:{course_id}',
                  json.dumps(rows, ensure_ascii=False))
        # 连 Ds/Dw/Dr 整份画像一起冻：此刻还没置 frozen，读到的是实时值
        prof = learner_profile(course_id)
        _flag_set(f'dpm_profile:{course_id}',
                  json.dumps(prof, ensure_ascii=False))
        _flag_set(f'dpm_frozen_at:{course_id}', str(time.time()))
        _flag_set(f'dpm_frozen:{course_id}', '1')
    else:
        _flag_set(f'dpm_frozen:{course_id}', '0')
        _flag_set(f'dpm_snapshot:{course_id}', '')
        _flag_set(f'dpm_profile:{course_id}', '')
        _flag_set(f'dpm_frozen_at:{course_id}', '')
    return freeze_info(course_id)


# =====================================================================
# 命名快照：snapshot_0（第一轮干净状态）/ snapshot_1（进化后），可一键回滚
# =====================================================================
def save_named_snapshot(course_id, name, label=''):
    rows = _classroom_rows(course_id, 10 ** 6)
    payload = {'name': name, 'label': label or name, 'ts': time.time(),
               'frozen': is_frozen(course_id), 'rows': rows,
               'profile': learner_profile(course_id)}
    _flag_set(f'snap:{course_id}:{name}', json.dumps(payload, ensure_ascii=False))
    return {'name': name, 'label': payload['label'], 'ts': payload['ts'],
            'rows': len(rows), 'frozen': payload['frozen']}


def list_named_snapshots(course_id):
    with _conn() as c:
        rows = c.execute('SELECT v FROM state WHERE k LIKE ?',
                         (f'snap:{course_id}:%',)).fetchall()
    out = []
    for r in rows:
        try:
            d = json.loads(r['v'] or '{}')
        except Exception:
            continue
        if not d.get('name'):
            continue
        out.append({'name': d['name'], 'label': d.get('label') or d['name'],
                    'ts': d.get('ts'), 'rows': len(d.get('rows') or []),
                    'frozen': bool(d.get('frozen'))})
    return sorted(out, key=lambda x: x['ts'] or 0)


def restore_named_snapshot(course_id, name):
    """把课堂记忆整份退回某个快照：先解冻、清空、按快照重写，再按快照状态决定是否冻结。"""
    raw = _flag_get(f'snap:{course_id}:{name}')
    if not raw:
        return None
    d = json.loads(raw)
    freeze_memory(course_id, False)
    clear_classroom(course_id)
    rows = d.get('rows') or []
    with _conn() as c:
        for r in rows:
            c.execute('''INSERT INTO classroom
                (course_id,kp_id,kp_label,kind,t_sec,t_label,text,weight,session_id,
                 created_ts,prov_json,source_type,confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (course_id, r.get('kp_id', ''), r.get('kp_label', ''),
                       r.get('kind', ''), float(r.get('t_sec') or 0),
                       r.get('t_label') or _mmss(r.get('t_sec')),
                       r.get('text', ''), float(r.get('weight') or 1),
                       r.get('session_id') or '', r.get('created_ts') or time.time(),
                       r.get('prov_json') or '', r.get('source_type') or 'auto',
                       float(r.get('confidence') if r.get('confidence') is not None else 1.0)))
    if d.get('frozen'):
        freeze_memory(course_id, True)
    return {'name': name, 'rows': len(rows), 'frozen': bool(d.get('frozen'))}


def _classroom_rows(course_id=None, limit=300):
    """直接读表（不看冻结），只在写快照时用。"""
    with _conn() as c:
        if course_id:
            rows = c.execute('''SELECT * FROM classroom WHERE course_id=?
                                ORDER BY t_sec ASC LIMIT ?''',
                             (course_id, limit)).fetchall()
        else:
            rows = c.execute('SELECT * FROM classroom ORDER BY created_ts DESC LIMIT ?',
                             (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_classroom_events(course_id, events, session_id=''):
    """写一批课堂证据。events 每项：
    {kp_id, kp_label, kind: ocr|asr|vibe|human, t_sec, text, weight,
     source_type: auto|human_input, confidence: 0-1}
    同一门课只进自己的子图谱，不跨课程混。
    记忆冻结期间拒绝写入——第二轮只读。"""
    if is_frozen(course_id):
        raise MemoryFrozen('DPM 已冻结：第二轮导学只读，先解冻才能写入新证据')
    now = time.time()
    n = 0
    with _conn() as c:
        for e in events or []:
            t_sec = float(e.get('t_sec') or 0)
            t_label = e.get('t_label') or _mmss(t_sec)
            source_type = e.get('source_type') or 'auto'
            confidence = float(e.get('confidence') if e.get('confidence') is not None else 1.0)
            # 溯源元数据：每条记忆都要能指回原始证据（哪一秒、哪一路、原文是什么）
            prov = e.get('evidence_source') or {
                'kind': e.get('kind', ''), 'kind_label': KIND_LABEL.get(e.get('kind'), ''),
                't_sec': t_sec, 't_label': t_label,
                'ref': (e.get('text') or '')[:60],
                'device': e.get('device_id', ''),
                'source_type': source_type,
                'confidence': confidence,
                'verify_status': e.get('verify_status'),
                'verify_reasons': e.get('verify_reasons') or [],
            }
            c.execute('''INSERT INTO classroom
                (course_id,kp_id,kp_label,kind,t_sec,t_label,text,weight,session_id,
                 created_ts,prov_json,source_type,confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (course_id, e.get('kp_id', ''), e.get('kp_label', ''),
                       e.get('kind', ''), t_sec, t_label,
                       e.get('text', ''), float(e.get('weight') or 1),
                       session_id, now,
                       json.dumps(prov, ensure_ascii=False),
                       source_type, confidence))
            n += 1
    return n


# =====================================================================
# 记忆冲突：新证据和已有记忆矛盾 → 不自动覆盖，留两条记录等人确认
# =====================================================================
def add_conflict(course_id, event, existing, reason, session_id=''):
    with _conn() as c:
        cur = c.execute('''INSERT INTO memory_conflicts
            (ts,course_id,kp_id,kp_label,new_event_json,existing_json,reason,source_type,session_id,status)
            VALUES (?,?,?,?,?,?,?,?,?,'pending')''',
                        (time.time(), course_id,
                         event.get('kp_id', ''), event.get('kp_label', ''),
                         json.dumps(event, ensure_ascii=False),
                         json.dumps(existing, ensure_ascii=False),
                         reason or '', event.get('source_type') or 'auto',
                         session_id))
        return cur.lastrowid


def list_conflicts(course_id='', status='pending', limit=50):
    with _conn() as c:
        if course_id:
            rows = c.execute('''SELECT * FROM memory_conflicts
                                WHERE course_id=? AND status=?
                                ORDER BY ts DESC LIMIT ?''',
                             (course_id, status, limit)).fetchall()
        else:
            rows = c.execute('''SELECT * FROM memory_conflicts
                                WHERE status=? ORDER BY ts DESC LIMIT ?''',
                             (status, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ('new_event_json', 'existing_json'):
            try:
                d[k.replace('_json', '')] = json.loads(d.pop(k) or '{}')
            except Exception:
                d[k.replace('_json', '')] = {}
        out.append(d)
    return out


def get_conflict(cid):
    with _conn() as c:
        r = c.execute('SELECT * FROM memory_conflicts WHERE id=?', (cid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ('new_event_json', 'existing_json'):
        try:
            d[k.replace('_json', '')] = json.loads(d.pop(k) or '{}')
        except Exception:
            d[k.replace('_json', '')] = {}
    return d


def set_conflict_status(cid, status):
    with _conn() as c:
        c.execute('UPDATE memory_conflicts SET status=? WHERE id=?', (status, cid))
    return get_conflict(cid)


# =====================================================================
# 人工纠错样本：给 Verify / Critic 做 few-shot，让纠错真的反哺下一轮
# =====================================================================
def add_feedback_sample(course_id, kp_label, text, action, reason='', session_id=''):
    with _conn() as c:
        c.execute('''INSERT INTO verifier_feedback
            (ts,course_id,kp_label,text,action,reason,session_id)
            VALUES (?,?,?,?,?,?,?)''',
                  (time.time(), course_id, kp_label or '', text or '',
                   action, reason or '', session_id or ''))
    return True


def list_feedback_samples(course_id='', limit=5):
    with _conn() as c:
        if course_id:
            rows = c.execute('''SELECT * FROM verifier_feedback WHERE course_id=?
                                ORDER BY ts DESC LIMIT ?''', (course_id, limit)).fetchall()
        else:
            rows = c.execute('SELECT * FROM verifier_feedback ORDER BY ts DESC LIMIT ?',
                             (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_feedback(course_id=''):
    with _conn() as c:
        if course_id:
            r = c.execute('SELECT COUNT(*) n FROM verifier_feedback WHERE course_id=?',
                          (course_id,)).fetchone()
        else:
            r = c.execute('SELECT COUNT(*) n FROM verifier_feedback').fetchone()
    return r['n'] if r else 0


def add_life_note(text, tag='life'):
    with _conn() as c:
        c.execute('INSERT INTO life_notes (ts,text,tag) VALUES (?,?,?)',
                  (time.time(), text or '', tag or 'life'))
    return True


def list_life_notes(tag='', limit=20):
    """读轻量记忆流；tag='mood' 时只取心境/反思。"""
    with _conn() as c:
        if tag:
            rows = c.execute(
                'SELECT * FROM life_notes WHERE tag=? ORDER BY ts DESC LIMIT ?',
                (tag, limit)).fetchall()
        else:
            rows = c.execute(
                'SELECT * FROM life_notes ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]


# =====================================================================
# 待办 / 事实 / 洞察 / Agent 会话 / 复习完成标记
# =====================================================================
def _json_load(raw, default):
    try:
        return json.loads(raw or '')
    except Exception:
        return default


def _todo_row(r):
    d = dict(r)
    d['meta'] = _json_load(d.pop('meta_json', ''), {})
    d['done'] = d.get('status') == 'done'
    d['created_at'] = time.strftime('%m-%d %H:%M', time.localtime(d.get('ts') or 0))
    if d.get('done_ts'):
        d['done_at'] = time.strftime('%m-%d %H:%M', time.localtime(d['done_ts']))
    else:
        d['done_at'] = ''
    return d


def add_todo(text, due='', source='manual', course_id='', kp='', kp_id='',
             priority=0, meta=None, dedupe=True):
    """写一条待办。dedupe=True 时，同课程同知识点且未完成的重复任务不重复写。"""
    text = (text or '').strip()
    if not text:
        return None
    with _conn() as c:
        if dedupe:
            row = c.execute('''SELECT * FROM todos
                WHERE status='open' AND COALESCE(course_id,'')=? AND COALESCE(kp,'')=?
                  AND text=? LIMIT 1''', (course_id or '', kp or '', text)).fetchone()
            if row:
                return _todo_row(row)
        cur = c.execute('''INSERT INTO todos
            (ts,text,due,source,course_id,kp,kp_id,status,priority,meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (time.time(), text, due or '', source or 'manual',
                         course_id or '', kp or '', kp_id or '', 'open',
                         int(priority or 0), json.dumps(meta or {}, ensure_ascii=False)))
        row = c.execute('SELECT * FROM todos WHERE id=?', (cur.lastrowid,)).fetchone()
    return _todo_row(row)


def list_todos(status='open', course_id='', limit=200):
    with _conn() as c:
        where, args = [], []
        if status and status != 'all':
            where.append('status=?')
            args.append(status)
        if course_id:
            where.append('course_id=?')
            args.append(course_id)
        sql = 'SELECT * FROM todos'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY CASE status WHEN \'open\' THEN 0 ELSE 1 END, priority DESC, ts ASC LIMIT ?'
        args.append(limit)
        rows = c.execute(sql, args).fetchall()
    return [_todo_row(r) for r in rows]


def get_todo(tid):
    with _conn() as c:
        r = c.execute('SELECT * FROM todos WHERE id=?', (tid,)).fetchone()
    return _todo_row(r) if r else None


def set_todo_done(tid, done=True, note=''):
    """勾选完成 → 写一条 mastery_marks，让 DPM 的 Dw 里这条薄弱点带上
    reviewed_at / todo_done，而不是简单地把证据删掉。"""
    todo = get_todo(tid)
    if not todo:
        return None
    now = time.time()
    with _conn() as c:
        c.execute('UPDATE todos SET status=?, done_ts=? WHERE id=?',
                  ('done' if done else 'open', now if done else None, tid))
        if done and todo.get('kp'):
            c.execute('''INSERT INTO mastery_marks
                (ts,course_id,kp,kp_id,source,todo_id,note)
                VALUES (?,?,?,?,?,?,?)''',
                      (now, todo.get('course_id') or '', todo.get('kp') or '',
                       todo.get('kp_id') or '', 'todo', tid, note or '待办勾选完成'))
        elif not done:
            c.execute('DELETE FROM mastery_marks WHERE todo_id=?', (tid,))
    return get_todo(tid)


def mastery_marks(course_id='', kp=''):
    with _conn() as c:
        where, args = [], []
        if course_id:
            where.append('course_id=?')
            args.append(course_id)
        if kp:
            where.append('kp=?')
            args.append(kp)
        sql = 'SELECT * FROM mastery_marks'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY ts DESC'
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def add_fact(text, fact_id='', source='', sample_id='', date='', tags=None, meta=None):
    text = (text or '').strip()
    if not text:
        return None
    with _conn() as c:
        if fact_id:
            row = c.execute('SELECT * FROM facts WHERE fact_id=?', (fact_id,)).fetchone()
            if row:
                return dict(row)
        cur = c.execute('''INSERT INTO facts
            (ts,fact_id,text,source,sample_id,date,tags_json,status,meta_json)
            VALUES (?,?,?,?,?,?,?,?,?)''',
                        (time.time(), fact_id or '', text, source or '',
                         sample_id or '', date or '', json.dumps(tags or [], ensure_ascii=False),
                         'active', json.dumps(meta or {}, ensure_ascii=False)))
        row = c.execute('SELECT * FROM facts WHERE id=?', (cur.lastrowid,)).fetchone()
    d = dict(row)
    d['tags'] = _json_load(d.pop('tags_json', ''), [])
    d['meta'] = _json_load(d.pop('meta_json', ''), {})
    return d


def list_facts(limit=100):
    with _conn() as c:
        rows = c.execute('SELECT * FROM facts WHERE status=\'active\' ORDER BY date DESC, ts DESC LIMIT ?',
                         (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['tags'] = _json_load(d.pop('tags_json', ''), [])
        d['meta'] = _json_load(d.pop('meta_json', ''), {})
        out.append(d)
    return out


def add_insight(title, description='', kind='insight', insight_id='', suggested=None,
                date='', related=None, source='corpus', meta=None):
    title = (title or '').strip()
    if not title:
        return None
    with _conn() as c:
        if insight_id:
            row = c.execute('SELECT * FROM insights WHERE insight_id=?', (insight_id,)).fetchone()
            if row:
                return dict(row)
        cur = c.execute('''INSERT INTO insights
            (ts,insight_id,kind,title,description,suggested_json,date,related_json,
             source,status,meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (time.time(), insight_id or '', kind or 'insight', title,
                         description or '', json.dumps(suggested or [], ensure_ascii=False),
                         date or '', json.dumps(related or [], ensure_ascii=False),
                         source or 'corpus', 'active',
                         json.dumps(meta or {}, ensure_ascii=False)))
        row = c.execute('SELECT * FROM insights WHERE id=?', (cur.lastrowid,)).fetchone()
    return _insight_row(row)


def _insight_row(row):
    d = dict(row)
    d['suggested_actions'] = _json_load(d.pop('suggested_json', ''), [])
    d['related_samples'] = _json_load(d.pop('related_json', ''), [])
    d['meta'] = _json_load(d.pop('meta_json', ''), {})
    return d


def list_insights(kind='', limit=200):
    with _conn() as c:
        if kind:
            rows = c.execute('''SELECT * FROM insights WHERE kind=? AND status='active'
                                ORDER BY date DESC, ts DESC LIMIT ?''', (kind, limit)).fetchall()
        else:
            rows = c.execute('''SELECT * FROM insights WHERE status='active'
                                ORDER BY date DESC, ts DESC LIMIT ?''', (limit,)).fetchall()
    return [_insight_row(r) for r in rows]


def get_insight(iid):
    with _conn() as c:
        r = c.execute('SELECT * FROM insights WHERE id=? OR insight_id=?', (iid, iid)).fetchone()
    return _insight_row(r) if r else None


def add_agent_message(session_id, role, content, context=None, level=1, label='', meta=None):
    with _conn() as c:
        c.execute('''INSERT INTO agent_messages
            (ts,session_id,role,content,context_json,strategy_level,strategy_label,meta_json)
            VALUES (?,?,?,?,?,?,?,?)''',
                  (time.time(), session_id or 'default', role or 'user', content or '',
                   json.dumps(context or {}, ensure_ascii=False), int(level or 1),
                   label or '', json.dumps(meta or {}, ensure_ascii=False)))
    return True


def list_agent_messages(session_id='default', limit=100):
    with _conn() as c:
        rows = c.execute('''SELECT * FROM agent_messages WHERE session_id=?
                            ORDER BY id ASC LIMIT ?''', (session_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['context'] = _json_load(d.pop('context_json', ''), {})
        d['meta'] = _json_load(d.pop('meta_json', ''), {})
        out.append(d)
    return out


def latest_strategy(session_id='default'):
    with _conn() as c:
        r = c.execute('''SELECT * FROM agent_messages WHERE session_id=?
                         AND strategy_level>0 ORDER BY id DESC LIMIT 1''',
                      (session_id,)).fetchone()
    if not r:
        return {'level': 2, 'label': '苏格拉底式启发引导', 'turns': 0}
    return {'level': int(r['strategy_level'] or 1),
            'label': r['strategy_label'] or '直接讲解',
            'turns': 0}


def count_agent_messages(session_id='default'):
    with _conn() as c:
        r = c.execute('SELECT COUNT(*) n FROM agent_messages WHERE session_id=?',
                      (session_id,)).fetchone()
    return r['n'] if r else 0


def corpus_imported(sample_id):
    with _conn() as c:
        r = c.execute('SELECT * FROM corpus_imports WHERE sample_id=?', (sample_id,)).fetchone()
    return dict(r) if r else None


def record_corpus_import(sample_id, category, path='', events=0, rejected=0,
                         facts=0, todos=0, insights=0, meta=None):
    with _conn() as c:
        c.execute('''INSERT INTO corpus_imports
            (ts,sample_id,category,path,events,rejected,facts,todos,insights,meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sample_id) DO UPDATE SET
              ts=excluded.ts, category=excluded.category, path=excluded.path,
              events=excluded.events, rejected=excluded.rejected, facts=excluded.facts,
              todos=excluded.todos, insights=excluded.insights, meta_json=excluded.meta_json''',
                  (time.time(), sample_id, category, path, events, rejected,
                   facts, todos, insights, json.dumps(meta or {}, ensure_ascii=False)))
    return True


def corpus_status():
    with _conn() as c:
        rows = c.execute('SELECT * FROM corpus_imports ORDER BY ts ASC').fetchall()
        ev = c.execute('SELECT COUNT(*) n FROM classroom').fetchone()['n']
        rej = c.execute('SELECT COUNT(*) n FROM classroom_rejected').fetchone()['n']
        facts = c.execute('SELECT COUNT(*) n FROM facts').fetchone()['n']
        todos = c.execute('SELECT COUNT(*) n FROM todos').fetchone()['n']
        ins = c.execute('SELECT COUNT(*) n FROM insights').fetchone()['n']
    return {'imported': len(rows), 'events': ev, 'rejected': rej,
            'facts': facts, 'todos': todos, 'insights': ins,
            'samples': [dict(r) for r in rows]}


def _today_start():
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def digest_today():
    """今日记忆聚合：课堂证据 + 被拒噪声 + 生活碎片，不分课程混在一起算。"""
    start = _today_start()
    with _conn() as c:
        evs = c.execute('SELECT * FROM classroom WHERE created_ts>=? ORDER BY created_ts DESC',
                        (start,)).fetchall()
        rej = c.execute('SELECT COUNT(*) n FROM classroom_rejected WHERE ts>=?',
                        (start,)).fetchone()['n']
        notes = c.execute('SELECT * FROM life_notes WHERE ts>=? ORDER BY ts DESC LIMIT 20',
                          (start,)).fetchall()
    by_course = {}
    for e in evs:
        cid = e['course_id'] or 'general'
        cur = by_course.setdefault(cid, {'course_id': cid, 'events': 0, 'kps': {}})
        cur['events'] += 1
        if e['kp_label']:
            cur['kps'][e['kp_label']] = cur['kps'].get(e['kp_label'], 0) + 1
    out = []
    for cid, v in by_course.items():
        kps = sorted(v['kps'].items(), key=lambda x: -x[1])
        out.append({'course_id': cid, 'events': v['events'],
                    'knots': len(kps), 'top_knots': kps[:3]})
    out.sort(key=lambda x: -x['events'])
    open_todos = list_todos('open', limit=6)
    today = time.strftime('%Y-%m-%d', time.localtime())
    ins = [x for x in list_insights(limit=20) if x.get('date') == today][:3]
    if not ins:
        ins = list_insights(limit=2)
    return {'date': today,
            'events': len(evs), 'rejected': rej,
            'reviews': list_memory_reviews('', 6),
            'courses': out,
            'todos_open': open_todos,
            'todos_done': len(list_todos('done', limit=200)),
            'insights': ins,
            'life': [dict(r) for r in notes]}


def list_rejected(course_id='', limit=50):
    with _conn() as c:
        if course_id:
            rows = c.execute('''SELECT * FROM classroom_rejected WHERE course_id=?
                                ORDER BY ts DESC LIMIT ?''', (course_id, limit)).fetchall()
        else:
            rows = c.execute('SELECT * FROM classroom_rejected ORDER BY ts DESC LIMIT ?',
                             (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['event'] = json.loads(d.pop('event_json') or '{}')
        except Exception:
            d['event'] = {}
        try:
            d['reasons_list'] = json.loads(d.pop('reasons') or '[]')
        except Exception:
            d['reasons_list'] = []
        out.append(d)
    return out


def add_rejected_events(course_id, events, status='UNVERIFIED', reasons=None, session_id=''):
    """校验不通过：只进原始日志，不进 classroom / DPM。"""
    with _conn() as c:
        for e in events or []:
            c.execute('''INSERT INTO classroom_rejected
                (ts,course_id,event_json,status,reasons,session_id)
                VALUES (?,?,?,?,?,?)''',
                      (time.time(), course_id,
                       json.dumps(e, ensure_ascii=False),
                       status, json.dumps(reasons or [], ensure_ascii=False),
                       session_id))
    return len(events or [])


def clear_classroom(course_id=''):
    with _conn() as c:
        if course_id:
            row = c.execute('SELECT COUNT(*) n FROM classroom WHERE course_id=?',
                            (course_id,)).fetchone()
            c.execute('DELETE FROM classroom WHERE course_id=?', (course_id,))
        else:
            row = c.execute('SELECT COUNT(*) n FROM classroom').fetchone()
            c.execute('DELETE FROM classroom')
    return row['n']


def classroom_events(course_id=None, limit=300):
    # 冻结后只读快照：第二轮无论怎么聊，读到的都是进化完成那一刻的证据
    if course_id and is_frozen(course_id):
        raw = _flag_get(f'dpm_snapshot:{course_id}')
        if raw:
            try:
                rows = json.loads(raw)
                return rows[:limit]
            except Exception:
                pass
    out = _classroom_rows(course_id, limit)
    for d in out:
        d['kind_label'] = KIND_LABEL.get(d['kind'], d['kind'])
        d['ev_id'] = f'ev:{d.get("id")}'          # 每条证据的稳定编号，前端可点开溯源
        prov = _json_load(d.get('prov_json') or '', {})
        d['evidence_source'] = prov
        d['verify_status'] = prov.get('verify_status') or 'PASS'
        d['verify_reasons'] = prov.get('verify_reasons') or []
        d['source_file'] = prov.get('source_file') or ''
        d['source_sample'] = prov.get('source_sample') or ''
    return out


def classroom_counts():
    with _conn() as c:
        rows = c.execute('SELECT course_id, COUNT(*) n FROM classroom GROUP BY course_id').fetchall()
    return {r['course_id']: r['n'] for r in rows}


def classroom_summary(course_id):
    """按知识点汇总：谁被打了多少点、最早/最晚出现的时间、证据原文。"""
    evs = classroom_events(course_id)
    by_kp = {}
    for e in evs:
        k = e['kp_id'] or e['kp_label']
        cur = by_kp.setdefault(k, {
            'kp_id': e['kp_id'], 'kp_label': e['kp_label'],
            'count': 0, 'weight': 0.0, 'kinds': [], 'first': e['t_label'],
            'last': e['t_label'], 'items': [],
        })
        cur['count'] += 1
        cur['weight'] += e['weight'] or 1
        if e['kind'] not in cur['kinds']:
            cur['kinds'].append(e['kind'])
        cur['last'] = e['t_label']
        prov = {}
        try:
            prov = json.loads(e.get('prov_json') or '{}') or {}
        except Exception:
            prov = {}
        if not prov:
            prov = {'kind': e['kind'], 'kind_label': e['kind_label'],
                    't_sec': e['t_sec'], 't_label': e['t_label'],
                    'ref': (e['text'] or '')[:60]}
        cur['items'].append({'ev_id': e.get('ev_id') or f'ev:{e.get("id")}',
                             'kind': e['kind'], 'kind_label': e['kind_label'],
                             't_label': e['t_label'], 'text': e['text'],
                             'source_type': e.get('source_type') or 'auto',
                             'confidence': e.get('confidence'),
                             'source_sample': e.get('source_sample') or '',
                             'evidence_source': prov})
    ordered = sorted(by_kp.values(), key=lambda x: (-x['weight'], x['first']))
    return {
        'course_id': course_id,
        'total': len(evs),
        'by_kind': {k: len([e for e in evs if e['kind'] == k])
                    for k in ('ocr', 'asr', 'vibe', 'human')},
        'by_kp': ordered,
        'events': evs,
    }


def kp_history(kp_label):
    """这个知识点在自测里的历史表现（给“分流看历史”用）。"""
    if not kp_label:
        return {'n': 0, 'wrong': 0, 'types': []}
    with _conn() as c:
        rows = c.execute('''SELECT error_type, correct FROM attempts
                            WHERE kp LIKE ?''', (f'%{kp_label}%',)).fetchall()
    wrong = [r for r in rows if not r['correct']]
    counts = {}
    for r in wrong:
        counts[r['error_type']] = counts.get(r['error_type'], 0) + 1
    return {'n': len(rows), 'wrong': len(wrong),
            'types': sorted(counts.items(), key=lambda x: -x[1])}


# =====================================================================
# Reviewer 层：记忆更新是否被原始证据支撑（不信任采集侧的自述）
# =====================================================================
SUPPORT_MIN_COUNT = 2       # 至少 2 条独立证据
SUPPORT_MIN_WEIGHT = 3.0    # 或者单条强信号（停顿打点）合计 ≥ 3


def support_passed(item):
    """一条学习者结论的门槛：证据条数或权重够不够。"""
    return (item.get('count', 0) >= SUPPORT_MIN_COUNT
            or float(item.get('weight') or 0) >= SUPPORT_MIN_WEIGHT)


def add_memory_reviews(course_id, reviews, session_id=''):
    """把 Reviewer 的裁决留痕：哪条结论、什么裁决、绑了哪些 evidence_source。"""
    with _conn() as c:
        for r in reviews or []:
            c.execute('''INSERT INTO memory_reviews
                (ts,course_id,kp_label,verdict,reason,support_json,session_id)
                VALUES (?,?,?,?,?,?,?)''',
                      (time.time(), course_id, r.get('kp_label', ''),
                       r.get('verdict', ''), r.get('reason', ''),
                       json.dumps(r.get('support') or [], ensure_ascii=False),
                       session_id))
    return len(reviews or [])


def list_memory_reviews(course_id='', limit=20):
    with _conn() as c:
        if course_id:
            rows = c.execute('''SELECT * FROM memory_reviews WHERE course_id=?
                                ORDER BY ts DESC LIMIT ?''', (course_id, limit)).fetchall()
        else:
            rows = c.execute('SELECT * FROM memory_reviews ORDER BY ts DESC LIMIT ?',
                             (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['support'] = json.loads(d.pop('support_json') or '[]')
        except Exception:
            d['support'] = []
        out.append(d)
    return out


# =====================================================================
# 记忆流 / 证据溯源 / 时间衰减复习
# =====================================================================
def memory_stream(course_id='', limit=300):
    """统一记忆流：通过 Critic 门禁的课堂证据 + 被拒证据 + 生活碎片。
    每条都带 ev_id、时间戳、来源和门禁状态，前端可直接展示“为什么进/没进图谱”。"""
    with _conn() as c:
        if course_id:
            evs = c.execute('''SELECT * FROM classroom WHERE course_id=?
                               ORDER BY created_ts DESC LIMIT ?''',
                            (course_id, limit)).fetchall()
            rej = c.execute('''SELECT * FROM classroom_rejected WHERE course_id=?
                               ORDER BY ts DESC LIMIT ?''',
                            (course_id, limit)).fetchall()
        else:
            evs = c.execute('SELECT * FROM classroom ORDER BY created_ts DESC LIMIT ?',
                            (limit,)).fetchall()
            rej = c.execute('SELECT * FROM classroom_rejected ORDER BY ts DESC LIMIT ?',
                            (limit,)).fetchall()
        notes = c.execute('SELECT * FROM life_notes ORDER BY ts DESC LIMIT ?',
                          (min(limit, 100),)).fetchall()
        reviews = c.execute('''SELECT * FROM memory_reviews
                               {} ORDER BY ts DESC LIMIT ?'''.format(
                                   'WHERE course_id=?' if course_id else ''),
                            ((course_id,) if course_id else ()) + (min(limit, 100),)).fetchall()

    items = []
    for r in evs:
        d = dict(r)
        prov = _json_load(d.get('prov_json') or '', {})
        items.append({
            'kind': 'evidence', 'status': prov.get('verify_status') or 'PASS',
            'ev_id': f'ev:{d.get("id")}', 'ts': d.get('created_ts'),
            'time_label': d.get('t_label') or _mmss(d.get('t_sec') or 0),
            'date_label': time.strftime('%m-%d %H:%M', time.localtime(d.get('created_ts') or 0)),
            'course_id': d.get('course_id'), 'kp': d.get('kp_label'),
            'kp_id': d.get('kp_id'), 'source_type': d.get('source_type') or 'auto',
            'kind_label': KIND_LABEL.get(d.get('kind'), d.get('kind')),
            'text': d.get('text'), 'confidence': d.get('confidence'),
            'source_sample': prov.get('source_sample') or '',
            'source_file': prov.get('source_file') or '',
            'reasons': prov.get('verify_reasons') or [],
        })
    for r in rej:
        d = dict(r)
        ev = _json_load(d.get('event_json') or '', {})
        reasons = _json_load(d.get('reasons') or '', [])
        items.append({
            'kind': 'rejected', 'status': d.get('status') or 'UNVERIFIED',
            'ev_id': f'rej:{d.get("id")}', 'ts': d.get('ts'),
            'time_label': ev.get('t_label') or _mmss(ev.get('t_sec') or 0),
            'date_label': time.strftime('%m-%d %H:%M', time.localtime(d.get('ts') or 0)),
            'course_id': d.get('course_id'), 'kp': ev.get('kp_label'),
            'kp_id': ev.get('kp_id'), 'source_type': ev.get('source_type') or 'auto',
            'kind_label': KIND_LABEL.get(ev.get('kind'), ev.get('kind')),
            'text': ev.get('text'), 'confidence': ev.get('confidence'),
            'source_sample': (ev.get('evidence_source') or {}).get('source_sample') or '',
            'source_file': (ev.get('evidence_source') or {}).get('source_file') or '',
            'reasons': reasons,
        })
    for r in notes:
        d = dict(r)
        items.append({
            'kind': 'life', 'status': 'STREAM_ONLY', 'ev_id': f'life:{d.get("id")}',
            'ts': d.get('ts'), 'time_label': '—',
            'date_label': time.strftime('%m-%d %H:%M', time.localtime(d.get('ts') or 0)),
            'course_id': '', 'kp': '生活 / 灵感', 'kp_id': '',
            'source_type': 'life', 'kind_label': '轻量记忆流',
            'text': d.get('text'), 'confidence': None,
            'source_sample': '', 'source_file': '',
            'reasons': ['防污染网关：未命中学习信号，留在轻量记忆流，不进知识图谱'],
        })
    items.sort(key=lambda x: -(x.get('ts') or 0))
    review_items = []
    for r in reviews:
        d = dict(r)
        review_items.append({
            'kind': 'review', 'ts': d.get('ts'), 'course_id': d.get('course_id'),
            'kp': d.get('kp_label'), 'verdict': d.get('verdict'),
            'reason': d.get('reason'),
            'support': _json_load(d.get('support_json') or '', []),
            'date_label': time.strftime('%m-%d %H:%M', time.localtime(d.get('ts') or 0)),
        })
    return {'course_id': course_id, 'items': items[:limit],
            'reviews': review_items, 'counts': {
                'pass': len([x for x in items if x['kind'] == 'evidence']),
                'rejected': len([x for x in items if x['kind'] == 'rejected']),
                'life': len([x for x in items if x['kind'] == 'life']),
            }}


def evidence_for_kp(course_id, kp_id='', kp=''):
    """点开图谱节点时，返回这个知识点的全部原始证据 + 拒收记录 + 审查裁决。"""
    rows = classroom_events(course_id)
    hit = []
    for e in rows:
        same = False
        if kp_id and (e.get('kp_id') or '') == kp_id:
            same = True
        if kp and ((e.get('kp_label') or '') == kp
                   or (kp and kp in (e.get('kp_label') or ''))
                   or ((e.get('kp_label') or '') and (e.get('kp_label') or '') in kp)):
            same = True
        if same:
            hit.append(e)
    rej = []
    for r in list_rejected(course_id, 200):
        ev = r.get('event') or {}
        if (kp_id and ev.get('kp_id') == kp_id) or (kp and ev.get('kp_label') == kp):
            rej.append(r)
    revs = [r for r in list_memory_reviews(course_id, 100)
            if (kp and (r.get('kp_label') or '') == kp) or (kp_id and kp_id in (r.get('kp_label') or ''))]
    return {'course_id': course_id, 'kp_id': kp_id, 'kp': kp,
            'evidence': hit, 'rejected': rej, 'reviews': revs,
            'counts': {'pass': len(hit), 'rejected': len(rej)}}


def _same_kp_local(a, b):
    """知识点匹配（数据层版本，不依赖 server.py）。"""
    a_id = (a.get('kp_id') or '').strip()
    b_id = (b.get('kp_id') or '').strip()
    if a_id and b_id and a_id == b_id:
        return True
    la = (a.get('kp_label') or a.get('kp') or '').strip()
    lb = (b.get('kp_label') or b.get('kp') or '').strip()
    if not la or not lb:
        return False
    if la == lb:
        return True
    if len(la) >= 2 and la in lb:
        return True
    if len(lb) >= 2 and lb in la:
        return True
    return False


def review_decay(course_id='', limit=40):
    """时间衰减复习视图：越久没碰、错得越多的薄弱点排越前。
    score = 错误/信号强度 × 遗忘因子(1 - exp(-days/half_life))。"""
    cids = [course_id] if course_id else [c['id'] for c in courses.COURSES]
    now = time.time()
    out = []
    for cid in cids:
        prof = learner_profile(cid)
        for it in (prof.get('Dw') or {}).get('items') or []:
            last = 0
            for e in classroom_events(cid):
                if _same_kp_local({'kp_id': it.get('kp_id'), 'kp_label': it.get('kp')},
                                  {'kp_id': e.get('kp_id'), 'kp_label': e.get('kp_label')}):
                    last = max(last, float(e.get('created_ts') or 0))
            days = max(0.0, (now - last) / 86400.0) if last else 30.0
            half = 7.0
            decay = 1.0 - pow(0.5, days / half)
            strength = float(it.get('wrong') or 0) * 2 + float(it.get('signals') or 0)
            score = round(strength * (0.25 + 0.75 * decay), 3)
            marks = mastery_marks(cid, it.get('kp') or '')
            out.append({
                'course_id': cid, 'kp': it.get('kp'), 'kp_id': it.get('kp_id'),
                'days_since': round(days, 1), 'decay': round(decay, 3),
                'strength': strength, 'score': score,
                'wrong': it.get('wrong') or 0, 'signals': it.get('signals') or 0,
                'reviewed': bool(marks), 'reviewed_at': marks[0]['ts'] if marks else None,
                'note': it.get('note') or '',
            })
    out.sort(key=lambda x: -x['score'])
    return {'course_id': course_id, 'items': out[:limit],
            'rule': '按 Ebbinghaus 遗忘曲线：7 天半衰期 × 历史错误强度；复习完成的项会标灰'}


# =====================================================================
# DPM：动态学习者记忆（绑定课程 ID）
#   D = {Ds 会话历史, Dw 薄弱知识点, Dr 引导策略反思}
#   第一轮：DPM 为空（built=False），Agent 只能读 SKG 静态图谱。
#   课堂证据写入后：Dw 直接来自课堂时序信号 + 历史错题。
# =====================================================================
def learner_profile(course_id):
    # 冻结后整份画像只读：第二轮再怎么聊，D 都停在进化完成那一刻
    if is_frozen(course_id):
        raw = _flag_get(f'dpm_profile:{course_id}')
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
    summary = classroom_summary(course_id)
    labels = [it['kp_label'] for it in summary['by_kp']]

    # ---- Ds：这门课的导学会话历史（课程绑定，别门课不混进来） ----
    sess = list_sessions(limit=40)
    mine = [s for s in sess if course_id and (s.get('course_id') or '') == course_id]
    if not mine:                      # 老数据没有课程字段时，按知识点标签兜底
        mine = [s for s in sess if any(l and l in (s.get('kp') or '') for l in labels)]
    ds_items = [{'ts': s['ts'], 'kp': s.get('kp') or '—',
                 'conclusion': (s.get('conclusion') or '')[:50],
                 'course_name': s.get('course_name') or ''} for s in mine[:5]]

    # ---- Dw：薄弱知识点 = 课堂上被证据支撑的卡点（Reviewer 门槛：≥2 条或权重≥3） ----
    dw, dw_rejected, interactions = [], [], 0
    for it in summary['by_kp']:
        h = kp_history(it['kp_label'])
        interactions += h['n']
        kinds = '、'.join(KIND_LABEL.get(k, k) for k in it['kinds'])
        if not support_passed(it):
            dw_rejected.append({
                'kp': it['kp_label'], 'signals': it['count'],
                'weight': round(it['weight'], 1),
                'reason': f'仅 {it["count"]} 条证据、权重 {round(it["weight"], 1)}，'
                          f'低于支撑门槛（≥2 条或权重≥3），不进入薄弱点',
            })
            continue
        marks = mastery_marks(course_id, it['kp_label'])
        dw.append({'kp': it['kp_label'], 'kp_id': it['kp_id'],
                   'signals': it['count'], 'weight': round(it['weight'], 1),
                   'kinds': kinds, 'wrong': h['wrong'], 'n': h['n'],
                   'sources': sorted({x.get('source_type') or 'auto' for x in it['items']}),
                   'note': f'课堂 {it["count"]} 条（{kinds}）+ 历史错 {h["wrong"]}/{h["n"]}',
                   'provenance': [x['evidence_source'] for x in it['items'][:3]],
                   'reviewed': bool(marks),
                   'reviewed_at': marks[0]['ts'] if marks else None,
                   'todo_done': len(marks),
                   'items': it['items'][:2]})

    # ---- Dr：引导策略反思（这条策略为什么改、改成什么） ----
    with _conn() as c:
        act = c.execute("SELECT title FROM strategies WHERE status='active' "
                        "ORDER BY created_ts DESC LIMIT 1").fetchone()
        chs = c.execute('SELECT title, reason, ts FROM changes '
                        'ORDER BY ts DESC LIMIT 4').fetchall()
    dr_items = [{'title': r['title'], 'reason': (r['reason'] or '')[:70],
                 'time': time.strftime('%m-%d %H:%M', time.localtime(r['ts'] or 0))}
                for r in chs]
    # 待办勾选完成也会进 Dr：这是“学生真的复习了”的证据
    for m in mastery_marks(course_id)[:3]:
        dr_items.insert(0, {
            'title': f'复习任务完成：{m.get("kp") or "薄弱点"}',
            'reason': m.get('note') or '待办勾选完成，已回写薄弱点画像',
            'time': time.strftime('%m-%d %H:%M', time.localtime(m.get('ts') or 0)),
        })

    built = summary['total'] > 0
    return {
        'course_id': course_id,
        'built': built,
        'counts': {'vibe': summary['by_kind'].get('vibe', 0),
                   'ocr': summary['by_kind'].get('ocr', 0),
                   'asr': summary['by_kind'].get('asr', 0),
                   'human': summary['by_kind'].get('human', 0),
                   'interactions': interactions},
        'Ds': {'n': len(mine), 'items': ds_items},
        'Dw': {'top': dw[0]['kp'] if dw else '—', 'items': dw[:4],
               'rejected': dw_rejected,
               'done': len([x for x in dw if x.get('reviewed')]),
               'total': len(dw)},
        'Dr': {'active': (act['title'] if act else ''),
               'items': dr_items if built else []},
        'todos': {'open': len(list_todos('open', course_id, 200)),
                  'done': len(list_todos('done', course_id, 200))},
    }


# =====================================================================
# 统一策略库（strategy_registry）
# 把 5001 strategies 与 OpenTutor skill_versions 物理导入同一张表。
# 这是“读合并”的主表；两个引擎仍各自写源表，sync 负责把新版本汇入。
# =====================================================================
def _registry_json(value, default):
    if isinstance(value, str):
        return value if value.strip() else default
    if value is None:
        return default
    return json.dumps(value, ensure_ascii=False)


def upsert_strategy_registry(item):
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    with _conn() as c:
        c.execute('''INSERT OR REPLACE INTO strategy_registry
            (id,source,name,version,status,strategy,change_summary,
             trigger_json,target_json,steps_json,metrics_json,
             source_evaluation_id,source_reflection_id,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (item.get('id') or '',
                   item.get('source') or '',
                   item.get('name') or '',
                   str(item.get('version') or ''),
                   item.get('status') or '',
                   item.get('strategy') or '',
                   item.get('change_summary') or '',
                   _registry_json(item.get('trigger_json'), '[]'),
                   _registry_json(item.get('target_json'), '[]'),
                   _registry_json(item.get('steps_json'), '[]'),
                   _registry_json(item.get('metrics_json'), '{}'),
                   item.get('source_evaluation_id') or '',
                   item.get('source_reflection_id') or '',
                   item.get('created_at') or '',
                   now))
    return item.get('id')


def sync_local_strategies():
    """把 5001 自己的 strategies 汇入统一表。"""
    with _conn() as c:
        rows = c.execute('SELECT * FROM strategies').fetchall()
    count = 0
    for row in rows:
        upsert_strategy_registry({
            'id': f"teaching:{row['id']}",
            'source': 'teaching_5001',
            'name': row['title'] or f"课堂策略 {row['id']}",
            'version': row['id'],
            'status': row['status'] or '',
            'strategy': row['strategy'] or '',
            'change_summary': row['reason'] or '',
            'trigger_json': row['diff_json'] or '[]',
            'target_json': '[]',
            'steps_json': '[]',
            'metrics_json': row['metrics_json'] or '{}',
            'source_evaluation_id': '',
            'source_reflection_id': '',
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S',
                                        time.localtime(row['created_ts'] or 0)),
        })
        count += 1
    return count


def sync_opentutor_strategies(db_path):
    """只读 OpenTutor 的 skill_versions，汇入统一表。"""
    if not db_path or not os.path.isfile(db_path):
        return 0
    uri = 'file:' + str(db_path).replace('\\', '/') + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT v.id, v.version, v.status, v.trigger_conditions,
                   v.applicable_objectives, v.target_dimensions, v.steps,
                   v.expected_effect, v.change_summary,
                   v.source_evaluation_id, v.source_reflection_id, v.created_at,
                   s.name, s.slug
            FROM skill_versions v
            JOIN teaching_skills s ON s.id = v.skill_id
        ''').fetchall()
    finally:
        conn.close()
    count = 0
    for row in rows:
        upsert_strategy_registry({
            'id': f"opentutor:{row['id']}",
            'source': 'opentutor',
            'name': row['name'] or row['slug'] or 'OpenTutor skill',
            'version': str(row['version']),
            'status': row['status'] or '',
            'strategy': row['expected_effect'] or row['change_summary'] or '',
            'change_summary': row['change_summary'] or '',
            'trigger_json': row['trigger_conditions'] or '[]',
            'target_json': row['target_dimensions'] or '[]',
            'steps_json': row['steps'] or '[]',
            'metrics_json': '{}',
            'source_evaluation_id': row['source_evaluation_id'] or '',
            'source_reflection_id': row['source_reflection_id'] or '',
            'created_at': row['created_at'] or '',
        })
        count += 1
    return count


def sync_strategy_registry(opentutor_db=''):
    local = sync_local_strategies()
    remote = sync_opentutor_strategies(opentutor_db)
    return {'teaching_5001': local, 'opentutor': remote, 'total': local + remote}


def _registry_load(value, default):
    try:
        return json.loads(value or '')
    except Exception:
        return default


def list_strategy_registry(source='', limit=200):
    with _conn() as c:
        if source:
            rows = c.execute('SELECT * FROM strategy_registry WHERE source=? '
                             'ORDER BY created_at DESC LIMIT ?', (source, limit)).fetchall()
        else:
            rows = c.execute('SELECT * FROM strategy_registry '
                             'ORDER BY source, created_at DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item['trigger_conditions'] = _registry_load(item.pop('trigger_json', ''), [])
        item['target_dimensions'] = _registry_load(item.pop('target_json', ''), [])
        item['steps'] = _registry_load(item.pop('steps_json', ''), [])
        metrics = _registry_load(item.pop('metrics_json', ''), {})
        item['metrics'] = metrics or None
        out.append(item)
    return out
