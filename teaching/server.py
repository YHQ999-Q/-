# -*- coding: utf-8 -*-
"""
MetaMind 多模态 → 自进化 后端骨架（Flask）

链路：
  硬件 POST /api/ingest  ──►  感知(ASR+OCR) ──► 对齐 ──► 检索子图
                                              ──► 裁决(有没有冲突)
                                              ──► 提候选 patch
                                              ──► 代码校验 + 落库(版本+1)
                                              ──► 策略偏好更新
  每一步都通过 WebSocket 推给前端（另有轮询兜底）。

设计要点（答辩可以照念）：
  1. “思考”不是一个模型，是 感知 → 检索 → 裁决 → 提议 → 校验 → 提交 的循环。
  2. 模型只负责【提候选 patch】，能不能改、怎么版本化、怎么回滚，全是代码说了算。
  3. 每一步都能说清用了哪个模型；断网时整体降级到规则版，demo 不会挂。

运行：
  pip install flask flask-sock requests
  python server.py
  打开 http://127.0.0.1:5000

接真模型（可选）：
  set LLM_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
  set LLM_KEY=sk-xxx
  set LLM_MODEL=qwen-plus
  set VL_MODEL=qwen-vl-max
  set MOCK=0
"""
import os
import io
import json
import time
import uuid
import queue
import base64
import hashlib
import re
import threading

import requests
from flask import Flask, request, jsonify, Response

import teach_db as db
import courses
import strategy_policy as policy
import classroom_demo as demo
import corpus_pack

try:
    from flask_sock import Sock
    HAS_WS = True
except Exception:
    HAS_WS = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
if HAS_WS:
    sock = Sock(app)

# ---------------- 配置 ----------------
MOCK = os.environ.get('MOCK', '1') == '1'      # 没有 API key 时保持 1
# 默认走硅基流动（一个 key 同时有文本 / 视觉 / 语音转写）。
# 想换回百炼：LLM_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 + 对应模型名
LLM_BASE = os.environ.get('LLM_BASE', 'https://api.siliconflow.cn/v1')
LLM_KEY = os.environ.get('LLM_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', 'deepseek-ai/DeepSeek-V4-Flash')  # 裁决/讲解：文本
VL_MODEL = os.environ.get('VL_MODEL', 'Qwen/Qwen3-VL-32B-Instruct')     # 视觉：板书/公式
ASR_MODEL = os.environ.get('ASR_MODEL', 'Qwen/Qwen3-ASR-1.7B')            # 语音转写：千问优先
ASR_FALLBACK = os.environ.get('ASR_FALLBACK', 'FunAudioLLM/SenseVoiceSmall')  # 千问不可用时兜底

# 不想把 key 写进环境变量？在项目根目录建 api_key.txt，只放一行 key 即可
KEY_FILE = os.path.join(BASE_DIR, 'api_key.txt')
if not LLM_KEY and os.path.exists(KEY_FILE):
    try:
        with open(KEY_FILE, encoding='utf-8') as _f:
            LLM_KEY = _f.readline().strip()
    except Exception as _e:
        print('[config] 读取 api_key.txt 失败：', _e)

# 有 key 就别演剧本了：自动关掉 MOCK（想强制 mock 就 set MOCK=1）
if os.environ.get('MOCK') is None and LLM_KEY:
    MOCK = False
    print(f'[config] 检测到 API key，已切到真实模型（{LLM_MODEL} / {VL_MODEL}）')

# 真实感知服务：esp32-ai-backend 的本机地址（RapidOCR + faster-whisper）
# 设了它，教学页/手表发来的图音会先走真模型；没设就退回 MOCK 剧本
PERCEPTION_URL = os.environ.get('PERCEPTION_URL', 'http://127.0.0.1:5000').rstrip('/')

db.init_db()


# =====================================================================
# 知识图谱（内存版，demo 够用；正式版换 SQLite/Postgres）
# =====================================================================
class KnowledgeGraph:
    """单门课程的子图谱。课程之间隔离，抽取/更新逻辑完全共用。"""
    def __init__(self, course, nodes, edges):
        self.course = course
        self.version = int(course.get('version', 1))
        self.nodes = nodes
        self.edges = list(edges)
        self.history = []
        self.synced_ids = set()      # 已经挂到节点上的 ev_id，防止重启后重复计数

    @classmethod
    def from_course(cls, c):
        nodes = {}
        for nid, label, status, conf in c['nodes']:
            nodes[nid] = {'id': nid, 'label': label, 'status': status,
                          'conf': conf, 'evidence': [], 'seen': 0}
        return cls(c, nodes, c['edges'])

    def snapshot(self):
        return {'version': self.version,
                'course_id': self.course.get('id', ''),
                'course_name': self.course.get('name', ''),
                'course_type': self.course.get('type', ''),
                'nodes': list(self.nodes.values()),
                'edges': [{'from': a, 'to': b} for a, b in self.edges],
                'synced_ids': sorted(self.synced_ids)[-5000:]}

    def retrieve(self, keywords):
        """只在这门课的子图里检索：命中关键字的节点 + 它的邻居。"""
        hit = set()
        for nid, n in self.nodes.items():
            if any(k and k in n['label'] for k in keywords):
                hit.add(nid)
        for a, b in self.edges:
            if a in hit:
                hit.add(b)
            if b in hit:
                hit.add(a)
        return [self.nodes[i] for i in hit if i in self.nodes]

    @staticmethod
    def _touch(node):
        node['seen'] = node.get('seen', 0) + 1
        node['last_seen'] = time.time()

    def apply(self, patch, reason, evidence):
        """patch 是候选修改。代码负责校验 + 版本化，模型没权限直接改。"""
        nid = patch['node_id']
        if nid not in self.nodes:
            self.nodes[nid] = {'id': nid, 'label': patch.get('label', nid),
                               'status': 'stable', 'conf': 0.6,
                               'evidence': [], 'seen': 0}
            if nid != 'root':
                self.edges.append(('root', nid))
        node = self.nodes[nid]
        before = {'label': node['label'], 'conf': node['conf'], 'status': node['status']}
        node['label'] = patch.get('label', node['label'])
        node['status'] = 'stable'
        node['conf'] = min(0.97, node['conf'] + 0.25)
        node['evidence'].append(evidence)
        self._touch(node)
        self.version += 1
        record = {'version': self.version, 'node_id': nid, 'reason': reason,
                  'course_id': self.course.get('id', ''),
                  'before': before, 'after': {'label': node['label'],
                                              'conf': node['conf'],
                                              'status': node['status']},
                  'ts': time.time()}
        self.history.append(record)
        return record

    def add_evidence(self, nid, evidence):
        """没冲突也要让图谱动：把本次证据挂到节点上，置信度小幅提升。"""
        n = self.nodes.get(nid)
        if not n:
            return None
        n['evidence'].append(evidence)
        n['conf'] = min(0.97, n['conf'] + 0.03)
        self._touch(n)
        self.version += 1
        return self.snapshot()

    def rollback(self, version):
        """回滚：demo 里只回滚标签和置信度，真系统是指针切换。"""
        for h in reversed(self.history):
            if h['version'] == version:
                n = self.nodes[h['node_id']]
                n['label'] = h['before']['label']
                n['conf'] = h['before']['conf']
                n['status'] = h['before']['status']
                self.version += 1
                return True
        return False


class CourseRegistry:
    """所有课程子图 + 路由。默认只加载当前课程，互不干扰。"""
    def __init__(self):
        self.graphs = {}
        self.active = 'math'
        for c in courses.COURSES:
            self.graphs[c['id']] = KnowledgeGraph.from_course(c)
        self.graphs['general'] = KnowledgeGraph.from_course(courses.GENERAL)

    def get(self, cid):
        if cid not in self.graphs:
            c = courses.get_course(cid) or courses.GENERAL
            self.graphs[cid] = KnowledgeGraph.from_course(c)
        return self.graphs[cid]

    def route(self, text):
        r = courses.route(text)
        self.active = r['id']
        self.get(r['id'])
        return r

    def list(self):
        out = []
        for c in courses.COURSES:
            g = self.get(c['id'])
            out.append({'id': c['id'], 'name': c['name'], 'type': c['type'],
                        'version': g.version, 'nodes': len(g.nodes)})
        if 'general' in self.graphs and self.graphs['general'].version > 1:
            out.append({'id': 'general', 'name': courses.GENERAL['name'],
                        'type': courses.GENERAL['type'],
                        'version': self.graphs['general'].version,
                        'nodes': len(self.graphs['general'].nodes)})
        return {'courses': out, 'active': self.active}

    def cross(self, cid):
        return courses.cross_links(cid)


# ---------------- 图谱持久化：内存只是工作副本，快照落 SQLite ----------------
def _graph_key(cid):
    return f'graph:{cid}'


def find_node_id(cid, kp):
    """把课堂里的 kp 标签对到知识图谱节点 ID；对不上就返回空，不硬编。"""
    try:
        graph = REG.get(cid)
        for node_id, node in (graph.nodes or {}).items():
            label = node.get('label') or ''
            if kp and (kp == label or kp in label or label in kp):
                return node_id
    except Exception:
        pass
    return ''


def save_graph(cid):
    g = REG.graphs.get(cid)
    if not g:
        return
    snap = g.snapshot()
    snap['history'] = g.history[-50:]
    db._flag_set(_graph_key(cid), json.dumps(snap, ensure_ascii=False))


def load_graphs():
    """启动时把落盘的图谱快照读回来；没有快照的课程继续用 courses.py 静态图谱。"""
    with db._conn() as c:
        rows = c.execute("SELECT k,v FROM state WHERE k LIKE 'graph:%'").fetchall()
    for r in rows:
        try:
            d = json.loads(r['v'] or '{}')
        except Exception:
            continue
        cid = d.get('course_id') or r['k'].split(':', 1)[1]
        base = courses.get_course(cid) or courses.GENERAL
        g = KnowledgeGraph.from_course(base)
        g.version = int(d.get('version') or g.version)
        nodes = {}
        for n in d.get('nodes') or []:
            nid = n.get('id')
            if not nid:
                continue
            node = {'id': nid, 'label': n.get('label', nid),
                    'status': n.get('status', 'stable'),
                    'conf': float(n.get('conf') or 0.6),
                    'evidence': n.get('evidence') or [],
                    'seen': int(n.get('seen') or 0)}
            if n.get('last_seen'):
                node['last_seen'] = n['last_seen']
            nodes[nid] = node
        if nodes:
            g.nodes = nodes
        edges = [(e.get('from'), e.get('to')) for e in (d.get('edges') or [])
                 if e.get('from') and e.get('to')]
        if edges:
            g.edges = edges
        g.history = d.get('history') or []
        g.synced_ids = set(d.get('synced_ids') or [])
        if not g.synced_ids:
            # 旧快照没有 synced_ids：用节点上已有的 ev_id 初始化，避免重启后重复挂证据
            for n in g.nodes.values():
                for x in n.get('evidence') or []:
                    if x.get('ev_id'):
                        g.synced_ids.add(x['ev_id'])
        REG.graphs[cid] = g
    return len(rows)


REG = CourseRegistry()
try:
    load_graphs()
except Exception as e:
    print('[graph] 落盘图谱加载失败，继续使用静态课程图谱：', e)
try:
    _registry_counts = db.sync_strategy_registry(
        os.path.join(BASE_DIR, '..', 'opentutor', 'native-data', 'data', 'opentutor.db'))
    print('[registry] 统一策略库已同步：', _registry_counts)
except Exception as e:
    print('[registry] 统一策略库同步跳过：', e)
# 兼容旧代码里少量 KG 调用：默认指向高数子图
KG = REG.get('math')


# ---------------- 课堂证据 → 图谱节点：让图谱和 DPM 用同一份证据 ----------------
def sync_graph_from_classroom(cid, limit=2000):
    """把 classroom 表里已通过门禁的证据挂到子图谱节点上，并落盘。
    这样服务重启/页面刷新后，图谱不是一张空壳，节点上能看到 ev_id 和原文。"""
    g = REG.get(cid)
    synced = getattr(g, 'synced_ids', set())
    if not synced:
        for n in g.nodes.values():
            for x in n.get('evidence') or []:
                if x.get('ev_id'):
                    synced.add(x['ev_id'])
    g.synced_ids = synced
    changed = 0
    for e in db.classroom_events(cid, limit):
        ev_id = e.get('ev_id') or f'ev:{e.get("id")}'
        if ev_id in synced:
            continue
        nid = e.get('kp_id') or ''
        if not nid:
            # 按中文标签兜底匹配
            for k, n in g.nodes.items():
                if n.get('label') == (e.get('kp_label') or ''):
                    nid = k
                    break
        if not nid:
            continue
        if nid not in g.nodes:
            label = e.get('kp_label') or nid
            g.nodes[nid] = {'id': nid, 'label': label, 'status': 'stable',
                            'conf': 0.6, 'evidence': [], 'seen': 0}
            if nid != 'root':
                g.edges.append(('root', nid))
        node = g.nodes[nid]
        synced.add(ev_id)
        prov = e.get('evidence_source') or {}
        node.setdefault('evidence', []).append({
            'ev_id': ev_id, 'kind': e.get('kind'), 'kind_label': e.get('kind_label'),
            't_label': e.get('t_label'), 't_sec': e.get('t_sec'),
            'text': (e.get('text') or '')[:180],
            'confidence': e.get('confidence'),
            'source_type': e.get('source_type'),
            'source_sample': e.get('source_sample') or prov.get('source_sample') or '',
            'verify_status': e.get('verify_status') or 'PASS',
        })
        node['evidence'] = node['evidence'][-30:]
        node['seen'] = int(node.get('seen') or 0) + 1
        node['last_seen'] = e.get('created_ts') or time.time()
        node['conf'] = round(min(0.97, float(node.get('conf') or 0.6) + 0.01), 3)
        changed += 1
    if changed:
        g.version += 1
        g.history.append({'version': g.version, 'node_id': 'sync',
                          'reason': f'课堂证据同步：{changed} 条新证据',
                          'course_id': cid, 'ts': time.time()})
        save_graph(cid)
    return changed


def recount_graph_counts(cid):
    """把节点上的 seen 重新按 classroom 表真实条数计算。
    早期同步可能重复计数；页面上的“证据条数”必须和数据库一致。"""
    g = REG.get(cid)
    counts = {}
    for e in db.classroom_events(cid, 100000):
        nid = e.get('kp_id') or ''
        if not nid and e.get('kp_label'):
            for k, n in g.nodes.items():
                if n.get('label') == e.get('kp_label'):
                    nid = k
                    break
        if nid:
            counts[nid] = counts.get(nid, 0) + 1
    for nid, node in g.nodes.items():
        node['seen'] = sum(counts.values()) if nid == 'root' else counts.get(nid, 0)
    save_graph(cid)
    return sum(counts.values())


def cold_start_seed():
    """冷启动：评委一打开网站，图谱、薄弱点、今日简报、待办、洞察都已有真实语料。
    导入是幂等的（corpus_imports 去重），重复启动不会翻倍。"""
    if os.environ.get('METAMIND_AUTOSEED', '1') == '0':
        return {'skipped': 'METAMIND_AUTOSEED=0'}
    try:
        res = corpus_pack.import_all(force=False, verbose=True)
        touched = set()
        for x in res.get('samples') or []:
            cid = x.get('course_id')
            if cid:
                touched.add(cid)
        for cid in touched:
            sync_graph_from_classroom(cid)
            recount_graph_counts(cid)
        # 没有课堂样本的课程也同步一次，防止旧库有课堂证据但图谱没跟上
        for c in courses.COURSES:
            if db.classroom_counts().get(c['id']):
                sync_graph_from_classroom(c['id'])
                recount_graph_counts(c['id'])
        if res.get('ok'):
            print(f"[corpus] 冷启动就绪：{res.get('counts', {}).get('events', 0)} 条证据，"
                  f"{res.get('counts', {}).get('rejected', 0)} 条拒收，"
                  f"{res.get('counts', {}).get('insights', 0)} 条洞察")
        return res
    except Exception as e:
        print('[corpus] 冷启动导入失败（不影响其他功能）：', e)
        return {'ok': False, 'error': str(e)}


COLD_START = cold_start_seed()

# ---------------- 事件总线（WS 推送 + HTTP 轮询兜底） ----------------
EVENTS = []
EVENT_LOCK = threading.Lock()
SUBSCRIBERS = set()
LAST_RESULT = {}          # device_id -> 给 OLED 的一行状态


def emit(stage, payload):
    with EVENT_LOCK:
        ev = {'id': len(EVENTS) + 1, 'stage': stage, 'ts': time.time(), 'payload': payload}
        EVENTS.append(ev)
        if len(EVENTS) > 500:
            del EVENTS[:100]
        subs = list(SUBSCRIBERS)
    for q in subs:
        q.put(ev)
    return ev


if HAS_WS:
    @sock.route('/ws')
    def ws_handler(ws):
        q = queue.Queue()
        SUBSCRIBERS.add(q)
        try:
            ws.send(json.dumps({'type': 'hello',
                                'graph': REG.get(REG.active).snapshot(),
                                'active_course': REG.active,
                                'courses': courses.list_courses(),
                                'events': EVENTS[-30:]},
                               ensure_ascii=False))
            while True:
                try:
                    ws.send(json.dumps(q.get(timeout=20)))
                except queue.Empty:
                    ws.send(json.dumps({'type': 'ping'}))
        except Exception:
            pass
        finally:
            SUBSCRIBERS.discard(q)


