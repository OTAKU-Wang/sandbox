# 密态沙箱系统（CDS）· 前端架构技术规格

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-03
> 模块：S2-15 ~ S2-19 前端实现
> 参考：React 18, TypeScript, Ant Design 5, Zustand

---

## 1. 概述

CDS 前端采用 React 18 + TypeScript + Ant Design 5 技术栈，提供数据产品管理、合约签署、沙箱操作、审计查询等功能的可视化界面。

### 1.1 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 框架 | React | 18.x | UI 框架 |
| 语言 | TypeScript | 5.x | 类型安全 |
| UI 组件 | Ant Design | 5.x | 企业级组件库 |
| 状态管理 | Zustand | 4.x | 轻量级状态管理 |
| 路由 | React Router | 6.x | SPA 路由 |
| 请求 | Axios + React Query | - | API 请求 + 缓存 |
| 表格 | ProTable | 2.x | 高级表格组件 |
| 表单 | ProForm | 2.x | 高级表单组件 |
| 图标 | Ant Design Icons | 5.x | 图标库 |
| 加密 | WebCrypto API | - | 客户端 SM2 签名 |

### 1.2 目录结构

```
src/
├── components/          # 公共组件
│   ├── Layout/          # 布局组件
│   ├── Auth/            # 认证组件
│   └── Common/          # 通用组件
├── pages/               # 页面组件
│   ├── Dashboard/       # 仪表盘
│   ├── DataProducts/    # 数据产品管理
│   ├── Contracts/       # 合约管理
│   ├── Sandbox/         # 沙箱管理
│   ├── Audit/           # 审计中心
│   └── Identity/        # 身份管理
├── stores/              # Zustand 状态管理
├── services/            # API 服务层
├── hooks/               # 自定义 Hooks
├── utils/               # 工具函数
├── types/               # TypeScript 类型定义
└── App.tsx              # 应用入口
```

---

## 2. 状态管理

### 2.1 认证状态

```typescript
// stores/authStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface User {
  id: string;
  name: string;
  email: string;
  role: 'provider' | 'consumer' | 'operator' | 'security_admin';
  organizationId: string;
  organizationName: string;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  updateUser: (user: Partial<User>) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      login: (user, token) => set({ user, token, isAuthenticated: true }),
      logout: () => set({ user: null, token: null, isAuthenticated: false }),
      updateUser: (updates) =>
        set((state) => ({
          user: state.user ? { ...state.user, ...updates } : null,
        })),
    }),
    {
      name: 'auth-storage',
    }
  )
);
```

### 2.2 合约状态

```typescript
// stores/contractStore.ts
import { create } from 'zustand';

interface Contract {
  id: string;
  contractNo: string;
  status: 'draft' | 'negotiating' | 'signed' | 'active' | 'terminated';
  providerId: string;
  buyerId: string;
  dataProductId: string;
  terms: ContractTerms;
  createdAt: string;
  expiresAt: string;
}

interface ContractState {
  contracts: Contract[];
  currentContract: Contract | null;
  loading: boolean;
  error: string | null;
  fetchContracts: () => Promise<void>;
  fetchContract: (id: string) => Promise<void>;
  createContract: (data: CreateContractDTO) => Promise<Contract>;
  signContract: (id: string, signature: string) => Promise<void>;
}

export const useContractStore = create<ContractState>((set, get) => ({
  contracts: [],
  currentContract: null,
  loading: false,
  error: null,
  fetchContracts: async () => {
    set({ loading: true, error: null });
    try {
      const contracts = await contractApi.list();
      set({ contracts, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },
  fetchContract: async (id) => {
    set({ loading: true, error: null });
    try {
      const contract = await contractApi.get(id);
      set({ currentContract: contract, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },
  createContract: async (data) => {
    set({ loading: true, error: null });
    try {
      const contract = await contractApi.create(data);
      set((state) => ({
        contracts: [...state.contracts, contract],
        loading: false,
      }));
      return contract;
    } catch (error) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },
  signContract: async (id, signature) => {
    set({ loading: true, error: null });
    try {
      await contractApi.sign(id, signature);
      await get().fetchContract(id);
      set({ loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },
}));
```

