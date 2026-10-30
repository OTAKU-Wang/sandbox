// antd 5.29 的静态 message/notification/Modal.confirm 通过 React 19 已移除的 ReactDOM.render 挂载，
// 不打补丁时提示会静默丢失（全站 212 处 message.* 调用点无 DOM、无报错）。
// 这里用 antd 自己导出的 unstableSetRender 复现官方 @ant-design/v5-patch-for-react-19 的实现，避免新增依赖。
//
// A254（2026-10-30）：卸载时必须把挂在宿主上的 root 一起清掉，并让「延迟卸载」期间新起的
// render 不被这次 cleanup 误伤。台账判定这两条**当前都触发不到**——antd 的 message 是模块级
// 单例（node_modules/antd/es/message/index.js:82-91：首次调用建持有者并 reactRender 一次，
// 后续消息走 instance/sync，不再过这个渲染钩子），容器不是复用的。但补丁是承重的（不打它
// 全站提示静默丢失），所以这两条按台账给的建议便宜地做掉：将来 antd 换成按容器渲染，
// 这里不会变成「在已 unmount 的 root 上 render」那种静默白屏。
import { unstableSetRender } from 'antd';
import type { ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

type PatchedHost = Element & { cdsReactRoot?: Root; cdsRenderSeq?: number };

unstableSetRender((node: ReactElement, container: Element | DocumentFragment) => {
  const host = container as PatchedHost;
  host.cdsReactRoot ??= createRoot(container as Element);
  const { cdsReactRoot: root } = host;
  // 每次 render 领一个序号；延迟卸载只对得上号的那次生效。
  const seq = (host.cdsRenderSeq ?? 0) + 1;
  host.cdsRenderSeq = seq;
  root.render(node);
  return async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    // 等待期间宿主上又起了一次 render（换了 root 或序号已前进）⇒ 这次不许动手，
    // 否则会把新提示连 DOM 一起卸载掉。
    if (host.cdsReactRoot !== root || host.cdsRenderSeq !== seq) return;
    delete host.cdsReactRoot;
    delete host.cdsRenderSeq;
    root.unmount();
  };
});
