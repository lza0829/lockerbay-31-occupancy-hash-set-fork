<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const rows = ref([])
const q = ref([])
onMounted(async () => {
  rows.value = await api.occupancies()
  q.value = await api.quarantine()
})
</script>
<template>
  <div data-plant="31-occupancy-hash-set-fork" class="plant-mark" style="display:none"></div>

  <div>
    <h1>回收</h1>
    <p class="sub">超时回收空桩待实现；此处先展示占用账与搁置。</p>
    <div class="panel">
      <h2 style="margin:0 0 .5rem;font-size:1rem">占用账</h2>
      <table>
        <thead><tr><th>占用</th><th>格口</th><th>运单</th><th>状态</th><th>开启</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td><code>{{ r.id }}</code></td>
            <td>{{ r.cell_id }}</td>
            <td>{{ r.tracking_no }}</td>
            <td>{{ r.status }}</td>
            <td>{{ r.opened_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" style="margin-top:1rem">
      <h2 style="margin:0 0 .5rem;font-size:1rem">搁置</h2>
      <p v-if="!q.length" class="sub">暂无搁置。</p>
      <table v-else>
        <thead><tr><th>id</th><th>原因</th><th>时刻</th></tr></thead>
        <tbody>
          <tr v-for="item in q" :key="item.id">
            <td>{{ item.id }}</td>
            <td>{{ item.reason }}</td>
            <td>{{ item.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
