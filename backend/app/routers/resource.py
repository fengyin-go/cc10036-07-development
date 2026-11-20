"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


class ComparableAggregatePayload(BaseModel):
    """日粒度可比聚合的重算请求：事实行 + 可选的立方容量上限。"""
    facts: list[dict[str, Any]] = Field(default_factory=list)
    capacity_limit: int | None = None


class MetricQueryPayload(BaseModel):
    metric_code: str
    cube_id: int
    start_date: str
    end_date: str


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


# ---- 跨日去重设备数：可比聚合与指标查询（固定路径需先于通用资源路由注册） ----

@router.post("/metric_def/query")
def query_metric(payload: MetricQueryPayload) -> dict[str, Any]:
    """指标查询接口：单日返回日值，日期范围返回跨日去重后的总值。"""
    service = require_service("metric_def")
    return service.query_metric(
        payload.metric_code, payload.cube_id, payload.start_date, payload.end_date,
    )


@router.post("/cube_builder/{cube_id}/comparable-aggregates")
def build_comparable_aggregates(
    cube_id: int, payload: ComparableAggregatePayload,
) -> dict[str, Any]:
    """构建/重算日粒度可比聚合，并登记本次重算可用的事实覆盖。"""
    service = require_service("cube_builder")
    return service.build_comparable_aggregates(
        cube_id, payload.facts, capacity_limit=payload.capacity_limit,
    )


@router.get("/cube_builder/{cube_id}/comparable-partitions")
def list_comparable_partitions(cube_id: int) -> dict[str, Any]:
    service = require_service("cube_builder")
    partitions = service.list_comparable_partitions(cube_id)
    return {"ok": True, "cube_id": cube_id, "partitions": partitions}


@router.get("/cube_builder/{cube_id}/device-range")
def resolve_device_range(
    cube_id: int, start_date: str, end_date: str,
) -> dict[str, Any]:
    """跨日设备并集：返回精确值或拒绝发布的诊断与各日贡献。"""
    service = require_service("cube_builder")
    return service.resolve_device_range(cube_id, start_date, end_date)


@router.delete("/cube_builder/{cube_id}/partitions/{date}")
def remove_day_partition(cube_id: int, date: str) -> dict[str, Any]:
    """移除某日事实分区，返回移除后的可用来源与覆盖范围。"""
    service = require_service("cube_builder")
    return service.remove_day_partition(cube_id, date)


@router.get("/metric_def/distinct-metrics/{metric_code}")
def get_distinct_metric(metric_code: str) -> dict[str, Any]:
    """查询指标的日粒度去重身份（与立方共用粒度标识）。"""
    service = require_service("metric_def")
    identity = service.get_distinct_metric(metric_code)
    if identity is None:
        raise HTTPException(
            status_code=404,
            detail=f"指标 {metric_code} 不是日粒度、不可累加的独立设备数口径",
        )
    return {"ok": True, "metric": identity}


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