### 2.3 沙箱状态

```typescript
// stores/sandboxStore.ts
import { create } from 'zustand';

interface SandboxSession {
  id: string;
  contractId: string;
  status: 'provisioning' | 'ready' | 'running' | 'paused' | 'terminated';
  sandboxType: 'l1_tee' | 'l2_microvm' | 'l3_container';
  tasks: Task[];
  resourceUsage: ResourceUsage;
  createdAt: string;
}

interface Task {
  id: string;
  type: 'sql' | 'script' | 'api_call';
  code: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  result: TaskResult | null;
}

interface SandboxState {
  sessions: SandboxSession[];
  currentSession: SandboxSession | null;
  loading: boolean;
  error: string | null;
  fetchSessions: () => Promise<void>;
  createSession: (data: CreateSessionDTO) => Promise<SandboxSession>;
  executeTask: (sessionId: string, task: TaskDTO) => Promise<TaskResult>;
  terminateSession: (sessionId: string) => Promise<void>;
}

export const useSandboxStore = create<SandboxState>((set, get) => ({
  sessions: [],
  currentSession: null,
  loading: false,
  error: null,
  // ... 实现类似 contractStore
}));
```

---

## 3. 页面组件

### 3.1 仪表盘页面

```typescript
// pages/Dashboard/index.tsx
import React from 'react';
import { Row, Col, Card, Statistic, Table, Tag } from 'antd';
import {
  FileTextOutlined,
  CloudServerOutlined,
  SafetyOutlined,
  AuditOutlined,
} from '@ant-design/icons';
import { useDashboard } from './hooks/useDashboard';

const Dashboard: React.FC = () => {
  const { stats, recentActivities, loading } = useDashboard();

  return (
    <div className="dashboard">
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic
              title="数据产品"
              value={stats.dataProducts}
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="活跃合约"
              value={stats.activeContracts}
              prefix={<SafetyOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="运行中沙箱"
              value={stats.runningSessions}
              prefix={<CloudServerOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="审计记录"
              value={stats.auditRecords}
              prefix={<AuditOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card title="最近活动" style={{ marginTop: 16 }}>
        <Table
          dataSource={recentActivities}
          columns={[
            { title: '事件', dataIndex: 'eventType', key: 'eventType' },
            { title: '操作者', dataIndex: 'actor', key: 'actor' },
            { title: '时间', dataIndex: 'timestamp', key: 'timestamp' },
            {
              title: '状态',
              dataIndex: 'status',
              key: 'status',
              render: (status: string) => (
                <Tag color={status === 'success' ? 'green' : 'red'}>
                  {status}
                </Tag>
              ),
            },
          ]}
          loading={loading}
        />
      </Card>
    </div>
  );
};

export default Dashboard;
```

### 3.2 数据产品页面

```typescript
// pages/DataProducts/ProductList.tsx
import React from 'react';
import { Table, Button, Space, Tag, Input } from 'antd';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useDataProducts } from './hooks/useDataProducts';

const ProductList: React.FC = () => {
  const navigate = useNavigate();
  const { products, loading, pagination, fetchProducts } = useDataProducts();

  const columns = [
    {
      title: '产品名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: any) => (
        <a onClick={() => navigate(`/data-products/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: '数据类型',
      dataIndex: 'dataType',
      key: 'dataType',
      render: (type: string) => (
        <Tag color={type === 'structured' ? 'blue' : type === 'unstructured' ? 'green' : 'orange'}>
          {type}
        </Tag>
      ),
    },
    {
      title: '敏感级别',
      dataIndex: 'sensitivity',
      key: 'sensitivity',
      render: (level: string) => (
        <Tag color={level === 'core' ? 'red' : level === 'sensitive' ? 'orange' : 'green'}>
          {level}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'published' ? 'green' : 'default'}>
          {status}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
    },
  ];

  return (
    <div className="product-list">
      <Space style={{ marginBottom: 16 }}>
        <Input
          placeholder="搜索产品"
          prefix={<SearchOutlined />}
          style={{ width: 300 }}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate('/data-products/create')}
        >
          创建产品
        </Button>
      </Space>

      <Table
        dataSource={products}
        columns={columns}
        loading={loading}
        pagination={pagination}
        onChange={(pag) => fetchProducts({ page: pag.current, pageSize: pag.pageSize })}
      />
    </div>
  );
};

