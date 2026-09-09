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

export const SELF_REGISTER_ROLE_OPTIONS = [
  { label: '数商', value: UserRole.DATA_PROVIDER },
  { label: '买方', value: UserRole.BUYER },
];

export const ROLE_GROUPS = {
  productReaders: [UserRole.DATA_PROVIDER, UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  productWriters: [UserRole.DATA_PROVIDER],
  dataResourceManagers: [UserRole.DATA_PROVIDER, UserRole.ADMIN],
  catalogReaders: [UserRole.BUYER, UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  contractReaders: [UserRole.DATA_PROVIDER, UserRole.BUYER, UserRole.REGULATOR, UserRole.ADMIN],
  contractWriters: [UserRole.DATA_PROVIDER],
  sandboxReaders: [UserRole.DATA_PROVIDER, UserRole.BUYER, UserRole.OPERATOR, UserRole.ADMIN],
  sandboxUsers: [UserRole.BUYER],
  devSandboxUsers: [UserRole.DATA_PROVIDER],
  outputControlReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  auditReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  monitoringReaders: [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN],
  k8sOpsReaders: [UserRole.OPERATOR, UserRole.ADMIN],
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
