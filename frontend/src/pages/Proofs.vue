<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const error = ref('')
const reverseViews = ref({})
const failed = computed(() => rows.value.filter((r) => !r.verify_ok))

const FAIL_TEXT = {
  proof_missing: '核对缺失（漏挂）',
  hash_missing: '指纹缺失',
  member_set_changed: '成员集合已变更',
  reverse_missing: 'hash 通过但反查缺行',
  reverse_hash_stale: '反查挂在旧 hash',
  member_set_fork: '反查与正算成员分叉',
}

async function load() {
  rows.value = await api.proofs()
}

async function rebuild(row) {
  error.value = ''
  try {
    await api.rebuildProof(row.occupancy_id)
    await load()
  } catch (e) {
    error.value = String(e)
  }
}

async function checkReverse(row) {
  error.value = ''
  try {
    reverseViews.value[row.occupancy_id] = await api.occupancyHashMembers(row.occupancy_id)
  } catch (e) {
    error.value = String(e)
  }
}

async function lookupByHash(row) {
  error.value = ''
  try {
    const out = await api.hashLookup(row.stored_hash)
    reverseViews.value[row.occupancy_id] = {
      occupancy_id: row.occupancy_id,
      hash: row.stored_hash,
      member_ids: (out.occupancies.find((x) => x.occupancy_id === row.occupancy_id) || {}).member_ids || [],
      aligned: out.aligned,
      fail_reason: null,
      reverse_count: 0,
    }
  } catch (e) {
    error.value = String(e)
  }
}

function short(hash) {
  return hash ? hash.replace('sha256:', '').slice(0, 12) : '—'
}

onMounted(load)
</script>
<template>
  <div data-plant="31-occupancy-hash-set-fork" class="plant-mark" style="display:none"></div>

  <div>
    <h1>核账</h1>
    <p class="sub">
      占用成员事件有序指纹与 <code>hash→成员集合</code> 反查表同事务落库；
      读时与当前事件集合三方对照（正算 ⇔ 落库 ⇔ 反查），缺行 / 分叉 / 漏挂即校验失败。
    </p>
    <p v-if="failed.length" class="panel" style="border-color:#c0392b">
      ⚠ {{ failed.length }} 条占用核对校验失败，禁止冒充成功；可核对差异后「重建指纹」（留审计，同事务重建反查）。
    </p>
    <p v-if="error" class="panel" style="border-color:#c0392b">{{ error }}</p>
    <div class="panel" style="overflow:auto">
      <table>
        <thead>
          <tr>
            <th>占用</th><th>格口</th><th>运单</th><th>状态</th>
            <th>成员事件（落库 → 正算 → 反查）</th><th>指纹（落库 → 正算 → 反查）</th>
            <th>校验</th><th>反查核证</th><th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="p in rows" :key="p.occupancy_id">
            <tr>
              <td><code>{{ p.occupancy_id }}</code></td>
              <td>{{ p.cell_id }}</td>
              <td>{{ p.tracking_no }}</td>
              <td>{{ p.status }}</td>
              <td>
                <code>{{ (p.stored_event_ids || []).join(',') || '—' }}</code>
                →
                <code>{{ (p.current_event_ids || []).join(',') || '—' }}</code>
                →
                <code :style="p.verify_ok ? '' : 'color:#c0392b'">{{ (p.reverse_event_ids || []).join(',') || '✗ 缺行' }}</code>
              </td>
              <td>
                <code :title="p.stored_hash">{{ short(p.stored_hash) }}</code>
                →
                <code :title="p.current_hash">{{ short(p.current_hash) }}</code>
                →
                <code :title="p.reverse_hash" :style="p.reverse_hash === p.stored_hash ? '' : 'color:#c0392b'">{{ short(p.reverse_hash) }}</code>
              </td>
              <td>
                <strong v-if="p.verify_ok" style="color:#27ae60">✓ 通过</strong>
                <strong v-else style="color:#c0392b">✗ {{ FAIL_TEXT[p.fail_reason] || '校验失败' }}</strong>
                <br /><small>{{ p.verified_at }}</small>
              </td>
              <td>
                <button @click="checkReverse(p)">反查核证</button>
                <button v-if="p.stored_hash" @click="lookupByHash(p)" style="margin-left:4px">按 hash 反查</button>
              </td>
              <td>
                <button v-if="!p.verify_ok" @click="rebuild(p)">重建指纹</button>
              </td>
            </tr>
            <tr v-if="reverseViews[p.occupancy_id]">
              <td colspan="9" class="panel" style="background:#f7f9fc">
                <template v-if="reverseViews[p.occupancy_id].aligned">
                  <strong style="color:#27ae60">✓ 正算成员 ⇔ 反查同集（{{ reverseViews[p.occupancy_id].reverse_count ?? reverseViews[p.occupancy_id].member_ids?.length }} 个成员）</strong>
                </template>
                <template v-else>
                  <strong style="color:#c0392b">✗ {{ FAIL_TEXT[reverseViews[p.occupancy_id].fail_reason] || '反查分叉' }}</strong>
                </template>
                <div style="margin-top:4px">
                  hash <code>{{ reverseViews[p.occupancy_id].hash }}</code> →
                  成员 <code>{{ (reverseViews[p.occupancy_id].member_ids || []).join(', ') || '（空）' }}</code>
                </div>
                <div v-if="reverseViews[p.occupancy_id].forward_event_ids" style="margin-top:4px">
                  正算 <code>{{ reverseViews[p.occupancy_id].forward_event_ids.join(', ') }}</code>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
