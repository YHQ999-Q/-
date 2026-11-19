# METAMIND 模块 3：Teaching Reflection Engine（教学反思）

> 目标：不是写课后总结，而是**根据真实证据诊断教学问题**，产出一份可执行、可验证的改进方案，并把它变成后续 Skill 进化的依据。
>
> 代码：`apps/api/services/evolution/reflection_engine.py`（**不是** `services/agent/reflection.py`，后者是回答质量检查）
> 接口：`POST /api/teaching/reflect`、`GET /api/teaching/reflections[/{id}]`
> 表：`teaching_reflections`

## 1. 输入：证据包（Evidence Package）

反思不是把评价分数丢给模型让它编故事。每次反思先构建一个可审计的证据包：

| 组成 | 来源 | 用途 |
|---|---|---|
| 学习目标 + objective_id | `teaching_evaluations.objective_*` / `knowledge_nodes` | 明确判断对象 |
| 当前学生状态 | `build_learner_state()`（模块 1） | 概念状态、掌握度、错题类型、练习次数 |
| 本轮教学策略及版本 | `teaching_evaluations.strategy_version` | 允许为 `null`（尚无版本时） |
| 实际教学过程与学生回答 | `practice_results` + `inputs_json` 里的开放题/迁移题作答 | 诊断的原始材料 |
| Evaluator 结果 | 分数、verdict、`evidence_json`、`rationale_json` | 判断依据 |
| 已有教学技能 | `services/agent/skills.load_skills()`（5 个原生技能） | 提案只能复用这些 |
| 学生主观反馈 | 请求参数，标记 `is_objective_self_report: true` | 仅作参考，不能当客观证据 |

证据包里同时给出 `how_to_cite.allowed_source_ids`（真实可引用的 id 白名单）和引用规则。

## 2. 输出：五段结构（Pydantic 强校验）

```json
{
  "problem": {"statement": "...", "severity": "low|medium|high",
              "dimension": "understanding|application|transfer|multiple", "affected_objective": "..."},
  "evidence": [{"source_type": "...", "source_id": "<白名单里的 id>", "quote": "<逐字片段>", "note": "..."}],
  "cause_hypothesis": {"statement": "...", "confidence": "low|medium|high",
                       "is_verified": false, "rationale": "...", "alternatives": ["..."]},
  "improvement_proposal": {"summary": "...", "target_dimension": "...",
                           "reuses_skills": ["<技能 id>"], "steps": ["...", "..."],
                           "trigger_conditions": ["..."], "expected_effect": "...",
                           "is_verified": false},
  "verification_plan": {"metric": "...", "comparison": "...", "next_run_procedure": ["..."],
                        "success_criteria": ["..."], "failure_criteria": ["..."],
                        "uses_parallel_items": true}
}
```

每个关键判断都必须能追溯到 evidence：`source_id` 必须在白名单内，`quote` 必须在该来源中**逐字存在**。

## 3. 校验规则（代码里硬性执行）

| 规则 | 失败码 |
|---|---|
| 引用的 id 不在证据包里 | `unknown_evidence_id` |
| 引用文本不是来源原文（改写/编造学生原话） | `evidence_not_verbatim` |
| `cause_hypothesis.is_verified = true` 或 `improvement_proposal.is_verified = true` | `unverified_claim` |
| `reuses_skills` 里有不存在的技能，或一个都没用 | `unknown_skill` |
| 步骤少于 2 条、步骤过短、含「多练习/更努力/讲清楚一点」这类空话 | `vague_proposal` |
| 缺成功标准或失败标准 | `vague_proposal` |
| 模型输出不是 JSON | `invalid_json` |
| 结构不符合 Schema | `schema_mismatch` |

失败会**重试**（默认最多 3 次，每次把具体失败原因回传模型），仍失败则写入
`status='failed'` + `error_code` + `raw_llm_response`（含每次尝试的原文），绝不伪装成成功。

引用容错：模型偶尔写 `practice_result:<uuid>` 而不是裸 uuid。这属于格式笔误而非编造，
只要裸 uuid 真在证据包里就接受；前缀救不活不存在的 id。

## 4. 门控（不调用模型就能拒绝）

| 情况 | 状态 | 理由 |
|---|---|---|
| 评价 `status='failed'` | `insufficient_evidence` | 评价本身失败，没有可用证据 |
| `dimensions_measured == 0` | `insufficient_evidence` | 一个维度都没测过 |
| `verdict == 'achieved'` | `no_improvement_needed` | 目标已达成，**不为强行进化虚构失败** |

这三种情况 `attempts = 0`，`cause_hypothesis` / `improvement_proposal` 均为 `null`。

## 5. 数据库结构（`teaching_reflections`）

