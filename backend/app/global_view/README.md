# Global View 后端模块

该目录负责把当前已经物化的评分投影转换为只读的 cohort 视图数据。它不创建评分、
不确认评分，也不修改评分标准。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `aggregation.py` | 按报告汇总总分、完成状态与评分来源 |
| `projection.py` | 按评分项汇总评分点、证据、平均失分与 attribution 覆盖 |
| `readiness.py` | 计算进入 Global View 的工作流门禁和投影完整性提示 |
| `synthesis.py` | 基于当前权威投影生成 Overall/评分项批次分析 |
| `__init__.py` | 暴露稳定的模块入口 |

评分事件的读取、物化和兼容 API 仍由 `../alignment.py` 负责。评分写入前的评分点分配
与证据锚点校验集中在 `../grade_validation.py`，避免 Global View 和写入流程各自复制
一套规则。

## 边界约束

- 所有聚合都基于当前评分标准版本的 current projection；
- conflict 可以展示，但不能进入总分或平均失分；
- 教师评分与模型评分一起参与汇总，同时保留来源字段；
- 未完成报告可以展示，且必须保留 queued、running、failed、abstained 等状态；
- Global View 只读；教师确认、标准更新和评分编辑仍在 Report View 完成；
- synthesis 只为右侧分析生成文本与受影响报告模式，不影响评分投影或写入任何状态；
- `projection.py` 和 `readiness.py` 接收已加载数据并返回普通字典，便于独立测试。
