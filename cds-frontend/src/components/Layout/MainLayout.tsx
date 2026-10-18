import { Layout } from 'antd';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import AppHeader from './Header';
import DataplaneBanner from './DataplaneBanner';
import PageCrumb from './PageCrumb';
import { useUIStore } from '../../stores/uiStore';

const { Sider, Content } = Layout;

/**
 * 按原型逐屏 1:1 收口的屏走「界面细节层」（卡头 48 / 表格行 47 / 表头 #f7f9fc / 标签无边框 /
 * 进度条 12px / hover 反馈），判据见 specs/ss-19-full-platform-docker-e2e.md §14 与
 * .cds-e2e/img-specs/。首页用自己的 .cds-home（在 DevHome 根节点），这里只覆盖其余原型屏；
 * 其余页面不继承，免得把没按图收口的屏一起改了尺度。
 */
const DETAIL_SCOPE_ROUTES = ['/flow/tasks', '/notebooks', '/deliveries', '/audit'];

export default function MainLayout() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const { pathname } = useLocation();
  const detailScope = DETAIL_SCOPE_ROUTES.some((r) => pathname === r || pathname.startsWith(`${r}/`));

  return (
    <Layout className="cds-app">
      {/* 顶栏横跨整页宽（原型是 1440×65 的实心蓝条），所以它是外层 Layout 的直接子节点，
          品牌块也从左栏搬到了这里。 */}
      <AppHeader />
      <Layout hasSider>
        {/* 左栏：白底 + 右 1px 分隔线，宽度取原型实测的 ≈220（原型右边界 x≈219）。
            窄屏折到 64 图标栏而不是 0：原型里没有折叠/收起按钮（转录明写导航底部空白、无折叠件），
            所以 trigger={null} 只拆钩子，breakpoint 自动收窄留着——折到 0 菜单就没入口了。 */}
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setSidebarCollapsed}
          collapsedWidth={64}
          breakpoint="lg"
          trigger={null}
          width={220}
          theme="light"
          className="cds-sidebar"
        >
          <Sidebar />
        </Sider>
        <Content className="cds-content">
          {/* 面包屑全面化（2026-10-17 定稿）后不再需要页签带；斜杠面包屑统一由 PageCrumb 渲染。 */}
          <div className={detailScope ? 'cds-content-inner cds-detail' : 'cds-content-inner'}>
            <PageCrumb />
          <DataplaneBanner />
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
