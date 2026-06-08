export const COMPOSER_READY_MARKERS = [
  "enter send",
  "Try edit <file> to...",
  "Enter submit",
  "Enter to submit",
] as const

export const findComposerReadyMarker = (text: string): string | null => {
  for (const marker of COMPOSER_READY_MARKERS) {
    if (text.includes(marker)) {
      return marker
    }
  }
  return null
}

export const includesComposerReady = (text: string): boolean => findComposerReadyMarker(text) !== null
