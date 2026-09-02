<!-- 六格分离验证码输入（与 App 端 _CodeField 同手感）：
     单位输入自动前进、退格回移清格、首格粘贴多位顺次铺开、六格填满 emit complete。
     仅供 6 位数字动态码（TOTP）；恢复码是 8 位 hex，走登录页文本框。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'

const props = defineProps<{ disabled?: boolean; autofocus?: boolean }>()
const emit = defineEmits<{ 'update:code': [string]; complete: [string] }>()

const boxes = ref<string[]>(Array(6).fill(''))
const els = ref<(HTMLInputElement | null)[]>([])
let done = false

onMounted(() => { if (props.autofocus) els.value[0]?.focus() })

function reset() {
  boxes.value = Array(6).fill('')
  done = false
  els.value[0]?.focus()
}
defineExpose({ reset })

function maybeEmit() {
  const joined = boxes.value.join('')
  emit('update:code', joined)
  if (!done && joined.length === 6 && !boxes.value.includes('')) {
    done = true
    emit('complete', joined)
  }
}

function onInput(i: number, e: Event) {
  const el = e.target as HTMLInputElement
  const digits = el.value.replace(/\D/g, '')
  el.value = ''
  if (!digits) { boxes.value[i] = ''; maybeEmit(); return }
  let k = i
  for (const d of digits) { if (k > 5) break; boxes.value[k] = d; k++ }
  els.value[Math.min(k, 5)]?.focus()
  maybeEmit()
}

function onKeydown(i: number, e: KeyboardEvent) {
  if (e.key === 'Backspace') {
    e.preventDefault()
    done = false
    if (!boxes.value[i] && i > 0) { boxes.value[i - 1] = ''; els.value[i - 1]?.focus() }
    else boxes.value[i] = ''
    emit('update:code', boxes.value.join(''))
  } else if (e.key === 'ArrowLeft' && i > 0) els.value[i - 1]?.focus()
  else if (e.key === 'ArrowRight' && i < 5) els.value[i + 1]?.focus()
}
</script>

<template>
  <div class="code-field" :class="{ disabled: props.disabled }">
    <input v-for="i in 6" :key="i" :ref="el => { els[i - 1] = el as HTMLInputElement | null }"
           :value="boxes[i - 1]" type="text" inputmode="numeric" autocomplete="one-time-code"
           maxlength="6" :disabled="props.disabled" aria-label="动态码"
           @input="onInput(i - 1, $event)" @keydown="onKeydown(i - 1, $event)"
           @focus="($event.target as HTMLInputElement).select()" />
  </div>
</template>

<style>
.code-field { display: flex; gap: 7px; justify-content: center; }
.code-field input {
  width: 42px; height: 52px; text-align: center; font-size: 22px; font-weight: 600;
  font-variant-numeric: tabular-nums; padding: 0;
}
</style>