# =====================================================================
# 模型调用（真模型 / 规则兜底）
# =====================================================================
def call_llm(messages, model, temperature=0.2, json_mode=False, timeout=60):
    """OpenAI 兼容接口。百炼/DeepSeek/本地 vLLM 都走这个。"""
    body = {'model': model, 'messages': messages, 'temperature': temperature}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    r = requests.post(f'{LLM_BASE}/chat/completions', json=body, timeout=timeout,
                      headers={'Authorization': f'Bearer {LLM_KEY}'})
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content']


def call_vl(image_b64, prompt):
    """多模态：图 → 文本/公式。"""
    return call_llm([{
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{image_b64}'}},
            {'type': 'text', 'text': prompt},
        ],
    }], VL_MODEL, json_mode=False, timeout=120)


def call_asr(audio_bytes):
    """硅基流动的 /audio/transcriptions。默认千问 Qwen3-ASR；不可用时退回 SenseVoice。"""
    if audio_bytes[:4] == b'RIFF':
        name, mime = 'audio.wav', 'audio/wav'
    elif audio_bytes[:3] == b'ID3' or audio_bytes[:2] in (b'\xff\xfb', b'\xff\xf3'):
        name, mime = 'audio.mp3', 'audio/mpeg'
    elif audio_bytes[4:8] == b'ftyp':
        name, mime = 'audio.m4a', 'audio/mp4'
    elif audio_bytes[:4] == b'\x1aE\xdf\xa3':
        name, mime = 'audio.webm', 'audio/webm'
    else:
        name, mime = 'audio.wav', 'audio/wav'

    models = [ASR_MODEL]
    if ASR_FALLBACK and ASR_FALLBACK not in models:
        models.append(ASR_FALLBACK)

    last_err = None
    for model in models:
        try:
            r = requests.post(
                f'{LLM_BASE}/audio/transcriptions',
                headers={'Authorization': f'Bearer {LLM_KEY}'},
                files={'file': (name, audio_bytes, mime)},
                data={'model': model},
                timeout=90,
            )
            if r.status_code >= 400:
                print(f'[ASR] {model} 返回 {r.status_code}: {r.text[:200]}')
                last_err = f'{model}: HTTP {r.status_code}'
                continue
            text = (r.json().get('text') or '').strip()
            if text:
                print(f'[ASR] 使用 {model} 转写成功')
                return text, model
        except Exception as e:
            print(f'[ASR] {model} 调用失败：', e)
            last_err = f'{model}: {e}'
    raise RuntimeError(last_err or 'ASR 无可用模型')



# ---------- 识图缓存：同一张图只让 32B 跑一次，答辩时秒出 ----------
CACHE_FILE = os.path.join(BASE_DIR, 'perception_cache.json')


def _img_hash(b):
    return hashlib.sha1(b).hexdigest()[:16]


def _cache_get(h):
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f).get(h)
    except Exception:
        return None


def _cache_put(h, text):
    try:
        d = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding='utf-8') as f:
                d = json.load(f)
        d[h] = text
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        print('[cache] 写失败：', e, flush=True)

# ---------------- 感知层 ----------------
MOCK_SCENARIOS = [
    {
        'asr': '这个 lim(sin x − x)/x³ 为什么不能直接把 sin x 换成 x？',
        'ocr': 'lim(x→0) (sin x − x) / x³',
        'conflict': True,
        'conflict_node': 'equiv',
        'error_type': '概念混淆',
        'kp': '极限',
        'reason': '你把等价无穷小用在了减法上，而等价替换只在乘除因子中成立',
        'patch': {'node_id': 'equiv', 'label': '等价无穷小（加减法不能直接换）'},
        'strategy': '反例优先',
        'conclusion': '你的思路本身没错，错在把等价无穷小用在了减法上。',
        'steps': ['先看它是「差」，不是「积」——等价替换在差里会吃掉主项',
                  '把 sin x 展开到 x³ 项，再看分子剩下什么',
                  '最后一步留给你：剩下的项除 x³ 是多少？'],
    },
    {
        'asr': '中值定理这里为什么要构造辅助函数？',
        'ocr': 'f(ξ) = 0，需构造 F(x) 使 F(a)=F(b)',
        'conflict': True,
        'conflict_node': 'midval',
        'error_type': '步骤缺失',
        'kp': '中值定理',
        'reason': '记忆里「直接对 f 用罗尔定理」的旧说法，在 f(a)≠f(b) 时失效',
        'patch': {'node_id': 'midval', 'label': '中值定理（先构造再套用）'},
        'strategy': '拆成两步的变式题',
        'conclusion': '不是所有题都能直接套罗尔，你得先把它变成能套的样子。',
        'steps': ['看条件：f(a) 和 f(b) 相等吗？',
                  '不等就想办法减掉那条连线（构造 F(x)=f(x)-直线）',
                  '最后一步：验证 F 满足罗尔定理的三条'],
    },
    {
        'asr': '导数运算法则这块我老是记混。',
        'ocr': "(uv)' = u'v + uv'",
        'conflict': False,
        'conflict_node': 'deriv',
        'error_type': '记忆遗忘',
        'kp': '导数',
        'reason': '与现有知识一致，只需要加强记忆路径',
        'patch': {'node_id': 'deriv', 'label': '导数运算法则（乘积法则）'},
        'strategy': '数形结合',
        'conclusion': '这块没问题，我们只差把它变成肌肉记忆。',
        'steps': ['先用一句话说出乘积法则在干嘛',
                  '给一个反例：如果按 (uv)\'=u\'v\' 会错成什么样',
                  '最后一步：你自己造一个能用上的题'],
    },
]

_scenario_idx = 0


def perceive(image_bytes, audio_bytes, text=''):
    """ASR + OCR。优先级：
       1) 页面直接给的字（text）
       2) PERCEPTION_URL 指向的本地服务（RapidOCR + faster-whisper）
       3) 百炼 qwen-vl 识图 + 文本模型兜底
       4) 全都没有 -> MOCK 剧本，保证答辩不断链
    """
    global _scenario_idx
    live = bool(LLM_KEY) and not MOCK or bool(PERCEPTION_URL)
    if not live and not text:
        s = MOCK_SCENARIOS[_scenario_idx % len(MOCK_SCENARIOS)]
        _scenario_idx += 1
        time.sleep(0.5)
        yield ('asr', {'text': s['asr'], 'model': f'{ASR_MODEL}(mock)', 'ok': True})
        time.sleep(0.5)
        yield ('ocr', {'text': s['ocr'], 'model': f'{VL_MODEL}(mock)', 'ok': True})
        yield ('_mock', s)
        return

    # --- ASR：有 key 先走云端千问，失败再退回本机 whisper ---
    asr_text = text.strip()
    asr_model = '页面文字' if asr_text else ''
    if not asr_text and audio_bytes and LLM_KEY:
        try:
            asr_text, asr_model = call_asr(audio_bytes)
        except Exception as e:
            print('[perceive] 在线 ASR 失败，转本机：', e)
    if not asr_text and audio_bytes and PERCEPTION_URL:
        try:
            r = requests.post(f'{PERCEPTION_URL}/upload_audio', data=audio_bytes,
                              headers={'Content-Type': 'application/octet-stream'}, timeout=60)
            r.raise_for_status()
            asr_text = (r.json().get('asr_result') or '').strip()
            asr_model = 'faster-whisper（本机兜底）'
        except Exception as e:
            print('[perceive] 本地 ASR 失败：', e)
    yield ('asr', {'text': asr_text or '（没听清，请再说一遍）',
                   'model': asr_model or '未接入 ASR', 'ok': bool(asr_text)})

    # --- OCR/VL：数学板书优先给视觉模型，本地 RapidOCR 兜底 ---
    ocr_text = ''
    ocr_model = ''
    if image_bytes and LLM_KEY:
        _h = _img_hash(image_bytes)
        _cached = _cache_get(_h)
        if _cached:
            ocr_text = _cached
            ocr_model = VL_MODEL + '（缓存）'
            print('[perceive] 命中识图缓存，跳过模型', flush=True)
        else:
            try:
                ocr_text = call_vl(base64.b64encode(image_bytes).decode(),
                                   '这是一张大学理工课的照片，通常是两种之一：A) 电路原理图：提取主要器件（位号+型号）和关键连接关系，看不清写 [?]，绝对不要编造器件。B) 数学/物理板书或作业：逐行提取公式和文字，公式用 LaTeX。先判断是哪一种再输出，不要解释过程。')
                ocr_model = VL_MODEL
                if ocr_text:
                    _cache_put(_h, ocr_text)
            except Exception as e:
                print('[perceive] 视觉模型失败：', e, flush=True)
    if not ocr_text and image_bytes and PERCEPTION_URL:
        try:
            r = requests.post(f'{PERCEPTION_URL}/upload_image', data=image_bytes,
                              headers={'Content-Type': 'application/octet-stream'}, timeout=60)
            r.raise_for_status()
            ocr_text = (r.json().get('ocr_text') or '').strip()
            ocr_model = 'RapidOCR'
        except Exception as e:
            print('[perceive] 本地 OCR 失败：', e)
    yield ('ocr', {'text': ocr_text or '（没识别到板书，请靠近一点重拍）',
                   'model': ocr_model or '未接入 OCR', 'ok': bool(ocr_text)})


# ---------------- 独立校验层：不合格证据不进图谱 ----------------
VERIFY_PROMPT = """你是课堂证据校验器。给你一段课程 ASR/OCR 文本，判断它是否可信。
只输出 JSON：{"status":"PASS|UNVERIFIED|FAIL","confidence":0.0-1.0,"reasons":["..."]}
规则：
- 明显乱码、识别残片、前后矛盾、和课程完全无关 -> FAIL 或 UNVERIFIED
- 看不清/不确定 -> UNVERIFIED，不要猜
- 正常课堂内容 -> PASS，并按内容完整度给 confidence（0-1）
不要评价对错，只评价“这段识别文本能不能作为写图谱的证据”。"""

_BAD_MARKERS = ('没听清', '没识别到', '无法识别', '乱码', '未接入 ASR', '未接入 OCR')


def _heuristic_verify(text):
    reasons = []
    t = (text or '').strip()
    if not t:
        return 'UNVERIFIED', ['没有有效文本']
    if any(m in t for m in _BAD_MARKERS):
        return 'UNVERIFIED', ['识别服务没有返回有效内容']
    if len(t) < 4:
        return 'UNVERIFIED', ['文本太短，无法判断']
    bad = t.count('�')
    if bad >= 3 or bad / max(1, len(t)) > 0.05:
        return 'UNVERIFIED', ['出现大量乱码字符']
    if re.search(r'(.)\1{9,}', t):
        return 'UNVERIFIED', ['出现连续重复字符，疑似识别故障']
    return 'PASS', reasons


def _llm_verify(text, course):
    if not LLM_KEY:
        return None
    try:
        raw = call_llm([
            {'role': 'system', 'content': VERIFY_PROMPT},
            {'role': 'user', 'content': json.dumps(
                {'course': course or {}, 'text': text[:1200]}, ensure_ascii=False)},
        ], LLM_MODEL, temperature=0, json_mode=True, timeout=25)
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception as e:
        print('[verify] LLM 校验失败，保留启发式结果：', e)
        return None


def verify_evidence(evidence, course=None):
    """独立校验：PASS 才允许写图谱；UNVERIFIED/FAIL 只留原始日志。
    ASR / OCR 分别校验——只拍图或只说一句都算有效，不要求两路都有。"""
    asr = (evidence.get('asr') or '').strip()
    ocr = (evidence.get('ocr') or '').strip()
    a_st, a_r = _heuristic_verify(asr) if asr else ('EMPTY', [])
    o_st, o_r = _heuristic_verify(ocr) if ocr else ('EMPTY', [])
    valid = []
    if a_st == 'PASS':
        valid.append(asr)
    if o_st == 'PASS':
        valid.append(ocr)

    if not valid:
        reasons = []
        if a_st not in ('PASS', 'EMPTY'):
            reasons += a_r
        if o_st not in ('PASS', 'EMPTY'):
            reasons += o_r
        if not reasons:
            reasons = ['没有可用的 ASR/OCR 文本']
        return {'status': 'UNVERIFIED', 'reasons': reasons[:4],
                'text': '', 'course': course or {}}

    text = ' '.join(valid)
    status = 'PASS'
    reasons = []
    # 只对有效文本做一次 LLM 校验；有 key 才跑
    llm = _llm_verify(text, course)
    if llm:
        s = str(llm.get('status', '')).upper()
        if s in ('PASS', 'UNVERIFIED', 'FAIL'):
            status = s
        reasons += [str(x) for x in (llm.get('reasons') or [])][:3]
    return {'status': status, 'reasons': reasons[:4],
            'text': text[:200], 'course': course or {}}


CONF_THRESHOLD = 0.65      # 低于这条线：只留原始日志，不写 DPM / 知识图谱


def _usable_text(t):
    """占位提示（没识别到 / 未接入）不是证据，别写进 DPM。"""
    t = (t or '').strip()
    return bool(t) and not any(m in t for m in _BAD_MARKERS)


def _event_confidence(e):
    """自动采集按识别质量给 0-1 置信度；人工补录默认按人工确认处理。"""
    if (e.get('source_type') or 'auto') == 'human_input':
        try:
            return round(max(0.0, min(1.0, float(e.get('confidence', 1.0)))), 2)
        except Exception:
            return 1.0
    if e.get('kind') == 'vibe':
        return 0.95                     # 打点只有时间戳，没有文本噪声
    t = (e.get('text') or '').strip()
    if not t:
        return 0.2
    conf = 0.92
    if len(t) < 8:
        conf -= 0.15
    conf -= min(0.35, (t.count('[?]') + t.count('？')) * 0.08)
    bad = t.count('�')
    if bad:
        conf -= min(0.5, bad / max(1, len(t)))
    if re.search(r'(.)\1{5,}', t):
        conf -= 0.2
    return round(max(0.05, min(0.99, conf)), 2)


def _verify_one(e):
    """单条证据的格式校验。人工补录只查空/乱码，不因文本短就拒。"""
    text = (e.get('text') or '').strip()
    kind = e.get('kind')
    if kind == 'human':
        if not text:
            return 'UNVERIFIED', ['没有有效文本']
        if any(m in text for m in _BAD_MARKERS):
            return 'UNVERIFIED', ['识别服务没有返回有效内容']
        return 'PASS', []
    st, reasons = _heuristic_verify(text)
    if kind not in ('ocr', 'asr', 'vibe'):
        st = 'UNVERIFIED'
        reasons.append('未知来源类型')
    return st, reasons


def _bigrams(t):
    t = re.sub(r'[^一-鿿A-Za-z0-9]', '', t or '')
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def _cross_modal_boost(events):
    """多模态交叉验证：板书和语音字面重合，两边都抬高置信度。"""
    ocr_t = ' '.join(e.get('text', '') for e in events if e.get('kind') == 'ocr')
    asr_t = ' '.join(e.get('text', '') for e in events if e.get('kind') == 'asr')
    a, o = _bigrams(asr_t), _bigrams(ocr_t)
    if not a or not o:
        return
    overlap = len(a & o) / max(1, min(len(a), len(o)))
    if overlap < 0.12:
        return
    for e in events:
        if e.get('kind') in ('ocr', 'asr'):
            e['confidence'] = round(min(0.99, float(e.get('confidence') or 0) + 0.05), 2)
            e.setdefault('verify_reasons', []).append(
                f'多模态交叉验证：板书与语音字面重合 {overlap:.0%}，置信度 +0.05')


def _lexicon_boost(events, course):
    """命中本课知识图谱用词 → 小幅抬置信度（用历史词典给孤立识别结果加权）。"""
    labels = [n[1] for n in ((course or {}).get('nodes') or [])
              if len(n) > 1 and len(str(n[1])) >= 2]
    if not labels:
        return
    for e in events:
        if e.get('kind') not in ('ocr', 'asr'):
            continue
        hit = next((l for l in labels if l in (e.get('text') or '')), None)
        if hit:
            e['confidence'] = round(min(0.99, float(e.get('confidence') or 0) + 0.03), 2)
            e.setdefault('verify_reasons', []).append(
                f'命中本课图谱用词「{hit}」，置信度 +0.03')


CRITIC_PROMPT = """你是独立的课堂证据审查员，只找硬伤，不评价对错。
检查这段 OCR/ASR 文本：
1) 明显识别错别字（例如「梯度弥散」被识别成「梯度迷失」）
2) 与该课程基本事实明显矛盾
3) 逻辑链断裂到无法作为记忆依据
用户会给你 recent_human_feedback：这是人工过往的判定样本。
decision=reject 表示人曾判定这类内容不可信；decision=accept 表示可以采信。把它当作参考，但不要照搬。
只输出 JSON：{"pass": true/false, "confidence": 0.0-1.0, "reason": "一句话"}
不确定时 pass=true；不要因为“信息不全”就拒。"""


def _llm_critic(text, course, course_id=''):
    if not LLM_KEY or not text:
        return None
    try:
        samples = db.list_feedback_samples(course_id, 5) if course_id else []
        raw = call_llm([
            {'role': 'system', 'content': CRITIC_PROMPT},
            {'role': 'user', 'content': json.dumps({
                'course': course or {}, 'text': text[:1200],
                'recent_human_feedback': [
                    {'text': (s.get('text') or '')[:120], 'decision': s.get('action'),
                     'reason': s.get('reason') or ''} for s in samples],
            }, ensure_ascii=False)},
        ], LLM_MODEL, temperature=0, json_mode=True, timeout=25)
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception as e:
        print('[critic] LLM 复核失败，保留现有结果：', e)
        return None


