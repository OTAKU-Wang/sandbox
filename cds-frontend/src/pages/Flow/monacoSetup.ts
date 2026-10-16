// 在线 Notebook 编辑器必须能在专网/内网交付环境离线可用：@monaco-editor/react 默认在运行时从
// jsDelivr 拉 monaco，SS-15 换版后真浏览器 QA 实测该机会一直空白（.monaco-editor 45s 内不出现）。
// 这里改为随包本地加载，并只注册 Python 语言以控制包体。
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js';
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution.js';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker.js?worker';
import { loader } from '@monaco-editor/react';

declare global {
  interface Window {
    MonacoEnvironment?: monaco.Environment;
  }
}

window.MonacoEnvironment = { getWorker: () => new EditorWorker() };

loader.config({ monaco });

export {};
