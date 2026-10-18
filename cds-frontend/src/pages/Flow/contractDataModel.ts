// 关联数据看板的纯口径层：把「按 id 逐项取回」的结果压成可核对的计数，取不回的留位而不是消失。
import type { DataProduct } from '../../types/models';

/** 一次最多解析的项数；超出部分如实标注未取回，不假装已全部展示。 */
export const CONTRACT_DATA_RESOLVE_LIMIT = 20;

export interface ContractDataItem {
  id: string;
  /** null = 当前账号取不回这份产品（未发布或无权查看），如实留空而不是丢弃这一行。 */
  product: DataProduct | null;
}

export interface ContractDataSummary {
  items: ContractDataItem[];
  granted: number;
  resolved: number;
  invisible: number;
  omitted: number;
}

/** 纯函数：把按 id 逐条取回的结果压成看板口径，未取回的留位并计数。 */
export function buildContractDataSummary(
  productIds: string[],
  settled: PromiseSettledResult<DataProduct>[],
): ContractDataSummary {
  const items: ContractDataItem[] = settled.map((entry, index) => ({
    id: productIds[index] ?? '',
    product: entry.status === 'fulfilled' ? entry.value : null,
  }));
  const resolved = items.filter((item) => item.product !== null).length;
  return {
    items,
    granted: productIds.length,
    resolved,
    invisible: items.length - resolved,
    omitted: Math.max(0, productIds.length - settled.length),
  };
}
