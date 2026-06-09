# 密态沙箱系统（CDS）· 前端实施指南

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-04
> 模块：S2-15 ~ S2-19, S3-06b ~ S3-08b 前端实现
> 上游：frontend-architecture-spec.md, ddd-implementation-spec.md

---

## 1. 项目初始化 (S2-15)

### 1.1 Vite + React + TypeScript 脚手架

```bash
npm create vite@latest cds-frontend -- --template react-ts
cd cds-frontend
npm install
```

### 1.2 依赖清单

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "antd": "^5.12.0",
    "@ant-design/pro-components": "^2.6.0",
    "@ant-design/icons": "^5.2.0",
    "@ant-design/charts": "^2.0.0",
    "zustand": "^4.4.0",
    "axios": "^1.6.0",
    "@tanstack/react-query": "^5.12.0",
    "@monaco-editor/react": "^4.6.0",
    "dayjs": "^1.11.0",
    "crypto-js": "^4.2.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "vite": "^5.0.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "vitest": "^1.0.0",
    "@testing-library/react": "^14.0.0",
    "@testing-library/jest-dom": "^6.0.0",
    "eslint": "^8.50.0",
    "tailwindcss": "^3.4.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0"
  }
}
```

### 1.3 目录结构

```
src/
├── main.tsx                     # 应用入口
├── App.tsx                      # 路由配置
├── vite-env.d.ts                # Vite 类型声明
│
├── components/                  # 公共组件
│   ├── Layout/
│   │   ├── MainLayout.tsx       # 主布局（侧边栏+顶栏+内容区）
│   │   ├── Sidebar.tsx          # 侧边导航菜单
│   │   ├── Header.tsx           # 顶部栏（用户信息+通知+面包屑）
│   │   └── Breadcrumb.tsx       # 面包屑导航
│   ├── Auth/
│   │   ├── ProtectedRoute.tsx   # 路由守卫
│   │   └── RoleGuard.tsx        # 角色权限守卫
│   ├── Common/
│   │   ├── StatusTag.tsx        # 通用状态标签
│   │   ├── DetailDrawer.tsx     # 详情抽屉
│   │   ├── ConfirmAction.tsx    # 确认操作弹窗
│   │   └── LoadingPage.tsx      # 全局加载页
│   └── Charts/
│       ├── AuditTimeline.tsx    # 审计时间线
│       ├── BudgetGauge.tsx      # DP预算仪表盘
│       └── TrendLine.tsx        # 趋势折线图
│
├── pages/                       # 页面组件
│   ├── Auth/
│   │   ├── Login.tsx            # 登录页
│   │   ├── Register.tsx         # 注册页
│   │   └── OrgSetup.tsx         # 组织初始化
│   ├── Dashboard/
│   │   ├── index.tsx            # 仪表盘主页
│   │   └── hooks/useDashboard.ts
│   ├── DataProducts/
│   │   ├── ProductList.tsx      # 产品列表
│   │   ├── ProductDetail.tsx    # 产品详情
│   │   ├── ProductCreate.tsx    # 创建产品（表单）
│   │   ├── ProductEdit.tsx      # 编辑产品
│   │   └── hooks/useDataProducts.ts
│   ├── Contracts/
│   │   ├── ContractList.tsx     # 合约列表
│   │   ├── ContractDetail.tsx   # 合约详情（含签名面板）
│   │   ├── ContractCreate.tsx   # 创建合约
│   │   ├── SignaturePanel.tsx   # SM2 签名面板
│   │   ├── PolicyViewer.tsx     # OPA 策略查看器
│   │   └── hooks/useContracts.ts
│   ├── Sandbox/
│   │   ├── SessionList.tsx      # 沙箱会话列表
│   │   ├── SessionDetail.tsx    # 会话详情（代码编辑+输出）
│   │   ├── CodeEditor.tsx       # Monaco 代码编辑器
│   │   ├── OutputViewer.tsx     # 任务输出查看器
│   │   ├── ResourceMonitor.tsx  # 资源使用监控
│   │   └── hooks/useSandbox.ts
│   ├── Audit/
│   │   ├── AuditLog.tsx         # 审计日志查询
│   │   ├── MerkleTreeViz.tsx    # Merkle树可视化
│   │   ├── VerifyRecord.tsx     # 存证验证页
│   │   └── hooks/useAudit.ts
│   ├── OutputControl/
│   │   ├── InspectionPipeline.tsx # 审查管线可视化
│   │   ├── DLPReport.tsx        # DLP扫描报告
│   │   ├── DPBudgetChart.tsx    # DP预算图表
│   │   └── hooks/useOutputControl.ts
│   └── Monitoring/
│       ├── StatsCards.tsx        # 统计卡片
│       ├── ActivityTrend.tsx     # 活动趋势
│       ├── SecurityAlerts.tsx    # 安全告警列表
│       └── hooks/useMonitoring.ts
│
├── stores/                      # Zustand 状态管理
│   ├── authStore.ts             # 认证状态
│   ├── contractStore.ts         # 合约状态
│   ├── sandboxStore.ts          # 沙箱状态
│   └── uiStore.ts               # UI状态（侧边栏/主题/语言）
│
├── services/                    # API 服务层
│   ├── api.ts                   # Axios 实例 + 拦截器
│   ├── authApi.ts               # 认证 API
│   ├── contractApi.ts           # 合约 API
│   ├── sandboxApi.ts            # 沙箱 API
│   ├── dataProductApi.ts        # 数据产品 API
│   ├── auditApi.ts              # 审计 API
│   └── monitoringApi.ts         # 监控 API
│
├── hooks/                       # 自定义 Hooks
│   ├── useAuth.ts               # 认证 Hook
│   ├── usePagination.ts         # 分页 Hook
│   ├── useDebounce.ts           # 防抖 Hook
│   └── useWebSocket.ts          # WebSocket Hook（沙箱实时状态）
│
├── utils/                       # 工具函数
│   ├── crypto.ts                # SM2/SM3/SM4 客户端工具
│   ├── formatters.ts            # 日期/数字/状态格式化
│   ├── validators.ts            # 表单验证规则
│   └── constants.ts             # 常量定义
│
└── types/                       # TypeScript 类型定义
    ├── api.ts                   # API 请求/响应类型
    ├── models.ts                # 领域模型类型
    ├── enums.ts                 # 枚举类型
    └── contract.ts              # 合约相关类型