def verify_classroom_events(events, course=None):
    """课堂证据的批量校验：格式 → 多模态交叉加权 → 置信度门禁 → 语义自洽 → Critic 复核。
    只有 PASS 才进 DPM；其余只留原始日志。"""
    ok, bad = [], []
    for e in events or []:
        e2 = dict(e)
        e2['source_type'] = e2.get('source_type') or 'auto'
        st, reasons = _verify_one(e2)
        try:
            t = float(e2.get('t_sec') or 0)
        except Exception:
            t = -1
        if t < 0:
            st = 'UNVERIFIED'
            reasons.append('时间戳非法')
        e2['confidence'] = _event_confidence(e2)
        e2['verify_status'] = st
        e2['verify_reasons'] = reasons
        (ok if st == 'PASS' else bad).append(e2)

    # 交叉验证：板书 ↔ 语音字面重合；命中本课图谱用词
    _cross_modal_boost(ok)
    _lexicon_boost(ok, course)

    # 置信度门禁：低于阈值只留日志，不进 DPM
    passed = []
    for e in ok:
        if e['confidence'] < CONF_THRESHOLD:
            e['verify_status'] = 'LOW_CONFIDENCE'
            e['verify_reasons'] = (e.get('verify_reasons') or []) + [
                f'置信度 {e["confidence"]:.2f} 低于 {CONF_THRESHOLD}，只留原始日志']
            bad.append(e)
        else:
            passed.append(e)
    ok = passed

    # 语义自洽校验只作用于自动采集的 OCR/ASR；人工补录是人写的，不再让模型二次判断
    text = ' '.join(e.get('text', '') for e in ok
                    if e.get('kind') in ('ocr', 'asr')
                    and e.get('source_type') != 'human_input')
    llm = _llm_verify(text, course) if text else None
    if llm:
        status = str(llm.get('status', '')).upper()
        llm_conf = llm.get('confidence')
        fail = status in ('FAIL', 'UNVERIFIED')
        if isinstance(llm_conf, (int, float)) and llm_conf < CONF_THRESHOLD:
            fail = True
        if fail:
            keep = []
            for e in ok:
                if e.get('kind') in ('ocr', 'asr') and e.get('source_type') != 'human_input':
                    e['verify_status'] = status if status in ('FAIL', 'UNVERIFIED') else 'LOW_CONFIDENCE'
                    e['verify_reasons'] = (e.get('verify_reasons') or []) + (llm.get('reasons') or [])[:2]
                    if isinstance(llm_conf, (int, float)):
                        e['confidence'] = round(min(e['confidence'], float(llm_conf)), 2)
                    bad.append(e)
                else:
                    keep.append(e)
            ok = keep

    # Critic 复核：只查 0.65–0.82 的边缘样本，正常高置信证据不额外花模型调用
    edge = [e for e in ok
            if e.get('kind') in ('ocr', 'asr')
            and e.get('source_type') != 'human_input'
            and float(e.get('confidence') or 0) < 0.82]
    if edge:
        critic = _llm_critic(' '.join(e.get('text', '') for e in edge), course,
                             (course or {}).get('id', ''))
        if critic and critic.get('pass') is False:
            keep = []
            for e in ok:
                if e in edge:
                    e['verify_status'] = 'UNVERIFIED'
                    e['verify_reasons'] = (e.get('verify_reasons') or []) + [
                        'Critic 复核：' + str(critic.get('reason') or '发现硬伤')]
                    bad.append(e)
                else:
                    keep.append(e)
            ok = keep
    return ok, bad


# ---------------- 记忆冲突：不自动覆盖，留两条等人确认 ----------------
CONFLICT_PROMPT = """你在检查一个学习者的课堂记忆是否自相矛盾。
给定【新证据】和【同知识点已有记忆】，判断两者是否明确冲突。
只输出 JSON：{"conflict": true/false, "reason": "一句话说明"}
规则：
- 只有“同一知识点上说法相反”才算冲突（例如一边说已经掌握、一边说老是错）
- 补充细节、同一方向的信息都不算冲突
- 不确定时输出 conflict=false，不要猜"""

_POS_MARKERS = ('已经掌握', '掌握了', '懂了', '会了', '没问题', '弄明白了', '都会')
_NEG_MARKERS = ('不会', '没懂', '不懂', '卡住', '混淆', '老是错', '又错', '出错', '薄弱', '没听明白')


def _heuristic_conflict(event, existing):
    """没有大模型时的规则版冲突检测：显式修正优先，其次看正/负向说法是否打架。"""
    new_text = event.get('text') or ''
    # 显式修正语境（“刚才那个说法不对，其实是…”）按冲突处理，但依旧不自动覆盖
    if (re.search(r'(刚才|之前|前面).{0,8}(不对|说错|算错|错了|修正|更正)', new_text)
            or re.search(r'(应该是|其实是|修正为|更正为)', new_text)):
        return '检测到显式修正语境（“刚才…不对 / 其实是…”）：按冲突处理，人工确认后才写入'
    new_pos = any(m in new_text for m in _POS_MARKERS)
    new_neg = any(m in new_text for m in _NEG_MARKERS)
    if not (new_pos or new_neg):
        return None
    for e in existing:
        t = e.get('text') or ''
        if new_pos and any(m in t for m in _NEG_MARKERS):
            return f'同知识点已有卡点记录「{t[:24]}」，新证据却说已经掌握'
        if new_neg and any(m in t for m in _POS_MARKERS):
            return f'同知识点已有“已掌握”记录「{t[:24]}」，新证据又说没懂'
    return None


def _llm_conflict(event, existing, course):
    if not LLM_KEY or not existing:
        return None
    try:
        raw = call_llm([
            {'role': 'system', 'content': CONFLICT_PROMPT},
            {'role': 'user', 'content': json.dumps({
                'course': course or {},
                'new': {'kp': event.get('kp_label'), 'text': event.get('text')},
                'existing': [{'kp': e.get('kp_label'), 'text': e.get('text'),
                              't': e.get('t_label')} for e in existing[:6]],
            }, ensure_ascii=False)},
        ], LLM_MODEL, temperature=0, json_mode=True, timeout=20)
        d = json.loads(raw)
        return (d.get('reason') or '') if d.get('conflict') else None
    except Exception as e:
        print('[conflict] LLM 冲突检测失败，保留规则结果：', e)
        return None


def _same_kp(event, e):
    """知识点匹配：id 相同，或中文标签一方包含另一方（“上拉电阻” vs “上拉电阻 R1→VCC”）。"""
    a, b = (event.get('kp_id') or '').strip(), (e.get('kp_id') or '').strip()
    if a and b and a == b:
        return True
    la = (event.get('kp_label') or '').strip()
    lb = (e.get('kp_label') or '').strip()
    if not la or not lb:
        return False
    if la == lb:
        return True
    if len(la) >= 2 and la in lb:
        return True
    if len(lb) >= 2 and lb in la:
        return True
    return False


def detect_conflict(course_id, event, course=None):
    """返回 (冲突理由 or None, 同知识点的已有记忆列表)。"""
    existing = [e for e in db.classroom_events(course_id) if _same_kp(event, e)]
    if not existing:
        return None, []
    reason = _heuristic_conflict(event, existing)
    llm_reason = _llm_conflict(event, existing, course)
    return (llm_reason or reason), existing


# ---------------- 裁决层 ----------------
JUDGE_PROMPT = """你是知识图谱的裁决器。给定【当前课程】【新证据】和【相关子图】，判断是否存在冲突。
你只处理当前课程的内容，只能改这门课的子图；不要引入其他课程的节点或概念。

只输出 JSON：
{
  "conflict": true/false,
  "node_id": "已有节点 id，或新节点的英文小写 id（只能用小写字母/数字/下划线）",
  "node_label": "这个知识点叫什么（中文，<=16 字）",
  "kp": "知识点分类，例如 极限/中值定理/电路/几何/力学/编程",
  "error_type": "概念混淆|步骤缺失|计算失误|读题理解|记忆遗忘|信心不足",
  "reason": "一句话说明冲突点",
  "confidence": 0.0-1.0,
  "patch": {"node_id": "上面的 id", "label": "修正后的节点名"},
  "strategy": "反例优先|数形结合|拆成两步的变式题|先给思路不给答案|检查清单",
  "conclusion": "一句话点出学生错在哪",
  "steps": ["第一步", "第二步", "最后一步留给他"]
}

判断标准：新证据与节点 label 表达的事实不一致，才算冲突；
只是补充细节、或置信度提升，都不算冲突。
如果证据属于子图里没有的新知识点，node_id 用新的英文 id，并在 node_label 写中文名。"""


def rule_judge(evidence, course=None):
    """没有大模型时的规则裁决：用关键词把真实现场输入归到最近的错因。
    这样做的好处是——真实拍的照片/录的音也能进链路，不会因为没 key 就退成剧本。"""
    text = (evidence.get('asr', '') + ' ' + evidence.get('ocr', '')).lower()

    # 英语 / 物理：按课程给出本课程内的节点，不混到高数/电路
    if course and course.get('id') == 'english':
        if '时态' in text or 'tense' in text:
            nid, label = 'tense', '时态（主将从现）'
        elif '从句' in text or 'clause' in text:
            nid, label = 'clause', '从句（定语/状语）'
        else:
            nid, label = 'grammar', '语法'
        return {'conflict': True, 'conflict_node': nid, 'error_type': '记忆遗忘',
                'kp': '英语语法', 'reason': f'英语课内容命中「{label}」，需要补这条语法规则',
                'patch': {'node_id': nid, 'label': label}, 'strategy': '拆成两步的变式题',
                'conclusion': '先把规则拆成“结构 + 例句”两步，再做题。',
                'steps': ['先看句子结构：主句和从句各在哪',
                          '再套规则：时态/语序/连接词',
                          '最后一步：把这句话改成正确的英文']}
    if course and course.get('id') == 'physics':
        if '牛顿' in text or 'f=ma' in text or '加速度' in text:
            nid, label = 'newton', '牛顿第二定律'
        else:
            nid, label = 'mech', '力学受力分析'
        return {'conflict': True, 'conflict_node': nid, 'error_type': '步骤缺失',
                'kp': '力学', 'reason': f'物理课内容命中「{label}」，补一条受力/定律关系',
                'patch': {'node_id': nid, 'label': label}, 'strategy': '拆成两步的变式题',
                'conclusion': '先画全受力，再沿加速度方向列方程。',
                'steps': ['选研究对象，画全外力',
                          '沿运动方向分解，写 F 合 = ma',
                          '最后一步：代入数值前先检查单位']}

    rules = [
        (['等价', '替换', 'sin', 'tan', '极限', 'lim'], MOCK_SCENARIOS[0]),
        (['中值', '罗尔', '辅助函数', '构造', 'ξ', 'f('], MOCK_SCENARIOS[1]),
        (['导数', '乘积', '求导', 'uv', "u'v", '法则'], MOCK_SCENARIOS[2]),
        (['电路', '电阻', '上拉', 'i2c', 'vcc', 'gpio', 'sda', 'scl', '开漏'],
         {'conflict': True, 'conflict_node': 'pullup', 'error_type': '概念混淆',
          'kp': '电路',
          'reason': '新证据涉及上拉电阻的接法，和旧条目「R1→VCC」需要对齐',
          'patch': {'node_id': 'pullup', 'label': '上拉电阻 R1→VCC（I2C 开漏）'},
          'strategy': '反例优先',
          'conclusion': '上拉电阻一端接 VCC，另一端接 SDA/SCL；接 GND 那是下拉。',
          'steps': ['先看 I2C 是不是开漏输出：只能拉低，不能主动拉高',
                    '所以高电平必须靠电阻拉到 VCC',
                    '最后一步：如果把上拉接到 GND，SDA 还能恢复高电平吗？']}),
        (['勾股', '直角三角', '斜边', 'a²', 'a2 +', 'c2', '面积'],
         {'conflict': False, 'conflict_node': 'pythagoras', 'error_type': '概念混淆',
          'kp': '几何',
          'reason': '与「勾股定理」节点一致，补充一条证据',
          'patch': {'node_id': 'pythagoras', 'label': '勾股定理 a²+b²=c²'},
          'strategy': '数形结合',
          'conclusion': '勾股定理只对直角三角形成立，c 必须是斜边。',
          'steps': ['先确认哪个角是直角、哪条边是斜边',
                    '把两条直角边平方相加',
                    '最后一步：如果 3、4 是直角边，斜边是多少？']}),
        (['牛顿', '受力', 'f=ma', 'f = ma', '力学', '摩擦', '斜面'],
         {'conflict': False, 'conflict_node': 'mech', 'error_type': '步骤缺失',
          'kp': '力学',
          'reason': '与「力学受力分析」节点一致，补充一条证据',
          'patch': {'node_id': 'mech', 'label': '力学受力分析（先画全再分解）'},
          'strategy': '拆成两步的变式题',
          'conclusion': '先画全所有外力，再按坐标系分解，别一上来就列方程。',
          'steps': ['选研究对象，把它隔离出来',
                    '画重力、支持力、摩擦力、外力，一个都别漏',
                    '最后一步：按 a 的方向列 F 合 = ma']}),
    ]
    for keys, s in rules:
        if any(k.lower() in text for k in keys):
            return dict(s)
    # 没认出来：如实说，不硬凑
    snippet = (evidence.get('ocr') or evidence.get('asr') or '这次的内容')
    return {
        'conflict': False, 'conflict_node': 'root',
        'error_type': '读题理解', 'kp': '待归类',
        'reason': '这次没有认出错因，先如实记录为待归类，不硬套已有知识点。',
        'patch': {'node_id': 'root', 'label': '核心知识库'},
        'strategy': '先给思路不给答案',
        'conclusion': f'我识别到的是：{snippet[:50]}。这道题我还没有对应条目，先记下来，不硬套公式。',
        'steps': ['把题目完整拍清楚，或把问题说完整',
                  '标出你卡住的那一步',
                  '下一次采集会把这条补进知识库'],
    }


def student_memory(limit=5):
    """第二大脑 → 教学判断的只读上下文。

    只取已确认事实和成长洞察；没有就返回空，不让模型据此编造学生画像。
    """
    try:
        facts = [f.get('text', '') for f in db.list_facts(limit) if f.get('text')]
    except Exception:
        facts = []
    try:
        insights = [
            {'title': i.get('title', ''), 'summary': (i.get('description') or '')[:160]}
            for i in db.list_insights('insight', limit)
            if i.get('title')
        ]
    except Exception:
        insights = []
    try:
        moods = [
            {
                'text': row.get('text', ''),
                'time': time.strftime('%m-%d %H:%M', time.localtime(row.get('ts') or 0)),
            }
            for row in db.list_life_notes('mood', 3)
        ]
    except Exception:
        moods = []
    print(
        f'[memory] 注入第二大脑：{len(facts)} 条已确认事实 / '
        f'{len(insights)} 条成长洞察 / {len(moods)} 条心境',
        flush=True,
    )
    return {
        'confirmed_facts': facts,
        'growth_insights': insights,
        'recent_moods': moods,
    }


def judge(evidence, subgraph, course=None):
    if evidence.get('_mock'):
        return dict(evidence['_mock'])
    # 电路/几何/力学主题先用规则锁死，别让文本模型把电路图套成“极限”
    raw_text = (evidence.get('asr', '') + ' ' + evidence.get('ocr', '')).lower()
    topic_guards = [
        ['电路', '电阻', '上拉', 'i2c', 'vcc', 'gpio', 'sda', 'scl', '开漏'],
        ['勾股', '直角三角', '斜边', 'a²', 'a2 +', 'c2', '面积'],
        ['牛顿', '受力', 'f=ma', 'f = ma', '力学', '摩擦', '斜面', '加速度', '电磁'],
        ['英语', '单词', '词汇', '语法', '时态', '从句', '阅读', '写作', '听力',
         'english', 'grammar', 'tense', 'clause'],
    ]
    if any(w in raw_text for ws in topic_guards for w in ws):
        return rule_judge(evidence, course)
    if not LLM_KEY:
        return rule_judge(evidence, course)
    try:
        raw = call_llm([
            {'role': 'system', 'content': JUDGE_PROMPT},
            {'role': 'user', 'content': json.dumps(
                {'course': course or {},
                 'evidence': {'asr': evidence['asr'], 'ocr': evidence['ocr']},
                 'subgraph': subgraph,
                 'student_memory': student_memory()}, ensure_ascii=False)},
        ], LLM_MODEL, json_mode=True)
        r = json.loads(raw)
        nid = r.get('node_id') or r.get('conflict_node') or 'root'
        label = r.get('node_label') or (r.get('patch') or {}).get('label') or nid
        r.setdefault('conflict_node', nid)
        r.setdefault('error_type', '概念混淆')
        r.setdefault('kp', '综合')
        if not r.get('patch'):
            r['patch'] = {'node_id': nid, 'label': label}
        return r
    except Exception as e:
        print('[judge] 模型裁决失败，退回规则：', e)
        return rule_judge(evidence, course)


