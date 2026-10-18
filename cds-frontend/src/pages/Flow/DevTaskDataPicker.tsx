// 图 2-2「关联数据」卡：双页签 + 右侧「已选择数据」面板。
//
// 勾选结果不是装饰：它进 POST /notebooks 的 data_resource_ids，服务端按子集注入并在
// 票据审计里记 subset/excluded。因此这里只允许勾选**所选合约已授权**的数据产品——
// 越权项在客户端就置灰并说明原因，而不是让用户提交后再吃一个 422。
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Alert, Button, Empty, Input, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { TablePaginationConfig } from 'antd';
import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { dataProductApi } from '../../services/dataProductApi';
import type { DataProduct } from '../../types/models';
import { CONTRACT_DATA_RESOLVE_LIMIT, buildContractDataSummary } from './contractDataModel';
import type { ContractOption } from './useContractOptions';

const { Text } = Typography;

/** 后端没下发该列时的占位口径（图 2-2 的「组织名称」列 5 行本身就是 `-`）。 */
export const DATA_PICKER_UNKNOWN = '未记录';

/** 取数口径披露：图 2-2 画在筛选带下方一行灰字，这里收进「查询」按钮的 Tooltip（与 2-1 同口径）。 */
export const DATA_PICKER_SCOPE_HINT = '只有所选合约已授权的数据产品可勾选；未授权项与未关联数据资源的产品置灰。';

/** 分页默认档：原型 2-2 分页带写 `10条/页`，与 2-1 的默认档同。 */
export const PICKER_PAGE_SIZE = 10;

/** 双页签（图 2-2 y1067–1100）。默认停在「我购买的数据产品」：只有它可直接勾选，
 *  落到「本节点」会让首屏除已授权项外全部置灰（原型把第二项画成选中态，属未拍板的 P-5）。 */
const PICKER_SCOPES = [
  { key: 'purchased', label: '我购买的数据产品' },
  { key: 'node', label: '本节点登记的数据产品' },
] as const;

type PickerScope = (typeof PICKER_SCOPES)[number]['key'];

/** 药签按图 2-2 y1067..1097 实采：选中=实心 #1b6ff2 白字，未选=白底 #f1f2f4 描边灰字。
 *  首页/详情的浅底药签是另一种形态，这里不拄——图上的选中态是实心块。 */
const PICKER_TAB_BLUE = '#1b6ff2';
const pickerTabStyle = (active: boolean): CSSProperties => ({
  lineHeight: '26px',
  borderRadius: 6,
  border: `1px solid ${active ? PICKER_TAB_BLUE : '#f1f2f4'}`,
  background: active ? PICKER_TAB_BLUE : '#fff',
  color: active ? '#fff' : '#4e5969',
});

export interface DevTaskDataPickerProps {
  contract: ContractOption | null;
  /** null = 未限定（注入合约授权的全部供数）；数组 = 已选数据资源 id（[] = 明确不注入）。 */
  value: string[] | null;
  onChange: (next: string[] | null) => void;
}

interface PickerRow {
  key: string;
  product: DataProduct | null;
  resource_id: string | null;
  authorized: boolean;
  blocked_reason: string | null;
}

function toRow(product: DataProduct | null, id: string, authorized: boolean): PickerRow {
  const resource = product?.resource_id ?? null;
  return {
    key: resource ?? `product:${id}`,
    product,
    resource_id: resource,
    authorized,
    blocked_reason: !authorized
      ? '未纳入所选合约的授权范围，勾选会被服务端拒绝'
      : product === null
        ? '当前账号取不回这份产品，无法确认其数据资源'
        : resource === null
          ? '该产品未关联数据资源，沙箱内无可注入文件'
          : null,
  };
}

