# 线上部署与配置

## 1. 环境与凭证

Linux 服务器安装 Docker Engine 和 Compose v2。代码使用锁文件安装；当前以单 worker / 单副本运行，进程内队列和 JSON 文件存储不能直接扩展为多副本。

```bash
cp backend/.env.example backend/.env
cp backend/ocr/config.example.toml backend/ocr/config.local.toml
chmod 600 backend/.env backend/ocr/config.local.toml
```

- `GRADING_AUTH_USER` / `GRADING_AUTH_PASSWORD`：网页 HTTP Basic 登录。密码至少 16 字符；生产入口缺失凭证会拒绝启动。
- `GRADING_LLM_PROVIDER` / `GRADING_LLM_MODEL`：评分模型；模板保留当前配置。仅填写所选供应商对应的 API key。部署前核对账户模型权限。
- `BAILIAN_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`：只在后端使用。`VITE_*` 会进入浏览器资源，禁止放密钥。
- 原生运行：OCR TOML 的 `[bailian]` 为图像增强密钥；`[docmind]` 为 RAM AccessKey ID/Secret，可使用 STS。
- 容器运行：在后端 `.env` 填 `GRADING_OCR_API_KEY`、`DOCMIND_ACCESS_KEY_ID`、`DOCMIND_ACCESS_KEY_SECRET`，STS 可加 `DOCMIND_SECURITY_TOKEN`。非空环境变量覆盖 OCR TOML 中对应凭证；不修改模板。容器使用公开模板，无需挂载私密 TOML。
- OCR 模板的费率是历史估算参数，部署前核对；预算是本地估算，不能替代供应商配额和账单限制。当前区域解析的模型/费率约束由 `Cloud` 校验。
- 本机原有 `backend/.env` 不会被覆盖；旧 OCR 配置已迁移到 `backend/ocr/config.local.toml`。

## 2. 启动

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
```

镜像只复制后端运行代码和前端构建，`.dockerignore` 使用允许列表；本地配置、Git、history 和真实数据不进入构建上下文。评分与 OCR 凭证均通过运行时 env 注入，不使用 Docker build ARG/ENV 存放凭证。数据保存在 `grading-data` 命名卷。

端口仅绑定服务器 `127.0.0.1:8000`。通过 Nginx/Caddy 提供 HTTPS，再向该端口转发。反向代理需保留 Host，支持至少 510 MB 的 multipart 请求，上传和推理超时建议 600 秒；启用网关请求限流。仅 `/api/health` 匿名可访问，其余页面、API、图片均要求认证。不要把开发入口 `backend.app.main:app` 直接暴露公网。

Nginx 已配置域名证书的 server 中使用：

```nginx
client_max_body_size 510m;
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}
```

生产不提供本地文件路径操作接口 `/convert`、`/grade`、`/criteria/update`，也不提供 debug/reset 和 API 文档。正常评分、标准编辑、上传、冲突确认和批次 API 保留。Basic 登录是共享工作区访问控制，不是每位教师的账号权限系统；有独立租户需求时需另行实现隔离。

## 3. 不使用容器

```bash
uv sync --frozen --no-dev
npm ci --prefix frontend
npm run build --prefix frontend
uv run --no-dev uvicorn backend.app.production:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

真实 `.env` 自动从 `backend/.env` 读取。同样需要 HTTPS 反向代理。可用 `GRADING_FRONTEND_DIR` 指向构建目录。数据目录变量见模板；相对路径相对启动工作目录，推荐绝对路径。

## 4. 数据迁移、备份与升级

新部署默认没有学生报告。通过网页上传，或停服后将本地 `backend/data/` 私下复制到持久卷，并将所有者设为 UID 10001。不要通过 Git 传学生报告或评分事件。已有 case 1 / case 2 的演示数据继续留在本机，容器不会自动导入。

升级前停服并完整备份数据卷；保留上一镜像和配置。备份包括 case、criteria、api、criteria_baselines、case_baselines，不能只备份报告 PDF。恢复也在停服状态执行；恢复数据与对应代码版本后再启动。不要执行 `docker compose down -v`，它会删除数据卷。

业务存储暂无自动 schema 降级迁移。回滚时使用同版本代码与备份，而不是让旧代码直接写新状态。

## 5. 发布边界

`history/`、`backend/data/`、`.env*`（示例除外）、`config.local.*`、证书私钥及验收截图均忽略；已跟踪数据已从索引移除，磁盘内容保留。

```bash
python3 scripts/release_check.py --history
python3 scripts/release_check.py --staged
git check-ignore backend/.env backend/ocr/config.local.toml history/README.md
```

索引检查需在选好提交文件后执行。旧提交仍可能保存历史数据，即使最新提交已删除；本次不重写 Git 历史、不执行 push。若目标是新公开仓库，使用发布源码包初始化干净仓库，避免旧学生资料随历史上传。

机制参考：[FastAPI 子应用](https://fastapi.tiangolo.com/advanced/sub-applications/)、[生命周期](https://fastapi.tiangolo.com/advanced/events/)、[Docker 构建上下文](https://docs.docker.com/build/concepts/context/)。
