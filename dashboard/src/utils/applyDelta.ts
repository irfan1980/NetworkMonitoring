export function mergeById<T extends { id: string } | { pipelineId: string | number }>(
  existing: Record<string, T>,
  patches: T[] | undefined,
  keyOf: (item: T) => string
): Record<string, T> {
  if (!patches || patches.length === 0) return existing;
  const next = { ...existing };
  for (const patch of patches) {
    const key = keyOf(patch);
    next[key] = { ...(next[key] as object), ...(patch as object) } as T;
  }
  return next;
}
