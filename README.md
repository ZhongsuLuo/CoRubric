# Progressive Grading

实验报告辅助评分系统：文件夹上传、PDF/OCR、结构化评分标准、教师批改、模型预评分、冲突处理与班级总览。

## 当前维护目录

| 目录 | 用途 |
| --- | --- |
| `backend/app/` | FastAPI、评分事件和业务流程；`production.py` 为线上入口 |
| `backend/libs/` | 评分模型与备用 PDF 解析器 |
| `backend/ocr/` | 当前 OCR + 区域图像增强解析器；不依赖实验目录 |
| `frontend/src/` | Vue/Pinia 界面 |
| `tests/`、`frontend/e2e/` | 隔离数据的自动化验收；fixtures 只含评分规则 |
| `scripts/` | 发布检查、QA 和案例重放工具 |
| `docs/` | 部署、验收和操作说明 |
| `backend/data/` | **本地运行数据，Git 忽略** |
| `history/` | **历史代码、实验、原始快照和维护记录，仅本地保存** |

旧版源代码、未提交修改的整理前快照、实验与原有数据历史，见本地 `history/README.md`。不要将历史目录加入 Git 或镜像。

## 本地开发

要求 Python 3.11/3.12、uv、Node 20.19+ 或 22.12+（推荐 22）、npm；OCR worker 需要 Linux/macOS。

```bash
uv sync --frozen
cp backend/.env.example backend/.env
cp backend/ocr/config.example.toml backend/ocr/config.local.toml
npm ci --prefix frontend
```

在 `.env` 配置评分模型密钥；在 OCR TOML 配置百炼与 DocMind 凭证。两份真实配置均被 Git 忽略。无需模型密钥即可启动和运行离线验收；模型评分和云端 OCR 需要有效配置。

```bash
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
# 另一个终端
npm run dev --prefix frontend -- --host 127.0.0.1
```

访问 `http://127.0.0.1:5173`。前端默认使用 `/api`，Vite 转发至后端。全新环境没有案例，使用 Upload folder 导入；本机已有案例不会被覆盖。

## 线上部署

见 [部署与配置](docs/DEPLOYMENT.md)。默认方案为单进程容器，构建后的前端和 API 同源，HTTPS 由反向代理提供。不是多租户系统：登录用户共用同一评分工作区。

```bash
# 先在 backend/.env 配置登录信息、评分 key、GRADING_OCR_API_KEY 和 DOCMIND_*
# 密码至少 16 字符，使用随机值；不要填入示例文件。
docker compose up -d --build
```

## 验收

```bash
uv run pytest -q
npm test --prefix frontend
npm run test:integration --prefix frontend
npm run build --prefix frontend
npm exec --prefix frontend -- playwright install chromium
cd frontend
npm run test:e2e
npm run test:e2e:import
npm run test:e2e:production
```

浏览器测试顺序执行，不并发运行；它们使用 `/tmp/grading-conflict-update-e2e`，不会修改本地真实案例。测试替换云端推理以保证结果可复现。生产浏览器测试使用真实构建产物、真实 API/文件写入和生产访问控制。

仅本机双案例数据验收：`uv run pytest tests/test_demo_baselines.py -m local_data -q`。它不属于新环境/CI 必需数据。

## 发布前

```bash
python3 scripts/release_check.py --history
# 启用本仓库的提交/推送检查（每次新克隆需要设置）
git config core.hooksPath .githooks
# 生成不含 .git、历史资料、真实数据和本地配置的发布源码包
python3 scripts/release_check.py --export history/releases/grading-source.tar.gz
```

扫描检查当前本机已知凭证和常见密钥格式，不代表能识别所有供应商的任意秘密。若历史扫描失败，不要直接 push；先轮换相关密钥，再单独处理历史或使用不含 `.git` 的源码包建立干净仓库。

[验收记录](docs/ACCEPTANCE.md) · [文件夹导入](docs/FOLDER_IMPORT.md) · [操作指南](docs/USER_GUIDE.md)
