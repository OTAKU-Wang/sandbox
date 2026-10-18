import { Alert, Button, Card, Empty, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  ArrowLeftOutlined,
  CodeOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  TASK_EXPORT_MODE_OPTIONS,
  UNKNOWN_TEXT,
  describeFlowError,
  taskEnumLabel,
  useNotebook,
  useOpenJupyterEnv,
  type JupyterEnvTicket,
} from '../../services/flow';
import {
  JUPYTER_PUBLIC_PATH,
  fetchJupyterTree,
  type JupyterContentEntry,
} from '../../services/jupyterContents';
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { formatDevTaskTime } from './devTasksModel';
import { formatGibFromMb } from './homeModel';

const { Text } = Typography;

// 图 1（2-3）实测栏宽：左栏 x245–495（≈250）、栏间缝隙 x495–520（25）、右栏吃满。
const LEFT_PANEL_WIDTH = 250;
const COLUMN_GAP = 25;
// 图 1 §3.1 实测文件行距 44（文字行 311/350/394/…）。行高只有这一处来源：文件树是递归的
// FileRows，本文件没有虚拟化列表组件，不存在需要同步的第二处 rowHeight。
const ROW_HEIGHT = 44;

/** 原型标题卡上那句「交付场景」说明：图 1 的副标题位置留给任务真值，这句话收进旁边 ⓘ。 */
const SCENE_NOTE = '交付场景：数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试';

/** 会话目录 = 后端下发的 notebook_path 去掉文件名；反代只允许列这个前缀以内的目录。 */
function sessionDirOf(notebookPath: string | null | undefined): string {
  const segments = String(notebookPath ?? '').split('/').filter(Boolean);
  segments.pop();
  return segments.join('/');
}

/** JupyterLab 深链（与 /api/jupyter/login 302 的落点同一形式；/files/ 与 /tree/ 被反代 403）。 */
function labDeepLink(path: string): string {
  return `${JUPYTER_PUBLIC_PATH}/lab/tree/${path.split('/').filter(Boolean).map(encodeURIComponent).join('/')}`;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** power;
  return `${power === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[power]}`;
}

function sortEntries(entries: JupyterContentEntry[]): JupyterContentEntry[] {
  return [...entries].sort((a, b) => {
    if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
    return a.name.localeCompare(b.name, 'zh-CN');
  });
}

function FileRows({ entries, depth }: { entries: JupyterContentEntry[]; depth: number }) {
  const [collapsed, setCollapsed] = useState<string[]>([]);

  return (
    <>
      {sortEntries(entries).map((entry) => {
        const open = !collapsed.includes(entry.path);
        return (
          <div key={entry.path}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                height: ROW_HEIGHT,
                paddingLeft: 4 + depth * 14,
                minWidth: 0,
              }}
            >
              {entry.isDirectory ? (
                <>
                  <Button
                    type="text"
                    size="small"
                    aria-label={`${open ? '折叠' : '展开'} ${entry.name}`}
                    style={{ width: 18, height: 18, minWidth: 18, padding: 0, color: '#8c8c8c' }}
                    onClick={() =>
                      setCollapsed((prev) => (open ? [...prev, entry.path] : prev.filter((p) => p !== entry.path)))
                    }
                  >
                    {open ? '▾' : '▸'}
                  </Button>
                  {open ? (
                    <FolderOpenOutlined style={{ color: '#faad14' }} />
                  ) : (
                    <FolderOutlined style={{ color: '#faad14' }} />
                  )}
                  <Text ellipsis style={{ minWidth: 0 }}>
                    {entry.name}
                  </Text>
                </>
              ) : (
                <>
                  <span style={{ width: 18 }} />
                  <FileTextOutlined style={{ color: '#8c8c8c' }} />
                  <Typography.Link
                    href={labDeepLink(entry.path)}
                    target="_blank"
                    rel="noreferrer"
                    ellipsis
                    style={{ minWidth: 0 }}
                  >
                    {entry.name}
                  </Typography.Link>
                </>
              )}
            </div>
            {entry.isDirectory && entry.error ? (
              <div style={{ paddingLeft: 26 + depth * 14, color: '#c0392b', fontSize: 12 }}>
                读取失败：{entry.error}
              </div>
            ) : null}
            {entry.isDirectory && open ? <FileRows entries={entry.children ?? []} depth={depth + 1} /> : null}
          </div>
        );
      })}
    </>
  );
}

/**
 * SS-19 在线开发环：反代之后的 JupyterLab（图 1「2-3 调试环境」的外框 + 真 iframe，内核不自造）。
 *
 * 隔离口径由后端下发（dev_env.microvm / sandbox_level），这里只如实转述：
 * 没接 CubeSandbox 的部署就是容器隔离，不能写成 MicroVM（禁伪造铁律）。
 * 左栏文件目录只认后端 Jupyter Contents API 的真实返回：读不到就说读不到，绝不画静态假树。
 *
 * 标题卡副标题与卡头第二行取 GET /notebooks/{id}（useNotebook）的真实任务字段；图上的
 * 「已自动保存 HH:MM:SS」与绿色对勾属红线——后端没有任何自动保存时间可下发，宁缺不造。
 */
