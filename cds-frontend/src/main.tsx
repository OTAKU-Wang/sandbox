import './antdReact19Patch';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';

// A305：#root 缺失/为空时不再静默白屏——用纯 DOM 写出可见错误后终止（此时无法再渲染 React）。
const rootEl = document.getElementById('root');
if (!rootEl) {
  const box = document.createElement('div');
  box.style.cssText = 'padding:48px;font:14px/1.6 system-ui,sans-serif;color:#cf1322;text-align:center';
  box.textContent = '页面加载失败：缺少 #root 挂载点，请检查 index.html 后强制刷新（Ctrl+Shift+R）。';
  document.body.appendChild(box);
} else {
  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
