import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 跨日去重设备数 ----

export async function loadDeviceDedupDemo(capacityLimit: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/analytics/device-dedup/demo?capacity_limit=${capacityLimit}`)
  if (!response.ok) throw new Error('跨日去重样本读取失败')
  return await response.json()
}

export async function queryMetricDays(
  metricCode: string,
  cubeId: number,
  startDate: string,
  endDate: string,
): Promise<Record<string, unknown>> {
  const response = await request('/api/metric_def/query', {
    method: 'POST',
    body: JSON.stringify({
      metric_code: metricCode,
      cube_id: cubeId,
      start_date: startDate,
      end_date: endDate,
    }),
  })
  return await response.json()
}

export async function removeDayPartition(
  cubeId: number,
  date: string,
): Promise<Record<string, unknown>> {
  const response = await request(`/api/cube_builder/${cubeId}/partitions/${date}`, {
    method: 'DELETE',
  })
  return await response.json()
}

export async function listComparablePartitions(cubeId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/cube_builder/${cubeId}/comparable-partitions`)
  if (!response.ok) throw new Error('可比聚合分区读取失败')
  return await response.json()
}
