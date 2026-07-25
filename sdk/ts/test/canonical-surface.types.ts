import type {
  CancelReceipt,
  CancellationReceipt,
  ExactEmptyPayload,
  EventId,
  LoggedSessionEvent,
  ObserveEvents,
  ObserveSessionRequest,
  OpenedSession,
  OpenedSessionRuntime,
  SubmitInput,
  SubmitTextTurn,
} from "../src/index.js"

type Equal<Left, Right> =
  (<Value>() => Value extends Left ? 1 : 2) extends
  (<Value>() => Value extends Right ? 1 : 2)
    ? (<Value>() => Value extends Right ? 1 : 2) extends
      (<Value>() => Value extends Left ? 1 : 2)
      ? true
      : false
    : false

type Expect<Value extends true> = Value

type TurnStartedPayload = Extract<LoggedSessionEvent, { readonly kind: "turn_started" }>["payload"]
type ObserveAfter = NonNullable<ObserveSessionRequest["after"]>

type CanonicalSurfaceAssertions = readonly [
  Expect<Equal<OpenedSessionRuntime, OpenedSession>>,
  Expect<Equal<CancelReceipt, CancellationReceipt>>,
  Expect<Equal<ObserveEvents, ObserveSessionRequest>>,
  Expect<Equal<ObserveAfter, { readonly eventId: EventId | string; readonly sequence: number }>>,
  Expect<Equal<SubmitTextTurn, SubmitInput>>,
  Expect<Equal<TurnStartedPayload, ExactEmptyPayload>>,
]

const canonicalSurfaceAssertions: CanonicalSurfaceAssertions = [true, true, true, true, true, true]
void canonicalSurfaceAssertions
