<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const error = ref('')
const failed = computed(() => rows.value.filter((r) => !r.verify_ok))

const FAIL_TEXT = {
  proof_missing: '核对缺失（漏挂）',
  hash_missing: '指纹缺失',
  member_set_changed: '成员集合已变更',
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

function short(hash) {
  return hash ? hash.replace('sha256:', '').slice(0, 12) : '—'
}

onMounted(load)
</script>
<template>
  <div data-plant="31-occupancy-hash-set-fork" class="plant-mark" style="display:none"></div>

  <div>
    <h1>核账</h1>
    <p class="sub">占用成员事件有序指纹落库；读时与当前事件集合对照，集合被改或漏挂即校验失败。</p>
    <p v-if="failed.length" class="panel" style="border-color:#c0392b">
      ⚠ {{ failed.length }} 条占用核对校验失败，禁止冒充成功；可核对差异后「重建指纹」（留审计）。
    </p>
    <p v-if="error" class="panel" style="border-color:#c0392b">{{ error }}</p>
    <div class="panel" style="overflow:auto">
      <table>
        <thead>
          <tr>
            <th>占用</th><th>格口</th><th>运单</th><th>状态</th>
            <th>成员事件（落库 → 当前）</th><th>指纹（落库 → 当前）</th><th>校验</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in rows" :key="p.occupancy_id">
            <td><code>{{ p.occupancy_id }}</code></td>
            <td>{{ p.cell_id }}</td>
            <td>{{ p.tracking_no }}</td>
            <td>{{ p.status }}</td>
            <td>
              <code>{{ (p.stored_event_ids || []).join(',') || '—' }}</code>
              →
              <code>{{ (p.current_event_ids || []).join(',') || '—' }}</code>
            </td>
            <td>
              <code :title="p.stored_hash">{{ short(p.stored_hash) }}</code>
              →
              <code :title="p.current_hash">{{ short(p.current_hash) }}</code>
            </td>
            <td>
              <strong v-if="p.verify_ok" style="color:#27ae60">✓ 通过</strong>
              <strong v-else style="color:#c0392b">✗ {{ FAIL_TEXT[p.fail_reason] || '校验失败' }}</strong>
              <br /><small>{{ p.verified_at }}</small>
            </td>
            <td>
              <button v-if="!p.verify_ok" @click="rebuild(p)">重建指纹</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
