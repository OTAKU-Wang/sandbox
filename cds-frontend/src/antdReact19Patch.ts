// antd 5.29 的静态 message/notification/Modal.confirm 通过 React 19 已移除的 ReactDOM.render 挂载，
// 不打补丁时提示会静默丢失（全站 212 处 message.* 调用点无 DOM、无报错）。
// 这里用 antd 自己导出的 unstableSetRender 复现官方 @ant-design/v5-patch-for-react-19 的实现，避免新增依赖。
import { unstableSetRender } from 'antd';
import type { ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

unstableSetRender((node: ReactElement, container: Element | DocumentFragment) => {
  const host = container as Element & { cdsReactRoot?: Root };
  host.cdsReactRoot ??= createRoot(container as Element);
  const { cdsReactRoot: root } = host;
  root.render(node);
  return async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    root.unmount();
  };
});