```

---

## 2. 认证页面 (S2-16)

### 2.1 登录页

```typescript
// pages/Auth/Login.tsx
import React from 'react';
import { Form, Input, Button, Card, Typography, Select, message } from 'antd';
import { UserOutlined, LockOutlined, BankOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { authApi } from '../../services/authApi';

const { Title, Text } = Typography;

const Login: React.FC = () => {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { email: string; password: string; role: string }) => {
    setLoading(true);
    try {
      const { user, token } = await authApi.login(values);
      login(user, token);
      message.success('登录成功');
      navigate('/');
    } catch (err: any) {
      message.error(err.response?.data?.detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f0f2f5' }}>
      <Card style={{ width: 420 }}>
        <Title level={3} style={{ textAlign: 'center' }}>密态沙箱系统</Title>
        <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginBottom: 24 }}>
          可信数据空间 · 安全数据流通
        </Text>
        <Form onFinish={onFinish} size="large">
          <Form.Item name="email" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
            <Input prefix={<UserOutlined />} placeholder="邮箱地址" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, min: 8, message: '密码至少8位' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item name="role" rules={[{ required: true, message: '请选择角色' }]}>
            <Select
              prefix={<BankOutlined />}
              placeholder="选择角色"
              options={[
                { label: '数据提供商', value: 'provider' },
                { label: '数据使用方', value: 'consumer' },
                { label: '数据开发商', value: 'developer' },
                { label: '运营方', value: 'operator' },
                { label: '安全管理员', value: 'security_admin' },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>登录</Button>
          </Form.Item>
          <div style={{ textAlign: 'center' }}>
            <a onClick={() => navigate('/register')}>注册新账户</a>
          </div>
        </Form>
      </Card>
    </div>
  );
};

export default Login;
```

### 2.2 注册页

```typescript
// pages/Auth/Register.tsx — 关键字段
interface RegisterForm {
  organizationName: string;     // 组织名称
  organizationType: string;     // provider / consumer / developer / operator
  unifiedSocialCreditCode: string; // 统一社会信用代码
  contactName: string;          // 联系人
  contactPhone: string;         // 联系电话
  email: string;                // 邮箱
  password: string;             // 密码
  confirmPassword: string;      // 确认密码
}

// 注册流程：
// 1. 填写组织信息 → 2. 创建管理员账户 → 3. 上传SM2证书(可选) → 4. 完成
```

### 2.3 组织管理页

```typescript
// pages/Auth/OrgSetup.tsx — 组织初始化后的配置页
// 功能：
// - 组织信息查看/编辑
// - 成员管理（邀请/移除/角色变更）
// - SM2证书管理（上传/更新/查看指纹）
// - API密钥管理（生成/吊销）
```

---

## 3. 数据产品页面 (S2-17)

### 3.1 产品列表

```typescript
// pages/DataProducts/ProductList.tsx
// 功能：
// - 搜索栏：关键词 + 数据类型筛选 + 敏感级别筛选 + 状态筛选
// - 表格列：产品名称、数据类型(structured/unstructured/semi-structured)、
//          敏感级别(core/sensitive/restricted/public)、状态(draft/published/archived)、
//          创建时间、操作
// - 操作：查看详情、编辑、发布/下架、删除
// - 创建按钮（provider角色可见）
// - 分页：每页20条，支持页码跳转
```

### 3.2 产品创建表单

```typescript
// pages/DataProducts/ProductCreate.tsx
interface ProductCreateForm {
  // 基本信息
  name: string;                  // 产品名称
  description: string;           // 产品描述
  dataType: 'structured' | 'unstructured' | 'semi-structured';
  sensitivity: 'core' | 'sensitive' | 'restricted' | 'public';

  // 数据源配置
  sourceType: 'database' | 'file' | 'api';
  // structured: 连接信息（host/port/db/table）
  // unstructured: 文件路径/MinIO bucket
  // semi-structured: API endpoint / JSON schema

  // 元数据
  schema?: ColumnDef[];          // structured: 字段定义
  fileFormats?: string[];        // unstructured: 支持格式
  sampleData?: string;           // 样本数据（预览）

  // 安全配置
  allowedOperations: string[];   // 允许的操作类型
  maxOutputRows: number;         // 最大输出行数
  dpBudgetEpsilon?: number;      // DP预算
}

// 表单步骤：
// Step 1: 基本信息 → Step 2: 数据源配置 → Step 3: 安全配置 → Step 4: 预览确认
```

### 3.3 产品详情页

```typescript
// pages/DataProducts/ProductDetail.tsx
// 布局：
// - 顶部：产品名称 + 状态标签 + 操作按钮(编辑/发布/下架)
// - Tab 1: 基本信息（描述、类型、敏感级别、创建时间）
// - Tab 2: 数据预览（structured: 表格视图; unstructured: 文件列表; semi-structured: JSON树）
// - Tab 3: 关联合约（引用该产品的合约列表）
// - Tab 4: 使用统计（查询次数、下载次数、最近使用）
// - Tab 5: 审计日志（该产品的操作记录）
```

---

## 4. 合约页面 (S2-18)

### 4.1 合约列表

```typescript
// pages/Contracts/ContractList.tsx
// 功能：
// - 搜索栏：合约编号 + 状态筛选 + 合约类型筛选
// - 表格列：合约编号(CTR-YYYY-NNN)、类型、状态、提供商、使用方、
//          数据产品、有效期、操作
// - 状态流转可视化：draft → negotiating → signed → active → completed
// - 操作：查看详情、协商、签署、终止
```

### 4.2 合约详情页

```typescript
// pages/Contracts/ContractDetail.tsx
// 布局：
// - 顶部：合约编号 + 状态Tag + 时间线(状态流转历史)
// - 左侧（60%）：
//   - Tab 1: 合约条款（自然语言条款列表）
//   - Tab 2: 策略视图（OPA Rego 语法高亮展示）
//   - Tab 3: 数据产品（关联产品列表+权限矩阵）
// - 右侧（40%）：
//   - 签名面板（SignaturePanel组件）
//   - 沙箱配置（允许的沙箱类型+资源限制）
//   - DP预算仪表盘（已用/剩余 ε）
```

### 4.3 签名面板

```typescript
// pages/Contracts/SignaturePanel.tsx
interface SignaturePanelProps {
  contractId: string;
  contractHash: string;           // SM3(合约条款JSON)
  status: ContractStatus;
  onSigned: () => void;
}

// 签名流程：
// 1. 显示合约哈希（SM3）供用户确认
// 2. 用户点击"签署" → WebCrypto API 生成 SM2 签名
// 3. 提交签名到后端 → POST /api/v1/contracts/{id}/sign
// 4. 后端验证签名 + 更新合约状态 → 返回结果
// 5. 前端刷新合约状态

// SM2签名实现（客户端）：
async function sm2Sign(message: string, privateKey: CryptoKey): Promise<string> {
  // 使用 WebCrypto ECDSA P-256 作为 SM2 的近似实现
  // 生产环境应使用 sm-crypto 或 gm-crypto 库
  const encoded = new TextEncoder().encode(message);
  const signature = await window.crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' },
    privateKey,
    encoded
  );
  return btoa(String.fromCharCode(...new Uint8Array(signature)));
}
```

### 4.4 策略查看器

```typescript
// pages/Contracts/PolicyViewer.tsx
// 功能：
// - 左侧：OPA Rego 策略源码（Monaco Editor, 语法高亮, readOnly）
// - 右侧：
//   - 策略元信息（编译时间、SM3哈希、沙箱模式）
//   - 允许操作列表（绿色标签）
//   - 禁止操作列表（红色标签）
//   - 字段ACL矩阵（表格：字段×操作→允许/拒绝）
//   - 资源限制（CPU/内存/GPU/运行时间）
// - 底部：策略测试面板（输入JSON → 模拟PDP评估 → 显示ALLOW/DENY）
```

---

## 5. 沙箱页面 (S2-19)

### 5.1 会话列表

```typescript
// pages/Sandbox/SessionList.tsx
// 功能：
// - 搜索栏：会话ID + 状态筛选 + 沙箱类型筛选
// - 表格列：会话ID、合约编号、沙箱类型(L1/L2/L3)、状态、
//          创建时间、运行时长、CPU/内存使用、操作
// - 操作：查看详情、暂停/恢复、终止
// - 创建按钮（选择合约→自动选择沙箱类型→配置资源→创建）
```

### 5.2 会话详情页

```typescript
// pages/Sandbox/SessionDetail.tsx
// 布局：
// - 顶部：会话ID + 状态Tag + 沙箱类型Badge + 操作按钮(暂停/终止)
// - 左侧（65%）：
//   - 代码编辑器（CodeEditor组件）
//   - 任务历史列表（已完成任务+状态+耗时）
// - 右侧（35%）：
//   - 资源监控面板（CPU/内存/磁盘/网络 实时图表）
//   - 输出查看器（OutputViewer组件）
//   - 审计事件流（最近操作时间线）
```

### 5.3 代码编辑器

```typescript
// pages/Sandbox/CodeEditor.tsx
interface CodeEditorProps {
  sessionId: string;
  language: 'python' | 'sql' | 'r';
  onSubmit: (code: string, type: 'sql' | 'script' | 'api_call') => void;
  running: boolean;
}

// 功能：
// - Monaco Editor 代码编辑（语法高亮+自动补全）
// - 语言切换（Python/SQL/R）
// - 运行/停止按钮
// - 快捷键：Ctrl+Enter 运行
// - 代码模板（常用查询/脚本片段）
// - 执行结果显示在 OutputViewer
```

### 5.4 输出查看器

```typescript
// pages/Sandbox/OutputViewer.tsx
// 功能：
// - Tab 1: 文本输出（stdout/stderr 分栏）
// - Tab 2: 表格输出（structured data → Ant Design Table）
// - Tab 3: 图表输出（matplotlib/echarts 渲染）
// - Tab 4: 文件输出（下载列表+预览）
// - 底部：执行状态+耗时+资源使用摘要
```

---

## 6. 审计中心 (S3-06b)

### 6.1 审计日志查询

```typescript
// pages/Audit/AuditLog.tsx
// 功能：
// - 筛选栏：事件类型、操作者、时间范围、资源类型、是否已锚定
// - 表格列：时间、事件类型、操作者、资源、操作详情、IP、状态
// - 详情抽屉：完整审计记录JSON + Merkle证明路径
// - 批量操作：选择多条记录 → 批量锚定到区块链
// - 导出：CSV/JSON 导出
```

### 6.2 Merkle树可视化

```typescript
// pages/Audit/MerkleTreeViz.tsx
// 功能：
// - 树形图渲染（D3.js 或 Ant Design Tree）
// - 节点：叶子节点=审计记录哈希，中间节点=父哈希，根节点=链上锚定
// - 交互：点击叶子节点 → 高亮证明路径（从叶子到根）
// - 验证：输入记录ID → 计算哈希 → 验证明路径 → 显示结果
// - 链上信息：区块号、交易哈希、锚定时间
```

---

## 7. 输出管控 (S3-07b)

### 7.1 审查管线可视化

```typescript
// pages/OutputControl/InspectionPipeline.tsx
// 功能：
// - 6阶段管线流程图（横向步骤条）：
//   格式校验 → DLP扫描 → 差分隐私 → 水印 → 签名 → 最终审批
// - 每个阶段显示：状态(通过/失败/跳过)、耗时、发现数
// - 点击阶段 → 展开详情（发现列表+修复建议）
// - DLP扫描详情：匹配的敏感数据类型+位置+脱敏结果
```

### 7.2 DP预算图表

```typescript
// pages/OutputControl/DPBudgetChart.tsx
// 功能：
// - 环形图：已用/剩余 ε 预算
// - 折线图：ε 消耗时间序列
// - 表格：每笔消耗明细（时间、操作、ε值、会话ID）
// - 告警：剩余 < 20% 时红色警告
```

---

## 8. 监控仪表盘 (S3-08b)

### 8.1 统计卡片

```typescript
// pages/Monitoring/StatsCards.tsx
// 4个核心指标卡片：
// 1. 活跃沙箱数（running sessions）+ 趋势箭头
// 2. 今日合约签署数 + 环比变化
// 3. 输出审查通过率 + 告警阈值线
// 4. 链上存证总数 + 最近锚定时间
```

### 8.2 活动趋势

```typescript
// pages/Monitoring/ActivityTrend.tsx
// - 折线图：过去7天/30天的 沙箱创建数/合约签署数/输出审查数
// - 堆叠柱状图：按沙箱类型(L1/L2/L3)分布
// - 热力图：按小时×星期的操作分布
```

### 8.3 安全告警

```typescript
// pages/Monitoring/SecurityAlerts.tsx
// - 告警列表：时间、级别(critical/high/medium/low)、类型、描述、状态
// - 告警类型：DLP命中、配额超限、DP预算耗尽、未授权访问、策略违规
// - 操作：确认告警、查看详情、跳转相关资源
```

---

## 9. API 服务层详细设计

### 9.1 API 端点汇总

```typescript
// 认证
POST   /api/v1/auth/register          // 注册
POST   /api/v1/auth/login             // 登录
POST   /api/v1/auth/refresh           // 刷新token
GET    /api/v1/auth/me                // 当前用户信息

// 数据产品
GET    /api/v1/data-products          // 列表（分页+筛选）
POST   /api/v1/data-products          // 创建
GET    /api/v1/data-products/:id      // 详情
PUT    /api/v1/data-products/:id      // 更新
DELETE /api/v1/data-products/:id      // 删除（owner-only）
POST   /api/v1/data-products/:id/publish   // 发布
POST   /api/v1/data-products/:id/archive   // 下架

// 合约
GET    /api/v1/contracts              // 列表
POST   /api/v1/contracts              // 创建
GET    /api/v1/contracts/:id          // 详情
POST   /api/v1/contracts/:id/sign     // 签署（SM2签名）
POST   /api/v1/contracts/:id/activate // 激活
POST   /api/v1/contracts/:id/terminate // 终止
GET    /api/v1/contracts/:id/policy   // 获取编译后的策略
GET    /api/v1/contracts/:id/terms    // 获取合约条款

// 沙箱
GET    /api/v1/sandbox-sessions       // 会话列表
POST   /api/v1/sandbox-sessions       // 创建会话
GET    /api/v1/sandbox-sessions/:id   // 会话详情
POST   /api/v1/sandbox-sessions/:id/tasks    // 提交任务
GET    /api/v1/sandbox-sessions/:id/output   // 获取输出
POST   /api/v1/sandbox-sessions/:id/pause    // 暂停
POST   /api/v1/sandbox-sessions/:id/resume   // 恢复
POST   /api/v1/sandbox-sessions/:id/terminate // 终止
GET    /api/v1/sandbox-sessions/:id/resources // 资源使用

// 审计
GET    /api/v1/audit/records          // 审计记录列表
GET    /api/v1/audit/records/:id      // 单条详情
POST   /api/v1/audit/anchor           // 批量锚定
GET    /api/v1/audit/verify/:id       // 验证存证
GET    /api/v1/audit/merkle-tree/:tree_id // Merkle树数据

// 输出管控
GET    /api/v1/output/inspections     // 审查记录列表
GET    /api/v1/output/inspections/:id // 审查详情（含6阶段结果）
GET    /api/v1/output/dlp-reports/:session_id // DLP报告
GET    /api/v1/output/dp-budget/:session_id   // DP预算

// 监控
GET    /api/v1/monitoring/stats       // 统计概览
GET    /api/v1/monitoring/trends      // 趋势数据
GET    /api/v1/monitoring/alerts      // 安全告警
```

### 9.2 Axios 拦截器

```typescript
// services/api.ts
import axios from 'axios';
import { useAuthStore } from '../stores/authStore';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截 — 添加认证token
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截 — 统一错误处理
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status;
    if (status === 401) {
      useAuthStore.getState().logout();
      window.location.href = '/login';
    } else if (status === 403) {
      // 无权限 — 显示提示
      console.error('Permission denied:', error.response?.data?.detail);
    } else if (status === 422) {
      // 参数校验错误 — 返回详细错误
      return Promise.reject(error.response?.data);
    }
    return Promise.reject(error.response?.data || error);
  }
);