| 列 | 说明 |
|---|---|
| `id` | reflection_id（独立主键） |
| `user_id` / `course_id` | 归属 |
| `evaluation_id` | **FK → teaching_evaluations.id（CASCADE）**，一条评价可有多条反思历史 |
| `objective_slug` / `objective_id` / `session_id` / `strategy_version` / `rubric_version` | 上下文快照 |
| `status` | completed / insufficient_evidence / no_improvement_needed / failed |
| `problem_statement` / `problem_severity` / `problem_dimension` | 问题段（平铺便于查询） |
| `cause_statement` / `cause_confidence` / `cause_hypothesis_json` | 原因假设（含 alternatives、is_verified） |
| `improvement_json` / `verification_plan_json` | 提案与验证计划全文 |
| `evidence_json` | `gate` / `cited`（引用清单）/ `evaluation_snapshot` / `allowed_source_ids` |
| `input_package_json` | 完整输入证据包（可复现） |
| `input_hash` | 证据包 sha256 |
| `attempts` / `model_provider` / `model_name` / `raw_llm_response` | 尝试次数与原始输出 |
| `error_code` / `error_message` | 失败原因 |
| `created_at` | |

索引：`(user_id, course_id, created_at)`、`(evaluation_id)`。

**不修改任何评价分数、学习者进度、技能或 Tutor Prompt。** 反思只读输入、只写自己的表。

## 6. 真实输出示例

概念混淆场景（`D:\Hermes\archive\metamind-20261114\reflection-completed.json`，`attempts=1`）：

- **problem**：学生把「记住偏好/长期记忆」直接等同于「自进化 Agent」，缺少四个必要条件，
  概念题与应用题均为 0 分，且主观「懂了」与实测表现严重背离。
- **evidence**（8 条，全部逐字校验通过）：学生开放题原话、2 条答题记录、评价理由、
  2 个概念状态（`concept:…`）、主观反馈、学习目标。
- **cause_hypothesis**：学生以「行为是否随我变化」作为唯一判据，缺少
  「经验积累→反思诊断→策略改变→结果验证」的因果链表征；`confidence=medium`，
  `is_verified=false`，另给 3 条替代假设（题目表述含糊 / 应试粗心 / 表征组织问题）。
- **improvement_proposal**：`reuses_skills = [error_analysis, worked_examples,
  socratic_questioning, analogies]`，6 条可执行步骤（拆解原句判据漏洞 → 恒温器对比案例 →
  四步完整样例 → 判定清单自查 → 苏格拉底追问 → 新场景独立判定），`is_verified=false`。
- **verification_plan**：指标 = 同一 rubric 下 understanding 档位 + 一道新的区分型选择题；
  对比 = 与本次评价（`understanding=0`）比较，**只用平行新题**；成功/失败标准明确。

三种状态的真实样本：

| 文件 | 场景 | status |
|---|---|---|
| `reflection-completed.json` | 概念混淆 | `completed`（attempts=1） |
| `reflection-achieved.json` | 三维全 2，目标达成 | `no_improvement_needed`（attempts=0，无提案） |
| `reflection-insufficient.json` | 评价没测到任何维度 | `insufficient_evidence`（attempts=0） |

## 7. 验证

```bash
# 单元（无网络、无模型，含门控/校验/重试/不落脏数据）：33 项
.venv-win/Scripts/python.exe scripts/metamind_demo/test_reflection_unit.py

# 场景（真实 API + 真实模型）：C1 概念混淆 / C2 重复一致性 / C3 已达成 /
# C4 无证据 / C5 持久化与查询
.venv-win/Scripts/python.exe scripts/metamind_demo/verify_reflection.py

# 抓真实样本
.venv-win/Scripts/python.exe scripts/metamind_demo/capture_reflection_sample.py
```

## 8. 已知问题

1. **模型输出偶发格式漂移**：同一输入重复调用，`status` 稳定落在 terminal 状态，
   但首次尝试可能因引文改写或 id 前缀而被拒，需要 1-3 次重试。这就是为什么
   `attempts`、`raw_llm_response`、`input_hash` 必须落库。
2. **`no_improvement_needed` 要求三维全满**（`achieved`）。只测两维即使都是 2 分也不触发，
   会走正常反思；这是刻意保守。
3. **不评价 Retention**：验证计划里可以安排延迟测验，但本模块不产出 retention 分数。
4. **反思不会自动变成 Skill**：本次只产出提案文本，Skill 写入/启用属于模块 4，
   且必须先经过人工或规则验收。
5. 反思质量依赖证据质量：如果 Evaluator 某维度是 `null`，反思里对应判断会缺失而不是瞎猜。

## 9. 回滚

- 代码：`git revert` 模块 3 的提交（删除 `models/teaching_reflection.py`、
  `services/evolution/reflection_engine.py`、`alembic/versions/20261114_0028_*.py`，
  以及 `routers/teaching.py` / `schemas/teaching.py` / `models/__init__.py` 的新增片段）。
- 数据：`DROP TABLE teaching_reflections;`；演示数据整体清理用
  `seed_demo_course.py --purge --yes`，`--reset-learner` 已包含反思记录。
- 回滚本模块不影响模块 1/2 的端点。
