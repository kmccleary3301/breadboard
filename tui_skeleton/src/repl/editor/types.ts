export interface ChipSnapshot {
  readonly id: string
  readonly start: number
  readonly end: number
  readonly label: string
  readonly color?: string
}

export interface Attachment {
  readonly id: string
  readonly kind: "image"
  readonly mimeType: string
  readonly approxBytes: number
  readonly approxSizeLabel: string
  readonly averageColor?: string
  /** Raw clipboard payload. May remain undefined until multipart upload is ready. */
  readonly data?: Uint8Array
}

export interface PasteToken {
  readonly id: string
  readonly length: number
}

export interface InputBufferState {
  readonly text: string
  readonly cursor: number
  readonly chips: readonly ChipSnapshot[]
  readonly attachments: readonly Attachment[]
}

export interface EditOperation {
  readonly kind: "insert" | "delete" | "replace" | "paste" | "attachment"
  readonly label: string
  readonly before: InputBufferState
  readonly after: InputBufferState
}

export const PASTE_COLLAPSE_THRESHOLD = 512
