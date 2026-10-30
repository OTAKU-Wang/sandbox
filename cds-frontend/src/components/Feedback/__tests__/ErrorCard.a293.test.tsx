/**
 * A293：ErrorCard 的「能不能重试」只能有一套口径。
 *
 * 旧代码两套并存：`canRetry = status===410 ? false : retryable ?? base.retryable`（吃调用方覆盖），
 * `warning = base.retryable && canRetry`（只看映射表）。调用方显式覆盖一个表里不可重试的码时，
 * 卡片一边长出「重试」按钮、一边继续顶着一枚红 Tag 说这是终态——同一张卡自相矛盾。
 * 修完只有一个派生值：色调 = 这条错误到底可不可重试；按钮还多要一个 `retryAction`
 * （那是「我这儿有动作」，不是对错误性质的第二套断言）。
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ErrorCard from '../ErrorCard';

/** 色调只由这两支边框色读出（金 = 可重试的告警态，红 = 硬失败态）。 */
function borderOf(): string {
  return screen.getByTestId('error-card').getAttribute('style') ?? '';
}
const isWarnTone = (style: string) => /#ffe58f|rgb\(255, 229, 143\)/.test(style);
const isHardTone = (style: string) => /#ffa39e|rgb\(255, 163, 158\)/.test(style);

describe('A293：重试按钮与卡片色调同一套口径', () => {
  it('映射表说不可重试、调用方覆盖成可重试 ⇒ 色调跟着变成告警态（旧写法这里红着长按钮）', () => {
    renderWithProviders(
      <ErrorCard code="SESSION_TERMINATED" message="会话已终止" retryable retryAction={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /重\s*试|重试/ })).toBeInTheDocument();
    expect(isWarnTone(borderOf())).toBe(true);
    expect(isHardTone(borderOf())).toBe(false);
  });

  it('410 契约压过调用方覆盖：按钮不出来，色调也回到硬失败态', () => {
    renderWithProviders(
      <ErrorCard code="SESSION_TERMINATED" message="会话已终止" status={410} retryable retryAction={() => {}} />,
    );
    expect(screen.queryByRole('button', { name: /重\s*试|重试/ })).toBeNull();
    expect(isWarnTone(borderOf())).toBe(false);
  });

  it('映射表说可重试且给了动作 ⇒ 按钮与告警态同时成立（正例不受收紧影响）', () => {
    renderWithProviders(
      <ErrorCard code="OPERATION_LOCKED" message="操作被锁" retryAction={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /重\s*试|重试/ })).toBeInTheDocument();
    expect(isWarnTone(borderOf())).toBe(true);
  });

  it('可重试但没有动作时仍然不出按钮，色调仍如实说「这条本来可重试」', () => {
    renderWithProviders(<ErrorCard code="OPERATION_LOCKED" message="操作被锁" />);
    expect(screen.queryByRole('button', { name: /重\s*试|重试/ })).toBeNull();
    expect(isWarnTone(borderOf())).toBe(true);
  });

  it('硬失败态（表里不可重试、无覆盖）保持红色，没有被一起改成告警', () => {
    renderWithProviders(<ErrorCard code="SESSION_TERMINATED" message="会话已终止" status={410} />);
    expect(isHardTone(borderOf())).toBe(true);
  });
});
