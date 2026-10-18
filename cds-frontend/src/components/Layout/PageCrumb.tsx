import { useLocation } from 'react-router-dom';
import { useCrumbStackStore } from '../../stores/crumbStackStore';
import { getRouteCrumb } from './routeCrumb';

/**
 * 全站面包屑（用户 2026-10-17 定稿：「平台全面使用面包屑导航 但是要考虑通用性」）。
 *
 * 通用性 = 页面不再各写一份面包屑：
 * - 静态段来自路由注册表 `routeCrumb.ts`（唯一真值表）；
 * - 页内钻取段由 `useCrumbSegments` 写入 `crumbStackStore`（卸载自动清空）。
 *
 * 口径变更登记：开发系四屏那条带子在原型 PNG 上画的是「已打开页签」（2026-10-16 口径，
 * 曾按此实现过 `pills` 样式），2026-10-18 用户裁定**彻底删除页签带**，本文件只剩斜杠一种
 * 渲染（台账 §10.2 / spec §23）。像素真值上的差异照旧记录，不声称原型画的就是斜杠。
 */
export default function PageCrumb() {
  const { pathname } = useLocation();
  const stack = useCrumbStackStore((s) => s.stack);
  const crumb = getRouteCrumb(pathname);
  const segs = [...crumb.segs, ...stack];

  if (crumb.style === 'none' || segs.length === 0) return null;

  return (
    <nav className="cds-crumb-slash" aria-label="页面位置">
      {/* 图 4-1 §1.2：斜杠式前置文件夹描边图标（13×12，#afb5c0），3-1 同坐标同形；
          形按墨迹用 mask 画（与 4-1 子代理逐行读数同源），不用 anticon 字形凑。 */}
      <span className="cds-crumb-lead" aria-hidden />
      {segs.map((seg, index) => {
        const last = index === segs.length - 1;
        return [
          index > 0 ? <span key={`sep-${index}`} className="cds-crumb-sep">/</span> : null,
          <span key={`${index}-${seg}`} className={last ? 'is-current' : undefined} data-testid={last ? 'crumb-current' : undefined}>
            {seg}
          </span>,
        ];
      })}
    </nav>
  );
}
