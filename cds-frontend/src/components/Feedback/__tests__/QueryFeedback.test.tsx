// antd v5 Spin 的 `tip` 只在 nested/fullscreen 模式下渲染，独立 <Spin tip> 被静默忽略——
// CenteredLoading 的加载文案因此从未出现过。真值：提示文字必须在 DOM 里可见。
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CenteredLoading } from '../QueryFeedback';

describe('CenteredLoading', () => {
  it('渲染转圈 + tip 文案（tip 文本自己渲染，不依赖 antd 的 tip prop）', () => {
    const { container } = render(<CenteredLoading tip="加载能力矩阵" />);
    expect(container.querySelector('.ant-spin-spinning')).not.toBeNull();
    expect(screen.getByText('加载能力矩阵')).toBeInTheDocument();
  });

  it('缺省 tip 为 加载中', () => {
    render(<CenteredLoading />);
    expect(screen.getByText('加载中')).toBeInTheDocument();
  });
});