# ---------------- 答案生成层（学生实际看到的东西） ----------------
ANSWER_PROMPT = """你是学霸助教。信息来自学生拍的照片 + 一段语音提问。
给出一份可以直接照着看懂的解答，要求：
1. 先复述学生的真实问题（口语化，一个字都别装）
2. 指出他卡住的那一步（用他原话里的词）
3. 分步讲，每步一句话，不给最终答案让他自己算
4. 最后留一个反问，逼他自己走最后一步
5. 当学习者表现出焦虑、挫败或疲惫时，先给予温和的情绪共情与认知负荷降载
   （只讲一步、给一个小例子、允许先休息），再进行启发式教学；不要一次塞太多。
若给了 student_memory（学生已确认的事实、成长洞察与最近心境），只把它当作背景，
用来选例子和调整语气；没有依据时不要补写学生经历，也不要把它说成新结论。
只输出 JSON：{"title": "...", "conclusion": "...", "steps": ["...", "..."]}"""


def compose_answer(evidence, result):
    """生成答案卡片。有 LLM 就现场生成；没有就按最接近的已知错因给一份可读的解析。
    重点：无论哪条路，asr/ocr 都必须是这一次真实拍/录进来的原文。"""
    if not LLM_KEY:
        return {
            'title': result.get('strategy') or '解析结果',
            'asr': evidence.get('asr', ''),
            'ocr': evidence.get('ocr', ''),
            'conclusion': result.get('conclusion') or (
                '你的推导里有一处和我记的不一样，已经标出来了。'
                if result.get('conflict') else '这部分没问题，我们往下走一步。'),
            'steps': result.get('steps') or [
                f'你问的是：{evidence.get("asr", "")[:40]}',
                f'图上看到的是：{evidence.get("ocr", "")[:40]}',
                result.get('reason', ''),
                result.get('strategy', ''),
            ],
            'strategy': result.get('strategy', ''),
        }
    try:
        raw = call_llm([
            {'role': 'system', 'content': ANSWER_PROMPT},
            {'role': 'user', 'content': json.dumps({
                'asr': evidence.get('asr', ''),
                'ocr': evidence.get('ocr', ''),
                'verdict': result,
                'student_memory': student_memory()}, ensure_ascii=False)},
        ], LLM_MODEL, json_mode=True)
        d = json.loads(raw)
        d['asr'] = evidence.get('asr', '')
        d['ocr'] = evidence.get('ocr', '')
        d['strategy'] = result.get('strategy', '')
        return d
    except Exception as e:
        return {'title': '生成失败', 'asr': evidence.get('asr', ''),
                'ocr': evidence.get('ocr', ''), 'conclusion': f'{e}',
                'steps': [], 'strategy': result.get('strategy', '')}


# =====================================================================
# 主流程：一次采集 → 一整条自进化链路
# =====================================================================
def run_pipeline(job_id, image_bytes, audio_bytes, device_id, text=''):
    t0 = time.time()
    emit('rx', {'job': job_id, 'device': device_id,
                'image_kb': round(len(image_bytes) / 1024, 1),
                'audio_kb': round(len(audio_bytes) / 1024, 1)})
    time.sleep(0.3)

    evidence = {}
    for stage, payload in perceive(image_bytes, audio_bytes, text):
        if stage == '_mock':
            evidence['_mock'] = payload
        else:
            emit(stage, payload)
            evidence[stage] = payload.get('text', '')
            time.sleep(0.25)

    LAST_RESULT[device_id] = '[..] HEARD' if evidence.get('asr') else '[..] NO AUDIO'
    emit('align', {'note': 'ASR 文本 + OCR 结构 对齐到同一知识点',
                   'asr': evidence.get('asr', '')[:60],
                   'ocr': evidence.get('ocr', '')[:60]})
    # 硬件语音也进「我的记忆」：有效 ASR 才路由，没听清不写脏数据。
    asr_text = (evidence.get('asr') or '').strip()
    if asr_text and '没听清' not in asr_text and '没识别' not in asr_text:
        try:
            routed = route_text_to_memory(asr_text, source='hardware')
            emit('memory', {'kind': routed.get('kind'),
                            'message': routed.get('message')})
        except Exception as exc:
            print('[hw-memory] 路由失败：', exc)
    time.sleep(0.4)

    # --- 课程路由：只加载这一门课的子图谱，不同课程互不干扰 ---
    course = REG.route((evidence.get('asr', '') or '') + ' ' + (evidence.get('ocr', '') or ''))
    g = REG.get(course['id'])
    emit('course', course)
    time.sleep(0.2)

    # --- 独立校验：不合格证据只留日志，不进图谱、不进决策 ---
    verification = verify_evidence(evidence, course)
    emit('verify', verification)
    if verification['status'] != 'PASS':
        LAST_RESULT[device_id] = '[!] ' + verification['status']
        ans = {
            'title': '证据校验未通过，先不写图谱',
            'asr': evidence.get('asr', ''),
            'ocr': evidence.get('ocr', ''),
            'conclusion': '这段识别文本可能有问题（' + '；'.join(verification['reasons'] or ['不确定']) + '）。'
                          '我先把它留在原始日志里，不参与知识图谱更新，也不出题。',
            'steps': ['请靠近一点重拍板书，或把问题完整说一遍',
                      '如果内容其实是对的，点「重新分析」再试一次',
                      '校验状态：' + verification['status']],
            'strategy': '证据校验',
            'kp': course['name'], 'error_type': '待确认',
            'course_id': course['id'], 'course_name': course['name'],
            'verification': verification['status'],
        }
        emit('answer', ans)
        db.save_session(job_id, evidence.get('asr', ''), evidence.get('ocr', ''),
                        ans['conclusion'], ans['steps'], '证据校验', f"v{g.version}",
                        '待确认', course['name'], [], course['id'], course['name'],
                        verification['status'])
        # 真机链路也进同一个拒收看板，和课堂采集共用一套噪声审计
        rej = []
        if _usable_text(evidence.get('ocr')):
            rej.append({'kp_label': course['name'], 'kind': 'ocr', 't_sec': 0,
                        'text': evidence['ocr'], 'source_type': 'auto'})
        if _usable_text(evidence.get('asr')):
            rej.append({'kp_label': course['name'], 'kind': 'asr', 't_sec': 5,
                        'text': evidence['asr'], 'source_type': 'auto'})
        if rej:
            db.add_rejected_events(course['id'], rej, status=verification['status'],
                                   reasons=verification.get('reasons'), session_id=job_id)
        emit('evolve', {'strategy': '证据校验', 'scope': 'personal',
                        'course_id': course['id'], 'course_name': course['name'],
                        'note': f'校验 {verification["status"]}：未写入图谱，只留原始日志',
                        'elapsed_ms': int((time.time() - t0) * 1000)})
        return

    keywords = ['等价', '无穷小', '极限', 'sin', 'tan', '中值', '罗尔',
                '构造', '导数', '乘积', '求导', '泰勒', '积分', '级数',
                '电路', '上拉', '电阻', '电容', '二极管', '三极管', '放大', '运放',
                'I2C', 'VCC', 'GPIO', '开漏',
                '勾股', '直角三角', '斜边', '几何',
                '力学', '受力', '牛顿', '摩擦', '斜面', '加速度', '电磁',
                '英语', '单词', '词汇', '语法', '时态', '从句', '阅读', '写作', '听力']
    sub = g.retrieve(keywords)
    emit('retrieve', {'hits': [n['label'] for n in sub],
                      'course': course['name'],
                      'method': '当前课程子图内关键字匹配（不跨课加载）'})
    time.sleep(0.5)

    result = judge(evidence, sub, course)
    node_id = result.get('conflict_node') or result.get('node_id') or 'root'
    result['conflict_node'] = node_id
    result.setdefault('strategy', db.TYPE_STRATEGY.get(result.get('error_type'), '反例优先'))
    result.setdefault('error_type', '概念混淆')
    result.setdefault('kp', '综合')
    # 按课程归一到题库 key，避免英语课拿到导数题
    topic_kp = {'circuit': '电路', 'english': '英语语法', 'physics': '力学'}.get(course['id'])
    if topic_kp:
        result['kp'] = topic_kp

    # L2：AI 在候选策略里选（UCB），人只定候选集和评价指标
    pol = policy.choose_strategy(course['id'], result['error_type'], result.get('strategy'))
    result['strategy'] = pol['strategy']
    result['policy'] = pol
    if not result.get('patch'):
        result['patch'] = {'node_id': node_id,
                           'label': result.get('node_label')
                                    or g.nodes.get(node_id, {}).get('label', node_id)}
    LAST_RESULT[device_id] = '[!] CONFLICT' if result['conflict'] else '[..] THINKING'
    # --- 双链路汇合：真机拍/录的证据写进 classroom，和两轮演示共用同一份 DPM ---
    try:
        kp_label = ((result.get('patch') or {}).get('label')
                    or g.nodes.get(node_id, {}).get('label')
                    or result.get('kp') or course['name'])
        dev_events = []
        if _usable_text(evidence.get('ocr')):
            dev_events.append({'kp_id': node_id if node_id in g.nodes else '',
                               'kp_label': kp_label, 'kind': 'ocr', 't_sec': 0,
                               't_label': '设备上传 · 板书', 'text': evidence['ocr'],
                               'weight': 1, 'source_type': 'auto', 'confidence': 0.9})
        if _usable_text(evidence.get('asr')):
            dev_events.append({'kp_id': node_id if node_id in g.nodes else '',
                               'kp_label': kp_label, 'kind': 'asr', 't_sec': 5,
                               't_label': '设备上传 · 语音', 'text': evidence['asr'],
                               'weight': 1, 'source_type': 'auto', 'confidence': 0.9})
        if dev_events:
            db.add_classroom_events(course['id'], dev_events, job_id)
            sync_graph_from_classroom(course['id'])
            review = demo.review_updates(course['id'])
            db.add_memory_reviews(course['id'], review['items'], job_id)
            emit('classroom', {'course_id': course['id'], 'inserted': len(dev_events),
                               'note': '真机证据已写入 DPM / 两轮演示'})
    except db.MemoryFrozen:
        emit('classroom', {'course_id': course['id'], 'inserted': 0,
                           'note': 'DPM 已冻结，真机证据暂不写入（演示只读）'})
    except Exception as e:
        print('[classroom] 真机证据写入失败：', e)
    emit('judge', {
        'conflict': result['conflict'],
        'node_id': node_id,
        'reason': result['reason'],
        'error_type': result['error_type'],
        'model': f'{LLM_MODEL}(mock)' if evidence.get('_mock')
                 else (LLM_MODEL if LLM_KEY else '规则裁决'),
    })
    time.sleep(0.6)

    if result['conflict']:
        emit('patch', {'candidate': result['patch'],
                       'note': '模型只提出候选，未写入图谱'})
        time.sleep(0.5)

        # --- 代码校验：模型没有直接改图的权限；新节点必须有合法 id + 中文名 ---
        p = result['patch']
        nid = (p.get('node_id') or node_id or '').strip()
        ok = bool(re.fullmatch(r'[a-z0-9_]{2,32}', nid)) and bool(p.get('label'))
        checks = ['节点 id 合法（小写英文/数字/下划线）',
                  '字段完整（node_id + label）',
                  '新节点会自动挂到核心知识库']
        emit('guard', {'pass': ok, 'checks': checks})
        time.sleep(0.3)

        if ok:
            p['node_id'] = nid
            ev = {'asr': evidence.get('asr', ''), 'ocr': evidence.get('ocr', ''),
                  'course_id': course['id'], 'ts': time.time()}
            rec = g.apply(p, result['reason'], ev)
            save_graph(course['id'])          # 节点改动落盘，重启后还在
            emit('commit', {'graph': g.snapshot(), 'version': rec['version'],
                            'course': course, 'record': rec,
                            'note': '新版本已落库，可回滚'})
            db.record_change(
                f'图谱更新：{p.get("label") or nid}',
                result.get('reason', '') or '本次采集修正了这个知识点',
                {'ctx': [f'来源：{device_id}', f'课程：{course["name"]}'],
                 'del': [],
                 'add': [f'节点 {nid} ← {p.get("label") or nid}',
                         f'证据：{(evidence.get("ocr") or evidence.get("asr") or "")[:48]}']},
                nid, job_id)
        else:
            emit('noop', {'reason': '候选补丁没通过校验，图谱不改（模型没有直接改图权限）'})
    else:
        ev = {'asr': evidence.get('asr', ''), 'ocr': evidence.get('ocr', ''),
              'course_id': course['id'], 'ts': time.time()}
        snap = g.add_evidence(node_id, ev)
        save_graph(course['id'])              # 证据补充也落盘
        label = g.nodes.get(node_id, {}).get('label', node_id)
        emit('noop', {'reason': f'与「{label}」一致，证据已挂到节点，不产生新版本'})
        if snap:
            emit('commit', {'graph': snap, 'version': g.version, 'course': course,
                            'note': '无冲突：只补证据，置信度 +0.03'})
            db.record_change(
                f'证据补充：{label}',
                f'本次采集与「{course["name"]}」已有知识一致，原始证据已挂到节点',
                {'ctx': [f'来源：{device_id}', f'课程：{course["name"]}'],
                 'del': [],
                 'add': [f'证据：{(evidence.get("ocr") or evidence.get("asr") or "")[:48]}']},
                node_id, job_id)

    # --- 学生实际看到的那张卡片（和上面的图谱同源） ---
    ans = compose_answer(evidence, result)
    ans['kp'] = result.get('kp', '综合')
    ans['error_type'] = result.get('error_type', '概念混淆')
    ans['node_label'] = (result.get('patch') or {}).get('label', node_id)
    ans['course_id'] = course['id']
    ans['course_name'] = course['name']
    ans['course_type'] = course['type']
    ans['verification'] = verification['status']
    ans['policy'] = pol
    emit('answer', ans)
    time.sleep(0.3)

    # --- 立刻自测：把"听懂了"变成"做对了"，作答结果驱动计划/策略 ---
    quiz = db.make_quiz(result['error_type'], result['kp'])
    db.save_session(job_id, evidence.get('asr', ''), evidence.get('ocr', ''),
                    ans.get('conclusion', ''), ans.get('steps', []),
                    result['strategy'], f"v{g.version}",
                    result['error_type'], result['kp'], quiz,
                    course['id'], course['name'], verification['status'])
    OLED_TYPE = {'概念混淆': 'CONCEPT', '步骤缺失': 'STEPS', '计算失误': 'CALC',
                 '读题理解': 'READING', '记忆遗忘': 'MEMORY', '信心不足': 'CONFIDENCE'}
    LAST_RESULT[device_id] = '[OK] ' + OLED_TYPE.get(result['error_type'], 'DONE')
    emit('quiz', {'session_id': job_id, 'error_type': result['error_type'],
                  'kp': result['kp'], 'strategy': result['strategy'],
                  'course_id': course['id'], 'course_name': course['name'],
                  'verification': verification['status'],
                  'policy': pol,
                  'items': db.public_quiz(quiz),
                  'note': '答完这 3 题，系统才知道你到底会不会；计划会跟着变。'})
    time.sleep(0.3)

    emit('evolve', {
        'strategy': result['strategy'],
        'scope': 'personal',
        'course_id': course['id'],
        'course_name': course['name'],
        'policy': pol,
        'note': '等 3 道自测作答：正确率 ≤1/3 就切策略并升版，可回滚；变化只写在这门课的子图里',
        'elapsed_ms': int((time.time() - t0) * 1000),
    })


# =====================================================================
# 路由
# =====================================================================
@app.after_request
def _cors(resp):
    """队友的站点如果 iframe 嵌这个页面，跨域也能读接口"""
    if request.path.startswith('/api/teach'):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return resp


@app.route('/')
@app.route('/app')
def index():
    """官网主页：首页 / 记忆 / 成长 / 待办 / 我的事实 / 教学 Agent 五页打通。"""
    return app.send_static_file('app.html')


@app.route('/hardware')
def hardware_page():
    """原来的硬件看板留在 /hardware，答辩时还能单独演示采集链路。"""
    return app.send_static_file('index.html')


@app.route('/teach')
def teach_page():
    return app.send_static_file('teach.html')


@app.post('/api/ingest')
def ingest():
    """ESP32 也调这个接口：multipart/form-data, 字段 image / audio / device_id"""
    img = request.files.get('image')
    aud = request.files.get('audio')
    device_id = request.form.get('device_id', 'esp32-s3-unknown')

    text = request.form.get('text', '')

    image_bytes = img.read() if img else b''
    audio_bytes = aud.read() if aud else b''

    # 糊图不让进：喂给模型只会得到编造的器件
    if image_bytes:
        try:
            from PIL import Image as _Im
            import io as _io
            _im = _Im.open(_io.BytesIO(image_bytes))
            _w, _h = _im.size
            print(f'[ingest] 图片 {_w}x{_h}')
            if _w < 600:
                # 小图不直接拒：先放大再交给模型，并在结果里说明
                _scale = max(3, int(1000 / max(_w, 1)) + 1)
                _im2 = _im.convert('RGB').resize((_w * _scale, _h * _scale), _Im.LANCZOS)
                _buf = _io.BytesIO()
                _im2.save(_buf, 'JPEG', quality=92)
                image_bytes = _buf.getvalue()
                print(f'[ingest] 图片偏小 {_w}x{_h}，已放大 {_scale}x', flush=True)
        except Exception as _e:
            print('[ingest] 图片解析失败：', _e)

    job_id = uuid.uuid4().hex[:8]
    stamp = time.strftime('%H%M%S')
    if image_bytes:
        open(os.path.join(UPLOAD_DIR, f'{stamp}_{job_id}.jpg'), 'wb').write(image_bytes)
    if audio_bytes:
        open(os.path.join(UPLOAD_DIR, f'{stamp}_{job_id}.wav'), 'wb').write(audio_bytes)

    threading.Thread(target=run_pipeline,
                     args=(job_id, image_bytes, audio_bytes, device_id, text),
                     daemon=True).start()
    return jsonify({'ok': True, 'job': job_id})


