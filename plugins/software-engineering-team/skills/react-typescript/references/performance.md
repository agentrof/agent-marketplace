# Frontend Performance Rules

Severity-tagged rules grouped by failure mode. Critical rows are review blockers. Rows marked **[conditional]** apply only when the project uses a server-rendered framework; never assume one.

## Async Waterfalls

| Severity | Rule | Do | Don't |
|---|---|---|---|
| Critical | Parallelize independent fetches | `const [user, posts] = await Promise.all([fetchUser(), fetchPosts()])` | Sequential `await` for unrelated data |
| Critical | Defer await into the branch that needs it | Return early before awaiting when the branch skips the data | Await at the top of a function, blocking all branches |
| Critical | Start promises early, await late | Kick off requests immediately, await where the value is used | Await each request before starting the next |
| High | Stream around slow data | Wrap slow sections in suspense-style boundaries with skeleton fallbacks | Block the entire page render on one fetch |

## Bundle Size

| Severity | Rule | Do | Don't |
|---|---|---|---|
| Critical | Import directly from source modules | `import Check from "icons/check"` | Pull one icon through a barrel that loads hundreds |
| Critical | Dynamically import heavy components | Lazy-load editors, charts, and modals not needed at first paint | Import heavy components at the top level |
| High | Load conditionally activated modules on activation | `if (enabled) import("./heavy")` inside the enabling path | Import feature-flagged modules unconditionally |
| Medium | Preload on user intent | Start the import on hover or focus of the trigger | Wait for the click, then pay the full load |
| Medium | Defer third-party scripts | Load analytics and logging after hydration | Bundle them into the critical path |

## Rerender Discipline

| Severity | Rule | Do | Don't |
|---|---|---|---|
| Medium | Narrow effect dependencies to primitives | `useEffect(fn, [user.id])` | `useEffect(fn, [user])` re-running on every new reference |
| Medium | Functional set-state for updates based on current value | `setItems(curr => [...curr, item])` | Read state in the closure and add it to dependencies |
| Medium | Lazy-init expensive state | `useState(() => buildIndex(items))` | Rebuild the index on every render |
| Medium | Mark non-urgent updates as transitions | Wrap scroll/filter updates in a transition | Block the UI on every rapid state change |
| Medium | Subscribe to derived booleans | `useMediaQuery("(max-width: 767px)")` | Subscribe to raw width and compare in render |
| Medium | Extract expensive work into memoized child components | A memoized child skips work above an early return | Compute expensive values before `if (loading) return` |
| Medium | Read rarely-used state on demand in callbacks | Read the URL inside the handler | Subscribe the whole component to state used only on click |

## Rendering and DOM

| Severity | Rule | Do | Don't |
|---|---|---|---|
| High | Virtualize long lists | Windowing or `content-visibility: auto` for long feeds | Render every row up front |
| Medium | Avoid hydration flicker for client-only values | Set theme/user-preference classes with a synchronous inline script | Set them in an effect after first paint |
| Low | Hoist static JSX and regexes to module scope | Create constants once | Recreate identical objects each render |
| Low | Debounce rapid-fire input | Deferred values or debounce for search-as-you-type | Filter the full dataset on every keystroke |

## Hot-Path JavaScript

| Severity | Rule | Do | Don't |
|---|---|---|---|
| Medium | Index repeated lookups | `new Map(users.map(u => [u.id, u]))`, then `get` | `.find()` inside a loop |
| Medium | Return early once the result is known | Bail on the first failing item | Scan everything, then check a flag |
| Low | Combine multiple passes | One loop that categorizes into several buckets | Chained `.filter()` calls over the same array |
| Low | Prefer non-mutating array methods | Copies for sort and reverse in render paths | In-place mutation of props or state arrays |

## Framework-Conditional (server-rendered projects only)

| Severity | Rule |
|---|---|
| High | **[conditional]** Pass only the fields the client uses across the server/client boundary; whole objects serialize into the payload |
| High | **[conditional]** Compose sibling server components so their fetches run in parallel; a parent awaiting before rendering children serializes them |
| Medium | **[conditional]** Deduplicate per-request server fetches with the framework's request cache |
| Medium | **[conditional]** Schedule logging and analytics after the response is sent, never before returning it |

## Method

Profile before optimizing: measure with the profiler, fix the largest cost, re-measure. Memoization added without a measurement is a review finding (see [review-checklist](review-checklist.md)).
