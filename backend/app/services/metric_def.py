"""指标定义领域服务：指标聚合计算、口径统一、跨模块指标消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.analytics_engine import (
    AGGREGATORS,
    DAY_GRAIN,
    build_fact_coverage,
    compute_metric,
    enumerate_dates,
    resolve_grain_id,
)


class MetricDefService(BaseModuleService):
    MODULE = "metric_def"
    ENTITY = "指标"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标编码", "指标名称", "业务域", "计算口径", "聚合函数", "数据粒度", "是否可累加"]
    REQUIRED_FIELDS = ["指标编码", "指标名称", "业务域"]
    STATUS_ORDER = ["草稿", "已审核", "已发布", "已停用"]
    ACTION_RULES = {"提交审核": "已审核", "发布指标": "已发布", "停用指标": "已停用"}
    NEGATIVE_ACTIONS = ["停用指标"]

    # ---- 业务方法 ----

    def calc_metric_value(self, metric_code: str, raw_values: list[float | int]) -> dict[str, Any]:
        """根据指标编码查找口径，执行聚合计算。"""
        rows = store.rows(self.MODULE)
        metric_def = next(
            (row for row in rows if row.get("指标编码") == metric_code and row.get("status") == "已发布"),
            None,
        )
        if metric_def is None:
            return {"ok": False, "message": f"指标 {metric_code} 未找到或未发布"}
        aggregator = str(metric_def.get("聚合函数", "SUM"))
        if aggregator not in AGGREGATORS:
            return {"ok": False, "message": f"不支持的聚合函数：{aggregator}"}
        result = compute_metric(raw_values, aggregator)
        return {
            "ok": True,
            "metric_code": metric_code,
            "metric_name": str(metric_def.get("指标名称", "")),
            "aggregator": aggregator,
            "result": result,
            "value_count": len(raw_values),
        }

    def get_published_metrics_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """获取某业务域下所有已发布指标（用于立方构建消费）。"""
        rows = store.rows(self.MODULE)
        return [
            row for row in rows
            if row.get("业务域") == domain and row.get("status") == "已发布"
        ]

    def resolve_metric_caliber(self, metric_code: str) -> dict[str, Any] | None:
        """解析指标口径（用于语义层映射和可比聚合身份判定）。"""
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("指标编码") == metric_code:
                return {
                    "code": row.get("指标编码"),
                    "name": row.get("指标名称"),
                    "domain": row.get("业务域"),
                    "caliber": row.get("计算口径"),
                    "aggregator": row.get("聚合函数"),
                    "granularity": row.get("数据粒度"),
                    "grain_id": resolve_grain_id(row.get("数据粒度")),
                    "additive": row.get("是否可累加"),
                }
        return None

    def get_distinct_metric(self, metric_code: str) -> dict[str, Any] | None:
        """返回可参与跨日去重的指标身份；跨日可比聚合与指标定义共用日粒度标识。"""
        caliber = self.resolve_metric_caliber(metric_code)
        if caliber is None:
            return None
        if str(caliber.get("aggregator") or "").upper() != "DISTINCT_COUNT":
            return None
        if caliber["grain_id"] != DAY_GRAIN:
            return None
        if str(caliber.get("additive") or "").strip() in ("是", "true", "True", "1"):
            return None
        return {
            "metric_code": caliber["code"],
            "metric_name": caliber["name"],
            "aggregator": caliber["aggregator"],
            "grain_id": caliber["grain_id"],
            "day_grain": True,
            "additive": False,
            "caliber": caliber["caliber"],
        }

    def query_metric(
        self,
        metric_code: str,
        cube_id: int,
        start_date: Any,
        end_date: Any,
    ) -> dict[str, Any]:
        """指标查询接口：单日返回日值，日期范围返回跨日去重后的总值。

        日值直接来自该日可比聚合的计数；范围值只有在该区间所有日分区都可用、
        且精确去重产物未超出立方容量上限时才发布。日值相加不是精确结果，
        任何情况下都不会作为范围精确值返回。
        """
        identity = self.get_distinct_metric(metric_code)
        if identity is None:
            return {
                "ok": False,
                "metric_code": metric_code,
                "publishable": False,
                "message": f"指标 {metric_code} 不是日粒度、不可累加的独立设备数口径",
            }

        from app.services import service_for
        cube_svc = service_for("cube_builder")
        if cube_svc is None:
            return {"ok": False, "publishable": False, "message": "立方服务未就绪"}

        try:
            dates = enumerate_dates(start_date, end_date)
        except ValueError as exc:
            return {"ok": False, "publishable": False, "message": str(exc)}

        if len(dates) == 1:
            day = dates[0]
            aggregate = cube_svc.get_comparable_partition(cube_id, day)
            if aggregate is None:
                return {
                    "ok": False,
                    "publishable": False,
                    "metric": identity,
                    "cube_id": cube_id,
                    "granularity": "day",
                    "date_range": {"start_date": day, "end_date": day, "day_count": 1},
                    "value": None,
                    "message": f"日事实分区 {day} 不可用，无法给出日值",
                    "fact_coverage": build_fact_coverage([day], []),
                }
            return {
                "ok": True,
                "publishable": True,
                "metric": identity,
                "cube_id": cube_id,
                "granularity": "day",
                "date_range": {"start_date": day, "end_date": day, "day_count": 1},
                "value": aggregate.device_count,
                "exact": True,
                "day_value": aggregate.device_count,
                "partition": aggregate.to_dict(),
                "fact_coverage": build_fact_coverage(
                    [day], [day], {day: aggregate.row_count},
                ),
                "message": (
                    "已返回该日独立设备数（日计数来自完整事实分区）"
                    if not aggregate.truncated
                    else "已返回该日独立设备数；该日精确去重产物已截断，"
                         "不能参与跨日并集"
                ),
            }

        range_result = cube_svc.resolve_device_range(cube_id, start_date, end_date)
        if not range_result.get("ok", False):
            range_result.update({
                "metric": identity,
                "granularity": "range",
                "publishable": False,
                "naive_sum_rejected": True,
            })
            return range_result
        return {
            "ok": True,
            "publishable": True,
            "metric": identity,
            "cube_id": cube_id,
            "granularity": "range",
            "date_range": range_result["date_range"],
            "value": range_result["value"],
            "exact": True,
            "sum_of_days": range_result["sum_of_days"],
            "overlap_devices": range_result["overlap_devices"],
            "contributions": range_result["contributions"],
            "fact_coverage": range_result["fact_coverage"],
            "message": "已返回日期范围内的独立设备数（跨日精确去重）",
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "指标数据集"
        base["published_metrics"] = sum(
            1 for row in store.rows(self.MODULE) if row.get("status") == "已发布"
        )
        return base