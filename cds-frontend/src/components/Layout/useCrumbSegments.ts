import { useEffect } from 'react';
import { useCrumbStackStore } from '../../stores/crumbStackStore';

export function useCrumbSegments(segs: string[]) {
  const setStack = useCrumbStackStore((s) => s.setStack);
  const key = segs.join('|');
  useEffect(() => {
    setStack(key ? key.split('|') : []);
    return () => setStack([]);
  }, [key, setStack]);
}