export function JupyterDevEnv({ notebookId }: { notebookId: string }) {
  const openEnv = useOpenJupyterEnv();
  const navigate = useNavigate();
  const [ticket, setTicket] = useState<JupyterEnvTicket | null>(null);
  const [labLoaded, setLabLoaded] = useState(false);

  const level = ticket?.sandbox_level ?? null;
  const sessionDir = sessionDirOf(ticket?.notebook_path);
  const editingFile = String(ticket?.notebook_path ?? '').split('/').filter(Boolean).pop() ?? '';

  // 原型副标题「客单价分析 / WebIDE / 结果集导出 / 8G」的四段，逐段取任务真字段；
  // 卡头第二行的语言位与日期位同源。后端没下发的段一律「未记录」，不拿示例值补位。
  const task = useNotebook(notebookId).data;
  const subtitle = [
    task?.title?.trim() || UNKNOWN_TEXT,
    taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, task?.debug_method),
    taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, task?.export_mode),
    formatGibFromMb(task?.debug_memory_mb),
  ].join(' / ');
  const languageText = task?.language?.trim() || UNKNOWN_TEXT;
  const updatedText = formatDevTaskTime(task?.updated_at) ?? UNKNOWN_TEXT;

  const contents = useQuery({
    queryKey: ['jupyter-contents', ticket?.session_id ?? '', sessionDir],
    queryFn: () => fetchJupyterTree(sessionDir),
    enabled: Boolean(ticket && sessionDir && labLoaded),
  });

  const refresh = () => contents.refetch();

  const connection = !ticket
    ? null
    : contents.isError
      ? { color: '#c0392b', label: '● 沙箱已断开' }
      : contents.isSuccess
        ? { color: '#2e9e5b', label: '● 沙箱已连接' }
        : { color: '#8c8c8c', label: '● 连接检测中' };

  const tree = contents.data;

  return (
    <div style={{ width: '100%' }}>
      <Card
        size="small"
        style={{ marginBottom: COLUMN_GAP }}
        title={
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>调试环境</div>
            <Space size={6} style={{ marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {subtitle}
              </Text>
              <Tooltip title={SCENE_NOTE}>
                <InfoCircleOutlined aria-label="交付场景说明" style={{ fontSize: 12, color: '#8c8c8c' }} />
              </Tooltip>
            </Space>
          </div>
        }
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => void navigate('/flow/tasks')}>
            返回列表
          </Button>
        }
      />

      <div style={{ display: 'flex', gap: COLUMN_GAP, alignItems: 'stretch' }}>
        <Card
          size="small"
          title="文件目录"
          style={{
            // 图 1 §3：左栏 y249 一直拉到 ≈1358，与右栏两块等高。原先的 alignSelf: 'flex-start'
            // 让面板按内容收缩，这里去掉它，跟随外层 alignItems: 'stretch' 的行高。
            width: LEFT_PANEL_WIDTH,
            flex: `0 0 ${LEFT_PANEL_WIDTH}px`,
          }}
          extra={
            <Tooltip title="重新读取沙箱目录">
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined />}
                aria-label="刷新文件目录"
                disabled={!ticket || !sessionDir}
                loading={contents.isFetching}
                onClick={refresh}
              />
            </Tooltip>
          }
        >
          {!ticket ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              开发环未打开：沙箱目录要连上本会话的开发环才读得到。
            </Text>
          ) : !sessionDir ? (
            <Alert
              type="error"
              showIcon
              message="文件目录读取失败"
              description="后端未下发本会话的目录前缀（票据里没有 notebook_path 的目录部分），无法列目录。"
            />
          ) : !labLoaded ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              正在连上开发环，随后按 Jupyter Contents API 读取真实目录。
            </Text>
          ) : contents.isPending ? (
            <Space>
              <Spin size="small" />
              <Text type="secondary" style={{ fontSize: 12 }}>
                加载中
              </Text>
            </Space>
          ) : contents.isError ? (
            <Alert
              type="error"
              showIcon
              message="文件目录读取失败"
              description={getErrorMessage(contents.error, '开发环目录接口未返回可用内容')}
              action={
                <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>
                  重试
                </Button>
              }
            />
          ) : tree && tree.fileCount === 0 && (tree.root.children ?? []).length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={<span style={{ fontSize: 12 }}>沙箱目录为空（后端返回空目录，没有任何文件）</span>}
            />
          ) : tree ? (
            <div>
              <FileRows entries={tree.root.children ?? []} depth={0} />
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #f0f0f0', fontSize: 12, color: '#8c8c8c' }}>
                <div>共 {tree.fileCount} 个文件</div>
                <div>占用 {formatBytes(tree.totalBytes)} · 磁盘配额未记录（后端未对开发环工作区下发配额）</div>
                {tree.incomplete && tree.note ? <div>{tree.note}</div> : null}
              </div>
            </div>
          ) : null}
        </Card>

        <Card
          size="small"
          style={{ flex: 1, minWidth: 0 }}
          title={
            <div>
              <Space size={6}>
                <CodeOutlined />
                <Text strong>{editingFile || 'JupyterLab'}</Text>
              </Space>
              {/* 图 1 卡头第二行只落两个有真值来源的位：语言（notebook.language）与更新日期
                  （notebook.updated_at，到分钟）。图上的绿勾 +「已自动保存 14:32:13」没有后端
                  事实支撑，按红线不上屏；语言位是静态标签，不做假的下拉控件。 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2, fontWeight: 400 }}>
                <Tag className="cds-tag-plain" style={{ marginInlineEnd: 0 }}>
                  {languageText}
                </Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  更新于 {updatedText}
                </Text>
              </div>
            </div>
          }
          extra={
            <Space wrap>
              {connection ? (
                <span style={{ color: connection.color, fontSize: 12, whiteSpace: 'nowrap' }}>{connection.label}</span>
              ) : null}
              <Button
                size="small"
                type="primary"
                loading={openEnv.isPending}
                onClick={() =>
                  openEnv.mutate(notebookId, {
                    onSuccess: (next) => {
                      setLabLoaded(false);
                      setTicket(next);
                    },
                  })
                }
              >
                {ticket ? '重新打开开发环' : '打开 Jupyter 开发环'}
              </Button>
            </Space>
          }
        >
          {openEnv.isError ? (
            <Alert
              type="error"
              showIcon
              message="打开开发环失败"
              description={describeFlowError(openEnv.error, '后端未返回可用票据')}
            />
          ) : null}

          {ticket ? (
            <>
              <Space wrap style={{ marginBottom: 8 }}>
                {level ? <Tag color="orange">沙箱等级 {level}</Tag> : null}
                {level && level !== 'l2c' ? <Tag color="red">容器隔离，非 MicroVM</Tag> : null}
              </Space>

              <Alert
                type="warning"
                show-icon
                message="开发环环境说明（不掩饰）"
                description={
                  <div style={{ lineHeight: 1.7 }}>
                    <div>
                      JupyterLab 跑在独立容器里（<Text code>cds-jupyter</Text>），会话等级{' '}
                      <Text code>{level ?? '未下发'}</Text>
                      {level === 'l2c' ? '（CubeSandbox MicroVM）' : '，不是 CubeSandbox MicroVM'}
                      。终端接口已在镜像内关闭，文件与 kernel 由后端按本会话目录前缀限制。
                    </div>
                    <div>
                      当前目录就是本会话的产物目录：只有写在这里的文件会被成果包采集，并且逐个过 T5 出口网关审查与水印；空产物会被如实记为真空。
                    </div>
                    <div>
                      合约授权数据：
                      {ticket.injected.length
                        ? `已注入 ${ticket.injected.length} 个文件到 input/（${ticket.injected
                            .slice(0, 3)
                            .join('、')}${ticket.injected.length > 3 ? '…' : ''}）`
                        : '未注入（input/ 为空）'}
                      。票据 {ticket.ttl}s 内一次性有效，会话 Cookie 8 小时。
                    </div>
                    {ticket.inject_errors?.length ? (
                      <div style={{ color: '#c0392b' }}>
                        供数异常 {ticket.inject_errors.length} 项（后端原话）：
                        {ticket.inject_errors.slice(0, 3).join('；')}
                        {ticket.inject_errors.length > 3 ? '…' : ''}
                      </div>
                    ) : null}
                  </div>
                }
              />
              <iframe
                title="Jupyter 开发环"
                src={ticket.url}
                onLoad={() => setLabLoaded(true)}
                style={{
                  width: '100%',
                  height: '78vh',
                  border: '1px solid rgba(128,128,128,0.35)',
                  borderRadius: 6,
                  background: '#fff',
                }}
              />
              <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 12, color: '#8c8c8c' }}>
                <span>
                  <CodeOutlined /> 沙箱会话 {ticket.session_id} · 编辑中的文件 {editingFile || '未下发'}
                </span>
              </div>
            </>
          ) : (
            <Text type="secondary">
              点「打开 Jupyter 开发环」会先向后端申请一张一次性票据（浏览器 iframe 带不了 Authorization 头），票据换成会话
              Cookie 后直连反代的 JupyterLab，左栏再按同一 Cookie 读真实目录。
            </Text>
          )}
        </Card>
      </div>
    </div>
  );
}
