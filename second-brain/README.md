# Bee Agent 复刻（第二大脑 · 最小可跑骨架）

这是按 `research/bee-agent-逆向.md` 做的 **Bee 个人第二大脑 agent 复刻**：录音转写 → 会话切分 → 纪要 → 事实记忆 → 待办建议 → 每日回顾 → Grow 洞察 → 检索 → 工具调用式 Chat → 语音笔记。
它**不是**教学 Agent，也不包含 Bee 的任何官方代码或提示词。

- 纯 Python 3.11+ 标准库（sqlite3 / json / urllib），不用装依赖。
- 没配模型 key 时走**确定性 mock**，全流程离线可跑；配上 DeepSeek / 通义千问 / Kimi 等 OpenAI 兼容接口就换成真实推理。
- `beeagent/prompts/` 里的 7 份提示词都是**根据 Bee 输出反推的还原版，不是官方原文**（对应报告 §5.1–§5.7）。

## 三条命令跑起来

```bash
python3 -m beeagent demo                       # 端到端演示：ingest → 切分 → 纪要 → 事实 → 待办建议 → 每日 → Grow → 检索 → Chat → 语音笔记 → 状态
python3 -m unittest discover -s tests -v       # 16 个离线测试
python3 -m beeagent chat --db data/demo.db     # 在 demo 数据上交互提问（空行退出）
```

demo 用 `data/demo.db`，每次重跑都会清空；生成的纪要在 `data/outputs/`。

### 全部子命令

| 命令 | 作用 |
|---|---|
| `ingest PATH [--date YYYY-MM-DD] [--seed-facts]` | 读入 transcript.txt（`[HH:MM:SS] Speaker: 文本`）或 JSON utterances（也认 Bee 会话 JSON 的 `transcriptions[0].utterances`），文件或目录都行，进原始语音流 |
| `process [--now ISO]` | 静音切分 → 整段重转写（桩）→ 摘要 → 同一时刻写事实和待办建议 → 预计算相关会话。给 `--now` 时，最后一句离 now 不到 360 秒的会话保持 CAPTURING，下次再处理 |
| `facts list / confirm ID / reject ID` | 事实确认流程（AI 抽的是 pending；确认后才会被后续摘要召回） |
| `todos list / suggest [--proactive --now ISO] / accept ID / dismiss ID` | 待办建议 → 正式待办（保留 emoji 前缀） |
| `daily` / `run-daily [--date] [--cutoff 20:20]` | 每日回顾；`--cutoff 20:20` 复现 Bee「20:20 后的会话进不了当天」 |
| `grow` / `run-grow [--now ISO] [--list]` | 72 小时窗口的 Grow 洞察 |
| `search QUERY [--filter all/conversations/daily/facts/journals] [--mode keyword/semantic]` | BM25 检索 |
| `chat [--ask 问题] [--now ISO]` | 工具调用式 Chat；不带 `--ask` 是交互模式 |
| `journal "文本"` | 语音笔记：cleanedUpText / message / followUp / todos |
| `status` / `show 会话ID` / `reset` | 状态、看单段纪要（含相关会话）、清库 |

全局参数 `--db 路径` 可以切换数据库（默认 `data/beeagent.db`）。

## 模块与 Bee 对照表

| 本项目 | Bee（报告章节） | 说明 |
|---|---|---|
| `transcribe.py` | 采集 + ASR §3.2 | 只接现成转写；`batch_retranscribe` 模拟「会话关闭后整段重转写，realtime=true→false」。`ASRBackend` 留了 FunASR / Whisper 接口（TODO） |
| `segment.py` | 会话切分 §3.1 | 静音 ≥360 秒关会话（可调）；首句 +30 秒才算立会话、之前的缓冲回填；end_time = 最后一句 +360 秒；CAPTURING → COMPLETED |
| `summarize.py` + `prompts/5.1` | Summaries §3.3 | 场景识别（lecture / meeting / one_on_one / casual…）、默认模板 Summary / Atmosphere / Key Takeaways / Action Items、场景模板 |
| `config/scene_templates.json`、`config/user_rules.json` | §7.3 改进 1、2 | 规则按场景直接注入（不靠检索碰运气），带版本号；`rules_applied` 记下每次用了哪些规则 |
| `facts.py` + `prompts/5.2` | My facts §3.4 | 每段 ≤3 条，与摘要同一时刻写入，默认 pending；标签只用固定 11 个；敏感类目不抽取 |
| `todos.py` + `prompts/5.3` | Todo suggestions §3.5 | 会话级（emoji + 祈使句 + details + alarm_at）与主动式（结合近 48 小时、facts、现有待办）；日期不明确就不填 alarm_at |
| `daily.py` + `prompts/5.4` | Daily §3.6 | 汇总当天会话；Bee 约 20:20 定时 |
| `grow.py` + `prompts/5.5` | Grow §3.7 | 72 小时窗口，6–8 条，每条**恰好 1 个**行动；Bee 约 03:23 批量 |
| `search.py` | 检索 §2、§4.4 | 纯 Python BM25 覆盖会话 / 每日 / 事实 / 语音笔记；语义检索是桩（只覆盖会话，先退化成 BM25）；相关会话按关键词重叠预计算，写进 `suggested_links` |
| `chat.py` + `prompts/5.6` | Chat / threads §3.8 | 工具：search、get_conversation、list_facts、create_fact、list_todos、create_todo、get_daily、get_insights；回答带日期和出处，找不到就说没找到 |
| `journal.py` + `prompts/5.7` | Journals §3.9 | 一次产出清洗稿、回应、追问、待办 |
| `scheduler.py`、`scripts/crontab.example` | 定时 §3.6 / §3.7 | 不常驻进程；cron 在 20:20 跑 `run-daily`、03:23 跑 `run-grow` |
| `store.py` | 数据模型 §2 + §7.2 | SQLite：conversations / utterances / facts / todos / todo_suggestions / dailies / insights / journals + 原始语音流 |

