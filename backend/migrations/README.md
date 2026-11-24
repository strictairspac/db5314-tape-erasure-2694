# 结构演进

`src/models/tables.py` 定义关系结构，API 启动时显式创建演示库的表。

## 结档字段

- `business_cases.closed_at` / `closed_by`：结档时间与经办，`closed_at` 为空表示在档。
- `business_runs.payload_snapshot`：任务受理时固定的原资料 JSON 副本；任务结束或结档清理后置空。

演示库可直接重建。在已有部署上升格表结构时，应单独引入版本化迁移（例如为 `business_cases` 增补两列、为 `business_runs` 增补可空 JSON 列并回填在队任务快照）。
