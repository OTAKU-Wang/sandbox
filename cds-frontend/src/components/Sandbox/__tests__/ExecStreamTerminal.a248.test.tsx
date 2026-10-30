/**
 * A248：流式终端 ExecStreamTerminal 的两个生命周期缺陷。
 * 台账 ocr-verified-findings.md §A248（逐行读过 effect）：
 *  1. 开连竞态：并发守卫只看 `socketRef.current`，而它要等 `await api.post('/auth/ws-ticket')`
 *     之后才被赋值 ⇒ 连点两次「运行」拿两张 ticket、开两条 WebSocket，第一条被覆盖后再也关不掉。
 *  2. disabled 后不断连：终端 effect 依赖只有稳定的 `[closeSocket]`，会话离开 running 让
 *     `disabled` 翻真、组件早退渲染 Alert 把宿主 div 换掉，但 effect 既不重跑也不跑 cleanup
 *     ⇒ xterm 与 WebSocket 都活着往已不存在的元素里写；本组件自己的 disabledReason 写的是
 *     「只有会话所有者可在 running 状态使用」，实现与策略不一致。
 *
 * xterm 用替身：被测行为是 socket 生命周期与 dispose 调用，替身保留了这两个通道。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ExecStreamTerminal from '../ExecStreamTerminal';

const post = vi.fn();
vi.mock('../../../services/api', () => ({ default: { post: (...args: unknown[]) => post(...args) } }));

interface FakeTerm {
  writeln: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
}

const terms: FakeTerm[] = [];

vi.mock('@xterm/xterm', () => ({
  Terminal: class FakeTerminal {
    writeln = vi.fn();
    clear = vi.fn();
    focus = vi.fn();
    loadAddon = vi.fn();
    open = vi.fn();
    dispose = vi.fn(() => {
      this.disposed = true;
    });
    disposed = false;
    constructor() {
      terms.push(this as unknown as FakeTerm);
    }
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class FakeFitAddon {
    fit = vi.fn();
    activate = vi.fn();
  },
}));

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  closed = false;
  sent: string[] = [];
  send = vi.fn((data: string) => {
    this.sent.push(data);
  });

  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  close = vi.fn(() => {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
  });

  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  openNow() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolver) => {
    resolve = resolver;
  });
  return { promise, resolve };
}

let ticket: ReturnType<typeof deferred<{ ticket?: string }>>;

beforeEach(() => {
  FakeWebSocket.instances = [];
  terms.length = 0;
  post.mockReset();
  ticket = deferred<{ ticket?: string }>();
  post.mockReturnValue(ticket.promise);
  vi.stubGlobal('WebSocket', FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function runButton() {
  return screen.getByRole('button', { name: /运\s*行/ });
}

describe('A248 流式终端连接生命周期', () => {
  it('ticket 还在飞的 120ms 里连点两次运行，只允许开一条 WebSocket', async () => {
    render(<ExecStreamTerminal sessionId="s-1" />);
    expect(post).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(runButton());
      fireEvent.click(runButton());
    });

    await act(async () => {
      ticket.resolve({ ticket: 'tk-1' });
    });

    // 第二次点击不得再弄一张 ticket，也不得再开一条流；它必须被明确拒接（终端里给一行提示）。
    expect(post).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances.length, '两次点击各开了一条流，第一条被覆盖后再也关不掉').toBe(1);
    expect(
      terms.at(-1)?.writeln.mock.calls.flat().some((line) => String(line).includes('已有流在运行')),
      '第两次点击被静默吞掉了',
    ).toBe(true);
  });

  it('会话离开 running（disabled 翻真）必须关掉 WebSocket 并销毁 xterm', async () => {
    const view = render(<ExecStreamTerminal sessionId="s-1" />);
    await act(async () => {
      fireEvent.click(runButton());
    });
    await act(async () => {
      ticket.resolve({ ticket: 'tk-1' });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.openNow();
    });
    expect(socket.sent.some((frame) => frame.includes('"start"'))).toBe(true);

    act(() => {
      view.rerender(<ExecStreamTerminal sessionId="s-1" disabled />);
    });

    expect(screen.getByText(/只有会话所有者可在 running 状态使用/)).toBeInTheDocument();
    expect(socket.close, 'disabled 后连接还开着').toHaveBeenCalled();
    expect(terms.at(-1)?.dispose, 'xterm 实例没随宿主 div 一起销毁').toHaveBeenCalled();
  });

  it('关掉的连接不许在重新可用后仍被标成「已连接」', async () => {
    const view = render(<ExecStreamTerminal sessionId="s-1" />);
    await act(async () => {
      fireEvent.click(runButton());
    });
    await act(async () => {
      ticket.resolve({ ticket: 'tk-1' });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => {
      FakeWebSocket.instances[0].openNow();
    });
    expect(screen.getByText('已连接')).toBeInTheDocument();

    act(() => {
      view.rerender(<ExecStreamTerminal sessionId="s-1" disabled />);
    });
    act(() => {
      view.rerender(<ExecStreamTerminal sessionId="s-1" />);
    });

    expect(screen.getByText('已断开')).toBeInTheDocument();
    expect(screen.queryByText('已连接')).not.toBeInTheDocument();
  });
});