@app.get('/api/events')
def events():
    """WS 不可用时的轮询兜底"""
    since = int(request.args.get('since', 0))
    cid = request.args.get('course_id') or REG.active
    with EVENT_LOCK:
        evs = [e for e in EVENTS if e['id'] > since]
    return jsonify({'events': evs, 'graph': REG.get(cid).snapshot(),
                    'courses': courses.list_courses(), 'active_course': cid})


@app.get('/api/graph')
def graph():
    cid = request.args.get('course_id') or REG.active
    return jsonify(REG.get(cid).snapshot())


@app.post('/api/rollback')
def rollback():
    body = request.get_json(silent=True) or {}
    cid = body.get('course_id') or REG.active
    v = int(body.get('version', 0))
    g = REG.get(cid)
    ok = g.rollback(v)
    if ok:
        save_graph(cid)                       # 回滚后的状态也落盘
        emit('commit', {'graph': g.snapshot(), 'version': g.version,
                        'course_id': cid, 'note': f'已回滚到 v{v}'})
    return jsonify({'ok': ok, 'graph': g.snapshot()})


@app.get('/api/teach/subgraph/<cid>')
def teach_subgraph(cid):
    """某门课的实时子图谱（主链路版本，不是两轮演示快照）。"""
    return jsonify(REG.get(cid).snapshot())


@app.get('/api/teach/cross/<cid>')
def teach_cross(cid):
    """跨课程关联：只在用户主动点开时返回，不自动融合。"""
    return jsonify({'course_id': cid, 'links': REG.cross(cid)})


@app.get('/api/health')
def health():
    return jsonify({'ok': True, 'mock': MOCK, 'ws': HAS_WS,
                    'perception': PERCEPTION_URL or 'offline-mock',
                    'models': {'asr': ASR_MODEL, 'vl': VL_MODEL, 'judge': LLM_MODEL}})


# =====================================================================
# 教学 Agent 契约接口（/api/teach/*）—— 供队友的站点页面直接调用
# 只读他们的数据用 /api/teach/profile、/api/teach/plan；
# 教学自己的状态全部走下面这些，不写进他们的 localStorage。
# =====================================================================
TEACH_VERSIONS = [
    {
        'id': 'v1', 'time': '10-02 09:00', 'title': '初始策略集（人工冷启动）',
        'status': 'retired', 'reason': '无历史数据，先人工写 5 个技能 + 规则路由',
        'metrics': None,
        'diff': {'ctx': ['路由规则：error_type → skill'],
                 'add': ['概念混淆 → 类比优先', '步骤缺失 → 分步拆解', '计算失误 → 检查清单'],
                 'del': []},
    },
    {
        'id': 'v3', 'time': '10-11 18:40', 'title': '「步骤缺失」改为先示范再放手',
        'status': 'retired', 'reason': '近 46 条里分步拆解二次正确率仅 48%',
        'metrics': {'old': 0.48, 'new': 0.63, 'n': 46},
        'diff': {'ctx': ['skill: step_by_step'],
                 'del': ['请学生自己把每一步写出来，卡住再提示'],
                 'add': ['老师先示范前两步，学生只完成最后一步',
                         '连续两次成功后才把前两步交还给学生']},
    },
    {
        'id': 'v5', 'time': '10-19 03:12', 'title': '新增技能：降难度成功体验',
        'status': 'retired', 'reason': '「信心不足」7 天内复发 11 次，现有技能都不对症',
        'metrics': {'old': 0.31, 'new': 0.57, 'n': 64},
        'diff': {'ctx': ['skills/'],
                 'del': [],
                 'add': ['confidence_builder：先给一道必对题建立成功体验，再回到原题',
                         '路由：error_type=信心不足 → confidence_builder']},
    },
    {
        'id': 'v7', 'time': '11-13 09:12', 'title': '「等价无穷小滥用」改为反例优先',
        'status': 'active', 'reason': '类比法在极限类知识上二次正确率偏低（n=38）',
        'metrics': {'old': 0.61, 'new': 0.74, 'n': 38},
        'diff': {'ctx': ['skill: analogy_first'],
                 'del': ['先用生活类比引入「等价无穷小」的直觉'],
                 'add': ['先给错解：把 sin x − x 直接当成 0',
                         '让他指出：等价无穷小能不能用在加减法上',
                         '再给正确解法（泰勒展开对比两种做法）']},
    },
]

TEACH_PROFILE = {
    'student': {'name': '陈默', 'stage': '大四 · 考研数学', 'days_left': 37},
    'fingerprint': [
        {'type': '概念混淆', 'count': 12, 'trend': 'down', 'note': '等价无穷小、洛必达条件'},
        {'type': '步骤缺失', 'count': 8, 'trend': 'down', 'note': '中值定理证明题'},
        {'type': '计算失误', 'count': 5, 'trend': 'flat', 'note': '分式化简'},
        {'type': '读题理解', 'count': 4, 'trend': 'up', 'note': '条件最值'},
        {'type': '记忆遗忘', 'count': 3, 'trend': 'flat', 'note': '常用泰勒展开'},
        {'type': '信心不足', 'count': 2, 'trend': 'down', 'note': '长期不做的题型'},
    ],
    'mastery': [
        {'kp': '极限', 'value': 0.62},
        {'kp': '导数', 'value': 0.78},
        {'kp': '中值定理', 'value': 0.41},
        {'kp': '积分', 'value': 0.55},
    ],
}

TEACH_PLAN = {
    'title': '今天只做一件事：把「等价无穷小」从加减法里摘出去',
    'reason': '你最近 6 道错题里有 4 道是同一个坑，且 v7 策略刚验证过有效（61%→74%）',
    'duration_min': 18,
    'items': [
        {'kp': '极限', 'task': '错解辨析 ×3（给你错解，你指出错在哪）'},
        {'kp': '极限', 'task': '变式题 ×2（不是原题重做）'},
    ],
}


@app.get('/api/teach/profile')
def teach_profile():
    return jsonify(db.get_profile())


@app.get('/api/teach/plan')
def teach_plan():
    out = db.get_plan()
    # 计划本身仍只由真实作答驱动；这里额外暴露它可读到的第二大脑来源，
    # 方便前端/答辩直接指认“记忆从哪里来”，而不是再做一个假画像。
    out['memory_sources'] = student_memory(3)
    return jsonify(out)


@app.get('/api/teach/strategies/registry')
def teach_strategy_registry():
    items = db.list_strategy_registry()
    sources = {}
    for item in items:
        sources[item.get('source') or 'unknown'] = sources.get(item.get('source') or 'unknown', 0) + 1
    return jsonify({'items': items, 'count': len(items), 'sources': sources})


@app.post('/api/teach/strategies/registry/sync')
def teach_strategy_registry_sync():
    counts = db.sync_strategy_registry(
        os.path.join(BASE_DIR, '..', 'opentutor', 'native-data', 'data', 'opentutor.db'))
    return jsonify({'ok': True, **counts})


@app.get('/api/teach/evolution')
def teach_evolution():
    return jsonify(db.list_strategies())


@app.get('/api/teach/evolution/<vid>')
def teach_version(vid):
    v = next((x for x in db.list_strategies()['versions'] if x['id'] == vid), None)
    if not v:
        return jsonify({'error': 'not found'}), 404
    return jsonify(v)


@app.post('/api/teach/evolution/<vid>/rollback')
def teach_rollback(vid):
    if not db.rollback_strategy(vid):
        return jsonify({'ok': False, 'error': 'not found'}), 404
    emit('rollback', {'version': vid, 'note': f'策略指针已回滚到 {vid}'})
    return jsonify({'ok': True, 'active': vid, 'evolution': db.list_strategies()})


# =====================================================================
# 两轮演示：课堂时序证据 → 子图谱 → 同一个 10 步闭环的两种判断
# =====================================================================
@app.get('/api/teach/courses')
def teach_courses():
    """课程列表 + 每门课子图谱里已有的课堂证据条数（证明按学科隔离）。"""
    counts = db.classroom_counts()
    out = []
    for c in courses.list_courses():
        out.append({**c, 'evidence': counts.get(c['id'], 0)})
    return jsonify({'courses': out})


@app.get('/api/teach/policy')
def teach_policy():
    """L2：当前课程/错因下，候选策略的 UCB 数据和本次选择。"""
    cid = request.args.get('course_id') or REG.active
    et = request.args.get('error_type') or '概念混淆'
    return jsonify(policy.snapshot(cid, et, request.args.get('fallback')))


@app.get('/api/teach/next-action')
def teach_next_action():
    """L3-lite：在 5 个允许动作里选一个下一步，并给理由。"""
    cid = request.args.get('course_id') or REG.active
    latest = (db.list_sessions(1) or [None])[0]
    return jsonify(policy.next_action(cid, latest, REG.get(cid).snapshot()))


@app.get('/api/teach/compare')
def teach_compare():
    """同一个闭环，第一轮 / 第二轮并排返回。course_id 决定看哪张子图谱。"""
    cid = request.args.get('course_id') or 'math'
    kp = request.args.get('kp', '')
    return jsonify(demo.build_compare(cid, kp))


@app.get('/api/teach/trace/<cid>')
def teach_trace(cid):
    """分层痕迹导出：T1 会话 / T2 任务 / T3 原始证据 + 可审计链。
    现场用来回答“第二轮这个判断，凭哪一秒的哪条证据”。"""
    return jsonify(demo.build_trace(cid))


@app.get('/api/teach/graph/<cid>')
def teach_course_graph(cid):
    """单门课的子图谱（实时版）：节点上的 evidence 来自 classroom 表，
    服务重启后从 state 表的 graph:<cid> 读回，不跨课程混合。"""
    g = REG.get(cid)
    snap = g.snapshot()
    counts = db.classroom_counts()
    snap['evidence_count'] = counts.get(cid, 0)
    snap['mode'] = 'live'
    return jsonify(snap)


@app.post('/api/teach/classroom/ingest')
def teach_classroom_ingest():
    """课堂采集入口：真实设备 / 手动回放都走这里。
    body: {course_id, events:[{kp_id,kp_label,kind,t_sec,text,weight}]}"""
    body = request.get_json(silent=True) or {}
    events = body.get('events') or []
    cid = body.get('course_id') or ''
    if not cid:
        # 没带课程时，先按课堂文本自动归到命中关键词的那门课；
        # 关键词一个都没命中才落「未分类」，不硬塞进上一门课。
        probe = ' '.join(
            f"{e.get('text', '')} {e.get('kp_label', '')}" for e in events[:12]
        )
        cid = courses.route(probe)['id']
    if not events:
        events = demo.sample_events(cid)
    if not events:
        return jsonify({'ok': False, 'error': '没有可写入的课堂证据'}), 400
    sid = uuid.uuid4().hex[:8]
    course = courses.get_course(cid) or courses.GENERAL
    verified, rejected = verify_classroom_events(events, course)
    # 冲突检测：和已有记忆矛盾的不自动覆盖，留两条等人确认
    conflicts = []
    if verified:
        kept = []
        for e in verified:
            reason, existing = detect_conflict(cid, e, course)
            if reason:
                conf_id = db.add_conflict(cid, e, {'items': existing[:5]}, reason, sid)
                conflicts.append({'id': conf_id, 'kp_label': e.get('kp_label'), 'reason': reason})
            else:
                kept.append(e)
        verified = kept
    if rejected:
        db.add_rejected_events(cid, rejected, status=rejected[0].get('verify_status', 'UNVERIFIED'),
                               reasons=rejected[0].get('verify_reasons'), session_id=sid)
    if not verified:
        note = (f'{len(conflicts)} 条证据与已有记忆冲突，等待人工确认' if conflicts
                else '课堂证据全部未通过校验，只留原始日志')
        emit('verify', {'status': 'UNVERIFIED',
                        'rejected': len(rejected) + len(conflicts), 'note': note})
        return jsonify({'ok': True, 'inserted': 0, 'verified': 0,
                        'rejected': len(rejected), 'conflicts': conflicts,
                        'course_id': cid, 'session_id': sid,
                        'rejected_items': rejected[:10]})
    try:
        n = db.add_classroom_events(cid, verified, sid)
    except db.MemoryFrozen as e:
        return jsonify({'ok': False, 'error': str(e), 'frozen': True}), 409
    sync_graph_from_classroom(cid)           # 证据同时挂到图谱节点并落盘
    review = demo.review_updates(cid)        # Reviewer：结论有没有被证据撑住
    db.add_memory_reviews(cid, review['items'], sid)
    db.record_change(
        f'课堂记忆写入「{course["name"]}」子图谱',
        f'本节课采集到 {n} 条通过校验的时序证据（板书 / 语音 / 振动打点），按知识点挂进子图谱',
        {'ctx': [f'course: {cid}'], 'del': [],
         'add': [f'{e.get("kp_label", "")} ← {e.get("kind", "")} @{e.get("t_sec", 0)}s'
                 for e in verified[:4]]},
        node_id=verified[0].get('kp_id', ''), session_id=sid)
    emit('verify', {'status': 'PASS', 'verified': len(verified), 'rejected': len(rejected),
                    'note': f'课堂证据校验：{len(verified)} 条通过，{len(rejected)} 条只留日志'})
    emit('evolve', {'course_id': cid, 'inserted': n,
                    'note': f'课堂时序证据已写入「{course["name"]}」子图谱'})
    return jsonify({'ok': True, 'inserted': n, 'verified': len(verified),
                    'rejected': len(rejected), 'conflicts': conflicts,
                    'course_id': cid, 'session_id': sid,
                    'rejected_items': rejected[:10], 'review': review,
                    'summary': db.classroom_summary(cid)})


@app.post('/api/teach/manual/ingest')
def teach_manual_ingest():
    """人工补录通道：识别链路失效时，人可以直接写一条课堂卡点。
    只补经验、不自动生成经验；来源打 human_input 标签，置信度按人工确认处理。"""
    body = request.get_json(silent=True) or {}
    cid = body.get('course_id') or 'math'
    kp_label = (body.get('kp_label') or '').strip()
    text = (body.get('text') or '').strip()
    if not kp_label or not text:
        return jsonify({'ok': False, 'error': '请填写知识点和卡点描述'}), 400
    if db.is_frozen(cid):
        return jsonify({'ok': False, 'frozen': True,
                        'error': 'DPM 已冻结：先解冻（允许写入）再补录'}), 409
    course = courses.get_course(cid) or courses.GENERAL
    kp_id = (body.get('kp_id') or '').strip()
    if not kp_id:
        for n in course.get('nodes') or []:
            if len(n) > 1 and n[1] == kp_label:
                kp_id = n[0]
                break
    rows = db.classroom_events(cid)
    try:
        t_sec = float(body.get('t_sec'))
    except Exception:
        t_sec = max([float(r.get('t_sec') or 0) for r in rows] + [0]) + 1
    event = {
        'kp_id': kp_id, 'kp_label': kp_label, 'kind': 'human', 't_sec': t_sec,
        'text': text, 'weight': float(body.get('weight') or 3),
        'source_type': 'human_input',
        'confidence': float(body.get('confidence')
                            if body.get('confidence') is not None else 1.0),
    }
    sid = uuid.uuid4().hex[:8]
    verified, rejected = verify_classroom_events([event], course)
    if rejected:
        db.add_rejected_events(cid, rejected, status=rejected[0].get('verify_status', 'UNVERIFIED'),
                               reasons=rejected[0].get('verify_reasons'), session_id=sid)
        return jsonify({'ok': False, 'rejected': 1, 'rejected_items': rejected,
                        'error': '；'.join(rejected[0].get('verify_reasons') or ['未通过校验'])}), 400
    event = verified[0]
    reason, existing = detect_conflict(cid, event, course)
    if reason:
        conf_id = db.add_conflict(cid, event, {'items': existing[:5]}, reason, sid)
        return jsonify({'ok': True, 'conflict': True, 'conflict_id': conf_id,
                        'reason': reason, 'course_id': cid,
                        'conflicts': db.list_conflicts(cid)})
    n = db.add_classroom_events(cid, [event], sid)
    sync_graph_from_classroom(cid)             # 人工补录也挂到图谱并落盘
    review = demo.review_updates(cid)          # Reviewer：证据够不够撑住这条结论
    db.add_memory_reviews(cid, review['items'], sid)
    db.record_change(
        f'人工补录「{course["name"]}」课堂卡点',
        f'识别链路失效时由人补充：{kp_label} · {text[:40]}；'
        f'来源 human_input，置信度 {event["confidence"]:.2f}',
        {'ctx': [f'course: {cid}'], 'del': [],
         'add': [f'{kp_label} ← 人工补录 @{event["t_sec"]}s']},
        node_id=kp_id, session_id=sid)
    emit('evolve', {'course_id': cid, 'inserted': n,
                    'note': f'人工补录已写入「{course["name"]}」'})
    return jsonify({'ok': True, 'inserted': n, 'event': event, 'session_id': sid,
                    'review': review, 'summary': db.classroom_summary(cid)})


