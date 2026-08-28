// 构建产物同步到 FastAPI 挂载目录（app/main.py 探测 web/dist）
import { cpSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const webDir = dirname(dirname(fileURLToPath(import.meta.url))) // .../lan-share/web
const projectRoot = dirname(webDir)
const dist = join(webDir, 'dist')
const target = join(projectRoot, 'static-vue')
rmSync(target, { recursive: true, force: true })
cpSync(dist, target, { recursive: true })
console.log('deployed vue dist ->', target)