export default ProductList;
```

### 3.3 合约签署组件

```typescript
// pages/Contracts/SignaturePanel.tsx
import React, { useState } from 'react';
import { Card, Button, Alert, Spin, Typography } from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { useContractStore } from '../../stores/contractStore';
import { sm2Sign } from '../../utils/crypto';

const { Text } = Typography;

interface SignaturePanelProps {
  contractId: string;
  contractHash: string;
  onSigned: () => void;
}

const SignaturePanel: React.FC<SignaturePanelProps> = ({
  contractId,
  contractHash,
  onSigned,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { signContract } = useContractStore();

  const handleSign = async () => {
    setLoading(true);
    setError(null);

    try {
      // 1. 使用 WebCrypto API 进行 SM2 签名
      const signature = await sm2Sign(contractHash);

      // 2. 调用 API 提交签名
      await signContract(contractId, signature);

      onSigned();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card title="合约签名">
      {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} />}

      <div style={{ marginBottom: 16 }}>
        <Text strong>合约哈希：</Text>
        <Text code>{contractHash}</Text>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Text>签名算法：SM2</Text>
      </div>

      <Button
        type="primary"
        icon={<SafetyCertificateOutlined />}
        loading={loading}
        onClick={handleSign}
      >
        签署合约
      </Button>
    </Card>
  );
};

export default SignaturePanel;
```

### 3.4 代码编辑器组件

```typescript
// pages/Sandbox/CodeEditor.tsx
import React, { useState } from 'react';
import { Card, Select, Button, Space, Tag } from 'antd';
import { PlayCircleOutlined, StopOutlined } from '@ant-design/icons';
import Editor from '@monaco-editor/react';

interface CodeEditorProps {
  language: 'python' | 'sql' | 'r';
  value: string;
  onChange: (value: string) => void;
  onSubmit: (code: string) => void;
  running?: boolean;
}

const CodeEditor: React.FC<CodeEditorProps> = ({
  language,
  value,
  onChange,
  onSubmit,
  running = false,
}) => {
  const [selectedLanguage, setSelectedLanguage] = useState(language);

  const handleRun = () => {
    onSubmit(value);
  };

  return (
    <Card
      title="代码编辑器"
      extra={
        <Space>
          <Select
            value={selectedLanguage}
            onChange={setSelectedLanguage}
            options={[
              { label: 'Python', value: 'python' },
              { label: 'SQL', value: 'sql' },
              { label: 'R', value: 'r' },
            ]}
            style={{ width: 120 }}
          />
          <Tag color={running ? 'processing' : 'default'}>
            {running ? '运行中' : '就绪'}
          </Tag>
          <Button
            type="primary"
            icon={running ? <StopOutlined /> : <PlayCircleOutlined />}
            onClick={handleRun}
            loading={running}
          >
            {running ? '停止' : '运行'}
          </Button>
        </Space>
      }
    >
      <Editor
        height="400px"
        language={selectedLanguage}
        value={value}
        onChange={(val) => onChange(val || '')}
        theme="vs-dark"
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
        }}
      />
    </Card>
  );
};

export default CodeEditor;
```

---

## 4. API 服务层

```typescript
// services/api.ts
import axios from 'axios';
import { useAuthStore } from '../stores/authStore';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// 请求拦截器 — 添加认证 token
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器 — 处理错误
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout();
      window.location.href = '/login';
    }
    return Promise.reject(error.response?.data || error);
  }
);

export default api;

// services/contractApi.ts
import api from './api';

