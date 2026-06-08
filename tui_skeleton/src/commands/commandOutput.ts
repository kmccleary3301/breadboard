import { Console } from "effect"

export type TextJsonOutputMode = "text" | "json"
export type TableJsonOutputMode = "table" | "json"

export const normalizeTextJsonOutputMode = (value: string): TextJsonOutputMode => (value === "json" ? "json" : "text")

export const normalizeTableJsonOutputMode = (value: string): TableJsonOutputMode => (value === "json" ? "json" : "table")

export const printJsonOutput = async (value: unknown): Promise<void> => {
  await Console.log(JSON.stringify(value, null, 2))
}
