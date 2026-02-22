export const nextSearchResultIndex = (length: number, currentIndex: number): number => {
  if (length <= 0) return -1
  return currentIndex < 0 ? 0 : (currentIndex + 1) % length
}

export const prevSearchResultIndex = (length: number, currentIndex: number): number => {
  if (length <= 0) return -1
  return currentIndex <= 0 ? length - 1 : currentIndex - 1
}

export const resolveSearchResultAnchors = (id: string): { primary: string; fallback: string | null } => {
  const artifactId = id.startsWith("artifact-") ? id.slice("artifact-".length) : ""
  return {
    primary: `entry-${id}`,
    fallback: artifactId ? `event-${artifactId}` : null,
  }
}
