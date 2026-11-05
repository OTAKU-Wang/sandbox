export function pickUploadFile(value: unknown): File | undefined {
  const entry = Array.isArray(value) ? value[0] : value;
  if (!entry || typeof entry !== 'object') return undefined;
  const fromList = (entry as { originFileObj?: File }).originFileObj;
  if (fromList instanceof File) return fromList;
  return entry instanceof File ? entry : undefined;
}
