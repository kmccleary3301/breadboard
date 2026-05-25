import type React from "react"
import type { TranscriptItem } from "../../transcriptModel.js"

export interface StaticFeedItem {
  readonly id: string
  readonly node: React.ReactNode
  readonly lineCount?: number
  readonly transcriptCell?: TranscriptItem
}

export interface TranscriptMatch {
  readonly line: number
  readonly start: number
  readonly end: number
}
