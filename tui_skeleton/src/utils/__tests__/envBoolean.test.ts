import { describe, expect, it } from "vitest"
import { parseBooleanEnv, parseBooleanLikeValue, parseOptionalBooleanEnv } from "../envBoolean.js"

describe("parseBooleanLikeValue", () => {
  it("accepts booleans and bool-like string tokens", () => {
    expect(parseBooleanLikeValue(true)).toBe(true)
    expect(parseBooleanLikeValue(false)).toBe(false)
    expect(parseBooleanLikeValue("1")).toBe(true)
    expect(parseBooleanLikeValue(" TRUE ")).toBe(true)
    expect(parseBooleanLikeValue("YeS")).toBe(true)
    expect(parseBooleanLikeValue("on")).toBe(true)
    expect(parseBooleanLikeValue("0")).toBe(false)
    expect(parseBooleanLikeValue(" FALSE ")).toBe(false)
    expect(parseBooleanLikeValue("No")).toBe(false)
    expect(parseBooleanLikeValue("off")).toBe(false)
  })

  it("returns undefined for absent, empty, and invalid values", () => {
    expect(parseBooleanLikeValue(undefined)).toBeUndefined()
    expect(parseBooleanLikeValue(null)).toBeUndefined()
    expect(parseBooleanLikeValue(1)).toBeUndefined()
    expect(parseBooleanLikeValue(0)).toBeUndefined()
    expect(parseBooleanLikeValue("")).toBeUndefined()
    expect(parseBooleanLikeValue("   ")).toBeUndefined()
    expect(parseBooleanLikeValue("enabled")).toBeUndefined()
    expect(parseBooleanLikeValue("constructor")).toBeUndefined()
    expect(parseBooleanLikeValue("toString")).toBeUndefined()
    expect(parseBooleanLikeValue("__proto__")).toBeUndefined()
  })
})

describe("parseBooleanEnv", () => {
  it("preserves caller fallback for absent, empty, and invalid env strings", () => {
    expect(parseBooleanEnv(undefined, true)).toBe(true)
    expect(parseBooleanEnv("", true)).toBe(true)
    expect(parseBooleanEnv("   ", false)).toBe(false)
    expect(parseBooleanEnv("maybe", true)).toBe(true)
    expect(parseBooleanEnv("maybe", false)).toBe(false)
  })
})

describe("parseOptionalBooleanEnv", () => {
  it("returns null for absent, empty, and invalid env strings", () => {
    expect(parseOptionalBooleanEnv(undefined)).toBeNull()
    expect(parseOptionalBooleanEnv("")).toBeNull()
    expect(parseOptionalBooleanEnv("   ")).toBeNull()
    expect(parseOptionalBooleanEnv("maybe")).toBeNull()
  })
})
