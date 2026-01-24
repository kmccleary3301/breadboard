export const displayPathForCwd = (fullPath: string, cwd: string): string => {
  if (!cwd || cwd === "." || cwd === "/") return fullPath
  const prefix = `${cwd}/`
  return fullPath.startsWith(prefix) ? fullPath.slice(prefix.length) : fullPath
}
