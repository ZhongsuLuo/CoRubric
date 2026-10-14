# 当前 PDF 解析器

维护入口：`python -m backend.ocr.general_parser.convert`，参数 `--pdf --output --progress --config`，可选 `--retry`。由 `backend.app.case_imports` 启动隔离子进程。

- `general_parser/core.py`：原生文字、表格、页面区域判定。
- `general_parser/cloud.py`：区域转写、缓存、持久化预算及未知计费处理。
- `general_parser/response.py` / `rendering.py`：响应校验与保守组装。
- `providers.py`：DocMind 文档解析和百炼图像请求。
- `hybrid_layout.py` / `source_polish/evidence.py`：当前解析路径使用的坐标和文字证据工具。
- `common.py`：文件摘要、原子 JSON 写入和图片引用匹配。

配置：复制 `config.example.toml` 为 `config.local.toml` 后填凭证；该真实文件不提交。容器使用公开模板，凭证来自后端 `GRADING_OCR_API_KEY` / `DOCMIND_*` 环境变量，见 `configuration.py`。主项目 `uv sync --frozen` 安装所有运行依赖，不再需要实验目录的虚拟环境。

迁移来源与被移除的 benchmark/pilot/旧算法完整保存在本地 `history/2026-10-09-deployment/archived/experiments/pdf_ocr_benchmark/`。当前解析算法及未知请求不自动重提、部分输出不发布等约束保持不变。测试：`uv run pytest tests/test_general_parser.py tests/test_general_runtime.py tests/test_case_imports.py -q`。