export const contractApi = {
  list: (params?: any) => api.get('/contracts', { params }),
  get: (id: string) => api.get(`/contracts/${id}`),
  create: (data: any) => api.post('/contracts', data),
  sign: (id: string, signature: string) =>
    api.post(`/contracts/${id}/sign`, { signature }),
  getPolicy: (id: string, mode: string) =>
    api.get(`/contracts/${id}/policy`, { params: { mode } }),
};

// services/sandboxApi.ts
export const sandboxApi = {
  list: (params?: any) => api.get('/sandbox-sessions', { params }),
  get: (id: string) => api.get(`/sandbox-sessions/${id}`),
  create: (data: any) => api.post('/sandbox-sessions', data),
  terminate: (id: string) => api.post(`/sandbox-sessions/${id}/terminate`),
  executeTask: (id: string, task: any) =>
    api.post(`/sandbox-sessions/${id}/tasks`, task),
  getOutput: (id: string) => api.get(`/sandbox-sessions/${id}/output`),
};
```

---

## 5. 路由配置

```typescript
// App.tsx
import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MainLayout from './components/Layout/MainLayout';
import Login from './pages/Auth/Login';
import Register from './pages/Auth/Register';
import Dashboard from './pages/Dashboard';
import ProductList from './pages/DataProducts/ProductList';
import ProductDetail from './pages/DataProducts/ProductDetail';
import ProductCreate from './pages/DataProducts/ProductCreate';
import ContractList from './pages/Contracts/ContractList';
import ContractDetail from './pages/Contracts/ContractDetail';
import SessionList from './pages/Sandbox/SessionList';
import SessionDetail from './pages/Sandbox/SessionDetail';
import AuditLog from './pages/Audit/AuditLog';
import { useAuthStore } from './stores/authStore';

const queryClient = new QueryClient();

const PrivateRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" />;
};

const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route
            path="/"
            element={
              <PrivateRoute>
                <MainLayout />
              </PrivateRoute>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="data-products" element={<ProductList />} />
            <Route path="data-products/create" element={<ProductCreate />} />
            <Route path="data-products/:id" element={<ProductDetail />} />
            <Route path="contracts" element={<ContractList />} />
            <Route path="contracts/:id" element={<ContractDetail />} />
            <Route path="sandbox-sessions" element={<SessionList />} />
            <Route path="sandbox-sessions/:id" element={<SessionDetail />} />
            <Route path="audit" element={<AuditLog />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

export default App;
```

---

## 6. 测试用例

```typescript
// __tests__/Dashboard.test.tsx
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Dashboard from '../pages/Dashboard';

const queryClient = new QueryClient();

const renderWithProviders = (component: React.ReactElement) => {
  return render(
    <QueryClientProvider client={queryClient}>
      {component}
    </QueryClientProvider>
  );
};

describe('Dashboard', () => {
  it('renders statistics cards', async () => {
    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText('数据产品')).toBeInTheDocument();
      expect(screen.getByText('活跃合约')).toBeInTheDocument();
      expect(screen.getByText('运行中沙箱')).toBeInTheDocument();
      expect(screen.getByText('审计记录')).toBeInTheDocument();
    });
  });
});
```

---

## 7. 部署配置

```json
// package.json
{
  "name": "cds-frontend",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest",
    "lint": "eslint src --ext ts,tsx"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "antd": "^5.12.0",
    "@ant-design/pro-components": "^2.6.0",
    "@ant-design/icons": "^5.2.0",
    "zustand": "^4.4.0",
    "axios": "^1.6.0",
    "@tanstack/react-query": "^5.12.0",
    "@monaco-editor/react": "^4.6.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "vite": "^5.0.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "vitest": "^1.0.0",
    "@testing-library/react": "^14.0.0",
    "eslint": "^8.50.0"
  }
}
```

---

## 8. 监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| `page_load_time_ms` | 页面加载时间 | > 3000ms |
| `api_response_time_ms` | API 响应时间 | > 1000ms |
| `error_rate` | 前端错误率 | > 1% |
| `user_session_duration` | 用户会话时长 | - |
| `concurrent_users` | 并发用户数 | > 100 |
