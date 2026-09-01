// BreadBoard-owned compatibility surface. These exports retain the real 0.2.5
// session, evidence, lifecycle, endpoint, and non-product client behavior
// behind an unstable path.
export {
  ApiError,
  createBreadboardClient as createInternalBreadboardClient,
  createApiClient as createInternalApiClient,
  type BreadboardClientConfig as InternalBreadboardClientConfig,
  type BreadboardClient as InternalBreadboardClient,
} from "./client.js"
export * from "./session-runtime.js"
export * from "./session-evidence.js"
export * from "./lifecycle-client.js"
export * from "./endpoint-client.js"
export type {
  AuthCredentialView,
  E4CatalogBinding,
  E4CatalogPage,
  E4ClaimDetail,
  E4ClaimList,
  E4CoverageMatrix,
  E4Health,
  E4LaneDetail,
  E4LaneList,
  E4LedgerRows,
  E4RecordList,
  E4ReverifyRequest,
  E4ReverifyResult,
  E4SchemaList,
  ReadSessionFileOptions,
  SessionSummary,
} from "./types.js"
export { ROUTES as INTERNAL_ROUTES } from "./generated/routes.js"
