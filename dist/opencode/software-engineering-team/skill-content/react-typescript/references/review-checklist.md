# Frontend Review Checklist

Checkbox assertions for reviewing React + TypeScript code. Each unmet assertion becomes a finding at the tagged severity: CRITICAL and MAJOR findings block approval; MINOR findings are advisory. Pattern-level rationale lives in [patterns](patterns.md) and [performance](performance.md).

## Token Compliance (gate)

- [ ] CRITICAL: All colors come from design-system tokens; no hardcoded hex/rgb/hsl anywhere in component code
- [ ] CRITICAL: All spacing uses the design-system scale; no arbitrary pixel values
- [ ] CRITICAL: All typography (family, size, weight, line-height) uses design-system tokens
- [ ] MAJOR: Components consume semantic/component tokens only, never primitives directly
- [ ] MAJOR: Z-index values come from the defined scale, not arbitrary numbers
- [ ] MINOR: Transitions use the duration scale and performant properties (`transform`, `opacity`)

Any CRITICAL row failing here fails the review regardless of other sections. A missing token is escalated to the design owner, not patched with a raw value.

## Architecture

- [ ] MAJOR: Separation holds: UI components vs business-logic hooks vs API layer; pages orchestrate only
- [ ] MAJOR: API calls isolated in the typed client layer, never inline in components
- [ ] MAJOR: Server state lives in the query/cache library exclusively; no hand-rolled fetch-plus-state
- [ ] MAJOR: Route structure matches the approved navigation; guards protect authenticated pages
- [ ] MAJOR: No circular dependencies between component modules
- [ ] MINOR: Folder structure and barrel exports follow the documented project conventions

## Type Safety

- [ ] CRITICAL: No `any` types (zero tolerance); strict compiler options enabled
- [ ] MAJOR: Every component has a typed, exported props interface
- [ ] MAJOR: API response types match the contract (shared or generated types)
- [ ] MAJOR: No type assertions or non-null assertions without written justification
- [ ] MINOR: Discriminated unions model view state; generics type reusable components

## Error Handling and States

- [ ] CRITICAL: Every data-dependent view implements loading, empty, error, and success states
- [ ] MAJOR: Error boundaries at route and widget level with user-friendly fallbacks
- [ ] MAJOR: API errors transformed to user language; no raw error strings rendered
- [ ] MAJOR: No unhandled promise rejections; async handlers catch and surface failures
- [ ] MAJOR: Zero console errors or warnings in normal operation
- [ ] MINOR: Retry and timeout behavior configured for network operations

## Hooks and State

- [ ] MAJOR: No state that can be derived from other state or props
- [ ] MAJOR: Effect dependency arrays complete; cleanup provided for every subscription, timer, and listener
- [ ] MAJOR: No state updates during render; no effects doing derived-state or event work
- [ ] MAJOR: Context values memoized; providers scoped to the subtree that needs them
- [ ] MAJOR: No direct state mutation; updates produce new references
- [ ] MINOR: Global store is minimal and read through selectors; no god-store

## Component Composition

- [ ] MAJOR: Single responsibility per component; no multi-purpose monoliths
- [ ] MAJOR: Lists keyed by stable data ids, never array index on dynamic lists
- [ ] MAJOR: Modals manage focus trap, portal rendering, and focus restoration
- [ ] MINOR: Prop drilling limited to two levels; composition or context beyond that
- [ ] MINOR: Props surface minimal; `children` composition preferred over configuration props
- [ ] MINOR: Interactive primitives forward refs and accept a `className` override

## Performance

- [ ] MAJOR: Independent async operations run in parallel; no sequential awaits for unrelated data
- [ ] MAJOR: Route-level code splitting; heavy components dynamically imported
- [ ] MAJOR: Long lists virtualized or paginated
- [ ] MAJOR: Direct imports from source modules, not barrels, for heavy libraries
- [ ] MINOR: Memoization present only where measured; no reflexive memo wrapping
- [ ] MINOR: Rapid-fire handlers (search, scroll, resize) debounced or marked as transitions

## Accessibility

- [ ] MAJOR: Semantic elements used for their purpose; no click handlers on non-interactive elements
- [ ] MAJOR: Every form control labeled; validation messages tied to their field
- [ ] MAJOR: Full keyboard operability; focus visible and managed on route/content changes
- [ ] MAJOR: Contrast meets WCAG AA in every theme
- [ ] MINOR: Live regions announce dynamic updates; skip link present
- [ ] MINOR: Images carry `alt` text; decorative images use `alt=""`

## Security

- [ ] CRITICAL: No raw HTML injection without sanitization
- [ ] CRITICAL: No tokens or sensitive data in local storage; no secrets in the client bundle
- [ ] CRITICAL: No `eval` or dynamic code execution with user data
- [ ] MAJOR: User input sanitized before rendering; no inline handlers built from user-controlled data
- [ ] MAJOR: No sensitive data in URLs, query parameters, or browser history
- [ ] MINOR: Third-party scripts integrity-pinned; CSP-compatible patterns used

## Auth

- [ ] CRITICAL: Route guards protect every authenticated page; unauthorized access redirects to login
- [ ] MAJOR: The client attaches auth headers centrally; 401/403 handled gracefully everywhere
- [ ] MAJOR: Logout clears all client state: tokens, cached user data, query cache, store
- [ ] MINOR: Role-based UI rendering mirrors backend permissions and never substitutes for them

## Finding Format

Each finding records: severity, category, `file:line`, what is wrong, why it matters, the specific fix, and how to verify the fix. Verdict: approve only when no CRITICAL or MAJOR finding remains open.
