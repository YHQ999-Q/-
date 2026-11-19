# -*- coding: utf-8 -*-
"""
课程分类本体（Schema）+ 路由

设计：
  - 顶层：课程（id/name/type）
  - 中层：每门课一张独立子图谱（nodes/edges）
  - 底层：证据节点（ASR/OCR 片段、时间戳）挂在具体知识点上

这里只放“课程数据 + 关键词路由”，不碰图谱更新逻辑。
Agent 的抽取/裁决/提交逻辑对所有课程完全一样，只是换一张子图。
"""

COURSES = [
    {
        'id': 'math',
        'name': '高等数学',
        'type': '理科',
        'version': 3,
        'keywords': ['极限', '导数', '积分', '微分', '中值', '泰勒', '洛必达',
                     '等价无穷小', '级数', 'sin', 'cos', 'tan', 'lim', '数列'],
        'nodes': [
            ('root', '高数核心知识库', 'stable', 0.95),
            ('limit', '极限', 'stable', 0.90),
            ('equiv', '等价无穷小', 'conflict', 0.41),
            ('deriv', '导数', 'stable', 0.86),
            ('integral', '积分', 'stable', 0.82),
            ('midval', '中值定理', 'stable', 0.74),
            ('taylor', '泰勒展开', 'stable', 0.80),
            ('series', '级数', 'stable', 0.70),
        ],
        'edges': [('root', 'limit'), ('limit', 'equiv'), ('equiv', 'taylor'),
                  ('limit', 'deriv'), ('deriv', 'integral'), ('root', 'midval'),
                  ('root', 'series')],
    },
    {
        'id': 'circuit',
        'name': '模拟电子技术',
        'type': '工科',
        'version': 2,
        'keywords': ['二极管', '三极管', '晶体管', '放大', '运放', '电阻', '电容',
                     '电感', '电路', '模电', '上拉', 'i2c', 'gpio', 'vcc', '开漏'],
        'nodes': [
            ('root', '模电核心知识库', 'stable', 0.95),
            ('diode', '二极管', 'stable', 0.88),
            ('bjt', '三极管', 'stable', 0.84),
            ('amplifier', '放大电路', 'stable', 0.80),
            ('resistor', '电阻', 'stable', 0.90),
            ('capacitor', '电容', 'stable', 0.82),
            ('opamp', '运算放大器', 'stable', 0.76),
            ('pullup', '上拉电阻 R1→VCC', 'conflict', 0.45),
            ('i2c', 'I2C 总线时序', 'stable', 0.78),
        ],
        'edges': [('root', 'diode'), ('diode', 'bjt'), ('bjt', 'amplifier'),
                  ('amplifier', 'opamp'), ('root', 'resistor'), ('root', 'capacitor'),
                  ('resistor', 'pullup'), ('pullup', 'i2c')],
    },
    {
        'id': 'english',
        'name': '大学英语',
        'type': '文科',
        'version': 1,
        'keywords': ['英语', '单词', '词汇', '语法', '时态', '从句', '阅读', '写作',
                     '听力', 'english', 'vocabulary', 'grammar', 'tense', 'clause'],
        'nodes': [
            ('root', '大学英语核心知识库', 'stable', 0.95),
            ('vocab', '词汇', 'stable', 0.88),
            ('grammar', '语法', 'stable', 0.86),
            ('tense', '时态', 'stable', 0.80),
            ('clause', '从句', 'stable', 0.78),
            ('reading', '阅读理解', 'stable', 0.74),
            ('writing', '写作', 'stable', 0.72),
            ('listening', '听力', 'stable', 0.70),
        ],
        'edges': [('root', 'vocab'), ('root', 'grammar'), ('grammar', 'tense'),
                  ('grammar', 'clause'), ('root', 'reading'), ('root', 'writing'),
                  ('root', 'listening')],
    },
    {
        'id': 'physics',
        'name': '大学物理',
        'type': '理科',
        'version': 1,
        'keywords': ['物理', '力学', '牛顿', '受力', '摩擦', '斜面', '加速度',
                     'f=ma', '电磁', '电场', '磁场', '热学', '光学', '波动'],
        'nodes': [
            ('root', '大学物理核心知识库', 'stable', 0.95),
            ('mech', '力学', 'stable', 0.88),
            ('newton', '牛顿定律', 'stable', 0.86),
            ('em', '电磁学', 'stable', 0.80),
            ('thermo', '热学', 'stable', 0.74),
            ('optics', '光学', 'stable', 0.72),
            ('calc_ref', '微积分应用', 'stable', 0.70),
        ],
        'edges': [('root', 'mech'), ('mech', 'newton'), ('root', 'em'),
                  ('root', 'thermo'), ('root', 'optics'), ('mech', 'calc_ref')],
    },
    {
        'id': 'edu_compare',
        'name': '比较教育学',
        'type': '教育',
        'version': 1,
        'keywords': ['比较教育', '国别', '日本', '留学生', '俄罗斯', '印度', 'nep',
                     '学制', '分流', '教育制度', '教育改革', '质量保障', '教育主权',
                     '国际化', '十万留学生', '国别研究', '教育发展动力'],
        'nodes': [
            ('root', '比较教育核心知识库', 'stable', 0.93),
            ('four_dim', '国别教育比较四维', 'stable', 0.88),
            ('japan', '日本留学生政策转型', 'stable', 0.82),
            ('russia', '俄罗斯教育主权与质量重建', 'stable', 0.80),
            ('india', '印度 NEP 2020', 'stable', 0.78),
            ('method', '比较教育方法论', 'stable', 0.84),
            ('quality', '教育质量保障', 'stable', 0.76),
        ],
        'edges': [('root', 'four_dim'), ('four_dim', 'japan'),
                  ('four_dim', 'russia'), ('four_dim', 'india'),
                  ('root', 'method'), ('method', 'quality'),
                  ('four_dim', 'quality')],
    },
    {
        'id': 'edu_curriculum',
        'name': '课程与教学论',
        'type': '教育',
        'version': 1,
        'keywords': ['课程与教学论', '后现代', '多尔', '四r', '泰勒', '永恒主义',
                     '赫钦斯', '经典', '通识', '理性', '课程设计', '生成',
                     '丰富性', '回归性', '关联性', '严密性', '学科知识', '课程理论'],
        'nodes': [
            ('root', '课程与教学论核心知识库', 'stable', 0.93),
            ('taylor', '泰勒原理', 'stable', 0.85),
            ('postmodern', '后现代课程理论', 'stable', 0.84),
            ('doll4r', '多尔四R原则', 'stable', 0.80),
            ('perennialism', '永恒主义教育思想', 'stable', 0.78),
            ('ai_era', 'AI 时代的知识观', 'stable', 0.72),
            ('assessment', '表现与能力区分', 'stable', 0.70),
        ],
        'edges': [('root', 'taylor'), ('root', 'postmodern'),
                  ('postmodern', 'doll4r'), ('root', 'perennialism'),
                  ('perennialism', 'ai_era'), ('postmodern', 'assessment')],
    },
    {
        'id': 'edu_ai',
        'name': 'AI 评估前沿',
        'type': '教育',
        'version': 1,
        'keywords': ['ai评估', '人工智能', '评估', 'goodhart', '表现', '能力',
                     'benchmark', '图灵', '大模型', '智能', '测量', '实验逻辑',
                     'opentutor', '元评估', '能力区分'],
        'nodes': [
            ('root', 'AI 评估核心知识库', 'stable', 0.92),
            ('ai_eval', 'AI 能力评估', 'stable', 0.85),
            ('goodhart', 'Goodhart 定律', 'stable', 0.80),
            ('capability', '表现与能力区分', 'stable', 0.78),
            ('meta_eval', '评估方法与实验逻辑', 'stable', 0.76),
            ('open_tutor', 'OpenTutor / 导学式评估', 'stable', 0.70),
        ],
        'edges': [('root', 'ai_eval'), ('ai_eval', 'goodhart'),
                  ('ai_eval', 'capability'), ('ai_eval', 'meta_eval'),
                  ('meta_eval', 'open_tutor')],
    },
]