# ---------------------------------------------------------------------
# 人生十维支点（只读聚合，不建新表）
# 固定枚举，不让模型/用户自由命名；没有命中就进 uncategorized。
# ---------------------------------------------------------------------
PIVOT_DEFS = [
    ('goal', '目标支点', ['作业', '截止', '明天', '下周', '这学期', '考研', '考证',
                          '计划', '目标', '完成', '减重', '马拉松', '存钱', 'deadline']),
    ('career', '职业支点', ['实习', '简历', '面试', '晋升', 'kpi', '职业', '工作',
                            '人脉', '跨部门', '职场']),
    ('health', '健康支点', ['跑步', '散步', '操场', '运动', '健身', '瑜伽', '拉伸',
                            '喝水', '早睡', '睡觉', '体检', '体重', '控糖', '饮食', '冥想']),
    ('emotion', '情感支点', ['朋友', '室友', '伴侣', '情绪', '焦虑', '疲惫', '累',
                             '感恩', '倾诉', '深聊', '关系', '陪伴', '心态', '压力']),
    ('family', '家庭支点', ['家人', '爸妈', '父母', '家庭', '家务', '亲戚', '家里']),
    ('economy', '经济支点', ['钱', '存款', '理财', '副业', '收入', '预算', '省钱',
                             '投资', '工资', '开销']),
    ('skill', '技能支点', ['ppt', 'excel', '编程', '演讲', '剪辑', '沟通', '写作',
                           '打字', '学会', '拆解', '技能', 'p图']),
    ('knowledge', '知识支点', ['读书', '课程', '课堂', '学习', '复习', '知识', '讲座',
                               '论文', '考试', '外语', '微积分', '导数', '极限', '定理']),
    ('interest', '兴趣支点', ['摄影', '绘画', '音乐', '电影', '书法', '烘焙', '手工',
                              '陶艺', '乐器', '打球']),
    ('life', '生活支点', ['旅行', '户外', '晒太阳', '发呆', '徒步', '聚会', '吃饭',
                          '食堂', '宿舍', '生活', '摆烂', '拒绝']),
]
PIVOT_IDS = {pid for pid, _, _ in PIVOT_DEFS}
PIVOT_NAMES = {pid: name for pid, name, _ in PIVOT_DEFS}


def classify_pivot(text, meta=None, tag=''):
    """固定十维分类：先信已有 meta.pivot，再按关键词命中，最后 uncategorized。"""
    if isinstance(meta, dict) and meta.get('pivot') in PIVOT_IDS:
        return meta['pivot']
    if tag == 'mood':
        return 'emotion'
    lowered = (text or '').lower()
    for pid, _name, keywords in PIVOT_DEFS:
        if any(keyword.lower() in lowered for keyword in keywords):
            return pid
    return 'uncategorized'


@app.get('/api/teach/pivots')
def teach_pivots():
    """十维支点仪表盘：只读事实/洞察/待办/心境，不写库、不改策略。"""
    rows = []
    for fact in db.list_facts(500):
        rows.append({'text': fact.get('text') or '', 'source': 'fact',
                     'ts': fact.get('ts') or 0, 'meta': fact.get('meta') or {}, 'tag': ''})
    for insight in db.list_insights('', 500):
        title = insight.get('title') or ''
        text = title
        if insight.get('description'):
            text = f"{title}：{insight['description']}"
        rows.append({'text': text, 'classify': title, 'source': 'insight',
                     'ts': insight.get('ts') or 0,
                     'meta': insight.get('meta') or {}, 'tag': ''})
    for todo in db.list_todos('all', '', 500):
        rows.append({'text': todo.get('text') or '', 'source': 'todo',
                     'ts': todo.get('ts') or 0, 'meta': todo.get('meta') or {},
                     'tag': '', 'done': bool(todo.get('done'))})
    for note in db.list_life_notes('', 500):
        rows.append({'text': note.get('text') or '', 'source': 'life',
                     'ts': note.get('ts') or 0, 'meta': {}, 'tag': note.get('tag') or ''})

    seen = set()
    items = []
    for row in rows:
        text = (row['text'] or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        row['pivot'] = classify_pivot(
            row.get('classify') or text, row.get('meta'), row.get('tag'))
        items.append(row)

    grouped = {pid: [] for pid, _, _ in PIVOT_DEFS}
    grouped['uncategorized'] = []
    for item in items:
        grouped.setdefault(item['pivot'], []).append(item)

    max_count = max((len(group) for group in grouped.values()), default=0) or 1
    pivots = []
    for pid, name, _keywords in PIVOT_DEFS:
        group = sorted(grouped.get(pid, []), key=lambda row: row.get('ts') or 0, reverse=True)
        latest = group[0] if group else None
        pivots.append({
            'id': pid,
            'name': name,
            'count': len(group),
            'intensity': round(len(group) / max_count, 2),
            'latest': (latest['text'][:80] if latest else ''),
            'last_at': (time.strftime('%m-%d %H:%M', time.localtime(latest['ts']))
                        if latest and latest.get('ts') else ''),
            'items': [
                {'text': row['text'][:120], 'source': row['source'],
                 'at': time.strftime('%m-%d %H:%M', time.localtime(row['ts']))
                 if row.get('ts') else '', 'done': row.get('done')}
                for row in group[:5]
            ],
        })

    counts = [p['count'] for p in pivots]
    active = sum(1 for count in counts if count > 0)
    mean = sum(counts) / len(counts) if counts else 0
    if mean:
        variance = sum((count - mean) ** 2 for count in counts) / len(counts)
        balance = max(0.0, 1 - (variance ** 0.5) / mean)
    else:
        balance = 0.0

    return jsonify({
        'pivots': pivots,
        'coverage': round(active / len(pivots), 2),
        'balance': round(balance, 2),
        'uncategorized': len(grouped.get('uncategorized', [])),
        'formula': 'coverage=有记录的支点数/10；balance=1-各支点计数的变异系数',
        'note': '只按已有事实/洞察/待办/心境聚合；不是心理测量，也不改教学策略。',
    })


@app.get('/api/teach/context')
def teach_context():
    """给问答助手用的紧凑课堂上下文：当前卡点、错因、课堂策略、SOLO。

    只读；不写库、不改策略。问答助手把它放进 OpenTutor 的 tab_context，
    tutor 的 system prompt 会显式引用它。
    """
    # 只让“有课程、有知识点”的采集定义当前课堂上下文。误按麦克风产生的空采集
    # 会落成「未分类课程」；直接取最新一条会让问答助手突然从「电路」跳到
    # 「未分类」，上下文条和知识图谱都会变空。
    sessions = db.list_sessions(12)
    usable = [
        row for row in sessions
        if row.get('course_id') and row.get('course_id') != 'general'
        and row.get('kp') not in ('', '未分类课程')
    ]
    latest = usable[0] if usable else (sessions[0] if sessions else {})
    evolution = db.list_strategies()
    active_id = evolution.get('active')
    active = next((v for v in evolution.get('versions', []) if v.get('id') == active_id), {})
    profile = db.get_profile()
    solo = profile.get('solo') or {}
    course_id = latest.get('course_id') or REG.active
    return jsonify({
        'active_kp': latest.get('kp') or '',
        'node_id': find_node_id(course_id, latest.get('kp') or ''),
        'course_name': latest.get('course_name') or '',
        'course_id': course_id,
        'error_type': latest.get('error_type') or '',
        'strategy': active.get('strategy') or latest.get('strategy') or '',
        'strategy_version': active_id or latest.get('version') or '',
        'strategy_status': active.get('status') or '',
        'solo_level': solo.get('current'),
        'solo_name': solo.get('name') or '',
        'source': '5001 classroom capture',
    })


@app.get('/api/teach/evidence')
def teach_evidence():
    """自测/问答反哺到知识点的证据；只读。"""
    cid = request.args.get('course_id', '')
    node_id = request.args.get('node_id', '')
    items = db.list_learning_evidence(cid, node_id, 100)
    return jsonify({'items': items, 'count': len(items),
                    'course_id': cid, 'node_id': node_id})


def comfort_for(text):
    """把一句状态转成“标签 + 先共情再降负荷”的短回复。

    有模型时让模型写；没有就按关键词给一个保守的规则回复。
    不诊断、不贴心理标签，只影响本轮语气和负荷。
    """
    text = (text or '').strip()
    if LLM_KEY and text:
        try:
            raw = call_llm([
                {'role': 'system', 'content': (
                    '你是学习伙伴。学生用语音说了一段状态：' + text + '\n'
                    '从 焦虑/挫败/疲惫/正常 里选一个状态；用 2-3 句中文先共情，'
                    '再给一个很小的下一步。不要诊断、不说教、不要一次给很多建议。'
                    '只输出 JSON：{"mood":"...","reply":"..."}'
                )},
            ], LLM_MODEL, temperature=0.4, json_mode=True, timeout=40)
            data = json.loads(raw)
            mood = data.get('mood') if data.get('mood') in ('焦虑', '挫败', '疲惫', '正常') else '正常'
            reply = (data.get('reply') or '').strip()
            if reply:
                return mood, reply
        except Exception as exc:
            print('[mood] 模型失败，退回规则：', exc)
    if any(word in text for word in ('累', '困', '疲', '撑不住', '没力气')):
        mood = '疲惫'
        reply = ('听起来你今天真的有点累。先不用解决全部：喝口水、离开屏幕两分钟，'
                 '回来只做最小的一步，比如把题目读一遍。')
    elif any(word in text for word in ('焦虑', '慌', '来不及', '怕')):
        mood = '焦虑'
        reply = ('先停一下，焦虑的时候不适合一次看完整道题。我们只挑最小的一步：'
                 '把已知条件写下来，再决定下一步。')
    elif any(word in text for word in ('挫败', '不会', '做不出', '失败', '崩')):
        mood = '挫败'
        reply = ('卡住不等于不会。先把你卡住的那一步说清楚，我们只修那一步，'
                 '不重做整道题。')
    else:
        mood = '正常'
        reply = '听到了。我们按现在的节奏来，先做最小的一步。'
    return mood, reply


def route_text_to_memory(text, source='qa'):
    """一句话 → 待办 / 事实 / 洞察 / 心境。

    问答页「存为记忆」和硬件语音共用；规则版，返回明确分类。
    """
    text = str(text or '').strip()
    source = str(source or 'qa')[:40]
    if not text:
        return {'ok': False, 'error': '先写或说一句话'}
    digest = hashlib.md5(text.encode('utf-8')).hexdigest()[:12]
    if re.search(r'累|疲惫|焦虑|压力|难过|心情|开心|困', text):
        mood = '疲惫' if re.search(r'累|疲惫|困', text) else (
            '焦虑' if re.search(r'焦虑|压力', text) else '正常')
        db.add_life_note(f'[{mood}] {text}', tag='mood')
        return {'ok': True, 'kind': 'mood', 'kind_label': '心境',
                'message': f'已存入第二大脑 · 心境「{mood}」'}
    if re.search(r'明天|后天|下周|截止|要交|记得|待办|作业|复习|完成', text):
        db.add_todo(text, source=source, priority=2,
                    meta={'route': 'rule', 'raw': text})
        return {'ok': True, 'kind': 'todo', 'kind_label': '待办',
                'message': '已存入待办：' + text[:40]}
    if re.search(r'悟到|发现|学到|明白|原来|心得|体会', text):
        db.add_insight(text[:40], description=text, kind='insight',
                       insight_id=f'{source}:{digest}', source=source,
                       meta={'route': 'rule'})
        return {'ok': True, 'kind': 'insight', 'kind_label': '洞察',
                'message': '已存入成长 · 洞察：' + text[:40]}
    db.add_fact(text, fact_id=f'{source}:{digest}', source=source,
                tags=['voice'], meta={'lane': 'life', 'route': 'rule'})
    return {'ok': True, 'kind': 'fact', 'kind_label': '事实',
            'message': '已存入第二大脑 · 事实：' + text[:40]}


@app.post('/api/teach/route')
def teach_route():
    """一句话 → 待办 / 事实 / 洞察 / 心境。"""
    body = request.get_json(silent=True) or {}
    result = route_text_to_memory(body.get('text'), body.get('source') or 'qa')
    if not result.get('ok'):
        return jsonify(result), 400
    return jsonify(result)


@app.post('/api/teach/asr')
def teach_asr():
    """问答页语音输入：音频 → 文字，不改教学状态。"""
    data = request.get_data()
    if not data and 'file' in request.files:
        data = request.files['file'].read()
    if not data:
        return jsonify({'ok': False, 'error': '没有收到语音'}), 400
    try:
        text, model = call_asr(data)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'语音没听清：{exc}'}), 502
    return jsonify({'ok': True, 'text': text, 'model': model})


@app.post('/api/teach/ask-image')
def teach_ask_image():
    """问答页拍照/上传图片：Qwen VL 回答，带上当前课堂上下文。"""
    body = request.get_json(silent=True) or {}
    image = body.get('image') or ''
    question = (body.get('question') or '').strip() or '这张图里我卡在哪？请结合当前知识点和策略讲。'
    if ',' in image and image.startswith('data:'):
        image = image.split(',', 1)[1]
    if not image:
        return jsonify({'ok': False, 'error': '没有收到图片'}), 400
    sessions = db.list_sessions(1)
    latest = sessions[0] if sessions else {}
    evolution = db.list_strategies()
    active_id = evolution.get('active')
    active = next((v for v in evolution.get('versions', []) if v.get('id') == active_id), {})
    profile = db.get_profile()
    solo = profile.get('solo') or {}
    prompt = (
        f'你是学习伙伴。学生上传了一张图片，问题是：{question}\n'
        f'当前课堂上下文：知识点「{latest.get("kp") or "未知"}」；'
        f'错因「{latest.get("error_type") or "未知"}」；'
        f'课堂策略「{active.get("strategy") or latest.get("strategy") or "未知"}」'
        f'（版本 {active_id or "未知"}）；SOLO L{solo.get("current") or "?"}。\n'
        '先说你从图里看到的关键信息，再结合当前策略分步引导；不要直接给最终答案。'
        '如果图片看不清，直接说看不清，不要编造。'
    )
    try:
        answer = call_vl(image, prompt)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'视觉模型暂时不可用：{exc}'}), 502
    return jsonify({
        'ok': True,
        'answer': answer,
        'context': {
            'active_kp': latest.get('kp') or '',
            'error_type': latest.get('error_type') or '',
            'strategy': active.get('strategy') or latest.get('strategy') or '',
            'strategy_version': active_id or '',
        },
    })


@app.post('/api/teach/mood/voice')
def teach_mood_voice():
    """语音记状态：ASR → 识别心境 → 先共情/降负荷 → 存一条 mood。"""
    data = request.get_data()
    if not data and 'file' in request.files:
        data = request.files['file'].read()
    if not data:
        return jsonify({'ok': False, 'error': '没有收到语音'}), 400
    try:
        text, asr_model = call_asr(data)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'语音没听清：{exc}'}), 502
    mood, reply = comfort_for(text)
    db.add_life_note(f'[{mood}] {text}', tag='mood')
    return jsonify({'ok': True, 'text': text, 'mood': mood,
                    'reply': reply, 'asr_model': asr_model})


@app.get('/api/teach/mood')
def teach_mood_list():
    """第二大脑的轻量心境流：只读最近记录，不参与策略版本。"""
    items = []
    for row in db.list_life_notes('mood', 20):
        items.append({
            'id': row.get('id'),
            'text': row.get('text', ''),
            'time': time.strftime('%m-%d %H:%M', time.localtime(row.get('ts') or 0)),
            'ts': row.get('ts'),
        })
    return jsonify({'items': items, 'count': len(items)})


@app.post('/api/teach/mood')
def teach_mood_add():
    """学生顺手记一条心境/反思；下一轮 judge/answer 会读到。

    这是记忆，不是策略：它只影响共情与负荷，不直接改教学策略版本。
    """
    body = request.get_json(silent=True) or {}
    mood = str(body.get('mood') or '').strip()[:20]
    text = str(body.get('text') or '').strip()[:500]
    if not mood and not text:
        return jsonify({'ok': False, 'error': '写一句状态，或选一个标签'}), 400
    label = mood or '状态'
    db.add_life_note(f'[{label}] {text}'.strip(), tag='mood')
    return jsonify({'ok': True, 'mood': label, 'text': text})


@app.get('/api/teach/conflicts')
def teach_conflicts():
    cid = request.args.get('course_id') or ''
    return jsonify({'course_id': cid, 'conflicts': db.list_conflicts(cid),
                    'feedback_count': db.count_feedback(cid)})


@app.get('/api/teach/rejected')
def teach_rejected():
    """被拒收的证据看板：低置信度 / 乱码 / 校验不通过，只留日志的那些。"""
    cid = request.args.get('course_id') or ''
    return jsonify({'course_id': cid, 'rejected': db.list_rejected(cid, 100)})


# ---------------- 今日记忆：生活碎片 + 课堂沉淀 + 待攻克 ----------------
LIFE_STUDY_TERMS = ('极限', '积分', '导数', '微分', '定理', '公式', '证明', '推导',
                    '电路', '电阻', '电容', '语法', '时态', '从句', '作业', '考试', '复习',
                    '函数', '矩阵', '概率', '物理', '化学')


def _classify_note(text):
    """极轻量的意图分流：像课堂内容就提示去采集，否则当生活碎片。"""
    t = text or ''
    if any(w in t for w in LIFE_STUDY_TERMS) or re.search(r'[=＝∑∫√∴≠≤≥]', t):
        return 'classroom_suggest'
    return 'life'


@app.post('/api/life/note')
def life_note_add():
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': '内容不能为空'}), 400
    tag = _classify_note(text)
    db.add_life_note(text, tag)
    return jsonify({'ok': True, 'tag': tag, 'digest': db.digest_today()})


