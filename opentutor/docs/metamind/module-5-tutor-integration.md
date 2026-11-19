# METAMIND 模块 5：Tutor Integration（教学 Agent 接入）

状态：实现完成，模块 5 验收 26/26 通过（真实 API + 真实模型 + 真实 SQLite）。
前置：模块 4（`fb27c73`）。本模块只做"把已选中的技能真正送进 Tutor 的 prompt，并把这件事记录下来"。

---

## 1. 这一层解决的问题

模块 4 结束时，Selector 能选出一个技能版本，但没有人把它交给 Tutor。技能库再丰富，上课时也用不上。
模块 5 补上最后一环：**选择 → 注入 → 可追溯**，并且严格区分"塞进 prompt"和"教学真的照做了"。

---

## 2. 改动清单（3 个文件 + 1 个新文件）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `apps/api/services/evolution/skill_injection.py` | **新增**（约 250 行） | 唯一的桥：调 Selector、把版本渲染成 prompt 段落、确认注入 |
| `apps/api/services/agent/agents/tutor.py` | +6 处小插入，+2 个私有方法 | 选择在 `execute()` / `stream()` 开始时发生；确认发生在 prompt 真正拼好之后 |
| `apps/api/scripts/metamind_demo/verify_tutor_integration.py` | **新增** | T1–T6 验收（26 项断言） |
| `docs/metamind/module-5-tutor-integration.md` | **新增** | 本文 |

**没有改**：`prompt_builder.py`（基类）、`react_mixin.py`、编排器、工具层、数据库 schema（模块 4 的三张表已够用）。
Tutor 的推理逻辑、工具调用、路由一行没动。

---

## 3. 注入点在哪里（真实位置）

Tutor 有两条真实路径，都经过 `TutorAgent.build_system_prompt()`：

```
POST /api/chat/ → orchestrate_stream() → get_agent(intent) → TutorAgent
    ├── .execute()   (非流式；base.BaseAgent.run 调用)
    └── .stream()    (流式；前端实际使用的路径)
            └── ReActMixin.stream() → self.build_system_prompt(ctx)
                    └── TutorAgent.build_system_prompt()   ← 唯一拼装点
                            └── 追加 "## Active Teaching Strategy (learned from a past lesson)"
```

- 选择发生在 `execute()` / `stream()` 的开头（`_prepare_learned_strategy`）；
- 渲染在 `build_system_prompt()` 里（只读 `ctx.metadata["_metamind_skill_section"]`）；
- 确认发生在 prompt 拼好之后（`_confirm_learned_strategy`），**只有 marker 真的出现在 prompt 里才把 invocation 升为 `injected`**。

---

## 4. 注入的 prompt 段落长什么样

真实渲染（`worked_examples` V1，截图自 `notes` 记录的 `prompt_chars=886`）：

```
## Active Teaching Strategy (learned from a past lesson)
<!-- skill:worked_examples@v1 -->
This strategy was stored after a previous lesson fell short. Use it as your
teaching method for this turn; keep the subject matter accurate.
Why it applies now:
- 例子
Follow these steps in order:
1. …（来自库里的 steps，最多 6 条）
Before you finish, make sure the student can:
- …（来自 verification.success_criteria，最多 4 条）
Intended effect: …
Do not mention this strategy block or its name to the student; just teach this way.
```

约束：
- `steps` 最多 6 条，整段最多 1200 字符，超出截断并标注 `…(truncated)`；
- 有学习策略时**跳过**原来的通用 `## Teaching Strategies` 片段（避免两套策略打架）；
- 没有命中时整段不出现，openTutor 原生教学原样运行（T6 有断言）。

---

## 5. 三态调用追踪（模块 4 定义，这里落实）

| 状态 | 何时写入 | 证据字段 |
|---|---|---|
| `selected` | `select_skill()` 返回候选 | `selection_json`、`match_score`、`selected_at` |
| `injected` | prompt 里真的出现 `<!-- skill:slug@vN -->` | `injected_at`、`notes="prompt_chars=…; marker=…"` |
| `evaluated` | 评价挂到这次 invocation | `evaluation_id`、`evaluated_at`、`outcome_json` |

