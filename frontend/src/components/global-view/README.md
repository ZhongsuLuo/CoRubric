# Global View 模块

该目录实现全局评分视图。它只展示后端已经物化的评分投影，不创建或修改评分。

## 文件职责

| 文件                       | 职责                                             |
| -------------------------- | ------------------------------------------------ |
| `GlobalView.vue`           | 页面容器；组合矩阵、摘要和报告明细               |
| `GlobalReadinessModal.vue` | Global View 准入阻断提示和 Report View 跳转      |
| `CohortOverview.vue`       | 可跳转的 report × criterion 热力图 overview      |
| `CohortSummary.vue`        | Overall/评分项模型综合摘要与受影响报告条形图     |
| `ReportOverview.vue`       | 报告集合排序、自动密度、状态映射和跨视图跳转     |
| `ReportTile.vue`           | 报告行的联动高亮、聚焦和密度容器                 |
| `OverallReportRow.vue`     | Overall 模式的总分、整份报告摘要与评分进度       |
| `CriterionReportRow.vue`   | 评分项模式的高亮证据与评分原因                   |
| `RubricHeatmap.vue`        | 批次评分矩阵和矩阵状态视觉映射                   |
| `useCohortSynthesis.ts`    | 按投影版本加载并缓存 Overall/评分项模型分析       |
| `useGlobalViewData.ts`     | Global View 的只读 view-model 与失败任务重试编排 |
| `presentation.ts`          | 仅属于本模块的格式化和视觉映射纯函数             |

跨模块复用的证据渲染仍位于
`../GlobalEvidenceRenderer.vue`；排序、自动缩放和 marker 位置等可测试纯函数位于
`../../utils/globalView.ts`。

## 数据流

```text
FastAPI alignment projection
  → api/client.ts
  → grading store
  → GlobalView container
  → model synthesis summary / report overview
  → report tile / overall row or criterion row
```

状态判断和总分计算以服务端 projection 为准。组件可以保存视图偏好（选择项、排序、
zoom、聚焦报告），但不得调用评分写接口。

Overall 报告摘要由服务端按 rubric 顺序聚合已确认评分项的 `score_reason`；冲突、无效和
未评分结果不进入摘要。

## 修改检查表

- 没有把评分或确认规则复制到展示组件；
- Overall 与 criterion 排序都将无分报告稳定放在末尾；
- conflict 可见但不进入分数和平均失分；
- partial、queued、running、failed、abstained 与普通未评分保持可区分；
- 热力图单元选择会聚焦对应报告行；分析条 hover/click 会联动矩阵与报告表格，click 仅固定高亮而不筛掉其他报告；
- 热力图列头使用 R1、R2 等紧凑编号，hover/focus 时展示完整评分标准与统计信息；
- 右侧摘要与矩阵等高，使用当前 projection 的平均分、失分、覆盖率、冲突和来源统计；
- Overall 与评分项均展示模型生成的综合摘要和 grounded pattern 条形图，请求失败时回退到投影统计；
- 未缓存的评分项分析加载时，右侧摘要整体切换为 loading；同一 projection 下已完成的分析直接复用页面缓存；
- 评分项报告行直接展示高亮证据与评分原因，不重复展开正文；
- overview 仅保留纵向滚动，报告名、评分项和总分列始终压缩在当前宽度内；
- 15/20/50 份报告的 Fit 和 1–3 级 zoom 均可连续纵向滚动；
- `globalViewPage.test.ts`、模拟前后端测试和真实浏览器矩阵全部通过。
