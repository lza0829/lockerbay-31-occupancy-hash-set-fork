<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const hours = ref(48)
const msg = ref('')
onMounted(async () => { hours.value = (await api.settings()).overdue_hours })
async function save() {
  const r = await api.setOverdue(Number(hours.value))
  hours.value = r.overdue_hours
  msg.value = '已保存'
}
</script>
<template>
  <div data-plant="31-occupancy-hash-set-fork" class="plant-mark" style="display:none"></div>

  <div>
    <h1>柜规</h1>
    <p class="sub">超时回收小时数（供后续空桩裁决；基座先可配置落库）。</p>
    <div class="panel" style="max-width:420px; display:flex; gap:.75rem; align-items:center">
      <label>overdue_hours
        <input v-model.number="hours" type="number" min="1" style="margin-left:.5rem; width:5rem; background:#111827; color:#e8eef6; border:1px solid #334155; border-radius:4px; padding:.3rem" />
      </label>
      <button @click="save">保存</button>
      <span class="sub">{{ msg }}</span>
    </div>
  </div>
</template>
