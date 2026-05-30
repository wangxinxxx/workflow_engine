# AGENTS.md

本仓库按工作台项目规范维护。

## 技术栈

- 前端：React、TypeScript、Vite。
- 后端：Python、FastAPI、Pydantic。
- 工作流：LangGraph。

## 目录规范

- FastAPI 路由放在 `backend/app/api/`。
- Workflow 主流程、状态、运行时、命令和 checkpoint 放在 `backend/app/workflow/`。
- 共享 Pydantic Schema 放在 `backend/app/schemas/`。
- API 服务适配和序列化放在 `backend/app/services/`。
- Agent 公共能力放在 `backend/app/agents/`。
- Tool 封装放在 `backend/app/tools/`。
- 外部平台 Client 放在 `backend/app/integrations/`。
- 前端页面放在 `frontend/src/pages/`，通用组件放在 `frontend/src/components/`，请求封装放在 `frontend/src/api/`，类型放在 `frontend/src/types/`。

## 工作流约束

- API 只承接前端请求和用户动作，不直接实现方案设计、SQL 生成、测试诊断等流程逻辑。
- 复杂流程判断放在 `backend/app/workflow/`。
- Agent 输入输出必须结构化，不直接返回散乱自然语言给 workflow。
- 高风险动作默认只生成草稿或计划，必须经过人工确认后执行。

## 产物文档规范

需求目录必须使用统一文档名：

- `docs/01 需求输入文档.md`
- `docs/02 需求解析确认文档.md`
- `docs/02-1 待确认项文档.md`
- `docs/03 开发方案文档.md`
- `docs/04 SQL代码与评审文档.md`
- `docs/05 测试项设计文档.md`
- `docs/06 数据测试报告文档.md`
- `docs/07 交付报告文档.md`
- `docs/08 上线确认记录文档.md`

回流、重跑、人工修正必须生成新版本；历史稳定版本放入需求目录下的 `history/`。

## 开发要求

- 只修改本次任务相关文件，不做无关重构。
- 不提交 `node_modules`、`dist`、`.runtime`、`.venv`、密钥和个人配置。
- 后端改动至少运行相关测试；前端改动至少运行构建或页面验证。
