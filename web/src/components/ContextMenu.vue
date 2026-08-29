<script setup lang="ts">
// 右键/⋯ 操作菜单：固定定位在触发点附近，自动防出屏
import { onMounted, onUnmounted, ref } from 'vue'

const props = defineProps<{ x: number; y: number }>()
const emit = defineEmits<{ close: [] }>()
const el = ref<HTMLElement | null>(null)
const pos = ref({ x: props.x, y: props.y })

function onDocClick(e: MouseEvent) {
  if (el.value && !el.value.contains(e.target as Node)) emit('close')
}
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') emit('close') }
function place() {
  const w = 176, h = el.value?.offsetHeight ?? 260
  pos.value = {
    x: Math.min(props.x, window.innerWidth - w - 10),
    y: Math.min(props.y, window.innerHeight - h - 10),
  }
}
onMounted(() => { place(); document.addEventListener('click', onDocClick, true); document.addEventListener('keydown', onKey) })
onUnmounted(() => { document.removeEventListener('click', onDocClick, true); document.removeEventListener('keydown', onKey) })
</script>

<template>
  <teleport to="body">
    <div ref="el" class="ctxmenu" :style="{ left: pos.x + 'px', top: pos.y + 'px' }">
      <slot />
    </div>
  </teleport>
</template>

<style>
.ctxmenu {
  position: fixed; z-index: 150; min-width: 176px; padding: 6px;
  border-radius: 12px; background: var(--glass-strong);
  border: 1px solid var(--glass-border); box-shadow: var(--shadow);
  backdrop-filter: blur(20px); animation: pop .16s ease;
}
.ctxmenu .mi {
  display: flex; align-items: center; gap: 9px; width: 100%;
  padding: 9px 11px; border: 0; border-radius: 8px; cursor: pointer;
  background: transparent; color: var(--text); font-size: 13.5px; text-align: left;
}
.ctxmenu .mi:hover { background: var(--hover); }
.ctxmenu .mi.danger { color: var(--danger); }
.ctxmenu .divider { height: 1px; margin: 5px 8px; background: var(--line); }
</style>
