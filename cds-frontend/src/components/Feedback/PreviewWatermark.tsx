// 在线预览层背景水印（2026-10-30 拍板）：csv/tsv 这类纯文本成果**文件本体未修改**，
// 平台在线预览时像 web 页面背景水印那样叠一层半透明斜向文字（当前用户 + 时间 + 包号）。
// 本文件只导出组件（react-refresh 门禁）；文案与文本构造在同目录 watermarkText.ts。
import { useMemo, type ReactNode } from 'react';
import { Typography } from 'antd';
import { buildPreviewWatermarkText, PREVIEW_WATERMARK_DISCLOSURE, type PreviewWatermarkInput } from './watermarkText';

const { Text } = Typography;

function escapeXml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** 平铺瓦片：斜向 -30°、低透明度文字——web 背景水印的常规观感。 */
function tileDataUri(text: string): string {
  const svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='380' height='210'>" +
    `<text x='190' y='115' text-anchor='middle' transform='rotate(-30 190 105)' ` +
    `font-size='15' fill='rgba(114,46,206,0.16)'>${escapeXml(text)}</text>` +
    '</svg>';
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

interface Props extends PreviewWatermarkInput {
  children?: ReactNode;
}

/**
 * 给预览内容叠背景水印层：绝对定位平铺 SVG、pointer-events 穿透、内容仍在最上可读。
 * 旁边文案固定为 PREVIEW_WATERMARK_DISCLOSURE——它是对「文件本体没动」的诚实
 * 披露，不只是装饰。
 */
export default function PreviewWatermarkOverlay({ user, bundleId, capturedAt, children }: Props) {
  const text = useMemo(
    () => buildPreviewWatermarkText({ user, bundleId, capturedAt }),
    [user, bundleId, capturedAt],
  );
  return (
    <div data-testid="preview-watermark-wrap" style={{ position: 'relative' }}>
      <div
        data-testid="preview-watermark-layer"
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 2,
          pointerEvents: 'none',
          backgroundImage: tileDataUri(text),
          backgroundRepeat: 'repeat',
          overflow: 'hidden',
        }}
      />
      {children}
      <div data-testid="preview-watermark-note" style={{ marginTop: 8 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {PREVIEW_WATERMARK_DISCLOSURE}：{text}（下载的文件不含此水印；是否内嵌以清单 watermark_method 为准）
        </Text>
      </div>
    </div>
  );
}
