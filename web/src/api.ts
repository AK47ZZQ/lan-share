// 全量 API 客户端·门面（v3.48 按域拆分：实现在 api/ 目录，本文件保留原导出）。
// 调用方无需改动——`import { api, publicApi, uploadResumable, ... } from './api'`
// 与拆分前完全一致；`api` 对象仍是单一入口（各域对象展开合并，键名不变）。
// 新代码如需按域引用，可直接 import { authApi } from './api/auth' 等。
import { authApi } from './api/auth'
import { consoleApi } from './api/console'
import { filesApi } from './api/files'
import { notifyApi } from './api/notify'
import { photosApi } from './api/photos'
import { sharesApi } from './api/shares'

export * from './api/types'
export { authApi } from './api/auth'
export { consoleApi } from './api/console'
export { filesApi } from './api/files'
export { notifyApi } from './api/notify'
export { photosApi } from './api/photos'
export { publicApi, sharesApi, type PublicShareApi } from './api/shares'
// 上传引擎原在 api.ts，随拆分原样保留导出（实现移居 api/upload.ts）
export { CHUNK, confirmLargeUpload, deleteUploadSession, isLanAccess, uploadResumable, type UploadHooks } from './api/upload'

export const api = {
  ...authApi,
  ...consoleApi,
  ...notifyApi,
  ...filesApi,
  ...photosApi,
  ...sharesApi,
}
