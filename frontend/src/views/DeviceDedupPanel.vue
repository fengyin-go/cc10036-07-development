<template>
  <section class="device-dedup" data-testid="device-dedup-panel">
    <header class="dedup-head">
      <div>
        <h3>跨日去重设备数</h3>
        <p class="page-desc">
          日级聚合只保存当日去重计数；多日合计按设备标识做并集，日值相加不会作为精确结果发布。
        </p>
      </div>
      <div class="dedup-controls">
        <label>
          <span>立方容量上限</span>
          <input v-model.number="capacityLimit" data-testid="capacity-limit" type="number" min="1" />
        </label>
        <button class="btn primary" data-testid="load-sample" type="button" @click="loadSample">
          重算可比聚合
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="dedup-error">{{ errorMessage }}</p>

    <div class="stat-row">
      <article v-for="card in dayCards" :key="card.date" class="stat-card">
        <span class="stat-label">{{ card.date }} 日值</span>
        <strong class="stat-value" :data-testid="`day-value-${card.date}`">
          {{ card.value ?? '—' }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">日值相加（不可累加）</span>
        <strong class="stat-value muted-value" data-testid="naive-sum">{{ naiveSum }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">范围精确值</span>
        <strong class="stat-value" data-testid="range-value">{{ rangeValue ?? '未发布' }}</strong>
      </article>
    </div>

    <section class="dedup-block">
      <h4>范围查询核验</h4>
      <p class="dedup-message" data-testid="range-message">{{ rangeMessage }}</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>来源</th>
            <th>来源粒度</th>
            <th>日值</th>
            <th>标识产物</th>
            <th>本日独有</th>
            <th>与其他日共用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in contributions" :key="row.date" :data-testid="`contrib-${row.date}`">
            <td>{{ row.date }}</td>
            <td>{{ row.available ? row.source : '不可用' }}</td>
            <td>{{ row.grain_id }}</td>
            <td>{{ row.day_value ?? '—' }}</td>
            <td>{{ row.artifact_size }}</td>
            <td>{{ row.unique_to_day ?? '—' }}</td>
            <td>{{ row.shared_with_other_days ?? '—' }}</td>
          </tr>
          <tr v-if="!contributions.length">
            <td colspan="7" class="empty-state">暂无范围查询结果</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="dedup-block">
      <div class="dedup-head">
        <h4>可用事实覆盖</h4>
        <button class="btn ghost" data-testid="remove-day" type="button" @click="removeDay">
          移除 {{ dayTwoDate }} 分区
        </button>
      </div>
      <p class="dedup-message" data-testid="coverage-message">{{ coverageMessage }}</p>
      <ul class="coverage-list">
        <li v-for="source in coverageSources" :key="source.date" :data-testid="`coverage-${source.date}`">
          {{ source.date }}：{{ source.available ? `可用（${source.source}，事实 ${source.fact_rows} 行）` : '缺失' }}
        </li>
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  loadDeviceDedupDemo,
  listComparablePartitions,
  queryMetricDays,
  removeDayPartition,
} from '@/api/backend-service'

type Contribution = {
  date: string
  available: boolean
  source: string | null
  grain_id: string
  day_value: number | null
  artifact_size: number
  unique_to_day: number | null
  shared_with_other_days: number | null
}

type MetricQuery = {
  ok?: boolean
  value?: number | null
  publishable?: boolean
  message?: string
  contributions?: Contribution[]
  fact_coverage?: {
    requested_dates?: string[]
    available_dates?: string[]
    missing_dates?: string[]
    sources?: { date: string; available: boolean; source: string | null; fact_rows: number }[]
  }
  day_value?: number
}

const METRIC_CODE = 'DAU'
const CUBE_ID = 1
const dayOneDate = '2026-11-01'
const dayTwoDate = '2026-11-02'

const capacityLimit = ref(12)
const dayOne = ref<MetricQuery | null>(null)
const dayTwo = ref<MetricQuery | null>(null)
const range = ref<MetricQuery | null>(null)
const naiveSum = ref<number | null>(null)
const coverage = ref<MetricQuery['fact_coverage'] | null>(null)
const errorMessage = ref('')

const dayCards = computed(() => [
  { date: dayOneDate, value: dayOne.value?.value ?? null },
  { date: dayTwoDate, value: dayTwo.value?.value ?? null },
])
const rangeValue = computed(() => (range.value?.ok ? range.value.value : null))
const contributions = computed(() => range.value?.contributions ?? [])
const rangeMessage = computed(() => {
  if (!range.value) return '尚未执行范围查询'
  return range.value.message ?? (range.value.ok ? '范围查询已完成' : '范围值不可发布')
})
const coverageSources = computed(() => coverage.value?.sources ?? [])
const coverageMessage = computed(() => {
  const missing = coverage.value?.missing_dates ?? []
  if (!coverage.value) return '尚未登记可用事实覆盖'
  return missing.length
    ? `范围内缺失事实分区：${missing.join('、')}`
    : '范围内全部事实分区可用'
})

async function refreshCoverage(known?: MetricQuery['fact_coverage']) {
  const payload = await listComparablePartitions(CUBE_ID)
  const partitions = (payload.partitions as { date: string; row_count?: number }[] | undefined) ?? []
  const available = partitions.map((item) => item.date)
  const rowsByDate = new Map(partitions.map((item) => [item.date, item.row_count ?? 0]))
  const factRows = new Map((known?.sources ?? []).map((item) => [item.date, item.fact_rows]))
  coverage.value = {
    requested_dates: [dayOneDate, dayTwoDate],
    available_dates: available,
    missing_dates: [dayOneDate, dayTwoDate].filter((date) => !available.includes(date)),
    sources: [dayOneDate, dayTwoDate].map((date) => ({
      date,
      available: available.includes(date),
      source: available.includes(date) ? `cube:${CUBE_ID}:fact_partition` : null,
      fact_rows: factRows.get(date) ?? rowsByDate.get(date) ?? 0,
    })),
  }
}

async function loadSample() {
  errorMessage.value = ''
  try {
    const demo = await loadDeviceDedupDemo(capacityLimit.value)
    const days = (demo.day_queries as MetricQuery[]) ?? []
    dayOne.value = days[0] ?? null
    dayTwo.value = days[1] ?? null
    range.value = (demo.range_query as MetricQuery) ?? null
    naiveSum.value = (demo.naive_sum_of_days as number | undefined) ?? null
    const sample = demo.sample as { fact_coverage?: MetricQuery['fact_coverage'] } | undefined
    await refreshCoverage(sample?.fact_coverage ?? range.value?.fact_coverage)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '跨日去重样本读取失败'
  }
}

async function removeDay() {
  errorMessage.value = ''
  try {
    await removeDayPartition(CUBE_ID, dayTwoDate)
    const rangePayload = await queryMetricDays(METRIC_CODE, CUBE_ID, dayOneDate, dayTwoDate)
    range.value = rangePayload as MetricQuery
    const dayTwoPayload = await queryMetricDays(METRIC_CODE, CUBE_ID, dayTwoDate, dayTwoDate)
    dayTwo.value = { ...(dayTwoPayload as MetricQuery), value: null }
    naiveSum.value = null
    coverage.value = rangePayload.fact_coverage as MetricQuery['fact_coverage']
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '移除分区失败'
  }
}

onMounted(loadSample)
</script>

<style scoped>
.device-dedup {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 14px;
}
.dedup-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.dedup-head h3, .dedup-head h4 { margin: 0 0 4px; }
.dedup-controls { display: flex; gap: 10px; align-items: flex-end; }
.dedup-controls label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.dedup-controls input { width: 110px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.dedup-block { margin-top: 14px; }
.dedup-message { font-size: 13px; color: var(--muted); }
.muted-value { color: var(--muted); text-decoration: line-through; }
.coverage-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
</style>
