# Railway 作品集试用部署

本方案保留完整工作台、PostgreSQL 租户隔离、私有附件与后端 AI 调用，初始业务数据为空。只使用账号现有试用额度，不升级付费计划。实际在线天数取决于额度消耗及平台试用限制；到期后应先导出需要保留的资料。

## 服务安排

全部从项目根目录构建，部署固定的、已检查的 Git commit。迁移完成前不启动 API 和 worker。

| 服务 | Dockerfile | 启动 | 网络 |
| --- | --- | --- | --- |
| postgres | `deployment/postgres/Dockerfile` | 镜像默认 | 仅内网 |
| migrate | `backend/Dockerfile` | `python -m app.release`，完成后退出，禁止自动重启 | 仅内网 |
| api | `backend/Dockerfile` | `uvicorn app.main:app --host :: --port 8000 --workers 1 --no-access-log` | 仅内网 |
| worker | `backend/Dockerfile` | `python -m app.worker` | 仅内网 |
| web | `frontend/Dockerfile.railway` | 镜像默认 | 平台 HTTPS 域名，容器端口 8080 |

worker 复用已有数据库任务轮询实现，不需要额外运行 Redis 和 Celery 调度器。postgres 挂载持久卷 `/var/lib/postgresql`，并配置 `POSTGRES_DB=aftercare`、`POSTGRES_USER=postgres`、随机 `POSTGRES_PASSWORD` 和独立随机 `APP_DB_PASSWORD`。初始化脚本创建不能绕过 RLS 的 `aftercare_app` 角色。

API 和 worker 的 `DATABASE_URL` 只使用应用角色，例如 `postgresql+psycopg://aftercare_app:${{postgres.APP_DB_PASSWORD}}@postgres.railway.internal:5432/aftercare`。只有 migrate 使用数据库所有者连接；不把该连接提供给 API、worker 或 web。迁移作业成功退出后，再启动应用。后续升级同样先完成迁移，再更新固定提交。

## 后端配置

api 和 worker 分别配置以下变量，实际凭据通过平台私有变量注入，不填写在本文或 Git 中：

```dotenv
APP_ENV=production
DEMO_MODE=false
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://${{web.RAILWAY_PUBLIC_DOMAIN}}
STORAGE_BACKEND=s3
S3_ADDRESSING_STYLE=virtual
REDIS_URL=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=<核实后的模型名称>
LLM_INPUT_USD_PER_MILLION=<核实后的输入价格>
LLM_OUTPUT_USD_PER_MILLION=<核实后的输出价格>
LLM_BUDGET_MICROUSD=250000
LLM_TENANT_DAILY_BUDGET_MICROUSD=1000000
LLM_TOTAL_BUDGET_MICROUSD=1000000
```

创建新加坡区域的私有 Railway Bucket，按平台返回的实际值配置 `S3_ENDPOINT`、`S3_BUCKET`、`AWS_DEFAULT_REGION`、`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`。该存储要求 virtual URL 样式；原有自托管 S3 环境仍保持 path 默认值。

仅 api 和 worker 配置 `LLM_API_KEY`，写入后在平台设为 Seal；不要配置到环境级共享变量或 web 服务。迁移服务不需要 AI 和存储凭据。平台封存值不会回显，但有权修改后端代码或进入容器的管理员仍须视为可信。

web 只需要 `PORT=8080` 和 `API_UPSTREAM=api.railway.internal:8000`。TLS 由 Railway 入口处理；Caddy 的内部 HTTP 端口不另行公开。整个前端构建不需要任何 AI、数据库或存储密钥。

上述模型预算是按配置价格估算的部署累计 1 美元上限，会在重启后保留；不代替供应商实际账单。对外分享前核实价格，并根据用途调整；不要将总预算改为 0（无限制）。

## 上线检查

1. 确认账号试用有效且能访问 DeepSeek HTTPS 接口，不进行付费升级。
2. 部署 postgres 与私有 bucket；完成一次 migrate 后保持该服务退出。
3. 注入并封存后端密钥，部署 api、worker、web；只为 web 生成域名。
4. 检查 `/health/ready`、`/health/workers`、首次注册和空白业务页面。
5. 检查浏览器请求与页面源码中没有密钥；进行一次有预算的真实 AI 调用，检查持久化结果和费用记录。
6. 检查附件私有上传和跨租户访问拒绝，再分享网址。

平台试用受资源、网络和数据保留限制。本文件是部署准备，不代表已通过云端验收；线上地址和验收结果以实际部署记录为准。

官方说明：[免费试用](https://docs.railway.com/pricing/free-trial)、[封存变量](https://docs.railway.com/variables#sealed-variables)、[私有存储](https://docs.railway.com/storage-buckets)。
