export const stripAnsiCodes = (value: string): string => value.replace(/\u001B\[[0-9;]*m/g, "")

const isCombiningCodePoint = (codePoint: number): boolean =>
  (codePoint >= 0x0300 && codePoint <= 0x036f) ||
  (codePoint >= 0x1ab0 && codePoint <= 0x1aff) ||
  (codePoint >= 0x1dc0 && codePoint <= 0x1dff) ||
  (codePoint >= 0x20d0 && codePoint <= 0x20ff) ||
  (codePoint >= 0xfe20 && codePoint <= 0xfe2f)

const isFullWidthCodePoint = (codePoint: number): boolean =>
  codePoint >= 0x1100 &&
  (codePoint <= 0x115f ||
    codePoint === 0x2329 ||
    codePoint === 0x232a ||
    (codePoint >= 0x2e80 && codePoint <= 0x3247 && codePoint !== 0x303f) ||
    (codePoint >= 0x3250 && codePoint <= 0x4dbf) ||
    (codePoint >= 0x4e00 && codePoint <= 0xa4c6) ||
    (codePoint >= 0xa960 && codePoint <= 0xa97c) ||
    (codePoint >= 0xac00 && codePoint <= 0xd7a3) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0xfe10 && codePoint <= 0xfe19) ||
    (codePoint >= 0xfe30 && codePoint <= 0xfe6b) ||
    (codePoint >= 0xff01 && codePoint <= 0xff60) ||
    (codePoint >= 0xffe0 && codePoint <= 0xffe6) ||
    (codePoint >= 0x1b000 && codePoint <= 0x1b001) ||
    (codePoint >= 0x1f200 && codePoint <= 0x1f251) ||
    (codePoint >= 0x20000 && codePoint <= 0x3fffd))

export const stringWidth = (value: string): number => {
  let width = 0
  for (const char of value) {
    const codePoint = char.codePointAt(0) ?? 0
    if (codePoint === 0) continue
    if (codePoint < 0x20 || (codePoint >= 0x7f && codePoint < 0xa0)) continue
    if (isCombiningCodePoint(codePoint)) continue
    width += isFullWidthCodePoint(codePoint) ? 2 : 1
  }
  return width
}

export const visibleWidth = (value: string): number => stringWidth(stripAnsiCodes(value))
