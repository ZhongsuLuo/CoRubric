# PDF 文件夹导入与评分

## 使用

1. 启动更新后的后端和前端，在顶部点击「上传文件夹」。
2. 选择文件夹，系统包含子文件夹中的 PDF，忽略其他文件。Case 名称默认取文件夹名，可修改；同名 case 不覆盖。
3. 默认「基础 OCR＋局部图像增强」，也可以切换原有解析器。点击「上传并开始解析」。
4. 上传完成后可以关闭弹窗。解析记录显示任务、逐文件阶段、失败原因及带时间的进度日志；刷新页面后仍可查看。
5. 任务结束后点击「选择此 case」。成功报告进入报告列表，Markdown 中保留图片和生成的图片描述。新 case 有独立的空白评分标准，可以继续创建标准、教师评分和自动评分。

上传不会自动开始评分。部分失败时仍可选择已成功的报告；「重试未完成文件」跳过已完成报告，并复用已有 OCR 和成功的图像增强缓存。解析尚有未完成区域的 PDF 不进入评分列表。

当前限制：单次最多 200 份 PDF，单份 100 MB，总计 500 MB；拒绝加密或损坏的 PDF。文件夹选择使用浏览器目录上传能力，已在 Chromium 验收。

## 运行配置

默认直接使用现有实验环境和配置，无须把 Key 填入前端：

| 项目 | 默认位置 / 行为 |
| --- | --- |
| 解析 Python | `.venv/bin/python` |
| 解析配置 | `backend/ocr/config.local.toml` |
| 覆盖 Python 路径 | 后端环境变量 `GRADING_PARSER_PYTHON`，使用绝对路径 |
| 覆盖配置路径 | 后端环境变量 `GRADING_PARSER_CONFIG`，使用绝对路径 |
| 基础 OCR | 配置中的阿里云文档智能凭证 |
| 局部图像增强 | 配置中的百炼凭证及已验证的 `qwen3.8-flash` |

后端使用自己的 Python 运行原有解析器。新解析器使用独立子进程，共享经过评估的通用版面处理和 Markdown 组装代码，不读取 benchmark 样本清单或审查标签。当前文件锁依赖 POSIX，适用于现有 macOS / Linux 环境。

增强调用每份文档并发为 2，默认每份文档增强预算上限为 9.5 元，计费未知的请求保留预算占位；这不是预估实际费用，也不包含基础 OCR 费用。可在解析配置中覆盖：

```toml
[runtime]
vision_budget_cny_per_document = 9.5
```

基础 OCR 提交结果未知且未取得任务编号时，不自动重新提交，避免重复计费；界面会报告失败，需要先核实云端任务。服务重启保留进度，将失去工作进程的任务标记为中断，之后手动重试。仍持有文件锁的工作进程不会被误判为中断，也不会重复启动。

## 文件位置

默认数据根目录下：

```text
backend/data/case/<case 名称>/
  case.json                          # case 与独立评分标准的关联
  source/<报告名称>.pdf
  markdown/<报告名称>/
    <报告名称>.md
    <报告名称>_text_only.md
    pages/p0001/images/...
    conversion.json                 # 增强调用数和费用估计

backend/data/api/case_imports/<任务编号>/
  job.json                          # 持久化状态与事件日志
  <文件编号>/worker.log              # 服务端诊断
  <文件编号>/progress.json
  <文件编号>/base_ocr/               # 基础 OCR 缓存
  <文件编号>/vision_cache/           # 增强响应和调用账本
```

评分标准保存在 `backend/data/criteria/`，文件名含 case 标识防止跨 case 覆盖；界面显示可读名称。相同文件名的 PDF 使用稳定后缀区分。已有 case 1 / case 2 的目录和评分标准配置不迁移。

## 验证

本次功能验收使用临时数据，模拟外部 OCR / 模型接口，没有新增付费 API 调用。验证的是应用集成和缓存、发布、恢复行为，不是重新测量 OCR 识别质量。

- 后端相关测试 15 项：上传校验、同名文件、case 隔离、评分标准绑定、教师评分保存、失败重试、重启恢复、锁保护、原子发布。
- 解析器测试 127 项：包括运行入口、OCR 缓存复用、未知提交阻止重发、增强重试账本。
- 前端测试 188 项；类型检查和生产构建通过。
- Chromium 实际浏览器：目录上传 → 逐文件解析 → 刷新恢复 → 选择 case → 图片加载 → 创建独立评分标准。

复现命令（项目根目录）：

```sh
.venv/bin/python -m pytest tests/test_case_imports.py tests/test_services.py -q
.venv/bin/python -m pytest tests/test_general_parser.py tests/test_general_runtime.py -q
npm --prefix frontend test
npm --prefix frontend run build
cd frontend
./node_modules/.bin/playwright test --config playwright.import.config.ts
```

浏览器测试启动隔离的本地前后端，不应与占用 8000 / 4173 端口的服务同时运行。

## 部署版说明

线上解析器已经迁入 `backend/ocr/`，由主项目锁文件安装依赖。旧实验完整归档在本地 `history/2026-10-09-deployment/archived/experiments/`，线上不依赖该目录。配置见 [DEPLOYMENT.md](DEPLOYMENT.md)。
