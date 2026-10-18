// SS-18 M2′「关联数据」看板：把选中合约真正授权了哪些数据如实列出来。
//
// 授权作用域是**合约**而不是任务——后端 contract_fulfillment 只承认
// session.data_product_id ∈ contract.product_ids。所以这里做成只读清单而不是勾选框：
// 「本任务挑这 2 份数据」是一份后端根本不执行的假勾选（禁伪造铁律）。
import { useQuery } from '@tanstack/react-query';
import { Alert, Descriptions, Empty, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { dataProductApi } from '../../services/dataProductApi';
import { SANDBOX_DIRECTORY_ENTRIES, SANDBOX_HARVEST_RULE } from './directoryContract';
import {
  CONTRACT_DATA_RESOLVE_LIMIT,
  buildContractDataSummary,
  type ContractDataItem,
} from './contractDataModel';

const { Text } = Typography;

export interface ContractDataBoardProps {
  contractNo: string | null;
  /** null = 合约未下发授权清单（与「下发了但是空的」是两件事，不得混为一谈）。 */
  productIds: string[] | null;
}

export default function ContractDataBoard({ contractNo, productIds }: ContractDataBoardProps) {
  const ids = productIds ?? [];
  const products = useQuery({
    queryKey: ['contract-data-products', ids],
    enabled: ids.length > 0,
    queryFn: async () => {
      const head = ids.slice(0, CONTRACT_DATA_RESOLVE_LIMIT);
      const settled = await Promise.allSettled(head.map((id) => dataProductApi.get(id)));
      return buildContractDataSummary(ids, settled);
    },
  });

  if (!contractNo) {
    return <Alert type="info" showIcon message="选择关联合约后，这里列出该合约授权进沙箱的数据" />;
  }
  if (productIds === null) {
    return (
      <Alert
        type="warning"
        showIcon
        message={`合约 ${contractNo} 未下发授权数据清单，不替它编一个数字出来`}
      />
    );
  }
  if (productIds.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该合约未绑定数据产品：任务在沙箱内没有可读的授权数据" />;
  }

  const summary = products.data;
  return (
    <div className="cds-task-data-board">
      <Descriptions
        size="small"
        column={2}
        items={[
          { key: 'contract', label: '关联合约', children: contractNo },
          {
            key: 'granted',
            label: '授权数据',
            children: summary
              ? `${summary.granted} 项（已取回 ${summary.resolved}${summary.invisible ? `，无权查看 ${summary.invisible}` : ''}${summary.omitted ? `，超出 ${CONTRACT_DATA_RESOLVE_LIMIT} 项上限未取回 ${summary.omitted}` : ''}）`
              : '取回中…',
          },
        ]}
      />
      {products.isPending ? (
        <Spin size="small" />
      ) : products.isError ? (
        <Alert type="error" showIcon message={`授权数据取不回：${getErrorMessage(products.error)}`} />
      ) : (
        <Tabs
          size="small"
          items={[
            {
              key: 'granted',
              label: '合约授权数据',
              children: (
                <Table<ContractDataItem>
                  size="small"
                  rowKey="id"
                  dataSource={summary?.items ?? []}
                  pagination={false}
                  columns={[
                    {
                      title: '名称',
                      dataIndex: ['product', 'name'],
                      render: (_: unknown, row) => row.product?.name ?? <Text type="warning">无权查看（未发布）</Text>,
                    },
                    { title: '类型', render: (_: unknown, row) => row.product?.product_type ?? '—' },
                    { title: '行数', render: (_: unknown, row) => row.product?.row_count ?? '—' },
                    { title: '安全级', render: (_: unknown, row) => row.product?.security_level ?? '—' },
                    {
                      title: '状态',
                      render: (_: unknown, row) => (row.product ? <Tag>{row.product.status}</Tag> : <Tag>未取回</Tag>),
                    },
                  ]}
                />
              ),
            },
            {
              key: 'paths',
              label: '沙箱内可见路径',
              children: (
                <div>
                  <Table
                    size="small"
                    rowKey="path"
                    dataSource={[...SANDBOX_DIRECTORY_ENTRIES]}
                    pagination={false}
                    columns={[
                      { title: 'VM 内路径', dataIndex: 'path' },
                      { title: '用途与采集面', dataIndex: 'purpose' },
                    ]}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {SANDBOX_HARVEST_RULE}
                  </Text>
                </div>
              ),
            },
          ]}
        />
      )}
      <Text type="secondary" style={{ fontSize: 12 }}>
        数据随合约整体授权进入沙箱（只读），任务不能单独勾选其中几份；能否离开开发环仍由出口网关与合约导出白名单把关。
      </Text>
    </div>
  );
}
