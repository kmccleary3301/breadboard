const IPV4_LOOPBACK = /^127(?:\.\d{1,3}){3}$/

const isLoopbackHostname = (hostname: string): boolean =>
  hostname === "localhost"
  || hostname === "[::1]"
  || IPV4_LOOPBACK.test(hostname)

export const assertProtectedBearerTransport = (baseUrl: string): void => {
  const origin = new URL(baseUrl)
  if (origin.protocol === "https:") return
  if (origin.protocol === "http:" && isLoopbackHostname(origin.hostname)) return
  throw new Error("Bearer authentication requires HTTPS except for loopback HTTP origins")
}