export default function DevTaskDataPicker({ contract, value, onChange }: DevTaskDataPickerProps) {
  const [keyword, setKeyword] = useState('');
  const [applied, setApplied] = useState('');
  const [scope, setScope] = useState<PickerScope>('purchased');
  const [nodePage, setNodePage] = useState(1);
  const [pageSize, setPageSize] = useState(PICKER_PAGE_SIZE);
  const productIds = useMemo(() => contract?.product_ids ?? [], [contract]);
  const authorizedIds = useMemo(() => new Set(productIds), [productIds]);

  const purchased = useQuery({
    queryKey: ['dev-task-purchased-products', productIds],
    enabled: productIds.length > 0,
    queryFn: async () => {
      const head = productIds.slice(0, CONTRACT_DATA_RESOLVE_LIMIT);
      const settled = await Promise.allSettled(head.map((id) => dataProductApi.get(id)));
      return buildContractDataSummary(productIds, settled);
    },
  });

  // 服务端分页：页码/页长进 queryKey，换页就是重新取那一页（不是把第 1 页切一刀）。
  // 取下一跳时留住上一页，否则分页器会瞬间掉回 total=0、「前往 X 页」与页码整条闪空。
  const nodeProducts = useQuery({
    queryKey: ['dev-task-node-products', applied, nodePage, pageSize],
    queryFn: () => dataProductApi.list({ q: applied || undefined, page: nodePage, page_size: pageSize }),
    placeholderData: keepPreviousData,
  });

  const applyQuery = () => {
    setApplied(keyword.trim());
    setNodePage(1);
  };
  const resetQuery = () => {
    setKeyword('');
    setApplied('');
    setNodePage(1);
  };

  if (!contract) {
    return <Alert type="info" showIcon message="选择关联合约后，这里列出可勾选的授权数据产品" />;
  }
  if (contract.product_ids === null) {
    return <Alert type="warning" showIcon message={`合约 ${contract.contract_no} 未下发授权数据清单，不替它编一个数字出来`} />;
  }

  const purchasedRows = (purchased.data?.items ?? [])
    .map((item) => toRow(item.product, item.id, true))
    .filter((row) => !applied || (row.product?.name ?? row.key).includes(applied));

  const nodeRows = (nodeProducts.data?.items ?? []).map((product) => toRow(product, product.id, authorizedIds.has(product.id)));

  const selected = value ?? [];
  const selectedNames = new Map<string, string>();
  for (const row of [...purchasedRows, ...nodeRows]) {
    if (row.resource_id) selectedNames.set(row.resource_id, row.product?.name ?? row.resource_id);
  }

  const columns = [
    {
      title: '数据产品名称',
      dataIndex: 'name',
      render: (_: unknown, row: PickerRow) =>
        row.product === null
          ? <Text type="secondary">取不回（id {row.key.replace('product:', '').slice(0, 8)}）</Text>
          : <Link to={`/data-products/${row.product.id}`}>{row.product.name}</Link>,
    },
    { title: '产品简介', dataIndex: 'desc', render: (_: unknown, row: PickerRow) => row.product?.description || DATA_PICKER_UNKNOWN },
    { title: '产品类型', dataIndex: 'type', render: (_: unknown, row: PickerRow) => row.product?.product_type || DATA_PICKER_UNKNOWN },
    { title: '交付方式', dataIndex: 'delivery', render: () => (contract.delivery_methods?.length ? contract.delivery_methods.join('、') : DATA_PICKER_UNKNOWN) },
    { title: '交付的使用场景', dataIndex: 'purpose', render: () => contract.purpose || DATA_PICKER_UNKNOWN },
    { title: '组织名称', dataIndex: 'org', render: () => DATA_PICKER_UNKNOWN },
    {
      title: '操作',
      dataIndex: 'op',
      render: (_: unknown, row: PickerRow) =>
        row.product === null
          ? DATA_PICKER_UNKNOWN
          : <Link to={`/data-products/${row.product.id}`}>详情</Link>,
    },
  ];

  // 分页带（图 2-2 y1399–1430）：`共 N 条｜10条/页｜< 1 >｜前往 X 页`，样式用已收口的 .cds-task-pager。
  const pagerBase: TablePaginationConfig = {
    size: 'small',
    className: 'cds-task-pager',
    pageSize,
    showSizeChanger: true,
    showQuickJumper: true,
    hideOnSinglePage: false,
  };

  // 「我购买的数据产品」是按 id 逐项取回的（上限 CONTRACT_DATA_RESOLVE_LIMIT），后端根本不给总数：
  // 分页条就只说「本页已载入 N 条」，拿 N 冒充「共 N 条」等于自称全量（取不回/无权/超限另有上方 Alert 点名）。
  const purchasedPagination: TablePaginationConfig = {
    ...pagerBase,
    total: purchasedRows.length,
    showTotal: () => `本页已载入 ${purchasedRows.length} 条`,
    onChange: (_page, size) => setPageSize(size),
  };

  // 「本节点登记的数据产品」走服务端分页：后端 total 必须交给分页器。以前不传，antd 退回
  // dataSource.length，节点产品超过单页条数时屏上写「共 20 条」且只剩一页 —— 把「本页已载入」
  // 伪装成全量。现在总页数、「共 N 条」都按后端总数算，并同时写明本页只载入 N 条。
  const nodeTotal = nodeProducts.data?.total ?? null;
  const nodePagination: TablePaginationConfig = {
    ...pagerBase,
    current: nodePage,
    total: nodeTotal ?? nodeRows.length,
    showTotal: () =>
      nodeTotal === null
        ? `本页已载入 ${nodeRows.length} 条`
        : `后端共 ${nodeTotal} 条，本页载入 ${nodeRows.length} 条`,
    onChange: (page, size) => {
      setPageSize(size);
      setNodePage(page);
    },
  };

  const renderTable = (
    rows: PickerRow[],
    pending: boolean,
    emptyText: string,
    pagination: TablePaginationConfig,
  ) => (
    <Table<PickerRow>
      size="small"
      rowKey="key"
      loading={pending}
      dataSource={rows}
      columns={columns}
      pagination={pagination}
      locale={{ emptyText: pending ? ' ' : emptyText }}
      rowSelection={{
        selectedRowKeys: selected,
        preserveSelectedRowKeys: true,
        onChange: (keys) => onChange(keys.map(String)),
        getCheckboxProps: (row) => ({
          disabled: row.blocked_reason !== null || row.resource_id === null,
          name: row.product?.name ?? row.key,
        }),
        renderCell: (_checked, row, _index, origin) =>
          row.blocked_reason ? <Tooltip title={row.blocked_reason}>{origin}</Tooltip> : origin,
      }}
    />
  );

  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Space style={{ display: 'flex', marginBottom: 12 }} wrap>
          <Text>数据产品名称</Text>
          <Input
            allowClear
            placeholder="请输入数据产品名称"
            value={keyword}
            style={{ width: 200 }}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={applyQuery}
          />
          {/*
           * 按钮顺序照图：重置在前、查询在后（图 2-2 筛选行 1:1 直读）。两颗都是白底描边：
           * 重置灰边灰字，查询蓝边蓝字（ghost）——与 2-1 页头的蓝实心查询不同屏不同口径，
           * 2-2 1:1 裁读确认（旧记录「蓝实心」是缩放视图误读，已更正）；取数口径收进 Tooltip。
           */}
          <Button onClick={resetQuery}>重置</Button>
          <Tooltip title={DATA_PICKER_SCOPE_HINT}>
            <Button type="primary" ghost onClick={applyQuery}>查询</Button>
          </Tooltip>
        </Space>
        {value === null && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`未勾选时默认注入合约授权的全部 ${contract.product_ids.length} 项数据；勾选后只注入所选，未选中的不进气`}
          />
        )}
        {purchased.data && purchased.data.omitted + purchased.data.invisible > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`授权 ${purchased.data.granted} 项中：取回 ${purchased.data.resolved} 项，无权查看 ${purchased.data.invisible} 项，超出 ${CONTRACT_DATA_RESOLVE_LIMIT} 项上限未取回 ${purchased.data.omitted} 项`}
          />
        )}
        <Space size={8} wrap style={{ display: 'flex', marginBottom: 12 }}>
          {PICKER_SCOPES.map((tab) => (
            <button
              key={tab.key}
              type="button"
              style={pickerTabStyle(scope === tab.key)}
              data-testid={`task-tab-${tab.key}`}
              aria-pressed={scope === tab.key}
              onClick={() => setScope(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </Space>
        {scope === 'purchased' ? (
          productIds.length === 0
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该合约未绑定数据产品：任务在沙箱内没有可读的授权数据" />
            : purchased.isError
              ? <Alert type="error" showIcon message={`授权数据取不回：${getErrorMessage(purchased.error)}`} />
              : renderTable(purchasedRows, purchased.isPending, '没有已购买的数据产品', purchasedPagination)
        ) : (
          nodeProducts.isError
            ? <Alert type="error" showIcon message={`节点产品列表取不回：${getErrorMessage(nodeProducts.error)}`} />
            : renderTable(nodeRows, nodeProducts.isFetching, '本节点没有登记的数据产品', nodePagination)
        )}
      </div>
      <div className="cds-picker-side">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text strong>{`已选择数据（${selected.length}个）`}</Text>
          <Button type="link" size="small" disabled={selected.length === 0} onClick={() => onChange([])}>清空</Button>
        </Space>
        {value === null ? (
          <Text type="secondary">未做选择：按合约授权全量注入</Text>
        ) : selected.length === 0 ? (
          <Text type="secondary">已清空：本任务不注入任何供数，沙箱 input/ 将为空</Text>
        ) : (
          <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0 }}>
            {selected.map((resourceId) => (
              <li key={resourceId} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 0' }}>
                <Text ellipsis style={{ maxWidth: 170 }}>{selectedNames.get(resourceId) ?? resourceId}</Text>
                <Space size={4}>
                  <Tag bordered={false} style={{ marginInlineEnd: 0 }}>{resourceId.slice(0, 8)}</Tag>
                  <Button type="link" size="small" onClick={() => onChange(selected.filter((id) => id !== resourceId))}>移除</Button>
                </Space>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
