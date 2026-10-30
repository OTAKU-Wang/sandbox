// antd Table locale.emptyText 配置工厂——从 QueryFeedback.tsx 拆出（P4-3 lint 清零）：
// 返回配置对象的函数不是组件，与组件同文件导出会触发 react-refresh/only-export-components。

import { Empty } from 'antd';
import type { ReactNode } from 'react';

export function tableEmpty(description: ReactNode) {
  return {
    emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />,
  };
}
