# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 跨日去重设备数

日级立方只保存"当日去重设备数"，直接把多天计数相加会把跨日访问的同一设备
重复计入。为此，日粒度立方额外保留一份**带来源粒度的可比聚合**：

- 立方的预聚合里保存当日去重后的设备标识（`backend/app/analytics_engine.py`
  的 `build_comparable_day_aggregates`），日计数始终由完整事实分区得到；
- 设备标识产物受立方容量上限约束，超出即截断；截断分区的单日计数仍然精确，
  但不能再参与跨日精确并集；
- 范围查询对区间内各日分区的标识做并集去重，并逐个列出各日贡献
  （日值、独有设备、与其他日共用设备）；
- 指标定义与立方共用日粒度标识（`resolve_grain_id`）。指标查询接口
  `POST /api/metric_def/query` 单日返回日值，日期范围返回跨日去重后的总值；
  只有全部日分区可用且产物未截断时才发布精确值，否则 `value` 为空并给出
  缺失分区/截断分区的诊断。日值相加的结果永远标记为不可累加、不发布。

相关接口：

| 接口 | 用途 |
| --- | --- |
| `POST /api/metric_def/query` | 单日返回日值，范围返回去重总值 |
| `POST /api/cube_builder/{id}/comparable-aggregates` | 重算可比聚合并记录可用事实覆盖 |
| `GET /api/cube_builder/{id}/comparable-partitions` | 查看各日分区产物与截断情况 |
| `GET /api/cube_builder/{id}/device-range` | 跨日并集/拒绝发布的诊断与各日贡献 |
| `DELETE /api/cube_builder/{id}/partitions/{date}` | 移除日事实分区并核对剩余来源 |
| `GET /api/analytics/device-dedup/demo` | 先看两天日值，再看含重访设备的合计 |

`多维立方`页面上有对应的"跨日去重设备数"面板，可重算样本、查看范围核验和
移除某个日分区后核对覆盖。测试：`cd backend && .venv/bin/python -m unittest discover -s tests`。

领域：数据工程
子方向：数据分析与数据应用
