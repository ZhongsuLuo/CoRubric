# Progressive Grading 前端

Vue 3 + TypeScript + Vite + Pinia 前端。应用提供页面级
`Report View / Global View` 切换；两种视图共享 case、rubric 和评分状态，但
Global View 保持只读。

## 主要目录

```text
src/
├── App.vue                         # 页面壳、case selector、页面级视图切换
├── api/client.ts                   # 后端 API 封装
├── stores/grading.ts               # 跨视图状态、加载和写入动作
├── stores/grading/
│   └── normalizers.ts              # alignment 响应的纯归一化函数
├── components/
│   ├── ReportView.vue              # 单报告评分与证据编辑
│   ├── CriteriaView.vue            # rubric/评分项浏览
│   ├── GlobalEvidenceRenderer.vue  # 两种视图复用的只读证据渲染
│   └── global-view/                # Global View 功能模块
├── utils/                          # 排序、布局、evidence 和 Markdown 纯函数
└── integration/                    # 模拟前后端业务流测试
```

Global View 的内部职责见
[`src/components/global-view/README.md`](src/components/global-view/README.md)。

## 开发约束

- 业务写操作只能经 `stores/grading.ts` 的显式 action 发出，不在展示组件内直接写 API。
- Global View 只读取确定性 projection；需要编辑时跳回 Report View。
- 新增复杂功能时优先建立语义明确的子目录，页面容器负责组合，子组件负责单一区域。
- 通用纯函数放入 `utils/`；仅 Global View 使用的展示函数放入其功能目录。
- 后端响应的兼容和默认值只在 `stores/grading/normalizers.ts` 处理，不散落在 action
  或组件中。
- 不复制评分、确认、冲突或 provenance 判断；后端返回权威状态，前端只负责呈现。
- 保留稳定的 `data-testid`，供组件、模拟联调和浏览器测试复用。

## 本地开发

```bash
npm ci
npm run dev
```

默认 API base 为 `/api`。需要连接其他后端时，在 `.env.local` 设置：

```dotenv
VITE_API_BASE=http://localhost:8000
```

## 检查与测试

```bash
npx eslint . --no-cache
npm run type-check
npm test
npm run build
```

Global View 的快速组件回归：

```bash
npx vitest run src/components/globalViewPage.test.ts --environment jsdom
```

模拟前后端业务测试由仓库根目录统一启动：

```bash
uv run python -m scripts.run_global_view_simulation
```

真实浏览器验收必须在上述检查全部通过后进行，详细矩阵和验收标准见
本地 `history/2026-10-09-deployment/archived/plans/GLOBAL_GRADING_VIEW_IMPROVEMENT_PLAN.md`。
