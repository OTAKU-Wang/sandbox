import { Navigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, type RoleLike } from '../../utils/roles';

interface Props {
  children: React.ReactNode;
  allowedRoles: readonly RoleLike[];
}

export default function RoleRoute({ children, allowedRoles }: Props) {
  const user = useAuthStore((s) => s.user);
  if (!user || !hasAnyRole(user.role, allowedRoles)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
