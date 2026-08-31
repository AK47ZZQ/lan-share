// admin 成员视角预览：sessionStorage 持久（跨路由/刷新）+ Vue ref 驱动顶栏横幅。
// 只影响只读列表（/api/files、/api/photos、/api/photos/facets、/api/shares）的
// 可见性解析——写操作仍按真实会话身份执行。
import { ref } from 'vue'

const KEY = 'lsPreviewAs'

export const previewAs = ref(sessionStorage.getItem(KEY) || '')

export function setPreviewAs(name: string) {
  previewAs.value = name
  if (name) sessionStorage.setItem(KEY, name)
  else sessionStorage.removeItem(KEY)
}
