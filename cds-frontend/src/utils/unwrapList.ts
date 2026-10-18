/**
 * 列表端点响应体的唯一解包入口。
 *
 * 后端分页/列表约定并不统一：有的端点返回裸数组（app/api/certificates.py:78
 * 的 /certificates/list、app/api/federation.py:94 的 /federation/trusts），
 * 有的返回 {items}（app/api/kms.py:104 的 /kms/keys、app/api/kms.py:238 的
 * /kms/audit）。历史上每个调用点各写一种解包，导致同一个约定在三个地方三种
 * 结果：一种静默丢数据（空列表），一种直接 `.map is not a function`。
 *
 * 这里统一成一条规则：认得的形态都接受，认不出的**抛错**——绝不静默返回 []，
 * 因为「解包失败」和「后端说没有数据」在运维界面上必须是两回事
 * （项目铁律：honest degradation，禁伪造）。
 */

/** 已知的列表载荷形态键。 */
export type ListShapeKey = 'array' | 'items' | 'data' | 'results'

const WRAPPER_KEYS = ['items', 'data', 'results'] as const

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const describePayload = (payload: unknown): string => {
  if (payload === null) return 'null'
  if (Array.isArray(payload)) return 'array'
  if (isRecord(payload)) {
    const keys = Object.keys(payload)
    return keys.length ? `object{${keys.slice(0, 6).join(', ')}}` : 'object{}'
  }
  return typeof payload
}

/** 载荷形态无法识别时抛出：调用方据此呈现错误态，而不是「暂无数据」。 */
export class ListShapeError extends Error {
  readonly endpoint: string
  readonly payload: unknown

  constructor(endpoint: string, payload: unknown) {
    super(`列表接口 ${endpoint} 的响应结构无法识别（收到 ${describePayload(payload)}），已拒绝按空列表处理`)
    this.name = 'ListShapeError'
    this.endpoint = endpoint
    this.payload = payload
  }
}

/**
 * 把列表响应体解成数组。
 *
 * @param payload 响应体（api 拦截器已解出 response.data）
 * @param endpoint 仅用于错误信息，让运维能定位到具体端点
 * @param canonical 该端点的规范形态，优先尝试（其余形态按兼容顺序回退）
 * @throws ListShapeError 载荷既不是数组，也不带 items/data/results 数组字段
 */
export function unwrapList<T>(payload: unknown, endpoint: string, canonical: ListShapeKey = 'array'): T[] {
  const order: ListShapeKey[] =
    canonical === 'array'
      ? ['array', ...WRAPPER_KEYS]
      : [canonical, 'array', ...WRAPPER_KEYS.filter((key) => key !== canonical)]

  for (const key of order) {
    if (key === 'array') {
      if (Array.isArray(payload)) return payload as T[]
      continue
    }
    if (isRecord(payload) && Array.isArray(payload[key])) return payload[key] as T[]
  }

  throw new ListShapeError(endpoint, payload)
}
