# ERP 登录接入方案

参考 `all-in-flow`（`apps/server/src/auth/*`、`apps/web/src/pages/Login.tsx`）的实现，为本系统接入 ERP 账号登录。

## 1. 已确认的决策

| 事项 | 结论 |
|---|---|
| 数据范围 | 上传的业务数据（订单、查询量、数据质量）所有登录用户共享同一份；只记录上传人 |
| 问答记录 | 每人只能看到自己的问答记录 |
| 管理员 | 暂不需要，不做 `ADMIN_USERNAMES` 白名单 |
| `X-TM-App-Code` | 暂不需要，请求 ERP 时不带此请求头 |
| Cookie Secure | 当前为 HTTP 直连 9092 端口，`SESSION_COOKIE_SECURE` 默认 `false`；上 HTTPS 后改为 `true` |

## 2. 登录流程

1. 前端提交 ERP 账号和密码到 `POST /api/user/login`。
2. 后端把账号密码转发给 ERP 的 `POST {ERP_BASE_URL}/api/login`，从响应的 `Set-Cookie` 中取出 `tm_sso_sid`。**本系统不保存密码。**
3. 后端用 `tm_sso_sid` 调用 `GET {ERP_BASE_URL}/user/api/get`，获取 `UserId`、`UserName`、`FullName`、`ExpireDuration`。
4. 以 ERP 的 `UserId` 作为主键写入或更新 `users` 表。
5. 生成随机会话 ID（32 字节，base64url），写入 `auth_sessions` 表，并保存 `erp_sid`（只存服务端，不返回给浏览器）。
6. 通过 httpOnly Cookie `fta_sid` 下发会话 ID，`SameSite=Lax`，`Path=/`。有效期取 ERP 剩余时长与 24 小时中的较小值。

ERP 返回的 `ExpireDuration` 可能是纳秒也可能是秒：大于 10^9 时按纳秒换算；换算后不大于 0 时按 86400 秒处理（与 all-in-flow 一致）。

## 3. 会话校验

每个受保护的请求：

1. 读取 Cookie `fta_sid`。没有该 Cookie，或本地会话不存在、已过期，返回 401。
2. 距上次核对（`verified_at`）超过 `SESSION_VERIFY_INTERVAL_SECONDS`（默认 300 秒）时，用 `erp_sid` 再调一次 ERP 的 `/user/api/get`：
   - ERP 返回未登录（401/302/307、`Success=false`），或返回的用户与会话中的用户不一致：删除本地会话，返回 401。
   - ERP 网络错误或返回其他异常状态码：**不删除会话**，返回 502。
   - 核对通过：更新 `verified_at`。

401 响应体为 `{"detail": "身份信息验证失败，请重新登录", "code": 40001}`，前端据此跳转登录页。

## 4. 登录限流

`POST /api/user/login` 按 IP 和用户名分别做固定窗口限流，超出返回 429。计数放在进程内存中（后端只有一个容器，够用）。IP 取 `X-Forwarded-For` 的第一个值，nginx 已透传该请求头。

## 5. 后端改动

| 文件 | 内容 |
|---|---|
| `app/core/config.py` | 新增 `erp_base_url`（默认 `https://erp.tourmind.cn`）、`session_cookie_secure`（默认 `false`）、`session_verify_interval_seconds`（默认 300） |
| `app/auth/erp.py` | ERP 客户端：`authenticate(username, password)`、`verify(erp_sid)`；用 httpx 同步调用，超时 5 秒，不跟随跳转；定义 `ErpAuthError`（账号或会话无效）和 `ErpUpstreamError`（ERP 不可用） |
| `app/auth/service.py` | `login`、`verify`、`destroy`、`purge_expired` |
| `app/auth/limiter.py` | 固定窗口限流 |
| `app/models/auth.py` | `User`、`AuthSession` 模型 |
| `alembic/versions/xxx_auth.py` | 建 `users`、`auth_sessions` 表 |
| `app/api/deps.py` | `current_user` 依赖：校验会话，返回当前用户 |
| `app/api/auth.py` | `POST /api/user/login`、`GET /api/user/info`、`POST /api/user/logout` |
| `app/api/uploads.py` | 上传时写入 `upload_batch.user_id`；列表接口仍返回全部批次（数据共享） |
| `app/api/chat.py` | 保存问答时写入 `chat_log.user_id`；新增 `GET /api/chat/logs`，只返回当前用户自己的记录 |
| `app/main.py` | 注册认证路由；`uploads`、`chat` 路由加 `Depends(current_user)`；`/api/health`、`/api/ready` 保持公开 |
| `pyproject.toml` | `httpx` 从 dev 依赖移到正式依赖 |

