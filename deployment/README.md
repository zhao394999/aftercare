# 循单 Aftercare 部署手册

## 首发资源

采用单机容器首发，建议先按 4 核 / 8 GB 内存 / 80 GB 系统盘准备 Linux 主机；这是起步配置建议，不是已验证的容量承诺。还需要一个指向主机的域名、可出站访问模型服务的网络，以及私有 S3 兼容存储桶和仅限该桶的应用凭据。安全组只开放 80/443；运维入口按你自己的可信来源限制。数据库和 Redis 不映射公网端口。

模型服务采用兼容 Chat Completions 的 HTTPS 接口，需要支持 JSON object 输出，并返回 prompt_tokens / completion_tokens。通用兼容接口发送max_completion_tokens和store=false；DeepSeek官方接口发送max_tokens并关闭思考模式，不发送store参数。配置价格币种为 USD / 百万 Token，金额账本币种为 CNY；模型费用按Token与配置单价估算，供应商最终账单应另行核对。价格与模型名称由运营方核实填写，不能照抄示例。首版每次调查最多12次调用、默认预算1美元；预算是单次运行的消费边界，不是服务器月费。外部计费结果未知时保留费用占用。

## 首次发布

在项目根目录执行，先安装 Docker Engine 和 Compose 插件。

```sh
python3 scripts/create-deployment-env.py
```

该命令只生成 `deployment/.env.production`，权限0600，不打印随机密码、不覆盖已有文件。由维护人员在本机编辑域名、证书邮箱、私有桶、存储凭据和模型配置；不要把这个文件发给他人或提交仓库。

S3 存储必须关闭匿名访问，应用权限限制为指定桶应用前缀的 GetObject / PutObject / DeleteObject；删除权限用于清理上传后事务失败的孤立对象。启用服务端加密、版本留存与生命周期，由存储平台完成。附件下载经 API 再次鉴权后代理输出，存储无需公开域名。可使用现有云厂商对象存储；`S3_ENDPOINT` 留空使用 AWS 默认端点。

```sh
docker compose --env-file deployment/.env.production -f deployment/compose.yaml config -q
docker compose --env-file deployment/.env.production -f deployment/compose.yaml build
docker compose --env-file deployment/.env.production -f deployment/compose.yaml up -d
```

`postgres` 首次初始化应用角色；`migrate` 使用独立迁移连接创建表、RLS、账本约束并授予应用权限。API 和 Worker 使用非表所有者 `aftercare_app`，生产启动时检查角色不能绕过 RLS、12张租户业务表均强制 RLS。迁移连接不用于常驻 API / Worker。

等 API 健康检查通过后，由维护人员创建真实管理员：

```sh
docker compose --env-file deployment/.env.production -f deployment/compose.yaml exec api python -m app.cli bootstrap --tenant '你的商家名称' --email 'admin@example.com' --name '管理员姓名'
```

密码通过终端隐藏输入，至少12位。线上没有默认账户，也没有演示角色切换。登录后邀请客服、主管、执行人和审计员，将一次性链接通过可信渠道交给本人。主管可以提交提案，但资金提案禁止自提自批；执行人与资金核验人必须不同。

同一邮箱可以加入多个商家，邀请不能修改已有身份密码。登录 API 支持指定 tenant_id；首版登录页面默认进入最早加入的可用商家，网页中的跨商家切换器尚未提供。独立商家日常使用不依赖这个扩展功能。

## 上线验收

执行顺序是：CI 的真实 PostgreSQL 用例通过 → 容器构建 / Caddy 配置校验 → 新库迁移与角色检查 → 真实账号邀请 / 会话退出 → 文件私有读写 → 合成订单业务闭环 → 真模型调查 / 缺证暂停 / 恢复 / 人工复核 → 备份恢复演练 → 小流量试运行。任一必要项失败时保留原版本，修复后重新验收。

发布前把基础镜像标签解析并固定为当次验证的 digest，扫描 Python / npm 依赖及镜像，并保留依赖锁文件、镜像版本、迁移版本、配置变更记录。仓库提供的主版本镜像标签是待验证的构建输入，不能直接当作经过审计的发布镜像。CI 会在 GitHub 推送后运行；以仓库 Actions 的实际结果为准。

