export const FORBIDDEN_ROUTE_MARKERS = ["/events/sse", "/artifacts/download", "/user_message"] as const

export const buildSessionDownloadPath = (sessionId: string): string => `/sessions/${sessionId}/download`
