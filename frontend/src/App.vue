<script setup>
import { onMounted, ref } from 'vue'
import { api } from './api.js'

const health = ref(null)
onMounted(async () => {
  try { health.value = await api.health() } catch { health.value = { 正常: false } }
})
</script>

<template>
  <div class="bay-shell">
    <header class="fascia">
      <div class="brand">
        <span class="led" aria-hidden="true"></span>
        <div>
          <strong>格湾</strong>
          <em>LockerBay</em>
        </div>
      </div>
      <p class="pitch">入柜事件织成格口占用账</p>
      <nav>
        <router-link to="/">格阵</router-link>
        <router-link to="/ingest">装载</router-link>
        <router-link to="/reclaim">回收</router-link>
        <router-link to="/proofs">核账</router-link>
        <router-link to="/settings">柜规</router-link>
      </nav>
      <div class="rail-foot" :class="{ ok: health?.正常 }">
        {{ health?.正常 ? '柜链路通' : '待连接' }}
      </div>
    </header>
    <main class="stage">
      <router-view />
    </main>
  </div>
</template>
