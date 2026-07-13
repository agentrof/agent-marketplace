# Live Runtime Verification Protocol

Execute after the automated suite is green. The application must be observed running; a passing suite alone never grants sign-off. All FAIL conditions are zero-tolerance unless explicitly documented as KNOWN with a reason.

Severity vocabulary: CRITICAL (maps to Critical), FAIL (maps to High), MINOR (maps to Low). See the severity table in SKILL.md.

## Step 1: Start the Application

- Start with the run command from `workspace/config.json`. Never invent a start command.
- Verify the process starts without compilation or startup errors in terminal output.
- Verify no build warnings in terminal output.
- Record the local URL or entry point the application serves.
- Application fails to start: CRITICAL, stop the protocol, report.

## Step 2: Enumerate and Visit Every Navigable Surface

Build the surface inventory from the routing or navigation configuration, plus any surfaces named in the brief. For each surface:

1. Navigate to it directly (deep link, not only via in-app navigation).
2. Wait for full load: async data resolved, deferred content rendered, images loaded.
3. Perform Steps 3 through 6 on that surface.

A surface that cannot be reached at all: CRITICAL.

## Step 3: Console Audit (per surface)

Inspect the developer console or application log output.

FAIL conditions:

- Any unhandled exception or uncaught error from application code.
- Failed module or asset resolution errors.
- Framework warnings about render-phase misuse (state updates during render, missing list keys, unrecognized props).
- Memory-leak warnings (updates to unmounted or disposed components).
- Any unhandled promise or async rejection.

MINOR/KNOWN exception: warnings originating from a third-party library, documented in the report with the library name and the reason they are outside project control. Everything else is FAIL, including "harmless" warnings.

## Step 4: Network Audit (per surface)

Inspect every request the surface fires on load.

FAIL conditions:

- Any 4xx or 5xx response that is not an intentional error-state demonstration.
- Cross-origin errors (request blocked by origin policy).
- Missing authorization credentials on requests to protected endpoints.
- Requests aimed at the wrong base URL, host, or port.

MINOR conditions (document, do not block):

- Duplicate requests: the same endpoint called more than once on a single load.
- Stale requests: fetching data the surface never displays.

## Step 5: Render Audit (per surface)

Inspect the rendered output.

CRITICAL conditions:

- Blank screen: the surface loads but nothing renders.
- Development error overlay or fatal error screen displayed.

FAIL conditions:

- Layout breakage: overlapping elements, content outside the viewport, elements hidden behind others.
- Missing images or icons (broken placeholders, empty media slots).
- Unstyled content: raw markup flashes or ships without its styles.
- Responsive breakage at any of three widths, checked on every surface:
  - Narrow (small phone width): no horizontal scrollbar, text readable, layout intact.
  - Medium (tablet width): intermediate layout renders correctly.
  - Wide (desktop width): full layout renders correctly.

## Step 6: Interaction Audit (on interactive surfaces)

- Activate every actionable control: visible response, expected behavior, no crash.
- Submit every form with valid data: success feedback or correct redirect.
- Submit every form with empty and invalid data: field-level validation messages appear; the submission is rejected.
- Follow internal navigation links: the correct surface loads without a full reload where the application is designed to avoid one.
- Open and close every modal or dialog: focus moves into the dialog on open (focus trap) and returns to the trigger on close (focus restore).
- Toggle every expandable region: content shows and hides correctly.
- Exercise the sign-out flow if present: redirect to the entry surface, session state cleared, cached private data cleared.

Any interaction that crashes, silently does nothing, or produces the wrong outcome: FAIL. Focus-management defects: FAIL.

## Step 7: Record Results

In the verification record (see report-format.md):

- Per surface: identifier, console PASS/FAIL with details, network PASS/FAIL with details, render PASS/FAIL with details.
- Per interaction: action performed, expected result, actual result, PASS/FAIL.
- Overall runtime verdict: PASS only with zero CRITICAL and zero FAIL findings; otherwise FAIL with the finding list mapped into the severity table.