@app.get('/api/digest/today')
def digest_today():
    d = db.digest_today()
    weak = None
    weak_items = []
    for c in d['courses']:
        course = courses.get_course(c['course_id']) or courses.GENERAL
        c['course_name'] = course.get('name') or c['course_id']
        prof = db.learner_profile(c['course_id'])
        for it in (prof.get('Dw') or {}).get('items') or []:
            score = [int(it.get('wrong') or 0), int(it.get('signals') or 0)]
            item = {'course_id': c['course_id'], 'course_name': c['course_name'],
                    'kp': it.get('kp'), 'kp_id': it.get('kp_id'),
                    'note': it.get('note') or '', 'score': score,
                    'reviewed': bool(it.get('reviewed'))}
            weak_items.append(item)
            if weak is None or score > weak['score']:
                weak = item
    # 没有达到薄弱点门槛时，用今日证据最多的知识点兜底（不硬说“你很弱”）
    if weak is None and d['courses']:
        c = d['courses'][0]
        kp = c['top_knots'][0][0] if c['top_knots'] else ''
        weak = {'course_id': c['course_id'], 'course_name': c['course_name'],
                'kp': kp, 'kp_id': '', 'note': '今天证据最多的知识点，还差一点才够薄弱点门槛',
                'score': [0, c['events']], 'reviewed': False}
    weak_items.sort(key=lambda x: (-x['score'][0], -x['score'][1]))
    open_todos = d.get('todos_open') or []
    ins = d.get('insights') or []
    # Hero 横幅：先看已有证据支撑的薄弱点 → 再看未完成待办 → 再看洞察
    cta = {'label': '开始 8 分钟', 'duration_min': 8}
    if weak:
        headline = f"今天先做：把「{weak.get('kp') or '这个知识点'}」用 3 道变式题走一遍"
        reason = weak.get('note') or '来自课堂证据与历史错题'
        cta.update({'course_id': weak.get('course_id'), 'kp': weak.get('kp'),
                    'kp_id': weak.get('kp_id'), 'context_type': 'weak'})
    elif open_todos:
        t = open_todos[0]
        headline = f"今天先做：{t.get('text') or '完成一条复习任务'}"
        reason = f"来自{t.get('source') or '记忆流'}的待办"
        cta.update({'todo_id': t.get('id'), 'course_id': t.get('course_id') or '',
                    'kp': t.get('kp') or '', 'kp_id': t.get('kp_id') or '',
                    'context_type': 'todo'})
    elif ins:
        headline = f"今天先做：把「{ins[0].get('title','')[:28]}」落成一个可验证行动"
        reason = '来自成长洞察'
        cta.update({'insight_id': ins[0].get('id'), 'context_type': 'insight'})
    else:
        headline = '今天先做：上传一段课堂录音，让系统先认识你'
        reason = '还没有课堂证据，先完成一次采集'
        cta.update({'context_type': 'empty'})
    return jsonify({**d, 'weak': weak, 'weak_items': weak_items[:3],
                    'headline': headline, 'headline_reason': reason,
                    'cta': cta, 'strategy': db.latest_strategy('default'),
                    'gateway': {'life': len(d.get('life') or [])},
                    'rule': corpus_pack.get_shared_rule()})


# =====================================================================
# 官网联动接口：待办 / 事实 / 洞察 / 记忆流 / 证据抽屉 / 时间衰减
# =====================================================================
def _kp_hint(text, course_id=''):
    """从一段文本里猜它属于哪门课、哪个知识点，给洞察卡片和 Agent 上下文用。"""
    if course_id:
        course = courses.get_course(course_id) or courses.GENERAL
    else:
        course = courses.get_course(courses.route(text or '')['id']) or courses.GENERAL
    t = (text or '').lower()
    best = None
    for nid, label, status, conf in course.get('nodes') or []:
        if nid == 'root':
            continue
        score = 0
        if label and label.lower() in t:
            score += 4
        for kw in corpus_pack._node_keywords(nid):
            if kw.lower() in t:
                score += 2
        if score and (best is None or score > best[0]):
            best = (score, nid, label)
    if best:
        return course['id'], best[1], best[2]
    for nid, label, status, conf in course.get('nodes') or []:
        if nid != 'root':
            return course['id'], nid, label
    return course['id'], 'root', course.get('name') or '综合'


@app.get('/api/todos')
def todos_list():
    status = request.args.get('status') or 'open'
    cid = request.args.get('course_id') or ''
    items = db.list_todos(status, cid, 300)
    return jsonify({'todos': items, 'counts': {
        'open': len(db.list_todos('open', cid, 300)),
        'done': len(db.list_todos('done', cid, 300)),
    }, 'course_id': cid})


@app.post('/api/todos')
def todos_add():
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': '待办内容不能为空'}), 400
    cid = body.get('course_id') or ''
    kp = body.get('kp') or ''
    kp_id = body.get('kp_id') or ''
    if (not cid or not kp) and text:
        cid2, kp_id2, kp2 = _kp_hint(text, cid)
        cid, kp, kp_id = cid or cid2, kp or kp2, kp_id or kp_id2
    todo = db.add_todo(text, due=body.get('due') or '',
                       source=body.get('source') or 'manual',
                       course_id=cid, kp=kp, kp_id=kp_id,
                       priority=int(body.get('priority') or 1),
                       meta=body.get('meta') or {})
    return jsonify({'ok': True, 'todo': todo})


@app.post('/api/todos/<int:tid>')
@app.patch('/api/todos/<int:tid>')
def todos_update(tid):
    body = request.get_json(silent=True) or {}
    done = bool(body.get('done', True))
    todo = db.set_todo_done(tid, done, body.get('note') or '')
    if not todo:
        return jsonify({'ok': False, 'error': '待办不存在'}), 404
    cid = todo.get('course_id') or ''
    prof = db.learner_profile(cid) if cid else None
    dpm = None
    if prof:
        dpm = {'course_id': cid, 'Dw_done': (prof.get('Dw') or {}).get('done'),
               'Dw_total': (prof.get('Dw') or {}).get('total'),
               'top': (prof.get('Dw') or {}).get('top')}
    return jsonify({'ok': True, 'todo': todo, 'dpm': dpm,
                    'message': ('已回写 DPM：这条薄弱点标记为复习过一次'
                                if done else '已重新打开，DPM 复习标记已撤销')})


@app.get('/api/facts')
def facts_list():
    items = db.list_facts(200)
    return jsonify({'facts': items, 'count': len(items)})


@app.get('/api/insights')
def insights_list():
    kind = request.args.get('kind') or ''
    items = db.list_insights(kind, 200)
    for it in items:
        cid, kp_id, kp = _kp_hint(f"{it.get('title','')} {it.get('description','')}")
        it['suggested_kp'] = {'course_id': cid, 'kp_id': kp_id, 'kp': kp}
        it['context'] = {'type': 'insight', 'insight_id': it.get('insight_id'),
                         'id': it.get('id'), 'title': it.get('title'),
                         'description': (it.get('description') or '')[:400],
                         'course_id': cid, 'kp_id': kp_id, 'kp': kp}
    return jsonify({'insights': items,
                    'daily': db.list_insights('daily', 50), 'count': len(items)})


@app.get('/api/insights/<int:iid>')
def insights_get(iid):
    it = db.get_insight(iid)
    if not it:
        return jsonify({'error': 'not found'}), 404
    cid, kp_id, kp = _kp_hint(f"{it.get('title','')} {it.get('description','')}")
    it['context'] = {'type': 'insight', 'insight_id': it.get('insight_id'),
                     'id': it.get('id'), 'title': it.get('title'),
                     'description': (it.get('description') or '')[:400],
                     'course_id': cid, 'kp_id': kp_id, 'kp': kp}
    return jsonify(it)


@app.get('/api/memory/stream')
def memory_stream():
    cid = request.args.get('course_id') or ''
    d = db.memory_stream(cid, int(request.args.get('limit') or 300))
    d['courses'] = [{'id': c['id'], 'name': c['name'], 'type': c['type']}
                    for c in courses.COURSES]
    return jsonify(d)


@app.get('/api/evidence/<cid>/<kp_id>')
def evidence_drawer(cid, kp_id):
    kp = request.args.get('kp') or ''
    return jsonify(db.evidence_for_kp(cid, kp_id, kp))


@app.get('/api/review/decay')
def review_decay():
    return jsonify(db.review_decay(request.args.get('course_id') or ''))


@app.get('/api/corpus/status')
def corpus_status():
    st = db.corpus_status()
    st['root'] = corpus_pack.corpus_root()
    st['manifest'] = bool(corpus_pack.load_manifest())
    st['rule'] = corpus_pack.get_shared_rule()
    return jsonify(st)


@app.post('/api/corpus/import')
def corpus_import():
    body = request.get_json(silent=True) or {}
    force = bool(body.get('force'))
    res = corpus_pack.import_all(force=force)
    for cid in {x.get('course_id') for x in res.get('samples') or [] if x.get('course_id')}:
        sync_graph_from_classroom(cid)
    res['status'] = db.corpus_status()
    return jsonify(res)


@app.post('/api/gateway/route')
def gateway_route():
    body = request.get_json(silent=True) or {}
    route, reason = corpus_pack.gateway(body.get('text') or '')
    return jsonify({'route': route, 'reason': reason,
                    'note': ('生活碎片只进轻量记忆流' if route == 'life'
                             else '学习信号，进入 Critic 门禁' if route == 'classroom'
                             else '噪声，只留拒收日志')})


# =====================================================================
# 教学 Agent：带上下文、动态策略、自动写待办、卡壳时用图谱反问
# =====================================================================
AGENT_LEVELS = [
    {'level': 1, 'label': '直接讲解', 'desc': '先把定义/步骤讲清楚，适合确认事实'},
    {'level': 2, 'label': '苏格拉底式启发引导', 'desc': '不直接给答案，用问题把思路逼出来'},
    {'level': 3, 'label': '反例挑战', 'desc': '给一个会失败的反例，逼学生自己修正'},
    {'level': 4, 'label': '迁移拓展', 'desc': '换场景/换条件，确认是真的掌握'},
]
GAP_TERMS = ('不会', '不懂', '没懂', '没听懂', '卡住', '卡壳', '老是错', '又错',
             '没掌握', '混淆', '不明白', '搞不清', '不会做', '没思路', '薄弱')

AGENT_PROMPT = """你是 MetaMind 教学 Agent，目标是把学生自己的课堂证据变成下一步学习动作。
规则：
1. 不直接给最终答案；先指出学生卡在哪一步，再给一个能自己走的小问题。
2. 必须使用给定的课堂证据（ev_id / 时间 / 原文）作为依据，不要编造。
3. 如果学生说“不会/卡住/没掌握”，先确认薄弱知识点，再给一个苏格拉底式反问。
4. 如果上下文是一条成长洞察，把它翻译成一个具体的学习动作，而不是泛泛建议。
5. 语气像助教，短句，中文。
只输出 JSON：
{"reply": "给学生的回复（<=180字）",
 "probing_question": "最后留给学生的一个问题",
 "kp": "本次聚焦的知识点",
 "todo_text": "如果要留复习任务，写一句可执行的待办；不需要则空字符串",
 "evidence_ev_ids": ["引用到的 ev_id"]}"""


def _pick_weak(course_id, kp=''):
    prof = db.learner_profile(course_id) if course_id else {}
    items = (prof.get('Dw') or {}).get('items') or []
    if kp:
        for it in items:
            if it.get('kp') == kp or (kp and kp in (it.get('kp') or '')):
                return it
    if items:
        return items[0]
    return None


def _graph_question(course_id, kp_id='', kp=''):
    """从图谱里找一个前置/邻居，生成不是“直接给答案”的反问。"""
    g = REG.get(course_id)
    node = g.nodes.get(kp_id) if kp_id else None
    if not node and kp:
        node = next((n for n in g.nodes.values() if n.get('label') == kp), None)
    label = (node or {}).get('label') or kp or '这个知识点'
    pre = []
    for a, b in g.edges:
        if b == (node or {}).get('id') and a != 'root':
            pre.append(g.nodes.get(a, {}).get('label', a))
    if pre:
        return (f"先别急着套公式。在进入「{label}」之前，你能说清「{pre[0]}」"
                f"在这里起什么作用吗？如果条件变一下，它还成立吗？")
    return (f"先别急着要答案。你能用一句话说清「{label}」最关键的那一步吗？"
            f"再说一个它“看起来能用、其实不能用”的场景。")


def _strategy_for(session_id, message, course_id=''):
    turns = db.count_agent_messages(session_id)
    text = message or ''
    gap = any(w in text for w in GAP_TERMS)
    asked = any(w in text for w in ('什么', '为什么', '怎么', '定义', '区别', '是什么'))
    if gap:
        level = 2
    elif turns >= 8:
        level = 4
    elif turns >= 4:
        level = 3
    elif asked and not gap:
        level = 1
    else:
        level = 2
    label = next(x['label'] for x in AGENT_LEVELS if x['level'] == level)
    return {'level': level, 'label': label, 'turns': turns,
            'progress': round(min(1.0, level / 4.0), 2),
            'levels': AGENT_LEVELS}


@app.get('/api/agent/strategy')
def agent_strategy():
    sid = request.args.get('session_id') or 'default'
    st = db.latest_strategy(sid)
    st.update(_strategy_for(sid, '', request.args.get('course_id') or ''))
    st['turns'] = db.count_agent_messages(sid)
    st['levels'] = AGENT_LEVELS
    return jsonify(st)


@app.get('/api/agent/messages')
def agent_messages():
    sid = request.args.get('session_id') or 'default'
    return jsonify({'session_id': sid, 'messages': db.list_agent_messages(sid, 200),
                    'strategy': _strategy_for(sid, '')})


@app.post('/api/agent/ask')
def agent_ask():
    body = request.get_json(silent=True) or {}
    message = (body.get('message') or '').strip()
    if not message:
        return jsonify({'ok': False, 'error': '消息不能为空'}), 400
    sid = body.get('session_id') or 'default'
    ctx = body.get('context') or {}
    cid = body.get('course_id') or ctx.get('course_id') or ''
    if not cid:
        cid = courses.route(message + ' ' + (ctx.get('title') or '') + ' ' + (ctx.get('description') or ''))['id']
    course = courses.get_course(cid) or courses.GENERAL
    cid = course.get('id') or cid
    kp = ctx.get('kp') or ''
    kp_id = ctx.get('kp_id') or ''
    if not kp:
        _, kp_id2, kp2 = _kp_hint(message + ' ' + (ctx.get('description') or '') + ' ' + (ctx.get('title') or ''), cid)
        kp, kp_id = kp2, kp_id or kp_id2
    weak = _pick_weak(cid, kp)
    if weak and not kp:
        kp, kp_id = weak.get('kp') or '', weak.get('kp_id') or ''
    strategy = _strategy_for(sid, message, cid)
    strategy['turns'] = db.count_agent_messages(sid) + 1
    gap = any(w in message for w in GAP_TERMS)

    # 自动写待办：识别到知识点未掌握时，写一条针对该知识点的复习任务
    todo = None
    todo_text = ''
    if gap and kp:
        todo_text = f"复习「{kp}」：用 3 道变式题走一遍，并说出每步依据"
        todo = db.add_todo(todo_text, due='今天', source='agent',
                           course_id=cid, kp=kp, kp_id=kp_id, priority=3,
                           meta={'session_id': sid, 'trigger': message[:80],
                                 'context': ctx})
    elif ctx.get('type') == 'insight' and kp:
        todo_text = f"把洞察落到「{kp}」：写一条可验证的小行动"
        todo = db.add_todo(todo_text, due='本周', source='agent',
                           course_id=cid, kp=kp, kp_id=kp_id, priority=2,
                           meta={'session_id': sid, 'insight_id': ctx.get('insight_id')})

    # 证据：只引用这门课真实通过门禁的原始证据
    ev = db.evidence_for_kp(cid, kp_id, kp)
    supports = [x for x in (ev.get('evidence') or [])][-3:]
    sources = [{'ev_id': x.get('ev_id'), 't_label': x.get('t_label'),
                'text': (x.get('text') or '')[:120],
                'source_sample': x.get('source_sample') or '',
                'confidence': x.get('confidence')} for x in supports]
    probing = _graph_question(cid, kp_id, kp)
    reply = ''

    if LLM_KEY:
        try:
            raw = call_llm([
                {'role': 'system', 'content': AGENT_PROMPT},
                {'role': 'user', 'content': json.dumps({
                    'student_message': message, 'context': ctx,
                    'course': {'id': cid, 'name': course.get('name')},
                    'weak_point': weak, 'focus_kp': {'kp': kp, 'kp_id': kp_id},
                    'strategy_level': strategy,
                    'evidence': sources,
                    'graph_neighbors': [n.get('label') for n in
                                        (REG.get(cid).retrieve([kp]) or [])][:6],
                }, ensure_ascii=False)},
            ], LLM_MODEL, temperature=0.3, json_mode=True, timeout=45)
            d = json.loads(raw)
            reply = (d.get('reply') or '').strip()
            probing = (d.get('probing_question') or probing).strip()
            if d.get('kp'):
                kp = d['kp']
            if d.get('todo_text') and gap and not todo:
                todo = db.add_todo(d['todo_text'][:90], due='今天', source='agent',
                                   course_id=cid, kp=kp, kp_id=kp_id, priority=3,
                                   meta={'session_id': sid, 'trigger': message[:80]})
        except Exception as e:
            print('[agent] LLM 回复失败，退回规则版：', e)

    if not reply:
        if ctx.get('type') == 'insight':
            reply = (f"你把这条洞察带进来了：{ctx.get('title') or '一条成长洞察'}。"
                     f"它落到学习上，最具体的一步是「{kp}」。我先不给结论："
                     f"你能从课堂记录里找一条证据，说明你在这上面卡过吗？")
        elif gap:
            reply = (f"先不急着要答案。你说卡在「{kp}」——我先确认一下："
                     f"你是概念没分清，还是步骤写到一半走不下去？"
                     f"如果是概念，我们先看一条你课堂上的原话。")
        else:
            reply = (f"我们现在聚焦「{kp}」。我不会直接给你最终答案，"
                     f"先看一条你课堂里的证据，再一起判断你卡在哪一步。")
        if sources:
            reply += f"（依据：{sources[-1]['t_label']} 的课堂记录）"

    db.add_agent_message(sid, 'user', message, ctx, strategy['level'], strategy['label'])
    db.add_agent_message(sid, 'assistant', reply + '\n\n' + probing, ctx,
                         strategy['level'], strategy['label'],
                         meta={'kp': kp, 'kp_id': kp_id,
                               'todo_id': (todo or {}).get('id'),
                               'ev_ids': [x['ev_id'] for x in sources]})
    return jsonify({
        'ok': True, 'reply': reply, 'probing_question': probing,
        'strategy': strategy, 'course_id': cid, 'course_name': course.get('name'),
        'weak_kp': {'kp': kp, 'kp_id': kp_id,
                    'reviewed': bool((weak or {}).get('reviewed')),
                    'note': (weak or {}).get('note') or ''},
        'todo_created': todo, 'todo_text': todo_text,
        'sources': sources, 'session_id': sid,
        'gateway': {'route': corpus_pack.gateway(message)[0],
                    'reason': corpus_pack.gateway(message)[1]},
    })


