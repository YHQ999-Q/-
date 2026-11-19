# MetaMind · 黄紫版协作源码

这是可编辑的 React + Vite + TypeScript 独立产品工程。解压后可以在自己的电脑上修改、运行和测试。不是双击 HTML 即可完整运行的离线网页。

## 第一次运行（Windows）

1. 安装 Node.js 22 或 24；安装后重新打开终端。
2. 解压整个文件夹，不要直接在压缩包预览窗口里运行。
3. 双击根目录 `start-windows.cmd`。首次会下载依赖，需要联网。
4. 打开终端显示的 Local 地址，通常为 `http://127.0.0.1:5173/`。
5. 保持终端窗口打开。结束时按 `Ctrl+C`。

也可以手动运行（Windows / macOS / Linux）：

```bash
npm ci
npm run dev -- --host 127.0.0.1
```

修改 `src` 下的文件后，开发页面自动更新。端口被占用时 Vite 会选择下一个端口，以终端输出为准。

## 千问配置

普通记录、待办和笔记不需要模型密钥。真实的转写、摘要、问答和成长洞察需要自己配置百炼。

把 `.env.example` 复制成 `.env.local`，在本地编辑器中填写：

```text
DASHSCOPE_API_KEY=你的百炼密钥
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
QWEN_ASR_MODEL=qwen3-asr-flash
```

重新启动开发服务。地域、模型权限、余额及 Base URL 必须与自己的百炼账号匹配。音频转写和生成内容会发送到配置的阿里云服务并可能产生费用；密钥只由本地服务端读取。

`.env.local` 不在分享包中，也不应提交到 Git 或发到群里。包内没有任何真实密钥。

## 现在可以使用的功能

- 自适应电脑、平板和手机的黄紫界面；教学 Agent 仍为空白接入页。
- 麦克风录音、音频导入、文本/Markdown 导入和原文编辑。
- 对话详情、收藏、搜索、按日期回顾、导出与删除。
- 待办新增、编辑、截止时间、完成与恢复；日历 ICS 导出、系统邮件草稿入口。
- 个人事实编辑、确认、标签筛选；笔记保存、转为对话。
- 配置千问后，调用服务端转写、摘要/待办提取、带来源的记忆问答和成长洞察接口。

## 当前边界

- 数据保存在每个人自己的浏览器，不跨设备同步；清除网站数据会丢失本地记录，请从设置页导出备份。
- 录音仅在当前录入页面暂存，最长约 5 分钟、7 MB；可在离开前下载音频。不支持可穿戴设备或后台持续录音。
- 设截止时间不会自动产生网页关闭后的系统提醒。日历入口导出文件，邮件入口打开草稿，都不自动替你提交。
- 千问真实调用尚待用各自密钥与设备联调，当前接口测试使用模拟响应，不能视为真实生成效果的证明。
- 分享包不包含你的个人记录、浏览器数据、生产站点身份或已有部署凭证。

## 去哪里修改

| 需求 | 文件 |
| --- | --- |
| 黄紫颜色、间距、响应式布局 | `src/workspace.css` |
| 侧栏、顶部栏、手机导航 | `src/components/Workspace.tsx` |
| 首页、记忆、待办、事实 | `src/pages/WorkspacePages.tsx` |
| 录音、笔记、问答、设置、成长 | `src/pages/ToolsPages.tsx` |
| 教学 Agent 空白页 | `src/pages/TeachingAgentPage.tsx` |
| 应用路由 | `src/App.tsx` |
| 数据类型与本地数据操作 | `src/types/index.ts`、`src/context/AppState.tsx` |
| 千问服务端接口 | `server/api.mjs` |
| 本地接口中间件 | `server/vite-api.mjs` |
| 模型输入/输出和错误处理测试 | `tests/api.test.mjs` |

## 验证与构建

```bash
npm test
npm run build
npm run preview -- --host 127.0.0.1
```

`npm run build` 会输出前端资源及可部署的 Worker ESM。包里的 `.openai/hosting.json` 已清除原生产站点 ID；在自己的托管平台上部署前，需要配置属于自己的站点和运行环境。不要把其他人的站点 ID 填回去。

## 建议的协作方式

每个人先改自己负责的文件。发回修改时，说明改了哪些文件、如何验证；不要回传 `node_modules`、`.env.local` 或自己的浏览器数据。需要合并时，优先使用 Git 分支和提交，或只分享有修改的源码文件。

配色说明见 `PALETTE.md`。