### 数据表

```text
users
  id             integer  主键，直接使用 ERP UserId
  username       varchar  唯一
  display_name   varchar
  created_at     timestamp
  last_login_at  timestamp

auth_sessions
  sid            varchar  主键，即 Cookie fta_sid 的值
  user_id        integer  外键 → users.id，建索引
  erp_sid        varchar  ERP 的 tm_sso_sid，仅服务端使用
  expires_at     timestamptz
  verified_at    timestamptz
```

`chat_log.user_id`（Integer）和 `upload_batch.user_id`（BigInteger）已有预留字段，直接使用，不改表结构；为 `chat_log.user_id` 新增索引，便于按用户查询。存量数据的 `user_id` 为空：不属于任何用户，所以任何人的问答记录里都不会出现。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/user/login` | 请求体 `{"username", "password"}`；成功时设置 Cookie，返回 `{"username", "display_name"}`；账号或密码错误返回 401，ERP 不可用返回 502，过于频繁返回 429 |
| GET | `/api/user/info` | 返回当前用户 `{"id", "username", "display_name"}` |
| POST | `/api/user/logout` | 删除会话并清除 Cookie |
| GET | `/api/chat/logs` | 当前用户自己的问答记录，按时间倒序，支持 `limit` 参数 |

## 6. 前端改动

| 文件 | 内容 |
|---|---|
| `src/api/client.ts` | axios 响应拦截器：收到 401 跳转 `/login?redirect=<当前路径>`；`/user/*` 接口除外，避免登录页循环跳转 |
| `src/api/chat.ts` | SSE（fetch）收到 401 同样跳转登录页 |
| `src/api/auth.ts` | `login`、`logout`、`getMe` |
| `src/pages/Login/index.tsx` | shadcn 风格表单（Tailwind），沿用现有配色；进入页面时先调 `/user/info`，已登录则直接跳到 `redirect` |
| `src/App.tsx` | 增加 `/login` 路由；其余页面加登录守卫；顶栏右侧显示用户名和退出按钮 |

问答记录的查看页面本期不做，先提供后端接口 `GET /api/chat/logs`；需要时再加前端页面。

## 7. 配置与部署

`.env.example` 新增：

```env
# ERP 登录
ERP_BASE_URL=https://erp.tourmind.cn
SESSION_COOKIE_SECURE=false
SESSION_VERIFY_INTERVAL_SECONDS=300
```

`deploy.sh` 以 `--env-file .env` 启动后端，nginx 已透传 `X-Forwarded-For`，两者都无需修改。部署后执行迁移（`RUN_MIGRATIONS_ON_START=true` 时自动执行）。

## 8. 本地开发

一键启动：

```bash
scripts/dev.sh          # 启动容器（数据库、后端热重载），前端在前台运行
scripts/dev.sh build    # 修改 backend/pyproject.toml 后重新构建后端镜像
scripts/dev.sh down     # 停止开发容器（数据库数据保留）
```

- 后端：http://127.0.0.1:8000（容器内 `uvicorn --reload`，改代码即时生效，无需重新构建）
- 前端：http://127.0.0.1:5173（本机 `npm run dev`，`/api` 代理到 8000）
- ERP：使用 `.env` 中的 `ERP_BASE_URL`，开发环境同样连接真实 ERP，账号使用你自己的 ERP 账号

开发模式的差异写在 `docker-compose.dev.yml` 中：后端挂载源码，前端不进容器。部署仍使用 `docker-compose.yml`，不受影响。

## 9. 测试

后端测试位于 `backend/tests/test_erp_auth.py`（ERP 客户端、限流，无需数据库）和 `backend/tests/test_auth_db.py`（会话与接口，需要 `TEST_DATABASE_URL` 指向 PostgreSQL，未设置则跳过）。

用假的 ERP 客户端覆盖以下场景：

- 登录成功：写入 `users` 和 `auth_sessions`，下发 Cookie
- 账号或密码错误返回 401，ERP 不可用返回 502，触发限流返回 429
- 未登录访问 `/api/uploads`、`/api/chat/stream` 返回 401；`/api/health` 不受影响
- 复核时 ERP 判定会话失效：删除本地会话并返回 401；ERP 故障返回 502 且会话保留
- `ExpireDuration` 纳秒和秒两种格式的换算
- `GET /api/chat/logs` 只返回当前用户的记录
- 退出登录后会话被删除
