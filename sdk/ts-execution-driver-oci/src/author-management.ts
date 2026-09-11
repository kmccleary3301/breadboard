import { Ajv, type JTDSchemaType, type JTDParser } from "ajv/dist/jtd.js"
import type { AuthorWorkerLaunchInputV1 } from "@breadboard/execution-drivers"

export const OWNER_LABEL = "dev.breadboard.author.owner"
export const EXECUTION_LABEL = "dev.breadboard.author.execution"
export const TOKEN_LABEL = "dev.breadboard.author.token"

type OwnerLabels = Record<typeof OWNER_LABEL | typeof EXECUTION_LABEL | typeof TOKEN_LABEL, string>
interface HelperSpec extends AuthorWorkerLaunchInputV1 {
  capturedStagingRoot: string
  stagingOwnerRef: string
  packageMountTarget: string
  expectedPackageSha256: string | null
  capacityAuthorization: string | null
  profile: { cpuCount: number; memoryBytes: number; processCount: number; scratchBytes: number }
  runtimeCommand: string
}
export interface DockerImageInspect {
  Id: string
  Os: string
  Architecture: string
  Variant?: string
}
export interface DockerContainerInspect {
  Id: string
  Image: string
  Config: { Image: string; User: string; Labels: OwnerLabels }
  State: { Status: string }
  HostConfig: {
    Privileged: boolean
    ReadonlyRootfs: boolean
    NetworkMode: string
    SecurityOpt: string[] | null
    CapDrop: string[] | null
    Memory: number
    MemorySwap: number
    PidsLimit: number | null
    NanoCpus: number
    Tmpfs: Record<string, string> | null
  }
  Mounts: Array<{ Type: string; Source: string; Destination: string; RW: boolean }>
}
const string = { type: "string" } as const
const number = { type: "float64" } as const
const boolean = { type: "boolean" } as const
const nullableString = { type: "string", nullable: true } as const
const helperSchema: JTDSchemaType<HelperSpec> = {
  properties: {
    ownerRef: string, executionId: string, executionToken: string, imageRef: string, platform: string,
    command: { elements: string }, capturedStagingRoot: string, stagingOwnerRef: string,
    packageMountTarget: string, expectedPackageSha256: nullableString, capacityAuthorization: nullableString,
    profile: { properties: { cpuCount: number, memoryBytes: number, processCount: number, scratchBytes: number } },
    runtimeCommand: string,
  },
}
const imageSchema: JTDSchemaType<DockerImageInspect[]> = {
  elements: {
    properties: { Id: string, Os: string, Architecture: string },
    optionalProperties: { Variant: string },
    additionalProperties: true,
  },
}
const containerSchema: JTDSchemaType<DockerContainerInspect[]> = {
  elements: {
    properties: {
      Id: string, Image: string,
      Config: {
        properties: {
          Image: string, User: string,
          Labels: {
            properties: { [OWNER_LABEL]: string, [EXECUTION_LABEL]: string, [TOKEN_LABEL]: string },
            additionalProperties: true,
          },
        },
        additionalProperties: true,
      },
      State: { properties: { Status: string }, additionalProperties: true },
      HostConfig: {
        properties: {
          Privileged: boolean, ReadonlyRootfs: boolean, NetworkMode: string,
          SecurityOpt: { elements: string, nullable: true }, CapDrop: { elements: string, nullable: true },
          Memory: number, MemorySwap: number, PidsLimit: { type: "float64", nullable: true },
          NanoCpus: number, Tmpfs: { values: string, nullable: true },
        },
        additionalProperties: true,
      },
      Mounts: { elements: { properties: { Type: string, Source: string, Destination: string, RW: boolean }, additionalProperties: true } },
    },
    additionalProperties: true,
  },
}
const ajv = new Ajv()
const helperParser = ajv.compileParser<HelperSpec>(helperSchema)
const imageParser = ajv.compileParser<DockerImageInspect[]>(imageSchema)
const containerParser = ajv.compileParser<DockerContainerInspect[]>(containerSchema)

function parse<T>(parser: JTDParser<T>, source: string, label: string): T {
  const value = parser(source)
  if (value === undefined) throw new Error(`${label}: ${parser.message ?? "invalid JSON"} at byte ${parser.position ?? 0}`)
  return value
}
export function parseAuthorHelperSpec(source: string): { input: AuthorWorkerLaunchInputV1; runtimeCommand: string } {
  const { runtimeCommand, ...input } = parse(helperParser, source, "author launch")
  if (!runtimeCommand) throw new Error("The configured Docker command is required")
  return { input, runtimeCommand }
}
export function parseDockerImage(source: string): DockerImageInspect {
  const images = parse(imageParser, source, "Docker image inspect")
  const image = images[0]
  if (images.length !== 1 || !image) throw new Error("Docker image inspect must return exactly one image")
  return image
}
export function parseDockerContainer(source: string): DockerContainerInspect {
  const containers = parse(containerParser, source, "Docker container inspect")
  const container = containers[0]
  if (containers.length !== 1 || !container) throw new Error("Docker container inspect must return exactly one container")
  return container
}
