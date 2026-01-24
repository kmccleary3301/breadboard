export interface ColumnVisibility {
  readonly showContext: boolean
  readonly showPriceIn: boolean
  readonly showPriceOut: boolean
  readonly providerWidth: number
}

export const PROVIDER_COLUMN_MIN_WIDTH = 24
export const CONTEXT_COLUMN_WIDTH = 6
export const PRICE_COLUMN_WIDTH = 7
export const MODEL_MENU_PADDING = 6

export const HIDE_CONTEXT_AT = 80
export const HIDE_PRICE_IN_AT = 68
export const HIDE_PRICE_OUT_AT = 50

/**
 * Computes which columns should be visible for the `/models` overlay based on the current terminal width.
 * The priority order matches product guidance: hide Context → $/M In → $/M Out.
 */
export const computeModelColumns = (columns: number): ColumnVisibility => {
  const safeCols = Number.isFinite(columns) && columns > 0 ? columns : 80

  let visibility: ColumnVisibility = {
    showContext: safeCols >= HIDE_CONTEXT_AT,
    showPriceIn: safeCols >= HIDE_PRICE_IN_AT,
    showPriceOut: safeCols >= HIDE_PRICE_OUT_AT,
    providerWidth: PROVIDER_COLUMN_MIN_WIDTH,
  }

  const reserved =
    MODEL_MENU_PADDING +
    (visibility.showContext ? CONTEXT_COLUMN_WIDTH : 0) +
    (visibility.showPriceIn ? PRICE_COLUMN_WIDTH : 0) +
    (visibility.showPriceOut ? PRICE_COLUMN_WIDTH : 0)

  const providerWidth = Math.max(PROVIDER_COLUMN_MIN_WIDTH, safeCols - reserved)

  return { ...visibility, providerWidth }
}