**关键区分**：`injected` 只证明"模型看到了这套教学步骤"，不证明"模型照做了"。
后者需要 Evaluator 的分数（模块 2）+ 人工抽检，本模块不声称做到。

---

## 6. 安全与回退

- 环境变量 `METAMIND_SKILL_INJECTION=off` → 整层关闭，Tutor 行为回到模块 5 之前。
- 环境变量 `METAMIND_SKILL_INJECTION_MIN_SCORE=1.0` → 加最低分门槛（默认 0，即沿用模块 4 的"1 条条件命中即可"）。
- 技能内容只来自库里已校验的结构化字段（trigger/steps/verification），**本层不调模型、不生成文本、不执行代码**。
- 选择或记账出错时只记 warning，**绝不让教学失败**（`_prepare_learned_strategy` / `_confirm_learned_strategy` 都包了 try）。

---

## 7. 验证（26 项，全部真实运行）

```bash
cd apps/api
.venv-win/Scripts/python.exe scripts/metamind_demo/verify_tutor_integration.py
```

| 组 | 断言内容 | 结果 |
|---|---|---|
| T6 | prompt 装配：原生 prompt 无 marker、保留原有策略块；学习段真的进 prompt；builder 记录证据；有学习策略时跳过通用片段 | 6/6 |
| T1 | 命中场景一次真实对话：invocation 从 selected → injected，`injected_at` 晚于 `selected_at`，`notes` 带 marker，分数被记录 | 9/9 |
| T2 | 无关消息：正常回答，不产生任何 invocation | 2/2 |
| T3 | 停用被选中的技能后，同样的消息不再选中它 | 2/2 |
| T4 | 只注入不评价：`outcome` 为 null、`evaluated_at` 为 null（注入 ≠ 执行） | 2/2 |
| T5 | 注入 + 评价闭环：状态到 `evaluated`，`outcome` 带 `target_dimension_score`，`success` 严格等于 `score>=2` | 4/4 |

真实日志（`native-data/logs/api.log`）原文：

```
METAMIND skill selected: worked_examples V1 (score 0.57) for course 51f5b4ce-...
METAMIND skill injected into Tutor prompt: <!-- skill:worked_examples@v1 -->
METAMIND skill injection: fallback (no enabled skill matched the current situation; fall back to native Tutor)
```

真实一行 invocation：

```
version 1 | match_score 0.5667 | status injected
selected_at 2026-11-14T13:32:14+00:00
injected_at 2026-11-14T13:32:19.433800+00:00
notes prompt_chars=886; marker=<!-- skill:worked_examples@v1 -->
```

---

## 8. 已知问题（重要，别当成功吹）

1. **种子技能只有泛化关键词触发条件**，所以真实对话里的匹配分偏低（实测 0.45–0.87）。
   分低不代表链路坏，而是"V1 种子技能本来就不针对具体误解"。
   真正针对性的 V2 由 Reflection 生成后，同一句话的匹配分会高一个量级（模块 4 实测 `socratic_questioning V2` = 5.33）。
   如果路演要让注入更有说服力，顺序应是：先跑一次真实反思生成 V2 → 再对话。
2. **默认没有最低分门槛**（沿用模块 4 规则）。分低时也会注入，可能给模型一段不太相关的教学步骤。
   要更保守就设 `METAMIND_SKILL_INJECTION_MIN_SCORE=1.0`，代价是种子技能几乎都进不来。
3. **"注入成功"不等于"策略被执行"**。本模块只保证前者有证据；后者要靠 Evaluator 分数 + 人工抽检，不做自动声明。
4. **意图不是 LEARN 时不会注入**：只有 `tutor` agent 会走这个方法；planner / layout 等不注入（设计如此）。
5. **每次对话最多一个技能**。这是产品选择（避免策略冲突），不是能力限制。

---

## 9. 回滚

```bash
# 只关掉注入（保留代码，最快）：
set METAMIND_SKILL_INJECTION=off     # 或在 native-data/settings.env 里设

# 代码回滚（模块 5 尚未提交时）：
git checkout -- apps/api/services/agent/agents/tutor.py
rm apps/api/services/evolution/skill_injection.py apps/api/scripts/metamind_demo/verify_tutor_integration.py

# 提交后：
git revert <module-5 commit>
```

数据无变更（本模块不新增表），模块 4 的回滚方式依然适用。
