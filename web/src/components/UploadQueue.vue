<script setup lang="ts">
import { computed } from 'vue'
import { fmtSpeed, iconForName } from '../ui'
import {
  cancelAll, cancelItem, clearFinished, clearResumables, hasFinished, pauseItem,
  resumables, resumeItem, retryItem, toggleCollapsed, upl, uplSpeed, uplSummary,
  type UploadOpts,
} from '../uploader'

const props = defineProps<{ destDir: string; retryOpts?: Partial<UploadOpts> }>()

function pct(sent: number, total: number): number {
  return total ? Math.min(100, Math.round(sent / total * 100)) : 0
}

// sparkline：速度采样归一化到 100×24 视图
const spark = computed(() => {
  const s = uplSpeed.samples
  if (s.length < 3) return null
  const max = Math.max(...s, 1)
  const pts = s.map((v, i) => {
    const x = s.length === 1 ? 100 : (i / (s.length - 1)) * 100
    const y = 22 - (v / max) * 20
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return {
    line: pts.join(' '),
    area: `0,24 ${pts.join(' ')} 100,24`,
    now: fmtSpeed(s[s.length - 1]),
  }
})

function retryOptsFull(): UploadOpts {
  return { destDir: props.destDir, ...props.retryOpts }
}
</script>

<template>
  <!-- 跨刷新续传提示：队列空但有未完成会话 -->
  <div v-if="!upl.items.length && resumables.length" class="upl card">
    <div class="upl-head" style="cursor:default">
      <span class="upl-title">未传完 {{ resumables.length }} 个</span>
      <span class="flex1" />
      <button class="iconbtn" title="清除续传记录" @click="clearResumables()">✕</button>
    </div>
    <div class="upl-row" v-for="r in resumables.slice(0, 5)" :key="r.sid" style="padding:5px 0">
      <span class="up-stage" style="margin:0">{{ r.name }}（{{ (r.size / 1024 ** 3).toFixed(1) }} GB）</span>
    </div>
    <div class="up-stage" style="padding:4px 0 2px">重新选择同一文件即可从已传位置继续（保留 3 天）</div>
  </div>

  <div v-if="upl.items.length" class="upl card" :class="{ collapsed: upl.collapsed }">
    <div class="upl-head" @click="toggleCollapsed()">
      <span class="upl-title">上传队列</span>
      <span v-if="uplSummary.active" class="upl-sub">{{ uplSummary.count }} 个 · {{ uplSummary.pct }}%</span>
      <span v-else-if="uplSummary.paused" class="upl-sub warn">已暂停 {{ uplSummary.paused }} 个</span>
      <span v-else-if="uplSummary.failed" class="upl-sub warn">{{ uplSummary.failed }} 个失败</span>
      <span v-else class="upl-sub">已完成</span>
      <span v-if="spark" class="upl-spark" :title="`当前 ${spark.now}`">
        <svg viewBox="0 0 100 24" preserveAspectRatio="none">
          <polygon :points="spark.area" fill="currentColor" opacity=".14" />
          <polyline :points="spark.line" fill="none" stroke="currentColor" stroke-width="2.5"
                    vector-effect="non-scaling-stroke" />
        </svg>
        <span>{{ spark.now }}</span>
      </span>
      <span class="flex1" />
      <button v-if="uplSummary.active" class="iconbtn" title="全部取消" @click.stop="cancelAll()">⏹</button>
      <button v-else-if="hasFinished" class="iconbtn" title="清空记录" @click.stop="clearFinished()">✕</button>
      <span class="upl-chev">{{ upl.collapsed ? '▴' : '▾' }}</span>
    </div>
    <div v-if="!upl.collapsed && uplSummary.active" class="bar upl-overall">
      <i :style="{ width: uplSummary.pct + '%' }" />
    </div>
    <template v-if="!upl.collapsed">
      <div v-for="u in upl.items" :key="u.id" class="item upl-row" :class="'st-' + u.status">
        <span class="up-ic">{{ iconForName(u.name) }}</span>
        <div class="up-main">
          <div class="up-line1">
            <span class="up-name" :title="u.name">{{ u.name }}</span>
            <span class="up-pct">{{ u.status === 'done' ? '100%' : pct(u.sent, u.total) + '%' }}</span>
          </div>
          <div class="bar"><i :style="{ width: pct(u.sent, u.total) + '%' }" /></div>
          <div class="up-stage">{{ u.stage }}</div>
        </div>
        <span class="up-acts">
          <button v-if="u.status === 'up'" class="iconbtn" title="暂停" @click="pauseItem(u)">❚❚</button>
          <button v-if="u.status === 'paused'" class="iconbtn" title="继续" @click="resumeItem(retryOptsFull(), u)">▶</button>
          <button v-if="u.status === 'up' || u.status === 'queue' || u.status === 'paused'" class="iconbtn" title="取消上传" @click="cancelItem(u)">✕</button>
          <button v-else-if="u.status === 'fail' || u.status === 'cancel'" class="iconbtn" title="重新上传" @click="retryItem(retryOptsFull(), u)">↻</button>
        </span>
      </div>
    </template>
  </div>
</template>
