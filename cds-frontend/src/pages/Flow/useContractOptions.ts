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
}

export function toContractOptions(contracts: Contract[] | undefined): ContractOption[] {
  return (contracts ?? []).map((contract) => {
    const withFlow = contract as unknown as ContractWithFlow;
    return {
      id: contract.id,
      title: contract.title,
      contract_no: contract.contract_no,
      status: contract.status,
      delivery_methods: Array.isArray(withFlow.delivery_methods) ? withFlow.delivery_methods : null,
    };
  });
}

/** 流通编排各页共用的合约下拉数据（同一 queryKey，多页复用同一份缓存）。 */
export function useContractOptions() {
  return useQuery({
    queryKey: ['contracts', 'flow-options', 100],
    queryFn: () => contractApi.list({ page: 1, page_size: 100 }),
    select: (data) => toContractOptions(data.items),
  });
}
