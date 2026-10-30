import { useEffect } from 'react';
import { useCrumbStackStore } from '../../stores/crumbStackStore';

/**
 * 把页内钻取段交给壳层 PageCrumb（栈在 `stores/crumbStackStore`）。
 *
 * A270（2026-10-30）两条都收在这一处：
 * ① key 不再是 `segs.join('|')` 再 `split('|')` 的往返。真值表里的段名（`routeCrumb.ts` 的
 *    中文词 + `OpenAPI导出`/`新建API`/`接口监控`）今天没有一个带 `|`，所以这条是潜伏；
 *    但只要有人把段名写成「A|B」，栈里就会凭空多一段，而页面上没人会去数段数。
 *    现在 key 只做 `JSON.stringify`（结构化、只当依赖签名用），交给 store 的是**原数组**，
 *    全程不再有「拼起来再拆回去」这一步。
 * ② 清空要有归属。这份栈是全局共享的，旧 cleanup 无条件 `setStack([])`：同一屏出现第二个
 *    消费者、或两个钻取视图交替挂载时有一次卸载排在后面，就会把别人刚写的栈清掉。
 *    归属凭据用「我写进去的那个数组实例」：只有当前栈仍然是我写的那一份时才清。
 *    store 的 `setStack(stack)` 签名因此一个字没改（现有测试与调用点不受影响）。
 */
export function useCrumbSegments(segs: string[]) {
  const setStack = useCrumbStackStore((s) => s.setStack);
  const key = JSON.stringify(segs);
  useEffect(() => {
    // key 是自己 stringify 出来的，parse 回来必然等于原数组；不引用外部数组，
    // 也就不需要把 `segs`（每次渲染都是新数组）放进依赖里。
    const stack: string[] = JSON.parse(key);
    setStack(stack);
    return () => {
      if (useCrumbStackStore.getState().stack === stack) setStack([]);
    };
  }, [key, setStack]);
}
