"""多维立方领域服务：预计算、维度组合生成、构建调度、日粒度可比聚合。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.analytics_engine import (
    DAY_GRAIN,
    ComparableDayAggregate,
    build_comparable_day_aggregates,
    build_cube_cell,
    build_fact_coverage,
    enumerate_dates,
    generate_dimension_combinations,
    merge_comparable_aggregates,
    normalize_date,
    resolve_grain_id,
)

# 默认容量上限：单日精确去重产物最多保留的标识数。
DEFAULT_DEVICE_ARTIFACT_CAPACITY = 12


class CubeBuilderService(BaseModuleService):
    MODULE = "cube_builder"
    ENTITY = "OLAP立方"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["立方名称", "事实表", "维度列表", "预聚合度量", "分区策略", "刷新频率", "存储引擎"]
    REQUIRED_FIELDS = ["立方名称", "事实表", "维度列表"]
    STATUS_ORDER = ["草稿", "构建中", "已上线", "已下线"]
    ACTION_RULES = {"开始构建": "构建中", "上线立方": "已上线", "下线立方": "已下线"}
    NEGATIVE_ACTIONS = ["下线立方"]

    # ---- 业务方法 ----

    def precompute_cube(self, cube_id: int, sample_data: list[dict[str, Any]]) -> dict[str, Any]:
        """对指定立方执行预计算，按维度组合分组聚合。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return {"ok": False, "message": f"立方 {cube_id} 不存在"}
        dimensions = [d.strip() for d in str(cube.get("维度列表", "")).split(",") if d.strip()]
        measures_raw = str(cube.get("预聚合度量", "")).split(",")
        measures: list[dict[str, str]] = []
        for m in measures_raw:
            parts = m.strip().split(":")
            if len(parts) >= 2:
                measures.append({"name": parts[0].strip(), "aggregator": parts[1].strip()})
            elif m.strip():
                measures.append({"name": m.strip(), "aggregator": "SUM"})

        cells = build_cube_cell(sample_data, dimensions, measures)
        dimension_combos = generate_dimension_combinations(dimensions)

        return {
            "ok": True,
            "cube_id": cube_id,
            "cube_name": str(cube.get("立方名称", "")),
            "dimensions": dimensions,
            "dimension_combinations": len(dimension_combos),
            "cells": len(cells),
            "cells_preview": [
                {
                    "dimensions": c.dimensions,
                    "measures": c.measure_values,
                    "row_count": c.row_count,
                }
                for c in cells[:10]
            ],
            "storage_engine": cube.get("存储引擎"),
        }

    def get_cube_dimensions(self, cube_id: int) -> list[str]:
        """获取立方的维度列表（供切片查询消费）。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return []
        return [d.strip() for d in str(cube.get("维度列表", "")).split(",") if d.strip()]

    def get_online_cubes(self) -> list[dict[str, Any]]:
        """获取所有已上线立方。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已上线"
        ]

    # ---- 日粒度可比聚合（跨日去重设备数） ----

    def _state(self) -> dict[str, Any]:
        if not hasattr(self, "_comparable"):
            self._comparable = {
                "partitions": {},   # cube_id -> {date -> ComparableDayAggregate}
                "capacity": {},     # cube_id -> int
                "recompute": {},    # cube_id -> [记录]
            }
        return self._comparable

    def cube_grain(self, cube_id: int) -> str | None:
        """解析立方的来源粒度标识，与指标定义的粒度标识共用词表。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return None
        return resolve_grain_id(cube.get("分区策略"))

    def get_capacity_limit(self, cube_id: int) -> int:
        return int(self._state()["capacity"].get(cube_id, DEFAULT_DEVICE_ARTIFACT_CAPACITY))

    def build_comparable_aggregates(
        self,
        cube_id: int,
        facts: list[dict[str, Any]],
        *,
        capacity_limit: int | None = None,
    ) -> dict[str, Any]:
        """重建指定立方的日粒度可比聚合，并记录本次重算可用的事实覆盖。

        日计数始终来自完整事实分区；设备标识产物按容量上限截断，
        截断分区会标记 artifact_truncated，不能再参与跨日精确并集。
        """
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return {"ok": False, "message": f"立方 {cube_id} 不存在"}
        grain_id = self.cube_grain(cube_id)
        if grain_id != DAY_GRAIN:
            return {
                "ok": False,
                "message": (
                    f"立方 {cube_id} 的来源粒度不是日粒度，无法生成日粒度可比聚合"
                ),
            }
        if capacity_limit is None:
            capacity_limit = self.get_capacity_limit(cube_id)
        if capacity_limit <= 0:
            return {"ok": False, "message": "立方容量上限必须为正数"}

        clean_facts = [row for row in facts if isinstance(row, dict)]
        aggregates = build_comparable_day_aggregates(
            clean_facts,
            capacity_limit=capacity_limit,
            grain_id=grain_id,
            source=f"cube:{cube_id}:fact_partition",
        )
        state = self._state()
        state["capacity"][cube_id] = capacity_limit
        state["partitions"][cube_id] = aggregates

        requested_dates = sorted(aggregates)
        coverage = build_fact_coverage(
            requested_dates,
            list(aggregates),
            {date: aggregates[date].row_count for date in aggregates},
        )
        coverage["unparsed_rows"] = _unparsed_row_count(clean_facts)
        state["recompute"][cube_id] = [{
            "cube_id": cube_id,
            "grain_id": grain_id,
            "capacity_limit": capacity_limit,
            "partition_count": len(aggregates),
            "coverage": coverage,
        }]

        return {
            "ok": True,
            "cube_id": cube_id,
            "cube_name": str(cube.get("立方名称", "")),
            "grain_id": grain_id,
            "capacity_limit": capacity_limit,
            "partition_count": len(aggregates),
            "partitions": [
                aggregates[date].to_dict() for date in requested_dates
            ],
            "fact_coverage": coverage,
        }

    def get_comparable_partition(
        self, cube_id: int, date: Any,
    ) -> ComparableDayAggregate | None:
        date_key = normalize_date(date)
        if date_key is None:
            return None
        return self._state()["partitions"].get(cube_id, {}).get(date_key)

    def list_comparable_partitions(self, cube_id: int) -> list[dict[str, Any]]:
        partitions = self._state()["partitions"].get(cube_id, {})
        return [partitions[date].to_dict() for date in sorted(partitions)]

    def resolve_device_range(
        self, cube_id: int, start_date: Any, end_date: Any,
    ) -> dict[str, Any]:
        """核对区间内各日分区贡献，返回跨日设备并集或拒绝发布的诊断。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return {"ok": False, "message": f"立方 {cube_id} 不存在"}
        grain_id = self.cube_grain(cube_id)
        if grain_id != DAY_GRAIN:
            return {
                "ok": False,
                "message": f"立方 {cube_id} 的来源粒度不是日粒度，无法做跨日去重",
            }
        try:
            dates = enumerate_dates(start_date, end_date)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}

        aggregates = self._state()["partitions"].get(cube_id, {})
        merge = merge_comparable_aggregates(aggregates, dates, grain_id=grain_id)
        coverage = build_fact_coverage(
            dates,
            [date for date in dates if date in aggregates],
            {
                date: aggregates[date].row_count
                for date in dates if date in aggregates
            },
        )
        payload = merge.to_dict()
        payload.update({
            "ok": merge.exact,
            "cube_id": cube_id,
            "cube_name": str(cube.get("立方名称", "")),
            "grain_id": grain_id,
            "date_range": {
                "start_date": dates[0] if dates else None,
                "end_date": dates[-1] if dates else None,
                "day_count": len(dates),
            },
            "fact_coverage": coverage,
            "capacity_limit": self.get_capacity_limit(cube_id),
        })
        if not merge.exact:
            payload["message"] = "；".join(merge.reasons) or "无法给出精确结果"
            payload["publishable"] = False
        else:
            payload["message"] = "跨日设备并集已完成精确去重"
            payload["publishable"] = True
        return payload

    def remove_day_partition(self, cube_id: int, date: Any) -> dict[str, Any]:
        """移除某日事实分区及其可比聚合，并返回移除后的覆盖情况。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return {"ok": False, "message": f"立方 {cube_id} 不存在"}
        date_key = normalize_date(date)
        if date_key is None:
            return {"ok": False, "message": "日期格式必须是 YYYY-MM-DD"}

        state = self._state()
        partitions = state["partitions"].get(cube_id, {})
        removed_aggregate = partitions.pop(date_key, None)
        remaining = sorted(partitions)

        recompute_log = state["recompute"].setdefault(cube_id, [])
        coverage = build_fact_coverage(
            [date_key], remaining, {d: partitions[d].row_count for d in remaining},
        )
        recompute_log.append({
            "cube_id": cube_id,
            "event": "移除日事实分区",
            "removed_date": date_key,
            "removed_existed": removed_aggregate is not None,
            "remaining_dates": remaining,
            "coverage": coverage,
        })

        return {
            "ok": True,
            "cube_id": cube_id,
            "removed_date": date_key,
            "removed": removed_aggregate is not None,
            "remaining_dates": remaining,
            "available_sources": [
                {
                    "date": date,
                    "source": partitions[date].source,
                    "grain_id": partitions[date].grain_id,
                    "device_count": partitions[date].device_count,
                    "artifact_truncated": partitions[date].truncated,
                }
                for date in remaining
            ],
            "requested_date_available": date_key in partitions,
            "coverage": coverage,
            "fact_coverage": coverage,
            "message": (
                f"日事实分区 {date_key} 已移除"
                if removed_aggregate is not None
                else f"日事实分区 {date_key} 原本不存在"
            ),
        }

    def get_recompute_records(self, cube_id: int) -> list[dict[str, Any]]:
        return list(self._state()["recompute"].get(cube_id, []))

    def estimate_storage(self, cube_id: int, sample_row_count: int) -> dict[str, Any]:
        """估算立方存储需求。"""
        cube = store.find(self.MODULE, cube_id)
        if cube is None:
            return {"ok": False, "message": f"立方 {cube_id} 不存在"}
        dimensions_count = len(
            [d for d in str(cube.get("维度列表", "")).split(",") if d.strip()]
        )
        measures_count = len(
            [m for m in str(cube.get("预聚合度量", "")).split(",") if m.strip()]
        )
        combos = 2 ** dimensions_count - 1
        estimated_cells = combos * min(sample_row_count, 1000)
        estimated_bytes = estimated_cells * measures_count * 32
        return {
            "ok": True,
            "dimension_count": dimensions_count,
            "measure_count": measures_count,
            "dimension_combinations": combos,
            "estimated_cells": estimated_cells,
            "estimated_mb": round(estimated_bytes / (1024 * 1024), 2),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        online_cubes = sum(
            1 for row in store.rows(self.MODULE) if row.get("status") == "已上线"
        )
        base["online_cubes"] = online_cubes
        base["artifact"] = "OLAP立方数据集"
        base["comparable_partitions"] = {
            cube_id: len(partitions)
            for cube_id, partitions in self._state()["partitions"].items()
        }
        return base


def _unparsed_row_count(facts: list[dict[str, Any]]) -> int:
    """统计缺少日期或设备标识、无法进入可比聚合的事实行。"""
    return sum(
        1 for row in facts
        if normalize_date(row.get("event_date")) is None
        or not str(row.get("device_id") or "").strip()
    )