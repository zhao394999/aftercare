# 循单 Aftercare

电商售后异常订单协作工作台。通过订单、证据、政策、智能调查、人工审批、外部执行登记与结果核验，组织物流异常和确认丢件后的退款协作。

这是当前源码的初始化版本。仓库不包含演示订单、模拟客户资料、预置业务数据、运行数据库、附件、备份、历史验收包或 API Key。首次启动为空库，需要自行注册商家空间并录入真实业务资料。单元测试使用隔离的临时用例，不会向应用业务库写入数据。

## 本地启动

需要 Python 3.14 和 Node.js 26 / npm。首次安装依赖需要联网。

```sh
git clone https://github.com/zhao394999/aftercare.git
cd aftercare
cp .env.example .env
sh scripts/dev.sh
```

打开 <http://127.0.0.1:5173>，点击“还没有账户？注册商家空间”。注册后订单、工单、政策与知识库均为空；没有默认账号。启动脚本仅迁移数据库，不生成或导入演示数据。按 Ctrl+C 停止服务。

默认使用本地 SQLite `runtime/aftercare.db` 和本地附件目录 `runtime/files/`。这两个目录不会提交到 Git。开发 API 文档位于 <http://127.0.0.1:8000/api/docs>。

## 模型与外部服务配置

只在本机 `.env` 或服务端私有环境文件填写模型、物流与飞书配置，变量名称见 `.env.example`。API Key 留空时，人工业务流程仍可使用，智能调查与知识问答不可用。启用模型需填写兼容接口地址、模型名称、密钥与经过确认的输入/输出计费单价。

模型和物流密钥仅由后端读取。可使用 `LLM_API_KEY_FILE`、`KUAIDI100_KEY_FILE` 指向仓库外的私有文件，或由部署平台注入 Secret。不要使用 `VITE_*` 保存密钥，前端环境变量会进入浏览器构建产物。不要将真实密钥写入源码、README、截图、日志或 GitHub Issue。

`.gitignore` 已排除环境文件、密钥文件、运行数据、备份与模拟数据目录；只保留没有凭据的 `.env.example`。提交前仍需检查 `git diff --cached`。如果未来误提交密钥，应立即在供应商后台撤销并重新生成。

## 核心流程

1. 注册商家，邀请客服与主管；单笔录入或批量导入订单。
2. 为异常订单创建售后工单，补充材料并核验事实。
3. 人工或 Agent 关联证据与政策，形成处置提案；由其他授权人员审批。
4. 在商家既有外部系统操作后登记流水、金额与凭证。
5. 由不同人员核验执行结果，登记客户沟通并关闭工单。

首版执行协作围绕物流异常和确认丢件后的退款展开。系统不会自动操作电商平台、发送客户消息或发起真实退款。尚未完成真实商家试运行与公网生产验收。

## 工程结构

- `frontend/`：React / TypeScript 工作台。
- `backend/`：FastAPI、SQLAlchemy、Alembic、LangGraph、任务队列与私有文件服务。
- `deployment/`：生产 Compose、Caddy HTTPS、数据库角色与部署手册。
- `scripts/`：空库开发启动、私有部署配置生成与数据库备份。
- `.github/workflows/ci.yml`：后端、前端、PostgreSQL 集成与容器检查。

## 验证

```sh
.venv/bin/pip install -r backend/requirements-dev.in
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
```

测试中的账户、订单和模型响应仅用于隔离验证，不是预置业务数据，也不代表真实客户效果。PostgreSQL / S3 集成测试需要单独配置对应的隔离服务。

正式部署请阅读 [部署手册](deployment/README.md)。

作品集云端试用准备见 [Railway 部署说明](deployment/RAILWAY.md)，使用后端私有密钥与空白业务库；实际部署状态以平台与上线验收记录为准。
