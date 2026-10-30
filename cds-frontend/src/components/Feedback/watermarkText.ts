// 预览层水印的纯文本口径（2026-10-30 拍板）。与组件文件分家：react-refresh 要求
// 组件模块只导出组件（cds-frontend lint 门禁），文案/构造函数放这里给测试与消费方共用。
// 禁伪造铁律不变：这里只声明「预览层水印」，绝不宣称文件内嵌了水印。

/** 旁边必须明写的口径文案（vitest 钉住，不许漂成「已加水印」）。 */
export const PREVIEW_WATERMARK_DISCLOSURE = '文件本体未修改，预览层水印';

export interface PreviewWatermarkInput {
  /** 当前用户（用户名优先，缺失退用户 ID）。 */
  user: string;
  /** 成果包编号。 */
  bundleId: string;
  /** 观看时刻 ISO 字符串；调用方在打开预览时定格一次，避免每次渲染漂移。 */
  capturedAt: string;
}

/** 水印文本口径与后端 MANIFEST.watermark_text 同构：包号 | 时间 | 主体。 */
export function buildPreviewWatermarkText({ user, bundleId, capturedAt }: PreviewWatermarkInput): string {
  return `${bundleId} | ${capturedAt} | user:${user}`;
}