@app.post('/api/agent/reset')
def agent_reset():
    body = request.get_json(silent=True) or {}
    sid = body.get('session_id') or 'default'
    with db._conn() as c:
        c.execute('DELETE FROM agent_messages WHERE session_id=?', (sid,))
    return jsonify({'ok': True, 'session_id': sid})


@app.post('/api/teach/conflicts/<int:conf_id>/resolve')
def teach_conflict_resolve(conf_id):
    """记忆冲突人工审阅：采用新证据 / 保留旧记忆。绝不自动消解。"""
    body = request.get_json(silent=True) or {}
    action = body.get('action') or ''
    item = db.get_conflict(conf_id)
    if not item or item.get('status') != 'pending':
        return jsonify({'ok': False, 'error': '这条冲突不存在或已处理'}), 404
    cid = item['course_id']
    if action == 'accept_new':
        if db.is_frozen(cid):
            return jsonify({'ok': False, 'frozen': True,
                            'error': 'DPM 已冻结：先解冻再确认写入'}), 409
        n = db.add_classroom_events(cid, [item.get('new_event') or {}],
                                    item.get('session_id') or '')
        db.set_conflict_status(conf_id, 'accepted')
        db.add_feedback_sample(cid, item.get('kp_label'),
                               (item.get('new_event') or {}).get('text', ''),
                               'accept', item.get('reason') or '',
                               item.get('session_id') or '')
        review = demo.review_updates(cid)
        db.add_memory_reviews(cid, review['items'], item.get('session_id') or '')
        db.record_change(
            f'冲突已人工确认：采用新证据「{item.get("kp_label")}」',
            (item.get('reason') or '人工确认') + '；旧记忆不删除，两条都保留',
            {'ctx': [f'course: {cid}'], 'del': [], 'add': ['新证据经人工确认写入']},
            session_id=item.get('session_id') or '')
        return jsonify({'ok': True, 'inserted': n, 'conflict': db.get_conflict(conf_id),
                        'review': review, 'summary': db.classroom_summary(cid)})
    if action == 'keep_old':
        db.set_conflict_status(conf_id, 'dismissed')
        db.add_feedback_sample(cid, item.get('kp_label'),
                               (item.get('new_event') or {}).get('text', ''),
                               'reject', item.get('reason') or '',
                               item.get('session_id') or '')
        return jsonify({'ok': True, 'inserted': 0, 'conflict': db.get_conflict(conf_id)})
    return jsonify({'ok': False, 'error': 'action 只能是 accept_new / keep_old'}), 400


@app.get('/api/teach/snapshots/<cid>')
def teach_snapshots(cid):
    return jsonify({'course_id': cid, 'snapshots': db.list_named_snapshots(cid)})


@app.post('/api/teach/snapshots/<cid>/restore')
def teach_snapshot_restore(cid):
    """一键回滚：snapshot_0 = 第一轮干净状态，snapshot_1 = 进化后冻结状态。"""
    body = request.get_json(silent=True) or {}
    name = body.get('name') or 'snapshot_0'
    out = db.restore_named_snapshot(cid, name)
    if not out:
        return jsonify({'ok': False, 'error': f'没有名为 {name} 的快照'}), 404
    course = courses.get_course(cid) or courses.GENERAL
    db.record_change(
        f'「{course["name"]}」课堂记忆已回滚到快照 {name}',
        f'恢复 {out["rows"]} 条证据；' + ('快照本身是冻结态，恢复后继续只读'
                                     if out['frozen'] else '快照未冻结，可继续写入'),
        {'ctx': [f'course: {cid}'], 'del': [], 'add': [f'restore: {name}']})
    emit('evolve', {'course_id': cid, 'inserted': out['rows'],
                    'note': f'已回滚到快照 {name}'})
    return jsonify({'ok': True, 'course_id': cid, **out,
                    'summary': db.classroom_summary(cid)})


@app.post('/api/teach/demo/evolve')
def teach_demo_evolve():
    """一键演示：把本节课的示例采集结果灌进去（可重复执行，不会叠加）。"""
    body = request.get_json(silent=True) or {}
    cid = body.get('course_id') or 'math'
    evs = demo.sample_events(cid)
    if not evs:
        return jsonify({'ok': False, 'error': f'课程 {cid} 没有示例采集结果'}), 404
    course = courses.get_course(cid) or courses.GENERAL
    sid = uuid.uuid4().hex[:8]
    verified, rejected = verify_classroom_events(evs, course)
    if rejected:
        db.add_rejected_events(cid, rejected, status=rejected[0].get('verify_status', 'UNVERIFIED'),
                               reasons=rejected[0].get('verify_reasons'), session_id=sid)
    if not verified:
        return jsonify({'ok': False, 'error': '示例证据没有通过校验', 'rejected': len(rejected)}), 400
    # 进化 = 快照0 → 解冻 → 清旧 → 写新 → 冻结 → 快照1；第二轮只读这份快照
    if (db.classroom_summary(cid)['total'] == 0
            and not any(s['name'] == 'snapshot_0' for s in db.list_named_snapshots(cid))):
        db.save_named_snapshot(cid, 'snapshot_0', '第一轮 · 干净状态')
    db.freeze_memory(cid, False)
    db.clear_classroom(cid)
    n = db.add_classroom_events(cid, verified, sid)
    review = demo.review_updates(cid)        # Reviewer：先核对结论，再冻结
    db.add_memory_reviews(cid, review['items'], sid)
    frozen = db.freeze_memory(cid, True)
    db.save_named_snapshot(cid, 'snapshot_1', '第二轮 · 已进化并冻结')
    kps = sorted({e['kp_label'] for e in verified})
    db.record_change(
        f'课堂记忆写入「{course["name"]}」子图谱',
        f'通过校验的 OCR 板书 + ASR 课堂语音 + 振动打点共 {n} 条，时间戳全部保留；'
        f'挂到 {"、".join(kps)} 这 {len(kps)} 个知识点上',
        {'ctx': [f'course: {cid}'], 'del': [],
         'add': [f'{e["kp_label"]} ← {e["kind"]} @{e["t_sec"]}s' for e in verified[:4]]},
        node_id=verified[0].get('kp_id', ''), session_id=sid)
    emit('verify', {'status': 'PASS', 'verified': len(verified), 'rejected': len(rejected),
                    'note': f'示例证据校验：{len(verified)} 条通过，{len(rejected)} 条只留日志'})
    emit('evolve', {'course_id': cid, 'inserted': n,
                    'note': f'课堂时序证据已写入「{course["name"]}」子图谱，并已冻结（只读）'})
    return jsonify({'ok': True, 'inserted': n, 'verified': len(verified),
                    'rejected': len(rejected), 'course_id': cid, 'session_id': sid,
                    'frozen': frozen, 'review': review,
                    'summary': db.classroom_summary(cid)})


@app.post('/api/teach/demo/freeze')
def teach_demo_freeze():
    """手动冻结 / 解冻 DPM。冻结后第二轮导学只读，写入会被拒绝。"""
    body = request.get_json(silent=True) or {}
    cid = body.get('course_id') or 'math'
    want = body.get('frozen')
    frozen = db.is_frozen(cid) if want is None else bool(want)
    info = db.freeze_memory(cid, frozen)
    course = courses.get_course(cid) or courses.GENERAL
    db.record_change(
        f'「{course["name"]}」DPM 已{"冻结（第二轮只读）" if frozen else "解冻"}',
        '冻结时把课堂证据拍成快照，后续读取全部走快照，保证两轮对比可复现'
        if frozen else '解冻后允许继续写入新的课堂证据',
        {'ctx': [f'course: {cid}'], 'add': [], 'del': []})
    return jsonify({'ok': True, 'course_id': cid, **info})


@app.post('/api/teach/demo/reset')
def teach_demo_reset():
    """清空某门课的课堂记忆，回到第一轮状态（演示重放用）。"""
    body = request.get_json(silent=True) or {}
    cid = body.get('course_id') or 'math'
    db.freeze_memory(cid, False)          # 先解冻，否则删了还读快照
    n = db.clear_classroom(cid)
    db.save_named_snapshot(cid, 'snapshot_0', '第一轮 · 干净状态')  # 演示安全网基线
    course = courses.get_course(cid) or courses.GENERAL
    if n:
        db.record_change(
            f'「{course["name"]}」课堂记忆已清空（回到第一轮）',
            f'删除 {n} 条课堂时序证据；静态技能图与作答历史不受影响',
            {'ctx': [f'course: {cid}'], 'add': [], 'del': [f'课堂证据 ×{n}']})
    return jsonify({'ok': True, 'removed': n, 'course_id': cid})


@app.post('/api/teach/quiz/answer')
def teach_quiz_answer():
    """学生交卷：逐题判分 → 写 attempts → 重算计划/画像 → 必要时升策略版本。"""
    body = request.get_json(silent=True) or {}
    sid = body.get('session_id', '')
    answers = body.get('answers') or []
    session = db.get_session(sid) if sid else None
    node_id = ''
    if session:
        node_id = find_node_id(session.get('course_id') or REG.active,
                               session.get('kp') or '')
    out = db.grade_quiz(sid, answers, node_id=node_id)
    if not out:
        return jsonify({'ok': False, 'error': 'session not found or quiz empty'}), 404
    emit('graded', {'session_id': sid, 'correct': out['correct'],
                    'total': out['total'], 'evolved': out['evolved']})
    return jsonify(out)


@app.get('/api/teach/sessions')
def teach_sessions():
    """历史记录：问过什么、给了什么结论、自测几分"""
    return jsonify({'items': db.list_sessions(int(request.args.get('limit', 30)))})


@app.post('/api/teach/quiz/retry')
def teach_quiz_retry():
    """策略切换后，用新讲法再测 3 题；不重新拍照，直接复用同一错因。"""
    body = request.get_json(silent=True) or {}
    src = body.get('session_id', '')
    new_sid = uuid.uuid4().hex[:8]
    out = db.create_retry_session(src, new_sid)
    if not out:
        return jsonify({'ok': False, 'error': 'session not found'}), 404
    return jsonify({'ok': True, **out})


@app.get('/api/teach/session/latest')
def teach_latest():
    """最近一次解答（给页面首次加载用，避免空白）"""
    with db._conn() as c:
        r = c.execute('SELECT * FROM sessions ORDER BY ts DESC LIMIT 1').fetchone()
    if r:
        quiz = []
        if not r['total']:
            quiz = db.public_quiz(json.loads(r['quiz_json'] or '[]'))
        return jsonify({
            'session_id': r['id'], 'asr': r['asr'], 'ocr': r['ocr'],
            'conclusion': r['conclusion'],
            'steps': json.loads(r['steps_json'] or '[]'),
            'strategy': r['strategy'], 'version': r['version'],
            'error_type': r['error_type'], 'kp': r['kp'],
            'course_id': r['course_id'] if 'course_id' in r.keys() else '',
            'course_name': r['course_name'] if 'course_name' in r.keys() else '',
            'verification': r['verification'] if 'verification' in r.keys() else 'PASS',
            'correct': r['correct'], 'total': r['total'],
            'quiz': {'session_id': r['id'], 'error_type': r['error_type'],
                     'kp': r['kp'], 'strategy': r['strategy'],
                     'course_id': r['course_id'] if 'course_id' in r.keys() else '',
                     'course_name': r['course_name'] if 'course_name' in r.keys() else '',
                     'items': quiz} if quiz else None,
        })
    return jsonify({
        'asr': '', 'ocr': '', 'conclusion': '', 'steps': [],
        'strategy': '反例优先', 'version': 'v7',
    })


@app.get('/api/teach/stream')
def teach_stream():
    """SSE：比 WebSocket 更容易嵌 iframe，也更省事"""
    def gen():
        q = queue.Queue()
        SUBSCRIBERS.add(q)
        try:
            yield 'data: ' + json.dumps({'type': 'hello',
                                         'graph': REG.get(REG.active).snapshot(),
                                         'active_course': REG.active,
                                         'courses': courses.list_courses(),
                                         'versions': db.list_strategies()['versions']},
                                        ensure_ascii=False) + '\n\n'
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield 'data: ' + json.dumps(ev, ensure_ascii=False) + '\n\n'
                except queue.Empty:
                    yield ': ping\n\n'
        finally:
            SUBSCRIBERS.discard(q)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                             'Connection': 'keep-alive'})


@app.post('/api/teach/ingest')
def teach_ingest():
    """页面上的「开始」按钮走这里；和硬件用的是同一条流水线"""
    img = request.files.get('image')
    aud = request.files.get('audio')
    image_bytes = img.read() if img else b''
    audio_bytes = aud.read() if aud else b''
    job_id = uuid.uuid4().hex[:8]
    threading.Thread(target=run_pipeline,
                     args=(job_id, image_bytes, audio_bytes,
                           request.form.get('device_id', 'teach-page'),
                           request.form.get('text', '')),
                     daemon=True).start()
    return jsonify({'ok': True, 'job': job_id})


# ---------------- 手表用的裸字节接口（C 写 multipart 太麻烦） ----------------
RAW_PENDING = {}


@app.post('/api/teach/raw')
def teach_raw():
    """ESP32 分两次发：?kind=image 发 JPEG，?kind=audio 发 WAV。"""
    kind = request.args.get('kind', '')
    device_id = request.args.get('device_id', 'esp32-s3')
    if kind not in ('image', 'audio'):
        return jsonify({'ok': False, 'error': 'kind must be image|audio'}), 400
    data = request.get_data()
    if not data:
        return jsonify({'ok': False, 'error': 'empty body'}), 400
    RAW_PENDING.setdefault(device_id, {})[kind] = data
    if kind == 'audio':
        try:
            path = os.path.join(UPLOAD_DIR, 'last_hardware.wav')
            with open(path, 'wb') as f:
                f.write(data)
            import wave
            import struct
            w = wave.open(path, 'rb')
            frames = w.readframes(w.getnframes())
            rate = w.getframerate()
            w.close()
            limit = min(len(frames), rate * 2 * 5)
            peak = 0
            for i in range(0, limit - 1, 2):
                value = abs(struct.unpack('<h', frames[i:i + 2])[0])
                if value > peak:
                    peak = value
            print(f'[hw-audio] bytes={len(data)} rate={rate} peak={peak}', flush=True)
        except Exception as exc:
            print('[hw-audio] inspect failed:', exc, flush=True)
    return jsonify({'ok': True, 'kind': kind, 'bytes': len(data)})


@app.post('/api/teach/commit')
def teach_commit():
    """手表按完一次，调用它开始分析；两张都到齐才跑。"""
    device_id = request.args.get('device_id', 'esp32-s3')
    buf = RAW_PENDING.pop(device_id, {})
    image_bytes = buf.get('image', b'')
    audio_bytes = buf.get('audio', b'')
    if not image_bytes and not audio_bytes:
        return jsonify({'ok': False, 'error': 'nothing uploaded'}), 400
    job_id = uuid.uuid4().hex[:8]
    threading.Thread(target=run_pipeline,
                     args=(job_id, image_bytes, audio_bytes, device_id, ''),
                     daemon=True).start()
    return jsonify({'ok': True, 'job': job_id})


@app.get('/api/teach/result/<device_id>')
def teach_result(device_id):
    """手表可轮询这一行，显示在 OLED 上（一行 <= 21 字符）。"""
    return jsonify({'line': LAST_RESULT.get(device_id, '[WAIT]')})


if __name__ == '__main__':
    print('=' * 60)
    print(f'  MOCK={MOCK}  WebSocket={HAS_WS}  模型: ASR={ASR_MODEL} VL={VL_MODEL} 裁决={LLM_MODEL}')
    print('  打开 http://127.0.0.1:5000')
    print('=' * 60)
    app.run(host='0.0.0.0', port=5001, threaded=True)