容量验证至少覆盖预期峰值的工单读写、同订单竞争审批、重复任务投递、Worker 终止 / 恢复，以及模型429/超时。压测应使用隔离环境的合成资料，并记录 API P95、错误率、队列等待时间、DB连接数与资源使用。本机已完成10会话600次读取及10路写竞争测量，目标生产容量仍需测试，不承诺固定并发数或可用性百分比。

## 观测与维护

业务链路保存在运行事件、调用计费与审计表中，可在网页查看。请求日志为 JSON，带 request_id、路由、状态、耗时与租户 ID，不记录请求正文、密码或密钥。审计记录关联请求 ID。配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 后导出 API / Worker 的 trace、任务步骤和请求次数 / 耗时指标；端点填基础地址，代码自动追加 `/v1/traces` 与 `/v1/metrics`，需同时支持两者。生产接收端由你的观测平台提供，并配置访问控制与留存策略。应用连接使用5秒连接 / 锁等待与15秒语句超时，避免资源无限等待；应结合实际数据规模调整并验证。

首发建议设置告警：API 5xx 持续升高、健康检查失败、队列长期无进展、Worker 离线、数据库磁盘 / 连接逼近限制、模型费用超预算、外部结果未知动作积压。当前代码有事件与指标导出，但没有替你建立云端告警规则，通知渠道需在实际观测平台配置。

应用升级前执行新增迁移 `20261018_worker_health`。`/health/workers` 在90秒内无后台心跳时返回503，区别于API数据库就绪检查；OTel提供 `aftercare.jobs`（含失效租约）、`aftercare.worker.alive` 与 `aftercare.jobs.oldest_pending_age`。授权审计角色可读取 `/api/v1/operations` 查看本租户队列及资金积压。默认最近24小时商家模型额度10美元，可通过 `LLM_TENANT_DAILY_BUDGET_MICROUSD` 调整，并与单次预算同时执行。

PDF解析采用独立子进程：10秒总时限、5秒CPU上限，Linux子进程限制768MiB地址空间，每个API进程最多1个解析任务。本轮已在Linux容器中验证解析限制；目标生产镜像仍需按发布流程复核，macOS宿主仅支持进程隔离、CPU及时间限制。生产Celery任务软/硬限制分别为600/620秒，崩溃重领最多3次。

```sh
docker compose --env-file deployment/.env.production -f deployment/compose.yaml ps
docker compose --env-file deployment/.env.production -f deployment/compose.yaml logs --tail 100 api worker scheduler
sh scripts/backup-db.sh
```

数据库备份由 pg_dump 从维护角色导出，避免 RLS 导出缺行。备份文件保存在 `runtime/backups`，权限由脚本限制；必须同时备份匹配的对象存储版本，并复制到独立位置。建议在试运行时设定 RPO / RTO，再通过演练确认，而不是预先宣称已达到某个值。

恢复必须在隔离的新数据库执行 `pg_restore`，核对记录数、金额账本、文件哈希、租户边界和代表性工单。恢复后运行迁移并重授应用角色权限，再切换应用连接。不要直接覆盖正在服务的数据库。

应用回滚到已保留的上一个镜像版本；数据库仅采用向后兼容的增量迁移。涉及不兼容变更时，先停写并保存完整快照，按发布变更单恢复。不要在资金业务运行中直接执行降级迁移。

遗忘密码由具备服务器维护权限的人员核实身份后执行，操作会撤销该账户全部会话并写入审计：

```sh
docker compose --env-file deployment/.env.production -f deployment/compose.yaml exec api python -m app.cli reset-password --email 'user@example.com' --reason '说明已核实身份及恢复原因'
```

## 当前边界

当前版本是独立商家的人工执行协作工作台。订单导入与业务材料由商家提供；电商平台尚未接入，系统不会自动操作电商平台或发起退款。配置快递100服务后，可查询订单物流，并在创建工单后的智能调查中按需查询；本机已验证真实接口返回暂无数据，有效运单轨迹仍待验收。结果来自授权人员登记与交叉核验，不能对外声称已经形成支付渠道级自动对账。多机高可用、SSO、扫描文件 OCR、病毒扫描、数据保留清理作业与语义检索均未作为已交付能力。

配置依据：[Caddy 请求体限制](https://caddyserver.com/docs/caddyfile/directives/request_body)、[PostgreSQL RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)、[Python 官方镜像](https://hub.docker.com/_/python)。
