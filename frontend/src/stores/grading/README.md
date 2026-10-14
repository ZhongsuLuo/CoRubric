# Grading Store 辅助模块

`../grading.ts` 是 Report View 与 Global View 的共享 Pinia store，负责 API 调度、队列、
异步状态和跨视图选择。该目录只放 store 使用的可复用辅助逻辑。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `normalizers.ts` | 将后端 alignment 响应一次性归一化为稳定的前端状态 |

## 边界约束

- normalizer 必须是纯函数，不能访问 Pinia ref、浏览器状态或 API；
- API 请求、自动评分队列和状态写入继续留在 `grading.ts`；
- 兼容旧响应字段的逻辑只允许在 normalizer 层出现一次；
- 新字段优先更新共享 schema，再更新 normalizer，不在组件内补默认业务状态；
- provenance、conflict 和评分有效性以服务端响应为准，组件不得重复推导。
