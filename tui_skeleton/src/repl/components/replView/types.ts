import type React from "react"

import type React from "react"

export interface StaticFeedItem {
  readonly id: string
  readonly node: React.ReactNode
}

export interface TranscriptMatch {
  readonly line: number
  readonly start: number
  readonly end: number
}
