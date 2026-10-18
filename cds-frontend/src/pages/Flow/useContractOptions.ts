import { useQuery } from '@tanstack/react-query';
import { contractApi } from '../../services/contractApi';
import type { ContractWithFlow, DeliveryMethod } from '../../services/flow';
import type { Contract } from '../../types/models';

export interface ContractOption {
  id: string;
  title: string;
  contract_no: string;
  status: string;
  /** null = 列表未下发该字段（未知），数组 = 已下发（可为空表示未配置）。 */
  delivery_methods: DeliveryMethod[] | null;
  /** 合约已批准的沙箱规格；memory_mb 是开发任务内存的硬上限（后端 _memory_ceiling 同源）。 */
  sandbox_resource_spec: Record<string, unknown> | null;
  /** null = 列表未下发该字段；数组 = 已下发（空数组表示该合约确实没绑数据产品）。 */
  product_ids: string[] | null;
  /** 图 2-2「交付的使用场景」列：合约用途，后端未下发时为 null。 */
  purpose: string | null;
}

/** 单条合约载荷 → 页面共用的合约选项形状（列表与按 id 取回共用同一份口径）。 */
export function toContractOption(contract: Contract): ContractOption {
  const withFlow = contract as unknown as ContractWithFlow;
  return {
    id: contract.id,
    title: contract.title,
    contract_no: contract.contract_no,
    status: contract.status,
    delivery_methods: Array.isArray(withFlow.delivery_methods) ? withFlow.delivery_methods : null,
    sandbox_resource_spec:
      contract.sandbox_resource_spec && typeof contract.sandbox_resource_spec === 'object' ? contract.sandbox_resource_spec : null,
    product_ids: Array.isArray(contract.product_ids) ? contract.product_ids : null,
    purpose: typeof contract.purpose === 'string' && contract.purpose.trim() ? contract.purpose : null,
  };
}

export function toContractOptions(contracts: Contract[] | undefined): ContractOption[] {
  return (contracts ?? []).map(toContractOption);
}

/** 流通编排各页共用的合约下拉数据（同一 queryKey，多页复用同一份缓存）。 */
export function useContractOptions() {
  return useQuery({
    queryKey: ['contracts', 'flow-options', 100],
    queryFn: () => contractApi.list({ page: 1, page_size: 100 }),
    select: (data) => toContractOptions(data.items),
  });
}

/**
 * 按 id 取回这一条合约（图 2-4 的「我的合约 / 关联数据」两卡的唯一来源）。
 *
 * 为什么不能只查列表：GET /contracts 的 limit 上限是 20（现网 total 22 时列表只回 20 条），
 * 靠列表命中会把「合约在后端、只是不在第一页」误判成「任务没关联合约」。
 */
export function useContractById(contractId: string | null) {
  return useQuery({
    queryKey: ['contracts', 'flow-detail', contractId],
    enabled: contractId !== null,
    queryFn: () => contractApi.get(contractId as string),
    select: (data) => toContractOption(data),
  });
}
