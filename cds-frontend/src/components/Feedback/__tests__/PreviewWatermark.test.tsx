// 2026-10-30 背景水印拍板：csv/tsv 类成果文件本体未修改，水印只在预览层。
// 禁伪造铁律：预览层必须明写「文件本体未修改」，各口径标签绝不把降级/预览层说成「已嵌入」。
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PreviewWatermarkOverlay from '../PreviewWatermark';
import { buildPreviewWatermarkText, PREVIEW_WATERMARK_DISCLOSURE } from '../watermarkText';

describe('buildPreviewWatermarkText', () => {
  it('文本口径含包号 + 时间 + 主体，与后端 MANIFEST.watermark_text 同构', () => {
    const text = buildPreviewWatermarkText({ user: 'alice', bundleId: 'B-42', capturedAt: '2026-10-30T13:00:00.000Z' });
    expect(text).toBe('B-42 | 2026-10-30T13:00:00.000Z | user:alice');
  });
});

describe('PreviewWatermarkOverlay 诚实文案（不假称内嵌）', () => {
  it('披露文案原样出现「文件本体未修改，预览层水印」', () => {
    render(
      <PreviewWatermarkOverlay user="alice" bundleId="B-42" capturedAt="2026-10-30T13:00:00.000Z">
        <p>结果表格</p>
      </PreviewWatermarkOverlay>,
    );
    expect(screen.getByTestId('preview-watermark-note').textContent).toContain(PREVIEW_WATERMARK_DISCLOSURE);
    expect(screen.getByTestId('preview-watermark-note').textContent).toContain('B-42');
    expect(screen.getByTestId('preview-watermark-note').textContent).toContain('user:alice');
    // 不许漂成「已加水印 / 已嵌入水印」——预览层是对未修改本体的替代，不是内嵌声明。
    expect(screen.getByTestId('preview-watermark-note').textContent).not.toMatch(/已嵌入水印/);
  });

  it('水印层 pointer-events 穿透且不遮挡内容文本可读', () => {
    const { container } = render(
      <PreviewWatermarkOverlay user="alice" bundleId="B-42" capturedAt="t">
        <p>密态成果</p>
      </PreviewWatermarkOverlay>,
    );
    const layer = screen.getByTestId('preview-watermark-layer');
    expect(layer).toHaveStyle({ pointerEvents: 'none' });
    // SVG data-uri 平铺背景（web 页面背景水印观感）
    expect(layer.style.backgroundImage).toContain('data:image/svg+xml');
    expect(screen.getByText('密态成果')).toBeInTheDocument();
    expect(container).toBeInTheDocument();
  });
});
