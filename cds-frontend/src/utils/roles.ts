import { UserRole } from '../types/enums';

export type RoleLike = UserRole | string | null | undefined;

const ROLE_VALUES = Object.values(UserRole) as string[];

const LEGACY_ROLE_ALIASES: Record<string, UserRole> = {
  provider: UserRole.DATA_PROVIDER,
  consumer: UserRole.BUYER,
  developer: UserRole.DATA_PROVIDER,
  security_admin: UserRole.REGULATOR,
};

export const ROLE_LABELS: Record<UserRole, string> = {
  [UserRole.DATA_PROVIDER]: '数商',
  [UserRole.BUYER]: '买方',
  [UserRole.OPERATOR]: '运营方',
  [UserRole.REGULATOR]: '监管方',
  [UserRole.ADMIN]: '系统管理员',
};

// PZ-10：自助注册不可选角色（后端 ALLOWED_SELF_REGISTER_ROLES={admin}，省略即默认买方），
// 旧的 SELF_REGISTER_ROLE_OPTIONS（数商/买方）与后端契约冲突已删除，勿恢复。

export const ROLE_GROUPS = {
  productReaders: [UserRole.DATA_PROVIDER, UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  productWriters: [UserRole.DATA_PROVIDER],
  dataResourceManagers: [UserRole.DATA_PROVIDER, UserRole.ADMIN],
  catalogReaders: [UserRole.BUYER, UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  contractReaders: [UserRole.DATA_PROVIDER, UserRole.BUYER, UserRole.REGULATOR, UserRole.ADMIN],
  contractWriters: [UserRole.DATA_PROVIDER],
  sandboxReaders: [UserRole.DATA_PROVIDER, UserRole.BUYER, UserRole.OPERATOR, UserRole.ADMIN],
  // 2026-10-30 拍板：沙箱会话按业务流转放开——合同双方（买方/数商）均可开
  // （后端 sandbox_sessions/dev_sandbox 已同步改为合同当事方判定），组级门相应
  // 放开两角色；细粒度「是否该合同的当事方」仍由后端把关，前端不重复判。
  sandboxUsers: [UserRole.BUYER, UserRole.DATA_PROVIDER],
  devSandboxUsers: [UserRole.DATA_PROVIDER],
  // 在线 Notebook 开发与负载发布：会话按合同当事方放开（后端 2026-10-30 口径，
  // 非买方角色会被后端 403），所以页面必须对 BUYER/数商 开放；数商保留入口
  // 是为了自己产品的开发沙箱。
  flowDevUsers: [UserRole.DATA_PROVIDER, UserRole.BUYER],
  outputControlReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  auditReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  monitoringReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  identityManagers: [UserRole.OPERATOR, UserRole.ADMIN],
  connectorManagers: [UserRole.OPERATOR, UserRole.ADMIN],
  federationUsers: [UserRole.BUYER, UserRole.OPERATOR, UserRole.ADMIN],
  trainingUsers: [UserRole.BUYER],
} as const;

export function normalizeUserRole(role: RoleLike): UserRole | undefined {
  if (!role) return undefined;
  const normalized = LEGACY_ROLE_ALIASES[role] ?? role;
  return ROLE_VALUES.includes(normalized) ? (normalized as UserRole) : undefined;
}

export function hasAnyRole(role: RoleLike, allowedRoles: readonly RoleLike[]): boolean {
  const normalizedRole = normalizeUserRole(role);
  if (!normalizedRole) return false;
  return allowedRoles.some((allowedRole) => normalizeUserRole(allowedRole) === normalizedRole);
}