export default api;
```

---

## 10. 状态管理详细设计

### 10.1 认证状态（带持久化）

```typescript
// stores/authStore.ts — 完整实现已在 frontend-architecture-spec.md 中定义
// 补充：refresh token 逻辑
interface AuthState {
  user: User | null;
  token: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  login: (user: User, token: string, refreshToken: string) => void;
  logout: () => void;
  updateUser: (updates: Partial<User>) => void;
  refreshAuth: () => Promise<void>;
}
```

### 10.2 UI 状态

```typescript
// stores/uiStore.ts
interface UIState {
  sidebarCollapsed: boolean;
  theme: 'light' | 'dark';
  language: 'zh' | 'en';
  toggleSidebar: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setLanguage: (lang: 'zh' | 'en') => void;
}
```

---

## 11. 路由配置

```typescript
// App.tsx
const routes = [
  { path: '/login', element: <Login /> },
  { path: '/register', element: <Register /> },
  {
    path: '/',
    element: <ProtectedRoute><MainLayout /></ProtectedRoute>,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'data-products', element: <ProductList /> },
      { path: 'data-products/create', element: <ProductCreate /> },
      { path: 'data-products/:id', element: <ProductDetail /> },
      { path: 'data-products/:id/edit', element: <ProductEdit /> },
      { path: 'contracts', element: <ContractList /> },
      { path: 'contracts/create', element: <ContractCreate /> },
      { path: 'contracts/:id', element: <ContractDetail /> },
      { path: 'sandbox-sessions', element: <SessionList /> },
      { path: 'sandbox-sessions/:id', element: <SessionDetail /> },
      { path: 'audit', element: <AuditLog /> },
      { path: 'audit/merkle-tree/:treeId', element: <MerkleTreeViz /> },
      { path: 'audit/verify/:recordId', element: <VerifyRecord /> },
      { path: 'output-control', element: <InspectionPipeline /> },
      { path: 'output-control/dlp/:sessionId', element: <DLPReport /> },
      { path: 'output-control/dp-budget', element: <DPBudgetChart /> },
      { path: 'monitoring', element: <MonitoringDashboard /> },
    ],
  },
];
```

---

## 12. 侧边栏菜单结构

```typescript
const menuItems = [
  {
    key: 'dashboard',
    icon: <DashboardOutlined />,
    label: '仪表盘',
    path: '/',
  },
  {
    key: 'data-products',
    icon: <DatabaseOutlined />,
    label: '数据产品',
    children: [
      { key: 'product-list', label: '产品列表', path: '/data-products' },
      { key: 'product-create', label: '创建产品', path: '/data-products/create', roles: ['provider'] },
    ],
  },
  {
    key: 'contracts',
    icon: <FileTextOutlined />,
    label: '合约管理',
    children: [
      { key: 'contract-list', label: '合约列表', path: '/contracts' },
      { key: 'contract-create', label: '创建合约', path: '/contracts/create' },
    ],
  },
  {
    key: 'sandbox',
    icon: <CloudServerOutlined />,
    label: '沙箱管理',
    children: [
      { key: 'session-list', label: '会话列表', path: '/sandbox-sessions' },
    ],
  },
  {
    key: 'audit',
    icon: <AuditOutlined />,
    label: '审计中心',
    roles: ['security_admin', 'operator'],
    children: [
      { key: 'audit-log', label: '审计日志', path: '/audit' },
      { key: 'dp-budget', label: 'DP预算', path: '/output-control/dp-budget' },
    ],
  },
  {
    key: 'output-control',
    icon: <SafetyOutlined />,
    label: '输出管控',
    children: [
      { key: 'inspection', label: '审查管线', path: '/output-control' },
    ],
  },
  {
    key: 'monitoring',
    icon: <MonitorOutlined />,
    label: '监控中心',
    roles: ['operator', 'security_admin'],
    path: '/monitoring',
  },
];
```

---

## 13. 实施计划

| Sprint | 任务 | 预估工时 | 前置依赖 |
|--------|------|---------|---------|
| S2-15 | 项目脚手架+路由+布局 | 4h | 无 |
| S2-16 | 登录/注册/组织管理 | 6h | S2-15 |
| S2-17 | 数据产品 CRUD 页面 | 8h | S2-15 |
| S2-18 | 合约管理+签名+策略查看 | 10h | S2-15, S2-17 |
| S2-19 | 沙箱管理+代码编辑+输出 | 10h | S2-15, S2-18 |
| S3-06b | 审计中心+Merkle可视化 | 6h | S2-15 |
| S3-07b | 输出管控+DLP报告+DP图表 | 6h | S2-15 |
| S3-08b | 监控仪表盘 | 4h | S2-15 |
| **合计** | | **54h** | |

### 关键路径

```
S2-15 (脚手架)
  ├── S2-16 (认证) ──────────────────────┐
  ├── S2-17 (数据产品) ──► S2-18 (合约) ──► S2-19 (沙箱) ──┐
  ├── S3-06b (审计) ────────────────────────────────────────┤
  ├── S3-07b (输出管控) ────────────────────────────────────┤
  └── S3-08b (监控) ────────────────────────────────────────┤
                                                            ▼
                                                    S4-01b (E2E测试)
```

---

## 14. 测试策略

### 14.1 单元测试

- 每个 Zustand store 的 action 测试
- 工具函数测试（crypto.ts, formatters.ts, validators.ts）
- 组件渲染测试（关键交互：表单提交、按钮点击、路由跳转）

### 14.2 集成测试

- API 服务层测试（mock axios）
- 页面级测试（登录→跳转→数据加载→操作）
- 权限测试（不同角色看到不同菜单/按钮）

### 14.3 E2E 场景

| # | 场景 | 覆盖页面 |
|---|------|---------|
| 1 | 注册→创建产品→创建合约→签署→创建沙箱→执行任务→终止 | 全流程 |
| 2 | 审计日志查询→Merkle验证→链上确认 | 审计中心 |
| 3 | 输出审查→DLP报告→DP预算查看 | 输出管控 |
| 4 | 未授权访问→403→跳转登录 | 权限边界 |
| 5 | 监控仪表盘→告警查看→告警确认 | 监控中心 |
