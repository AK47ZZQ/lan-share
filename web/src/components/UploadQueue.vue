<script setup lang="ts">
import { iconForName } from '../ui'
import { cancelAll, cancelItem, clearFinished, hasFinished, retryItem, toggleCollapsed, upl, uplSummary, type UploadOpts } from '../uploader'

const props = defineProps<{ destDir: string; retryOpts?: Partial<UploadOpts> }>()

function pct(sent: number, total: number): number {
  return total ? Math.min(100, Math.round(sent / total * 100)) : 0
}
</script>

<template>
  <div v-if="upl.items.length" class="upl card" :class="{ collapsed: upl.collapsed }">
    <div class="upl-head" @click="toggleCollapsed()">
      <span class="upl-title">上传队列</span>
      <span v-if="uplSummary.active" class="upl-sub">
        {{ uplSummary.count }} 个 · {{ uplSummary.pct }}%
      </span>
      <span v-else-if="uplSummary.failed" class="upl-sub warn">{{ uplSummary.failed }} 个失败</span>
      <span v-else class="upl-sub">已完成</span>
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
          <button v-if="u.status === 'up' || u.status === 'queue'" class="iconbtn" title="取消上传" @click="cancelItem(u)">✕</button>
          <button v-else-if="u.status === 'fail' || u.status === 'cancel'" class="iconbtn" title="重新上传" @click="retryItem({ destDir: props.destDir, ...props.retryOpts }, u)">↻</button>
        </span>
      </div>
    </template>
  </div>
</template>
