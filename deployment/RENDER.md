# 免费作品集部署：Render + Supabase

使用一个 Render **Free** Docker Web Service 与一个专用 Supabase **Free** 项目。Render 同时运行静态网页、一个 API 进程和数据库任务轮询进程，不创建付费 Worker、磁盘或 Redis。不会导入任何业务演示数据。

## Supabase 初始化

1. 新建免费项目，创建独立 `aftercare_app` 登录角色：`NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT`。密码随机生成，只放在后端私有配置中。
2. 使用迁移所有者连接运行现有 Alembic 迁移和 `deployment/postgres/grants.sql`，再执行 `deployment/postgres/supabase-hardening.sql`。不要让应用使用迁移所有者连接。
3. 关闭 Data API；应用经 FastAPI 访问数据库，不直接使用 Supabase REST/GraphQL。保留 Storage API。
4. 创建私有桶 `aftercare-files`，单文件上限 20 MB。生成服务端 S3 凭据，禁止写入前端或仓库。
5. 从 Connect 面板获取 **Session pooler / 5432** 的 IPv4 连接地址，用户名改为 `aftercare_app.<project-ref>`。连接参数使用 `sslmode=require`。

## Render Web Service

- 仓库：公开的 Aftercare 初始化仓库，分支 `main`。
- Runtime：Docker；Dockerfile：`deployment/Dockerfile.render`；context：`.`。
- Plan：**Free**；Region：Singapore；HTTP health check：`/health/ready`。
- 使用 Render 默认 HTTPS 地址，无需购买域名。`PUBLIC_ORIGIN` 缺省取 `RENDER_EXTERNAL_URL`。
- 不设置任何包含密钥的构建参数或 `VITE_*` 变量。

在平台私有环境变量中配置：

```dotenv
APP_ENV=production
DEMO_MODE=false
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://YOUR_SERVICE.onrender.com
DATABASE_URL=postgresql+psycopg://aftercare_app.PROJECT_REF:PRIVATE_PASSWORD@SESSION_POOLER_HOST:5432/postgres?sslmode=require
STORAGE_BACKEND=s3
S3_BUCKET=aftercare-files
S3_ENDPOINT=https://PROJECT_REF.storage.supabase.co/storage/v1/s3
S3_ADDRESSING_STYLE=path
AWS_DEFAULT_REGION=ap-southeast-1
AWS_ACCESS_KEY_ID=PRIVATE_S3_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=PRIVATE_S3_SECRET_KEY
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=FILL_CURRENT_SUPPORTED_MODEL
LLM_API_KEY=PRIVATE_DEEPSEEK_KEY
LLM_INPUT_USD_PER_MILLION=FILL_VERIFIED_PRICE
LLM_OUTPUT_USD_PER_MILLION=FILL_VERIFIED_PRICE
LLM_BUDGET_MICROUSD=250000
LLM_TENANT_DAILY_BUDGET_MICROUSD=1000000
LLM_TOTAL_BUDGET_MICROUSD=1000000
```

这些是占位符，不能直接用于部署。真实值仅注入托管平台。静态服务仅接收端口和本机 API 地址，不接收数据库、存储或 AI 凭据。API 只监听容器本机地址。

## 验收与额度

检查网页、`/health/ready`、`/health/workers`，注册后确认订单、工单、政策和知识库为空；验证模型可用及私有附件上传/下载。检查网页构建、API 响应、日志与 Git 历史中没有密钥。云端真实验证用完应清理验收内容，不预置业务案例。

可临时将 Docker Command 设为 `python deployment/check_storage.py`，验证私有附件上传、读取和删除；日志出现 `PRIVATE_STORAGE_ROUND_TRIP_PASSED` 后恢复空白命令，使用镜像默认启动。该检查需要上述完整生产环境变量，不创建订单或客户资料。

由公开 Git 仓库地址创建的服务需要在 Render 部署最新提交；连接 GitHub 仓库后可启用自动部署。先确认最新提交的 GitHub Actions 检查通过，再部署。

Render Free 空闲约 15 分钟后休眠，再次访问需要冷启动；同实例任务随休眠暂停，活跃演示时恢复处理。数据库和附件保留在 Supabase。免费额度、暂停及保留政策以平台当前条款为准。不绑付费资源，不开启超额付费；DeepSeek 调用仍使用供应商余额，部署总预算设置为 1 美元，到达预算后停止新增模型调用。
