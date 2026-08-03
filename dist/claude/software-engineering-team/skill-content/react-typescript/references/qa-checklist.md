# Frontend QA Checklist

Per-artifact test matrices for QA planning. Every component, hook, and store in scope gets its matrix; an artifact with an unjustified untested row is a named gap in the QA report. Query and fixture conventions live in [testing](testing.md).

## Component Matrix

Apply to every component in the deliverable.

### Render Tests

- [ ] Renders with default props (expected role/text present)
- [ ] Renders every declared variant and size combination
- [ ] Renders fallback (or nothing, by design) when required data is missing or null
- [ ] Renders the empty state with empty array data
- [ ] Renders correctly inside its error boundary when a child throws (where applicable)

### Props Tests

- [ ] Every prop type exercised (string, number, boolean, object, array, function, node)
- [ ] Missing optional props do not crash; defaults apply correctly
- [ ] Edge values handled: empty string, `0`, `false`, empty array, `null`, `undefined`
- [ ] Callback props called with the correct arguments
- [ ] Callbacks NOT called when the component is disabled

### State Tests

- [ ] Initial state on mount matches the specification
- [ ] State changes after each user interaction (click, type, select)
- [ ] State changes after async completion (response arrival, timer)
- [ ] Reset behavior: unmount/remount and explicit reset produce a clean state
- [ ] Derived display values compute correctly from source state

### Event Tests

- [ ] Every handler prop fires on its interaction (click, change, submit, blur, focus, key events)
- [ ] Form submission delivers the entered values as a typed payload
- [ ] Invalid submission blocks the callback and shows field-level errors
- [ ] Debounced/throttled handlers: final call value and timing verified
- [ ] Keyboard path triggers the same behavior as the pointer path

### All-States Coverage (data views)

Every data-dependent view proves all four, each with an assertion on visible output:

- [ ] Loading: indicator present, content absent, controls disabled as designed
- [ ] Empty: designed empty-state message and call to action, not a blank region
- [ ] Error: friendly message, retry affordance where specified, no raw error text
- [ ] Success: data rendered, count/order correct, interactive elements enabled

### Style Verification

- [ ] Each variant applies its token-driven classes; conditional classes toggle with state (active, disabled, loading, error)
- [ ] No hardcoded color/spacing/type values asserted anywhere; assertions reference token-derived classes
- [ ] Theme variants render correctly where the component has theme-dependent styling

## Hook Matrix

Apply to every exported custom hook.

- [ ] Initial return value: every exposed property checked
- [ ] Every exposed action produces its expected state transition
- [ ] Async flows: loading -> success and loading -> error, in order
- [ ] Re-runs occur when inputs change and do not occur when they are stable
- [ ] Cleanup on unmount: timers cleared, listeners removed, subscriptions cancelled
- [ ] Error scenarios: invalid input, failed async operation, rejected promise
- [ ] Edge cases: null/empty input, rapid successive calls, concurrent invocations

## Store Matrix

Apply to every global client store; reset the store between tests.

- [ ] Initial state matches the specification, every field verified
- [ ] Every action produces exactly its documented state change, tested independently
- [ ] Every selector/derived value computes correctly from state
- [ ] Async actions: loading -> success and loading -> error paths, state updated in order
- [ ] Reset/logout clears all owned state; no leakage between tests
- [ ] Components subscribed via selectors re-render only on relevant changes
- [ ] Edge cases: actions with invalid data, repeated actions (logout when logged out)

## Accessibility Tests

Run for every interactive component (details in [accessibility](accessibility.md)):

- [ ] Automated axe audit passes with zero violations, in default and interactive states
- [ ] Keyboard navigable: tab order correct, Enter/Space activate, Escape dismisses
- [ ] ARIA states correct where used (`aria-busy`, `aria-expanded`, `aria-invalid`)
- [ ] Modal focus: trapped while open, restored to trigger on close
- [ ] Form labels associated; validation messages announced and linked to fields

## Integration and Routing

- [ ] Every route renders its page; unknown routes show the designed not-found page
- [ ] Guarded routes redirect unauthenticated users; permitted roles pass through
- [ ] Network failure on each API-backed page shows the error state (request mock returning failures)
- [ ] Auth expiry mid-session is handled: refresh or redirect, no dead UI
- [ ] Logout clears query cache, store state, and storage, then lands on the public entry

## Traceability

- [ ] Every business rule in scope has at least one `[BR-###]`-tagged test
- [ ] The rule-to-test matrix has no untested rows without a written justification
- [ ] Suite runs use the configured test command; e2e runs target an environment stood up through the configured environment command; results recorded per category above
