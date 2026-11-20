"""跨日去重设备数的回归测试：日值、范围并集、容量截断和分区移除。"""
from __future__ import annotations

import unittest

from app.analytics_engine import (
    build_comparable_day_aggregates,
    build_fact_coverage,
    enumerate_dates,
    merge_comparable_aggregates,
    normalize_date,
    resolve_grain_id,
)
from app.main import _sample_device_facts
from app.services import service_for


class ComparableAggregateTest(unittest.TestCase):
    def test_grain_tokens_shared_with_metric_def(self) -> None:
        self.assertEqual(resolve_grain_id("按日"), "DAY")
        self.assertEqual(resolve_grain_id("按日期分区,日级"), "DAY")
        self.assertEqual(resolve_grain_id("按日/按小时"), "DAY")
        self.assertEqual(resolve_grain_id("按小时"), "HOUR")
        self.assertIsNone(resolve_grain_id("按月"))

    def test_same_device_repeated_within_day_counts_once(self) -> None:
        aggregates = build_comparable_day_aggregates(
            [
                {"event_date": "2026-11-01", "device_id": "d1"},
                {"event_date": "2026-11-01", "device_id": "d1"},
                {"event_date": "2026-11-01", "device_id": "d2"},
            ],
            capacity_limit=10,
        )
        day = aggregates["2026-11-01"]
        self.assertEqual(day.device_count, 2)
        self.assertEqual(day.row_count, 3)
        self.assertFalse(day.truncated)

    def test_capacity_truncates_artifact_but_keeps_day_count(self) -> None:
        aggregates = build_comparable_day_aggregates(
            [{"event_date": "2026-11-01", "device_id": f"d{i}"} for i in range(5)],
            capacity_limit=2,
        )
        day = aggregates["2026-11-01"]
        self.assertEqual(day.device_count, 5)
        self.assertEqual(day.artifact_size, 2)
        self.assertTrue(day.truncated)

    def test_truncated_partition_refuses_exact_range(self) -> None:
        aggregates = build_comparable_day_aggregates(
            [{"event_date": "2026-11-01", "device_id": f"d{i}"} for i in range(5)],
            capacity_limit=2,
        )
        merge = merge_comparable_aggregates(aggregates, ["2026-11-01"])
        self.assertFalse(merge.exact)
        self.assertIsNone(merge.value)
        self.assertEqual(merge.truncated_dates, ["2026-11-01"])

    def test_missing_partition_refuses_exact_range(self) -> None:
        aggregates = build_comparable_day_aggregates(
            [{"event_date": "2026-11-01", "device_id": "d1"}], capacity_limit=10,
        )
        merge = merge_comparable_aggregates(
            aggregates, ["2026-11-01", "2026-11-02"],
        )
        self.assertFalse(merge.exact)
        self.assertIsNone(merge.value)
        self.assertEqual(merge.missing_dates, ["2026-11-02"])

    def test_coverage_lists_every_requested_date(self) -> None:
        coverage = build_fact_coverage(
            ["2026-11-01", "2026-11-02"], ["2026-11-01"], {"2026-11-01": 3},
        )
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["missing_dates"], ["2026-11-02"])
        self.assertEqual(len(coverage["sources"]), 2)

    def test_date_helpers_reject_bad_input(self) -> None:
        self.assertEqual(normalize_date("2026/11/01 10:00"), "2026-11-01")
        self.assertIsNone(normalize_date("not-a-date"))
        with self.assertRaises(ValueError):
            enumerate_dates("2026-11-02", "2026-11-01")


class MetricRangeQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cube_svc = service_for("cube_builder")
        self.metric_svc = service_for("metric_def")
        self.cube_svc.build_comparable_aggregates(
            1, _sample_device_facts(), capacity_limit=12,
        )

    def test_day_values(self) -> None:
        day_one = self.metric_svc.query_metric("DAU", 1, "2026-11-01", "2026-11-01")
        day_two = self.metric_svc.query_metric("DAU", 1, "2026-11-02", "2026-11-02")
        self.assertEqual(day_one["value"], 8)
        self.assertEqual(day_two["value"], 7)

    def test_range_total_dedups_returning_devices(self) -> None:
        result = self.metric_svc.query_metric("DAU", 1, "2026-11-01", "2026-11-02")
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 12)
        self.assertEqual(result["sum_of_days"], 15)
        self.assertEqual(result["overlap_devices"], 3)

    def test_naive_sum_never_published(self) -> None:
        self.cube_svc.build_comparable_aggregates(
            1, _sample_device_facts(), capacity_limit=5,
        )
        result = self.metric_svc.query_metric("DAU", 1, "2026-11-01", "2026-11-02")
        self.assertFalse(result["ok"])
        self.assertFalse(result["publishable"])
        self.assertIsNone(result["value"])

    def test_removed_partition_blocks_range(self) -> None:
        removed = self.cube_svc.remove_day_partition(1, "2026-11-02")
        self.assertTrue(removed["removed"])
        self.assertEqual(removed["remaining_dates"], ["2026-11-01"])
        result = self.metric_svc.query_metric("DAU", 1, "2026-11-01", "2026-11-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["fact_coverage"]["missing_dates"], ["2026-11-02"])
        day_two = self.metric_svc.query_metric("DAU", 1, "2026-11-02", "2026-11-02")
        self.assertFalse(day_two["ok"])

    def test_non_distinct_metric_rejected(self) -> None:
        self.assertIsNone(self.metric_svc.get_distinct_metric("GMV"))
        self.assertIsNone(self.metric_svc.get_distinct_metric("ARPU"))
        self.assertEqual(
            self.metric_svc.get_distinct_metric("DAU")["grain_id"], "DAY",
        )


if __name__ == "__main__":
    unittest.main()
