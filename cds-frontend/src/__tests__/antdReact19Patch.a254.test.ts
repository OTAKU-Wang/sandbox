/**
 * A254：`unstableSetRender` 补丁的延迟卸载不能误伤后来者，也不能把 root 留在宿主上。
 *
 * 台账判定这两条**今天都触发不到**（antd 的 message 是模块级单例，容器不复用），
 * 所以这里不假装修了什么线上故障，只把补丁的契约钉住：
 * ① cleanup 之后宿主上的缓存被删掉（否则下次 render 会在已 unmount 的 root 上 render ⇒ 静默白屏）；
 * ② 等待期内宿主上又起了一次 render 时，旧的那次 cleanup 必须认出自己过期、不动手。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const roots: Array<{ render: ReturnType<typeof vi.fn>; unmount: ReturnType<typeof vi.fn> }> = [];

vi.mock('react-dom/client', () => ({
  createRoot: () => {
    const root = { render: vi.fn(), unmount: vi.fn() };
    roots.push(root);
    return root;
  },
}));

let registered: ((node: unknown, container: Element) => Promise<() => void>) | null = null;

vi.mock('antd', () => ({
  unstableSetRender: (fn: typeof registered) => {
    registered = fn;
  },
}));

beforeEach(async () => {
  roots.length = 0;
  registered = null;
  vi.resetModules();
  await import('../antdReact19Patch');
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('A254：补丁的卸载契约', () => {
  it('注册的是真渲染钩子，node 会落到 createRoot 出来的 root 上', async () => {
    const host = document.createElement('div');
    await registered!({ type: 'div' }, host);
    expect(roots).toHaveLength(1);
    expect(roots[0].render).toHaveBeenCalledTimes(1);
  });

  it('cleanup 之后宿主上的 root 缓存被删除（下次 render 重新建 root，而不是复用已卸载的）', async () => {
    const host = document.createElement('div');
    const cleanup = await registered!({ type: 'div' }, host);
    expect((host as Element & { cdsReactRoot?: unknown }).cdsReactRoot).toBeDefined();

    await cleanup();
    await tick();

    expect(roots[0].unmount).toHaveBeenCalledTimes(1);
    expect((host as Element & { cdsReactRoot?: unknown }).cdsReactRoot).toBeUndefined();
    expect((host as Element & { cdsRenderSeq?: unknown }).cdsRenderSeq).toBeUndefined();

    // 关键后果：同一个宿主再 render 时拿到的是新 root，不会在未 unmount 的旧 root 上 render。
    await registered!({ type: 'span' }, host);
    expect(roots).toHaveLength(2);
  });

  it('等待期内又起了一次 render ⇒ 旧 cleanup 认出自己过期，不动新提示的手', async () => {
    const host = document.createElement('div');
    const firstCleanup = await registered!({ type: 'div' }, host);
    await registered!({ type: 'div' }, host); // 同宿主第二次（复用 root，序号前进）

    await firstCleanup();
    await tick();

    expect(roots[0].unmount).not.toHaveBeenCalled();
    // 缓存仍在：第二次 render 的 root 还得能被后面的 cleanup 正常收走。
    expect((host as Element & { cdsReactRoot?: unknown }).cdsReactRoot).toBe(roots[0]);
  });

  it('最后那次 render 的 cleanup 仍然会把宿主清干净（不能有第二条泄漏路径）', async () => {
    const host = document.createElement('div');
    await registered!({ type: 'div' }, host);
    const lastCleanup = await registered!({ type: 'div' }, host);
    await lastCleanup();
    await tick();
    expect(roots[0].unmount).toHaveBeenCalledTimes(1);
    expect((host as Element & { cdsReactRoot?: unknown }).cdsReactRoot).toBeUndefined();
  });
});
