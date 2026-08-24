# React + TypeScript Patterns

Severity-ranked do/don't pairs and component API design. Load when implementing or reviewing frontend code. Severity maps to the review scale: High findings block, Medium should be fixed, Low is advisory.

## State

| Severity | Do | Don't |
|---|---|---|
| High | Derive values in render: `const total = items.reduce(...)` | Mirror derivable data into state and sync it with effects |
| Medium | Use a reducer when several values always update together | Keep many `useState` calls that must change in lockstep |
| Medium | Initialize expensive state lazily: `useState(() => parse(raw))` | Compute the expensive initial value on every render |
| Medium | Lift shared state to the nearest common ancestor | Drill props through many intermediate layers |
| Medium | Keep non-render values (interval ids, DOM nodes) in refs | Put them in state and trigger pointless re-renders |

## Effects

| Severity | Do | Don't |
|---|---|---|
| High | List every value the effect reads in its dependency array | Use an empty array while referencing props or state |
| High | Return a cleanup that unsubscribes, clears timers, removes listeners | Leave subscriptions running after unmount |
| High | Transform data in render and handle events in handlers | Use effects for derived state or event logic |

## Rendering

| Severity | Do | Don't |
|---|---|---|
| High | Key list items by stable data id: `key={item.id}` | Key dynamic lists by array index |
| High | Pass handler references: `onClick={handleSave}` | Invoke in JSX: `onClick={handleSave()}`, firing on render |
| High | Virtualize or paginate long lists | Render thousands of rows as live DOM nodes |
| Low | Use an explicit ternary when the condition can be `0` | Use `&&` with numeric conditions and render a stray `0` |
| Low | Group siblings with fragments | Add wrapper `div` elements that break layout and semantics |

## Context

| Severity | Do | Don't |
|---|---|---|
| High | Memoize the provider value object | Pass `value={{ user, theme }}` created fresh each render |
| Medium | Split contexts by concern (theme, auth, locale) | Ship one giant app context that re-renders everything |
| Medium | Scope providers to the subtree that needs them | Wrap the entire app by default |

## Error Handling

| Severity | Do | Don't |
|---|---|---|
| High | Place error boundaries at route and widget level with fallback UI | Let one component crash the whole tree |
| High | Catch rejections in async handlers and surface a friendly message | Leave unhandled promise rejections in event handlers |
| Medium | Transform API errors to user language at the client layer | Render raw error strings or status codes |

## Accessibility (implementation rows; full checklist in accessibility.md)

| Severity | Do | Don't |
|---|---|---|
| High | Use `<button>`, `<nav>`, `<a>` for their purpose | Attach `onClick` to a `div` and call it a button |
| High | Associate every control with a label (`htmlFor` to input id) | Use placeholder text as the only label |
| High | Trap focus in modals and restore it on close | Open overlays that keyboard users cannot leave |
| Medium | Announce dynamic updates with `aria-live` regions | Update content silently for screen readers |

## Typing

| Severity | Do | Don't |
|---|---|---|
| High | Define a props interface for every component | Accept `props: any` or skip prop types |
| Medium | Type state explicitly when inference gives `null`: `useState<User \| null>(null)` | Let state infer to an unusable narrow or `any` type |
| Medium | Type event handlers with the framework's event types | Use the generic DOM `Event` and cast inside |
| Medium | Use generics for reusable list/table components | Fall back to `any[]` items for flexibility |

## Component API Design

Principles:

- Composition over configuration: accept `children` before adding content props; prefer compound components (`Tabs` + `Tab` + `TabPanel` sharing context) over parallel config arrays.
- Reach for render props only when plain composition cannot express the customization.
- Semantic prop names with sensible defaults: `variant`, `size`, `isLoading`, `isDisabled`; boolean props read as predicates (`is`/`has`/`should`).
- Keep the surface minimal: a component approaching double-digit props wants to be split or composed.
- Forward refs on interactive primitives and allow a `className`/`style` override so consumers can extend without wrapping.
- Variants map to component tokens, not to inline values; the variant table lives with the component, the values live in the token layer.

```tsx
interface ButtonProps extends NativeButtonProps {
  variant?: "primary" | "secondary" | "ghost";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", isLoading, children, ...rest }, ref) => (
    <button
      ref={ref}
      className={mergeClasses(buttonVariantClass(variant, size), className)}
      disabled={isLoading || rest.disabled}
      {...rest}
    >
      {isLoading ? <Spinner /> : null}
      {children}
    </button>
  ),
);
```

## Structure Conventions

- `PascalCase` components, `camelCase` hooks and utils, `UPPER_SNAKE_CASE` constants; hooks always start with `use`.
- Event handlers named `handle*`; callback props named `on*`.
- Colocate a component with its hook, styles, and test; components stay under roughly 200 lines, hooks under 100, split when they grow past that.
- Custom hooks own reusable stateful logic; components never duplicate a hook's body inline.
