# Frontend Testing Approach

Behavioral testing with testing-library semantics. Load when writing or reviewing the frontend suite. The per-artifact matrices live in [qa-checklist](qa-checklist.md).

## Query Priority

Query the way a user perceives the page. In order of preference:

1. `getByRole` with an accessible name: `getByRole("button", { name: /save/i })`
2. `getByLabelText` for form controls
3. `getByText` / `getByPlaceholderText` for non-interactive content
4. `getByTestId` only as a last resort, and treat needing it as a signal the markup is not semantic

Assert observable behavior (rendered text, roles, attributes, calls to callback props), never internal state or implementation details. A refactor that preserves behavior must not break tests.

```tsx
it("[BR-012] submits the form with entered credentials", async () => {
  const user = userEvent.setup();
  const handleSubmit = vi.fn();
  render(<LoginForm onSubmit={handleSubmit} />);

  await user.type(screen.getByLabelText(/email/i), "jane@example.com");
  await user.type(screen.getByLabelText(/password/i), "Password!");
  await user.click(screen.getByRole("button", { name: /sign in/i }));

  expect(handleSubmit).toHaveBeenCalledWith({ email: "jane@example.com", password: "Password!" });
});
```

Use the user-event layer for interactions (it fires the full event sequence a real user triggers); reserve low-level fire-event calls for events user-event cannot express.

## Scenario Tags: Business-Rule Traceability

Prefix each test name with the id of the business rule or story it proves: `[BR-###] description` (or `[US-###]`). The runner's JSON output then yields the traceability matrix (rule id -> tests -> pass/fail) mechanically instead of by hand.

- A business rule with no tagged test is an untested requirement; the QA pass flags it.
- One test may prove one rule; a rule may need several tests (happy path plus each violation).
- The tag mirrors the backend convention, so cross-stack traceability aggregates on the same ids.

## Factory Fixtures

Factories build valid objects and accept overrides so each test states only what it cares about:

```typescript
export function createUserFixture(overrides?: Partial<User>): User {
  return {
    id: randomUUID(),
    name: "Jane Doe",
    email: "jane@example.com",
    role: "member",
    createdAt: new Date("2024-01-01"),
    ...overrides,
  };
}
```

- One factory per entity, colocated under a fixtures directory; compose factories for nested shapes.
- Deterministic defaults; randomize only where the test needs variation.
- Network responses are mocked at the request boundary (a request-interception mock layer), not by stubbing the API client functions, so the typed client code is exercised too.
- Contract check against the exported schema: the suite includes a check that validates the typed client's request and response shapes against the backend's exported schema artifact (generate types from the schema and diff, or validate the mock fixtures against it). Hand-written mock shapes with no schema check are the classic parallel-build escape; a drift must be a red suite, not a runtime surprise.

## Hook Testing

Prefer testing hooks through a component that uses them. When a hook is a public reusable unit, test it directly with `renderHook` and `act`:

- initial return value (every exposed property),
- each exposed action produces the expected state transition,
- async flows: loading -> success and loading -> error,
- cleanup on unmount (timers cleared, listeners removed, subscriptions cancelled),
- error and edge inputs (null, empty, rapid successive calls).

## Setup Conventions

- Component tests run in a DOM environment with a shared setup file (jest-dom matchers, request-mock server lifecycle, cleanup between tests).
- Global test state resets in `beforeEach`: stores to initial state, request mocks to defaults, storage cleared. Test pollution is a bug.
- Keep unit/component tests fast and isolated; reserve a thin end-to-end layer for critical user journeys via the project's configured e2e command.

## Behavioral Coverage Contract

No percentage thresholds. A frontend deliverable is covered when, for each component, hook, and store in scope:

- the happy path renders and behaves correctly,
- every data view proves all four states: loading, empty, error, success,
- every exposed prop variant and every callback prop is exercised,
- every user interaction (click, type, submit, keyboard path) has a test,
- every business rule in scope has at least one `[BR-###]`-tagged test,
- the accessibility test list in [qa-checklist](qa-checklist.md) passes.

Anything short of that list is a named gap in the QA report, not a percentage.
