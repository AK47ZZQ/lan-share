<script setup lang="ts">
// 通用弹窗：teleport 到 body，Esc/遮罩关闭
const props = defineProps<{ title: string; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <teleport to="body">
    <div class="overlay" @click.self="emit('close')" @keydown.esc="emit('close')">
      <div class="modal-card" :style="wide ? 'width:min(560px,94vw)' : ''">
        <h3>{{ props.title }}</h3>
        <slot />
      </div>
    </div>
  </teleport>
</template>