# 跨课程关联：只在用户主动查询时返回，不自动融合
CROSS_LINKS = [
    {'from_course': 'physics', 'from_node': 'calc_ref', 'from_label': '微积分应用',
     'to_course': 'math', 'to_node': 'integral', 'to_label': '积分',
     'reason': '物理里的功/通量计算会用到积分'},
    {'from_course': 'physics', 'from_node': 'em', 'from_label': '电磁学',
     'to_course': 'math', 'to_node': 'integral', 'to_label': '积分',
     'reason': '电磁学的通量、环流需要积分工具'},
    {'from_course': 'circuit', 'from_node': 'amplifier', 'from_label': '放大电路',
     'to_course': 'physics', 'to_node': 'em', 'to_label': '电磁学',
     'reason': '放大器的载流子运动与电磁学相关'},
    {'from_course': 'edu_curriculum', 'from_node': 'assessment', 'from_label': '表现与能力区分',
     'to_course': 'edu_ai', 'to_node': 'capability', 'to_label': '表现与能力区分',
     'reason': '课堂里的“表现/能力区分”和 AI 评估里的同名概念是同一条论证链'},
    {'from_course': 'edu_curriculum', 'from_node': 'ai_era', 'from_label': 'AI 时代的知识观',
     'to_course': 'edu_ai', 'to_node': 'ai_eval', 'to_label': 'AI 能力评估',
     'reason': '讨论“什么知识值得学”时，需要先知道 AI 到底能做什么、评估是否可信'},
    {'from_course': 'edu_compare', 'from_node': 'quality', 'from_label': '教育质量保障',
     'to_course': 'edu_ai', 'to_node': 'meta_eval', 'to_label': '评估方法与实验逻辑',
     'reason': '国别教育比较里的质量指标，与 AI 评估的指标设计都要防 Goodhart 定律'},
]

_BY_ID = {c['id']: c for c in COURSES}

GENERAL = {
    'id': 'general',
    'name': '未分类课程',
    'type': '综合',
    'version': 1,
    'keywords': [],
    'nodes': [('root', '未分类知识库', 'stable', 0.5),
              ('pending', '待归类', 'stable', 0.4)],
    'edges': [('root', 'pending')],
}


def get_course(cid):
    return _BY_ID.get(cid) or (GENERAL if cid == 'general' else None)


def list_courses():
    return [{'id': c['id'], 'name': c['name'], 'type': c['type']} for c in COURSES]


def route(text):
    """输入一段课堂文本，返回它属于哪门课；只做课程路由，不改图谱。"""
    t = (text or '').lower()
    best = None
    for c in COURSES:
        matched = [k for k in c['keywords'] if k.lower() in t]
        score = len(matched)
        if score and (best is None or score > best['score']):
            best = {'id': c['id'], 'name': c['name'], 'type': c['type'],
                    'score': score, 'matched': matched[:5],
                    'reason': f'命中关键词：{"、".join(matched[:5])}'}
    if best:
        return best
    return {'id': 'general', 'name': GENERAL['name'], 'type': GENERAL['type'],
            'score': 0, 'matched': [],
            'reason': '没有命中预置课程，先放进未分类子图谱，等下一次更明确的输入'}


def cross_links(cid):
    out = []
    for x in CROSS_LINKS:
        if x['from_course'] == cid or x['to_course'] == cid:
            out.append(dict(x))
    return out