数据模型字段对齐 Bee：Conversation（id, start_time, end_time, state, short_summary, summary, scene, keywords, suggested_links）、Utterance（text, speaker, start, end, spoken_at, realtime）、Fact（text, tags, confirmed, source_conversation_id）、Todo、TodoSuggestion（emoji, text, details, alarm_at）、Daily（date, short_summary, summary, conversations_count）、Insight（title, description, suggested_actions[1]）、Journal（text, aiResponse{cleanedUpText, message, followUp, todos}）。

## 接真实模型

```bash
cp .env.example .env      # 填 OPENAI_COMPATIBLE_BASE_URL、OPENAI_API_KEY、MODEL 三项
python3 -m beeagent demo  # 第一行会显示 模式：llm:模型名
```

- 支持任何 OpenAI 兼容的 `/chat/completions` 接口，例子里有 DeepSeek、通义千问（DashScope 兼容模式）、Kimi。Chat 需要模型支持 function calling。
- 某一步调用失败或返回的不是合法 JSON，会打印 `[warn]` 并回退 mock，流程不中断。
- 模型输出会被强制校验：事实最多 3 条、标签只留固定词表、洞察最多 8 条且每条只留 1 个行动。
- `BEEAGENT_MOCK=1` 可以在有 key 时强制离线。

## 测试语料（fixtures/）

- `fixtures/raw/<日期>/*.txt`：6 段脱敏转写，跨 11/11–11/13 三天（4 段课堂/讲座、1 段项目讨论、1 段一对一咨询）。demo 把它们当成**一条连续语音流**读入，再用静音规则重新切成 6 段。
- `fixtures/calendar.json`：脱敏课表（只有课程名和时间段），用来对齐课程名。
- `fixtures/facts_seed.json`：2 条预置的已确认事实，其中 1 条是用户写进 Bee 的「课堂或讲座」纪要规则，用来复现 Bee「规则 = 已确认 fact，靠召回生效」的机制。
- `fixtures/reference/`：Bee 当时的原始产出（摘要、每日回顾、洞察，均已脱敏），用来**对照**，不是标准答案。

人名、学校、绩点、联系方式和私人生活内容都已去掉。

## 和 Bee 相比还缺什么

1. **没有硬件**：不采集音频。以后可以接 Omi 开源硬件，或「安卓手机 + 领夹麦」。
2. **没有 ASR**：只接现成转写。`transcribe.ASRBackend` 留了接口，计划接 FunASR（Paraformer-zh / SenseVoice）或 Whisper large-v3。
3. **没有说话人分离**：和 Bee 本账号一样全是 Unknown。所以 mock 不从「我想……」这类句子抽事实，免得把别人的话记成用户的。计划接 3D-Speaker / pyannote 加声纹注册。
4. **切分只按静音**：Bee 还有按话题切分和单段时长上限（报告 §3.1、§8 都没能确认），这里没做。并发音源的重叠会话也没处理。
5. **mock 只是能跑通**：抽取式摘要直接用转写原句（带口语和同音字），关键词用 n-gram 统计，场景识别靠关键词计数，待办建议会混进老师自己的任务。要像样得接真实模型。
6. **语义检索是桩**：先退化成 BM25。要做的话接 pgvector / Milvus 加 bge-m3 一类的向量模型。
7. **没接外部数据**：没有邮件、日历（只有脱敏课表）、位置、照片、HealthKit，也不提供发邮件、建日程这类对外动作。
8. **事实只有新增**：`op=update` 改写旧事实还没做。也没有 corrections（纠错）回流。

## 以后把 Bee 接成输入源（现在不调用）

报告 §7.4 提过一个合规做法：短期内继续用 Bee 当采集端，下游全用本项目。

- 增量：`bee changed`（`GET /v1/changes?cursor=`）拉新会话，把 `transcriptions[0].utterances` 存成 JSON，再 `python3 -m beeagent ingest 文件.json`（`transcribe.parse_utterances_json` 已经认这个格式）。
- 实时：`bee stream`（SSE）的 `new-utterance` 事件转成 Utterance 写进原始语音流，`update-conversation` / `delete-conversation` 用来对齐会话边界。
- 代价是要受 Bee 切分粗、不分说话人的限制。本骨架目前**不调用任何 Bee API**。

## 目录

```
beeagent/      代码（cli、pipeline 和各模块）
beeagent/prompts/  7 份还原提示词（5.1–5.7）
config/        场景模板、按场景的用户规则
fixtures/      脱敏转写、课表、预置事实、Bee 原始产出对照
tests/         离线单元测试（含一个本地假 OpenAI 服务，测真实模型那条代码路径）
scripts/       run_demo.sh、crontab.example
```
