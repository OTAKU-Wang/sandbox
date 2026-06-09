import { Layout, Grid } from 'antd';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import AppHeader from './Header';
import { useUIStore } from '../../stores/uiStore';

const { Sider, Content } = Layout;

export default function MainLayout() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const screens = Grid.useBreakpoint();
  const collapsedWidth = screens.lg ? 64 : 0;

  return (
    <Layout className="cds-app">
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setSidebarCollapsed}
        collapsedWidth={collapsedWidth}
        breakpoint="lg"
        width={244}
        theme="light"
        className="cds-sidebar"
      >
        <div className="cds-brand">
          <div className="cds-brand-mark">CDS</div>
          {!collapsed && (
            <div className="cds-brand-text">
              <span>密态沙箱系统</span>
              <small>Confidential Data Sandbox</small>
            </div>
          )}
        </div>
        <Sidebar />
      </Sider>
      <Layout>
        <AppHeader />
        <Content className="cds-content">
          <div className="cds-content-inner">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
