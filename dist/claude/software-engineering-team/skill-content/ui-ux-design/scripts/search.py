# -*- coding: utf-8 -*-
"""CLI entry point for the design intelligence corpus.

Usage: python search.py "<query>" [--domain <domain>] [--stack <stack>] [--max-results 3]
       python search.py "<query>" --design-system [-p "Project Name"] [--style-emphasis "<keywords>"]
       python search.py "<query>" --design-system --persist [-p "Project Name"] [--page "dashboard"]

Domains: style, color, chart, landing, product, ux, typography, icons
Stacks: react, html-tailwind, shadcn

Persistence (Master + Overrides pattern):
  --persist       Save MASTER.md under --output-dir (default workspace/docs/design-system)
  --page          Also create a page-specific override file under pages/
  --force-master  Overwrite an existing MASTER.md (without it, persist refuses
                  and routes to the design-system entry's UPDATE mode)
"""

import argparse
import io
import sys
from core import CSV_CONFIG, AVAILABLE_STACKS, MAX_RESULTS, search, search_stack
from design_system import DEFAULT_OUTPUT_DIR, generate_design_system

# Force UTF-8 stdout/stderr so output is stable on every platform.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


FIELD_CHAR_CAP = 110      # single field value cap
RESULT_CHAR_BUDGET = 460  # per-result body cap; keeps 3 results under 2000 chars total


def format_output(result):
    """Format results for model consumption: token-optimized, deterministic.

    Fields print in row order, each capped at FIELD_CHAR_CAP; once a
    result's body exceeds RESULT_CHAR_BUDGET the remaining fields are
    dropped with a marker. Full detail always lives in the CSV row.
    """
    if "error" in result:
        return f"Error: {result['error']}"

    output = []
    if result.get("stack"):
        output.append("## Stack Guidelines")
        output.append(f"**Stack:** {result['stack']} | **Query:** {result['query']}")
    else:
        output.append("## Design Search Results")
        output.append(f"**Domain:** {result['domain']} | **Query:** {result['query']}")
    output.append(f"**Source:** {result['file']} | **Found:** {result['count']} results\n")

    for i, row in enumerate(result['results'], 1):
        output.append(f"### Result {i}")
        used = 0
        omitted = 0
        for key, value in row.items():
            value_str = str(value).strip()
            if not value_str:
                continue
            # A truncated URL or import line is useless; generation reads
            # the full CSV row, so skip these in search output entirely.
            if value_str.startswith(("http", "@import")):
                omitted += 1
                continue
            if used >= RESULT_CHAR_BUDGET:
                omitted += 1
                continue
            if len(value_str) > FIELD_CHAR_CAP:
                value_str = value_str[:FIELD_CHAR_CAP] + "..."
            line = f"- **{key}:** {value_str}"
            output.append(line)
            used += len(line)
        if omitted:
            output.append(f"- ({omitted} more fields in {result['file']}, row above)")
        output.append("")

    return "\n".join(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Design intelligence search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--domain", "-d", choices=list(CSV_CONFIG.keys()), help="Search domain")
    parser.add_argument("--stack", "-s", choices=AVAILABLE_STACKS, help=f"Stack-specific search. Available: {', '.join(AVAILABLE_STACKS)}")
    parser.add_argument("--max-results", "-n", type=int, default=MAX_RESULTS, help="Max results (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    # Design system generation
    parser.add_argument("--design-system", "-ds", action="store_true", help="Generate complete design system recommendation")
    parser.add_argument("--project-name", "-p", type=str, default=None, help="Project name for design system output")
    parser.add_argument("--style-emphasis", type=str, default=None, help="Style keywords to emphasize (seed for divergent candidates)")
    # Persistence (Master + Overrides pattern)
    parser.add_argument("--persist", action="store_true", help="Save design system to MASTER.md under --output-dir")
    parser.add_argument("--page", type=str, default=None, help="Create page-specific override file under pages/")
    parser.add_argument("--output-dir", "-o", type=str, default=DEFAULT_OUTPUT_DIR, help=f"Output directory for persisted files (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--force-master", action="store_true", help="Overwrite an existing MASTER.md when persisting (default: refuse and route to UPDATE mode)")

    args = parser.parse_args()

    if args.design_system:
        try:
            result = generate_design_system(
                args.query,
                args.project_name,
                persist=args.persist,
                page=args.page,
                output_dir=args.output_dir,
                style_emphasis=args.style_emphasis,
                force_master=args.force_master,
            )
        except FileExistsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(result)
        if args.persist:
            print("=" * 60)
            print(f"Design system persisted to {args.output_dir}/")
            print(f"  {args.output_dir}/MASTER.md (Global Source of Truth)")
            if args.page:
                page_filename = args.page.lower().replace(' ', '-')
                print(f"  {args.output_dir}/pages/{page_filename}.md (Page Overrides)")
            print("Usage: when building a page, check pages/[page].md first.")
            print("If it exists, its rules override MASTER.md. Otherwise, use MASTER.md.")
            print("=" * 60)
    elif args.stack:
        result = search_stack(args.query, args.stack, args.max_results)
        if args.json:
            import json
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(format_output(result))
    else:
        result = search(args.query, args.domain, args.max_results)
        if args.json:
            import json
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(format_output(result))
