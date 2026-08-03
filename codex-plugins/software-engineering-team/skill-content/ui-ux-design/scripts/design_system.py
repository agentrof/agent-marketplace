# -*- coding: utf-8 -*-
"""Design System Generator - aggregates corpus searches, applies reasoning
rules, and persists a Master + page-overrides design system.

Usage:
    from design_system import generate_design_system
    text = generate_design_system("SaaS dashboard", "My Project")
    text = generate_design_system("SaaS dashboard", "My Project", persist=True)
    text = generate_design_system("SaaS dashboard", "My Project", persist=True,
                                  page="dashboard", style_emphasis="brutalism bold")
"""

import csv
import json
import re
from pathlib import Path
from core import search, DATA_DIR

# ============ CONFIGURATION ============
REASONING_FILE = "ui-reasoning.csv"
DEFAULT_OUTPUT_DIR = "workspace/docs/design-system"

SEARCH_CONFIG = {
    "product": {"max_results": 1},
    "style": {"max_results": 3},
    "color": {"max_results": 2},
    "landing": {"max_results": 2},
    "typography": {"max_results": 2}
}

BREAKPOINTS = [375, 640, 768, 1024, 1280, 1536]


# ============ DESIGN SYSTEM GENERATOR ============
class DesignSystemGenerator:
    """Generates design system recommendations from aggregated searches."""

    def __init__(self):
        self.reasoning_data = self._load_reasoning()

    def _load_reasoning(self) -> list:
        """Load reasoning rules from CSV."""
        filepath = DATA_DIR / REASONING_FILE
        if not filepath.exists():
            return []
        with open(filepath, 'r', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def _multi_domain_search(self, query: str, style_priority: list = None) -> dict:
        """Execute searches across multiple domains.

        The style search injects the top style-priority keywords into the
        query. This injection point is the divergence lever: re-running with
        a different emphasis yields a genuinely different candidate system.
        The color and typography searches receive the same terms once (not
        doubled) so palettes and type pairings diverge with the emphasis
        instead of only the style row changing.
        """
        results = {}
        priority_query = " ".join(style_priority[:2]) if style_priority else ""
        for domain, config in SEARCH_CONFIG.items():
            if domain == "style" and priority_query:
                # Priority terms lead and repeat so they dominate the BM25
                # ranking, and the pool widens so _select_best_match has
                # genuinely different candidates to choose from.
                results[domain] = search(
                    f"{priority_query} {priority_query} {query}", domain,
                    max(config["max_results"], 8))
            elif domain in ("color", "typography") and priority_query:
                # Widened pool for the same reason as style: the base query's
                # head terms dominate BM25 rank 1, so the emphasis match is
                # often at rank 2-5 and selection must be able to reach it.
                results[domain] = search(f"{query} {priority_query}", domain,
                                         max(config["max_results"], 5))
            else:
                results[domain] = search(query, domain, config["max_results"])
        return results

    def _find_reasoning_rule(self, category: str) -> dict:
        """Find matching reasoning rule for a category (exact, partial, keyword)."""
        category_lower = category.lower()

        for rule in self.reasoning_data:
            if rule.get("UI_Category", "").lower() == category_lower:
                return rule

        for rule in self.reasoning_data:
            ui_cat = rule.get("UI_Category", "").lower()
            if ui_cat in category_lower or category_lower in ui_cat:
                return rule

        for rule in self.reasoning_data:
            ui_cat = rule.get("UI_Category", "").lower()
            keywords = ui_cat.replace("/", " ").replace("-", " ").split()
            if any(kw in category_lower for kw in keywords):
                return rule

        return {}

    def _apply_reasoning(self, category: str) -> dict:
        """Apply reasoning rules for a product category."""
        rule = self._find_reasoning_rule(category)

        if not rule:
            return {
                "pattern": "Hero + Features + CTA",
                "style_priority": ["Minimalism", "Flat Design"],
                "color_mood": "Professional",
                "typography_mood": "Clean",
                "key_effects": "Subtle hover transitions",
                "anti_patterns": "",
                "decision_rules": {},
                "severity": "MEDIUM"
            }

        decision_rules = {}
        try:
            decision_rules = json.loads(rule.get("Decision_Rules", "{}"))
        except json.JSONDecodeError:
            pass

        return {
            "pattern": rule.get("Recommended_Pattern", ""),
            "style_priority": [s.strip() for s in rule.get("Style_Priority", "").split("+")],
            "color_mood": rule.get("Color_Mood", ""),
            "typography_mood": rule.get("Typography_Mood", ""),
            "key_effects": rule.get("Key_Effects", ""),
            "anti_patterns": rule.get("Anti_Patterns", ""),
            "decision_rules": decision_rules,
            "severity": rule.get("Severity", "MEDIUM")
        }

    def _select_best_match(self, results: list, priority_keywords: list) -> dict:
        """Select best matching result based on priority keywords."""
        if not results:
            return {}
        if not priority_keywords:
            return results[0]

        # Stems bridge morphology gaps: "brutalist" reaches Brutalism and
        # Neubrutalism, "glassmorphism" reaches Glassmorphism.
        def _stem(word):
            word = word.lower().strip()
            return word[:7] if len(word) > 7 else word

        # First: style name match on stems
        for priority in priority_keywords:
            p_stem = _stem(priority)
            if len(p_stem) < 4:
                continue
            for result in results:
                if p_stem in result.get("Style Category", "").lower():
                    return result

        # Second: score by keyword match (style name +10, Keywords field +3, other +1)
        scored = []
        for result in results:
            result_str = str(result).lower()
            score = 0
            for kw in priority_keywords:
                kw_stem = _stem(kw)
                if len(kw_stem) < 3:
                    continue
                if kw_stem in result.get("Style Category", "").lower():
                    score += 10
                elif kw_stem in result.get("Keywords", "").lower():
                    score += 3
                elif kw_stem in result_str:
                    score += 1
            scored.append((score, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored and scored[0][0] > 0 else results[0]

    def generate(self, query: str, project_name: str = None, style_emphasis: str = None) -> dict:
        """Generate complete design system recommendation.

        style_emphasis: optional keywords that override the reasoning rule's
        style priority, used to produce divergent candidate systems.
        """
        # Step 1: search product to get category
        product_result = search(query, "product", 1)
        product_results = product_result.get("results", [])
        category = "General"
        if product_results:
            category = product_results[0].get("Product Type", "General")

        # Step 2: reasoning rules for this category
        reasoning = self._apply_reasoning(category)
        style_priority = reasoning.get("style_priority", [])
        if style_emphasis:
            style_priority = [s.strip() for s in re.split(r"[+,]", style_emphasis) if s.strip()] + style_priority

        # Step 3: multi-domain search with style priority hints
        search_results = self._multi_domain_search(query, style_priority)
        search_results["product"] = product_result

        # Step 4: select best matches per domain
        style_results = search_results.get("style", {}).get("results", [])
        color_results = search_results.get("color", {}).get("results", [])
        typography_results = search_results.get("typography", {}).get("results", [])
        landing_results = search_results.get("landing", {}).get("results", [])

        best_style = self._select_best_match(style_results, style_priority)
        # Color and typography follow the same priority-keyed selection as
        # style so the emphasis reaches palette and pairing, not just the
        # style row; with no keyword hit this falls back to BM25 rank 1.
        best_color = self._select_best_match(color_results, style_priority)
        best_typography = self._select_best_match(typography_results, style_priority)
        best_landing = landing_results[0] if landing_results else {}

        # Step 5: assemble
        style_effects = best_style.get("Effects & Animation", "")
        combined_effects = style_effects if style_effects else reasoning.get("key_effects", "")

        palette = {
            "primary": best_color.get("Primary", "#2563EB"),
            "on_primary": best_color.get("On Primary", "#FFFFFF"),
            "secondary": best_color.get("Secondary", "#3B82F6"),
            "accent": best_color.get("Accent", "#F97316"),
            "background": best_color.get("Background", "#F8FAFC"),
            "foreground": best_color.get("Foreground", "#1E293B"),
            "muted": best_color.get("Muted", "#F1F5F9"),
            "border": best_color.get("Border", "#E2E8F0"),
            "destructive": best_color.get("Destructive", "#DC2626"),
            "ring": best_color.get("Ring", best_color.get("Primary", "#2563EB")),
        }

        # Dark-native styles (Light Mode "No" or Dark Mode "Only") publish
        # their palette as the dark theme; the light pair is derived, not
        # sourced. Light-native styles keep the original direction.
        dark_native = (best_style.get("Light Mode", "").strip().lower() == "no"
                       or best_style.get("Dark Mode", "").strip().lower() == "only")
        if dark_native:
            dark = palette
            light = derive_light_palette(dark)
            status = derive_status_colors(light)
            light.update(status)
            dark.update(derive_dark_palette(status))
        else:
            light = palette
            light.update(derive_status_colors(light))
            dark = derive_dark_palette(light)

        return {
            "project_name": project_name or query.upper(),
            "category": category,
            "style_emphasis": style_emphasis or "",
            "pattern": {
                "name": best_landing.get("Pattern Name", reasoning.get("pattern", "Hero + Features + CTA")),
                "sections": best_landing.get("Section Order", "Hero > Features > CTA"),
                "cta_placement": best_landing.get("Primary CTA Placement", "Above fold"),
                "color_strategy": best_landing.get("Color Strategy", ""),
                "conversion": best_landing.get("Conversion Optimization", "")
            },
            "style": {
                "name": best_style.get("Style Category", "Minimalism"),
                "type": best_style.get("Type", "General"),
                "effects": style_effects,
                "keywords": best_style.get("Keywords", ""),
                "best_for": best_style.get("Best For", ""),
                "performance": best_style.get("Performance", ""),
                "accessibility": best_style.get("Accessibility", ""),
                "light_mode": best_style.get("Light Mode", ""),
                "dark_mode": best_style.get("Dark Mode", ""),
                "system_variables": best_style.get("Design System Variables", ""),
            },
            "colors": light,
            "colors_dark": dark,
            "palette_native_mode": "dark" if dark_native else "light",
            "color_notes": best_color.get("Notes", ""),
            "typography": {
                "heading": best_typography.get("Heading Font", "Inter"),
                "body": best_typography.get("Body Font", "Inter"),
                "category": best_typography.get("Category", ""),
                "mood": best_typography.get("Mood/Style Keywords", reasoning.get("typography_mood", "")),
                "best_for": best_typography.get("Best For", ""),
                "google_fonts_url": best_typography.get("Google Fonts URL", ""),
                "css_import": best_typography.get("CSS Import", "")
            },
            "radius": derive_radius_scale(best_style.get("Design System Variables", "")),
            "motion": derive_motion_tokens(combined_effects),
            "icon_set": select_icon_set(category, best_style.get("Style Category", "")),
            "key_effects": combined_effects,
            "anti_patterns": reasoning.get("anti_patterns", ""),
            "decision_rules": reasoning.get("decision_rules", {}),
            "severity": reasoning.get("severity", "MEDIUM")
        }


# ============ DERIVED TOKEN HELPERS ============
def _hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    if len(hex_color) != 6:
        return None
    try:
        return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02X}" for c in rgb)


def _rgb_to_hls(r, g, b):
    """RGB (0..1) to HLS (0..1). Self-contained so no color module is needed."""
    maxc, minc = max(r, g, b), min(r, g, b)
    l = (minc + maxc) / 2.0
    if minc == maxc:
        return 0.0, l, 0.0
    delta = maxc - minc
    s = delta / (2.0 - maxc - minc) if l > 0.5 else delta / (maxc + minc)
    rc = (maxc - r) / delta
    gc = (maxc - g) / delta
    bc = (maxc - b) / delta
    if r == maxc:
        h = bc - gc
    elif g == maxc:
        h = 2.0 + rc - bc
    else:
        h = 4.0 + gc - rc
    return (h / 6.0) % 1.0, l, s


def _hls_to_rgb(h, l, s):
    """HLS (0..1) to RGB (0..1)."""
    if s == 0.0:
        return l, l, l
    m2 = l * (1.0 + s) if l <= 0.5 else l + s - (l * s)
    m1 = 2.0 * l - m2

    def _v(m1, m2, hue):
        hue = hue % 1.0
        if hue < 1 / 6:
            return m1 + (m2 - m1) * hue * 6.0
        if hue < 0.5:
            return m2
        if hue < 2 / 3:
            return m1 + (m2 - m1) * (2 / 3 - hue) * 6.0
        return m1

    return _v(m1, m2, h + 1 / 3), _v(m1, m2, h), _v(m1, m2, h - 1 / 3)


def _adjust(hex_color: str, lightness=None, sat_factor=1.0, l_min=0.0, l_max=1.0) -> str:
    """Shift a hex color in HLS space. Returns the input unchanged if unparseable."""
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return hex_color
    h, l, s = _rgb_to_hls(*rgb)
    l = l if lightness is None else lightness
    l = max(l_min, min(l_max, l))
    s = max(0.0, min(1.0, s * sat_factor))
    return _rgb_to_hex(_hls_to_rgb(h, l, s))


def derive_dark_palette(light: dict) -> dict:
    """Derive the dark value for each semantic role deterministically.

    Derivation rules (documented so the output is reproducible):
    - Surfaces (background, muted, border) swap their lightness relationship:
      dark lightness is approximately 1 - light lightness, clamped so the
      background stays near-black without being pure black and borders stay
      visible (background 0.06-0.12, muted 0.12-0.20, border 0.18-0.30).
    - Text (foreground, on_primary) swaps the same way, clamped to 0.85-0.96
      to avoid pure-white glare on dark surfaces.
    - Accents (primary, secondary, accent, ring) keep their hue, desaturate
      by about 15 percent, and lift lightness into 0.55-0.72 so they read on
      a dark background without vibrating.
    - Destructive keeps its hue (red must stay red), desaturates 15 percent,
      and lifts to 0.55-0.68.
    """
    dark = {}
    for role, value in light.items():
        rgb = _hex_to_rgb(value)
        if rgb is None:
            dark[role] = value
            continue
        _, l, _ = _rgb_to_hls(*rgb)
        swapped = 1.0 - l
        if role == "background":
            dark[role] = _adjust(value, lightness=swapped, l_min=0.06, l_max=0.12)
        elif role == "muted":
            dark[role] = _adjust(value, lightness=swapped, l_min=0.12, l_max=0.20)
        elif role == "border":
            dark[role] = _adjust(value, lightness=swapped, l_min=0.18, l_max=0.30)
        elif role in ("foreground", "on_primary"):
            dark[role] = _adjust(value, lightness=swapped, l_min=0.85, l_max=0.96)
        elif role == "destructive":
            dark[role] = _adjust(value, lightness=max(l, 0.55), sat_factor=0.85, l_min=0.55, l_max=0.68)
        else:  # primary, secondary, accent, ring
            dark[role] = _adjust(value, lightness=max(l, 0.55), sat_factor=0.85, l_min=0.55, l_max=0.72)
    return dark


def derive_light_palette(dark: dict) -> dict:
    """Inverse of derive_dark_palette, for dark-native styles.

    The dark palette is the sourced one; the light pair applies the same
    lightness swap in reverse:
    - Surfaces swap toward white (background 0.88-0.97, muted 0.80-0.90,
      border 0.70-0.85).
    - Text (foreground, on_primary) swaps toward near-black (0.08-0.20).
    - Accents (primary, secondary, accent, ring) keep their hue, regain the
      15 percent saturation, and settle at 0.35-0.50 lightness so they read
      on a light background.
    - Destructive keeps its hue at 0.38-0.50 lightness.
    """
    light = {}
    for role, value in dark.items():
        rgb = _hex_to_rgb(value)
        if rgb is None:
            light[role] = value
            continue
        _, l, _ = _rgb_to_hls(*rgb)
        swapped = 1.0 - l
        if role == "background":
            light[role] = _adjust(value, lightness=swapped, l_min=0.88, l_max=0.97)
        elif role == "muted":
            light[role] = _adjust(value, lightness=swapped, l_min=0.80, l_max=0.90)
        elif role == "border":
            light[role] = _adjust(value, lightness=swapped, l_min=0.70, l_max=0.85)
        elif role in ("foreground", "on_primary"):
            light[role] = _adjust(value, lightness=swapped, l_min=0.08, l_max=0.20)
        elif role == "destructive":
            light[role] = _adjust(value, lightness=min(l, 0.45), sat_factor=1 / 0.85, l_min=0.38, l_max=0.50)
        else:  # primary, secondary, accent, ring
            light[role] = _adjust(value, lightness=min(l, 0.45), sat_factor=1 / 0.85, l_min=0.35, l_max=0.50)
    return light


# Fixed status hues (degrees / 360): green for success, amber for warning.
SUCCESS_HUE = 145 / 360
WARNING_HUE = 38 / 360


def derive_status_colors(light: dict) -> dict:
    """Derive Success and Warning roles from the palette's accent character.

    Fixed hues (green 145, amber 38) take the accent's saturation and
    lightness, clamped to 0.45-0.90 saturation and 0.32-0.50 lightness, so
    status colors match the palette's intensity instead of being invented
    downstream. Their dark pair follows the standard accent dark rule.
    """
    seed = light.get("accent") or light.get("primary") or "#2563EB"
    rgb = _hex_to_rgb(seed)
    if rgb is None:
        rgb = _hex_to_rgb("#2563EB")
    _, l, s = _rgb_to_hls(*rgb)
    s = max(0.45, min(0.90, s))
    l = max(0.32, min(0.50, l))
    return {
        "success": _rgb_to_hex(_hls_to_rgb(SUCCESS_HUE, l, s)),
        "warning": _rgb_to_hex(_hls_to_rgb(WARNING_HUE, l, s)),
    }


_FALLBACK_STACKS = {
    "mono": "ui-monospace, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
    "serif": "ui-serif, Georgia, 'Times New Roman', serif",
    "sans": "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
}


def _classify_face(label: str) -> str:
    """Map a pairing-category part to serif/sans/mono for the fallback stack."""
    label = (label or "").lower()
    if "mono" in label:
        return "mono"
    if "serif" in label and "sans" not in label:
        return "serif"
    return "sans"


def font_fallback_stacks(category: str) -> tuple:
    """Heading and body fallback stacks from the pairing's category field.

    'Serif + Sans' classifies the heading face serif and the body face
    sans; a single-part category applies to both faces. Display, script,
    and handwritten faces fall back to the sans stack.
    """
    parts = [p.strip() for p in (category or "").split("+") if p.strip()]
    heading = _classify_face(parts[0] if parts else "")
    body = _classify_face(parts[1] if len(parts) > 1 else (parts[0] if parts else ""))
    return _FALLBACK_STACKS[heading], _FALLBACK_STACKS[body]


def derive_radius_scale(system_variables: str) -> dict:
    """Derive a radius scale from the style row's design-system variables.

    Reads a base like '--border-radius: 12px'; falls back to 8px when absent.
    A zero base (sharp styles) keeps every step at 0 to honor the style.
    """
    match = re.search(r"radius[^:]*:\s*(\d+)\s*px", system_variables or "", re.IGNORECASE)
    base = int(match.group(1)) if match else 8
    if base == 0:
        return {"sm": "0px", "md": "0px", "lg": "0px", "xl": "0px", "full": "0px"}
    return {
        "sm": f"{max(2, base // 2)}px",
        "md": f"{base}px",
        "lg": f"{round(base * 1.5)}px",
        "xl": f"{base * 2}px",
        "full": "9999px",
    }


def derive_motion_tokens(effects_text: str) -> dict:
    """Derive motion tokens from the style's effects text.

    Uses durations named in the effects description when present; otherwise
    the 150/200/300ms defaults from the animation guidelines.
    """
    nums = [int(n) for n in re.findall(r"(\d{2,4})\s*ms", effects_text or "")]
    nums += [int(n) for pair in re.findall(r"(\d{2,4})-(\d{2,4})\s*ms", effects_text or "") for n in pair]
    if nums:
        normal = max(150, min(300, min(nums)))
    else:
        normal = 200
    tokens = {
        "fast": f"{max(100, normal - 50)}ms",
        "normal": f"{normal}ms",
        "slow": f"{min(400, normal + 100)}ms",
        "easing": "ease-out on enter, ease-in on exit; never linear for UI transitions",
    }
    if "spring" in (effects_text or "").lower():
        tokens["easing"] += "; prefer spring curves for expressive moments"
    return tokens


def select_icon_set(category: str, style_name: str) -> str:
    """Declare one icon set for the system, sourced from the icons corpus."""
    result = search(f"{category} {style_name} interface icons", "icons", 1)
    rows = result.get("results", [])
    library = (rows[0].get("Library", "") if rows else "").split("(")[0].strip()
    style = rows[0].get("Style", "").strip() if rows else ""
    if library:
        detail = f" ({style})" if style else ""
        return f"{library}{detail} - single set, consistent stroke width, no emoji glyphs"
    return "One outline vector icon set - consistent stroke width, no emoji glyphs"


# ============ OUTPUT FORMATTERS ============
COLOR_ROLES = [
    ("Primary", "primary", "--color-primary"),
    ("On Primary", "on_primary", "--color-on-primary"),
    ("Secondary", "secondary", "--color-secondary"),
    ("Accent/CTA", "accent", "--color-accent"),
    ("Background", "background", "--color-background"),
    ("Foreground", "foreground", "--color-foreground"),
    ("Muted", "muted", "--color-muted"),
    ("Border", "border", "--color-border"),
    ("Destructive", "destructive", "--color-destructive"),
    ("Success", "success", "--color-success"),
    ("Warning", "warning", "--color-warning"),
    ("Ring", "ring", "--color-ring"),
]

FORBIDDEN_PATTERNS = [
    "Emojis as icons - use the declared vector icon set",
    "Missing cursor:pointer on clickable elements",
    "Layout-shifting hovers - avoid transforms that move surrounding content",
    "Low contrast text - maintain 4.5:1 minimum ratio",
    "Instant state changes - always transition (150-300ms)",
    "Invisible focus states - focus must be visible for keyboard users",
]

CHECKLIST_ITEMS = [
    "No emojis used as icons (use SVG from the declared icon set)",
    "All icons from one consistent icon set",
    "cursor-pointer on all clickable elements",
    "Hover states with smooth transitions (150-300ms)",
    "Text contrast 4.5:1 minimum, verified in light AND dark palette",
    "Focus states visible for keyboard navigation",
    "prefers-reduced-motion respected",
    "Responsive at 375, 640, 768, 1024, 1280, 1536px",
    "No content hidden behind fixed navbars",
    "No horizontal scroll on mobile",
]


def _color_table(design_system: dict) -> list:
    """Light + dark palette pair as a markdown table."""
    colors = design_system.get("colors", {})
    dark = design_system.get("colors_dark", {})
    lines = ["| Role | Light | Dark | CSS Variable |", "|------|-------|------|--------------|"]
    for label, key, css_var in COLOR_ROLES:
        light_val = colors.get(key, "")
        if light_val:
            lines.append(f"| {label} | `{light_val}` | `{dark.get(key, light_val)}` | `{css_var}` |")
    return lines


def format_markdown(design_system: dict) -> str:
    """Format design system as chat-friendly markdown."""
    pattern = design_system.get("pattern", {})
    style = design_system.get("style", {})
    typography = design_system.get("typography", {})
    lines = [f"## Design System: {design_system.get('project_name', 'PROJECT')}", ""]

    lines.append("### Pattern")
    lines.append(f"- **Name:** {pattern.get('name', '')}")
    if pattern.get("conversion"):
        lines.append(f"- **Conversion Focus:** {pattern.get('conversion', '')}")
    if pattern.get("cta_placement"):
        lines.append(f"- **CTA Placement:** {pattern.get('cta_placement', '')}")
    lines.append(f"- **Sections:** {pattern.get('sections', '')}")
    lines.append("")

    lines.append("### Style")
    lines.append(f"- **Name:** {style.get('name', '')}")
    if style.get("keywords"):
        lines.append(f"- **Keywords:** {style.get('keywords', '')}")
    if style.get("best_for"):
        lines.append(f"- **Best For:** {style.get('best_for', '')}")
    lines.append("")

    lines.append("### Colors (Light / Dark)")
    lines.extend(_color_table(design_system))
    if design_system.get("color_notes"):
        lines.append(f"\n*Notes: {design_system.get('color_notes', '')}*")
    lines.append("")

    lines.append("### Typography")
    lines.append(f"- **Heading:** {typography.get('heading', '')} | **Body:** {typography.get('body', '')}")
    if typography.get("google_fonts_url"):
        lines.append(f"- **Fonts URL:** {typography.get('google_fonts_url', '')}")
    lines.append("")

    radius = design_system.get("radius", {})
    motion = design_system.get("motion", {})
    lines.append(f"### Tokens")
    lines.append(f"- **Radius:** sm {radius.get('sm')} / md {radius.get('md')} / lg {radius.get('lg')} / xl {radius.get('xl')}")
    lines.append(f"- **Motion:** fast {motion.get('fast')} / normal {motion.get('normal')} / slow {motion.get('slow')}; {motion.get('easing')}")
    lines.append(f"- **Icon Set:** {design_system.get('icon_set', '')}")
    lines.append(f"- **Breakpoints:** {', '.join(str(b) for b in BREAKPOINTS)}px")
    lines.append("")

    if design_system.get("key_effects"):
        lines.append("### Key Effects")
        lines.append(design_system["key_effects"])
        lines.append("")

    if design_system.get("anti_patterns"):
        lines.append("### Avoid")
        for anti in design_system["anti_patterns"].split(" + "):
            lines.append(f"- DO NOT: {anti}")
        lines.append("")

    lines.append("### Pre-Delivery Checklist")
    for item in CHECKLIST_ITEMS:
        lines.append(f"- [ ] {item}")
    lines.append("")
    return "\n".join(lines)


def vault_frontmatter(doc_type: str, title: str) -> list[str]:
    """Vault-law frontmatter (obsidian-vault skill): typed note, block-list
    tag mirror. The writer emits compliance; personas never patch it in."""
    return [
        "---",
        f"type: {doc_type}",
        f"title: {title}",
        "tags:",
        f"  - doc/{doc_type.replace('_', '-')}",
        "---",
        "",
    ]


def vault_nav_section(peers: list[tuple[str, str]]) -> list[str]:
    """The nav section every authored vault note ends with: owning map
    first, then the contextual peers."""
    links = ["[[maps/design-system|Design System]]"]
    links += [f"[[{target}|{label}]]" for target, label in peers]
    return ["", "## Navigation <!-- sec: nav -->", "", " -\n".join(links), ""]


def format_master_md(design_system: dict) -> str:
    """Format design system as MASTER.md with hierarchical override logic."""
    pattern = design_system.get("pattern", {})
    style = design_system.get("style", {})
    typography = design_system.get("typography", {})
    radius = design_system.get("radius", {})
    motion = design_system.get("motion", {})

    # Born-compliant title: a natural phrase closing in the canonical
    # ENGLISH designation ("design master") so it passes the vault's
    # word-boundary designation check against an English default map; a
    # non-English project's build-docs-vault localizes it like any title.
    title = f"{design_system.get('project_name', 'PROJECT')} design master"
    lines = []
    lines += vault_frontmatter("design_master", title)
    lines.append(f"# {title}")
    lines.append("")
    lines.append("> **LOGIC:** When building a specific page, first check `pages/[page-name].md`")
    lines.append("> next to this file. If that file exists, its rules **override** this Master file.")
    lines.append("> If not, strictly follow the rules below.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**Project:** {design_system.get('project_name', 'PROJECT')}")
    lines.append(f"**Category:** {design_system.get('category', 'General')}")
    if design_system.get("style_emphasis"):
        lines.append(f"**Style Emphasis:** {design_system.get('style_emphasis')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Global Rules")
    lines.append("")

    lines.append("### Color Palette (Light + Dark)")
    lines.append("")
    lines.extend(_color_table(design_system))
    lines.append("")
    if design_system.get("palette_native_mode", "light") == "dark":
        lines.append("Native mode: dark; light derived. The style is dark-native, so the")
        lines.append("Dark column holds the sourced palette. Light values invert the same")
        lines.append("lightness swap deterministically: surfaces lift toward white")
        lines.append("(background 0.88-0.97 lightness), text drops to near-black")
        lines.append("(0.08-0.20), accents keep hue, regain the 15 percent saturation, and")
        lines.append("deepen to 0.35-0.50 so they read on light surfaces.")
    else:
        lines.append("Native mode: light; dark derived. Dark values are derived")
        lines.append("deterministically: surfaces swap lightness (background near-black,")
        lines.append("borders stay visible), text lifts to 0.85-0.96 lightness, accents")
        lines.append("keep hue with about 15 percent desaturation, and destructive keeps")
        lines.append("its red hue.")
    lines.append("Success and Warning are derived, not sourced: fixed hues (green 145,")
    lines.append("amber 38) take the accent's saturation and lightness (clamped to")
    lines.append("0.45-0.90 and 0.32-0.50) so status colors match the palette's")
    lines.append("intensity; their dark values follow the accent dark rule. Verify")
    lines.append("contrast per theme; do not assume light-mode ratios carry over.")
    lines.append("")
    if design_system.get("color_notes"):
        lines.append(f"**Color Notes:** {design_system.get('color_notes', '')}")
        lines.append("")

    lines.append("### Typography")
    lines.append("")
    lines.append(f"- **Heading Font:** {typography.get('heading', 'Inter')}")
    lines.append(f"- **Body Font:** {typography.get('body', 'Inter')}")
    if typography.get("mood"):
        lines.append(f"- **Mood:** {typography.get('mood', '')}")
    if typography.get("google_fonts_url"):
        lines.append(f"- **Google Fonts:** [{typography.get('heading', '')} + {typography.get('body', '')}]({typography.get('google_fonts_url', '')})")
    lines.append("")
    if typography.get("css_import"):
        lines.append("**CSS Import:**")
        lines.append("```css")
        lines.append(typography.get("css_import", ""))
        lines.append("```")
        lines.append("")
    heading_stack, body_stack = font_fallback_stacks(typography.get("category", ""))
    lines.append(f"**Local fallback stack:** headings `{heading_stack}`; body `{body_stack}`")
    lines.append("")
    lines.append("Previews and demos never fetch remote fonts; they use the fallback stack. The import line is for application code only.")
    lines.append("")

    lines.append("### Spacing Variables")
    lines.append("")
    lines.append("| Token | Value | Usage |")
    lines.append("|-------|-------|-------|")
    lines.append("| `--space-xs` | `4px` / `0.25rem` | Tight gaps |")
    lines.append("| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |")
    lines.append("| `--space-md` | `16px` / `1rem` | Standard padding |")
    lines.append("| `--space-lg` | `24px` / `1.5rem` | Section padding |")
    lines.append("| `--space-xl` | `32px` / `2rem` | Large gaps |")
    lines.append("| `--space-2xl` | `48px` / `3rem` | Section margins |")
    lines.append("| `--space-3xl` | `64px` / `4rem` | Hero padding |")
    lines.append("")

    lines.append("### Radius Scale (derived from style)")
    lines.append("")
    lines.append("| Token | Value |")
    lines.append("|-------|-------|")
    for step in ("sm", "md", "lg", "xl", "full"):
        lines.append(f"| `--radius-{step}` | `{radius.get(step, '8px')}` |")
    lines.append("")

    lines.append("### Motion Tokens")
    lines.append("")
    lines.append("| Token | Value | Usage |")
    lines.append("|-------|-------|-------|")
    lines.append(f"| `--duration-fast` | `{motion.get('fast', '150ms')}` | Hover, pressed, focus feedback |")
    lines.append(f"| `--duration-normal` | `{motion.get('normal', '200ms')}` | Micro-interactions, reveals |")
    lines.append(f"| `--duration-slow` | `{motion.get('slow', '300ms')}` | Modals, page-level transitions |")
    lines.append("")
    lines.append(f"**Easing:** {motion.get('easing', 'ease-out on enter, ease-in on exit')}")
    lines.append("")

    lines.append("### Breakpoints")
    lines.append("")
    lines.append("| Name | Width | Target |")
    lines.append("|------|-------|--------|")
    labels = ["Small phone", "Large phone", "Tablet portrait", "Tablet landscape / small laptop", "Desktop", "Wide desktop"]
    for bp, label in zip(BREAKPOINTS, labels):
        lines.append(f"| `{bp}` | `{bp}px` | {label} |")
    lines.append("")

    lines.append("### Icon Set")
    lines.append("")
    lines.append(f"**Declared set:** {design_system.get('icon_set', 'One outline vector icon set')}")
    lines.append("")

    lines.append("### Shadow Depths")
    lines.append("")
    lines.append("| Level | Value | Usage |")
    lines.append("|-------|-------|-------|")
    lines.append("| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |")
    lines.append("| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |")
    lines.append("| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |")
    lines.append("| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Component Specs")
    lines.append("")
    lines.append("Components theme through the semantic tokens defined above. No raw hex")
    lines.append("in component code; the palette tables are the only place hex appears.")
    lines.append("")
    lines.append("### Buttons")
    lines.append("")
    lines.append("```css")
    lines.append("/* Primary Button */")
    lines.append(".btn-primary {")
    lines.append("  background: var(--color-primary);")
    lines.append("  color: var(--color-on-primary);")
    lines.append("  padding: 12px 24px;")
    lines.append("  border-radius: var(--radius-md);")
    lines.append("  font-weight: 600;")
    lines.append("  transition: all var(--duration-normal) ease;")
    lines.append("  cursor: pointer;")
    lines.append("}")
    lines.append("")
    lines.append(".btn-primary:hover {")
    lines.append("  opacity: 0.9;")
    lines.append("  transform: translateY(-1px);")
    lines.append("}")
    lines.append("")
    lines.append("/* Secondary Button */")
    lines.append(".btn-secondary {")
    lines.append("  background: transparent;")
    lines.append("  color: var(--color-primary);")
    lines.append("  border: 2px solid var(--color-primary);")
    lines.append("  padding: 12px 24px;")
    lines.append("  border-radius: var(--radius-md);")
    lines.append("  font-weight: 600;")
    lines.append("  transition: all var(--duration-normal) ease;")
    lines.append("  cursor: pointer;")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("### Cards")
    lines.append("")
    lines.append("```css")
    lines.append(".card {")
    lines.append("  background: var(--color-background);")
    lines.append("  border-radius: var(--radius-lg);")
    lines.append("  padding: 24px;")
    lines.append("  box-shadow: var(--shadow-md);")
    lines.append("  transition: all var(--duration-normal) ease;")
    lines.append("}")
    lines.append("")
    lines.append(".card:hover {")
    lines.append("  box-shadow: var(--shadow-lg);")
    lines.append("  transform: translateY(-2px);")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("### Inputs")
    lines.append("")
    lines.append("```css")
    lines.append(".input {")
    lines.append("  padding: 12px 16px;")
    lines.append("  border: 1px solid var(--color-border);")
    lines.append("  border-radius: var(--radius-md);")
    lines.append("  font-size: 16px;")
    lines.append("  transition: border-color var(--duration-normal) ease;")
    lines.append("}")
    lines.append("")
    lines.append(".input:focus {")
    lines.append("  border-color: var(--color-ring);")
    lines.append("  outline: none;")
    lines.append("  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-ring) 20%, transparent);")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("### Modals")
    lines.append("")
    lines.append("```css")
    lines.append(".modal-overlay {")
    lines.append("  background: rgba(0, 0, 0, 0.5);")
    lines.append("  backdrop-filter: blur(4px);")
    lines.append("}")
    lines.append("")
    lines.append(".modal {")
    lines.append("  background: var(--color-background);")
    lines.append("  border-radius: var(--radius-xl);")
    lines.append("  padding: 32px;")
    lines.append("  box-shadow: var(--shadow-xl);")
    lines.append("  max-width: 500px;")
    lines.append("  width: 90%;")
    lines.append("}")
    lines.append("```")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Style Guidelines")
    lines.append("")
    lines.append(f"**Style:** {style.get('name', 'Minimalism')}")
    lines.append("")
    if style.get("keywords"):
        lines.append(f"**Keywords:** {style.get('keywords', '')}")
        lines.append("")
    if style.get("best_for"):
        lines.append(f"**Best For:** {style.get('best_for', '')}")
        lines.append("")
    if design_system.get("key_effects"):
        lines.append(f"**Key Effects:** {design_system.get('key_effects', '')}")
        lines.append("")

    lines.append("### Page Pattern (landing and marketing surfaces)")
    lines.append("")
    lines.append("Scope: applies when composing landing/marketing surfaces; app screens")
    lines.append("follow the component specs and UX rules instead.")
    lines.append("")
    lines.append(f"**Pattern Name:** {pattern.get('name', '')}")
    lines.append("")
    if pattern.get("conversion"):
        lines.append(f"- **Conversion Strategy:** {pattern.get('conversion', '')}")
    if pattern.get("cta_placement"):
        lines.append(f"- **CTA Placement:** {pattern.get('cta_placement', '')}")
    lines.append(f"- **Section Order:** {pattern.get('sections', '')}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Anti-Patterns")
    lines.append("")
    for anti in [a.strip() for a in design_system.get("anti_patterns", "").split("+") if a.strip()]:
        lines.append(f"- DO NOT: {anti}")
    for item in FORBIDDEN_PATTERNS:
        lines.append(f"- DO NOT: {item}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Pre-Delivery Checklist")
    lines.append("")
    lines.append("Before delivering any UI code, verify:")
    lines.append("")
    for item in CHECKLIST_ITEMS:
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines += vault_nav_section([])
    return "\n".join(lines)


def format_page_override_md(design_system: dict, page_name: str, page_query: str = None) -> str:
    """Format a page-specific override file. Only deviations from the Master."""
    page_title = page_name.replace("-", " ").replace("_", " ").title()
    page_overrides = _generate_intelligent_overrides(page_name, page_query, design_system)

    # Born-compliant title closing in the canonical ENGLISH designation
    # ("page override"); the check enforces it, build-docs-vault localizes.
    title = f"{page_title} page override"
    lines = []
    lines += vault_frontmatter("page_override", title)
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> **PROJECT:** {design_system.get('project_name', 'PROJECT')}")
    lines.append(f"> **Page Type:** {page_overrides.get('page_type', 'General')}")
    lines.append("")
    lines.append("> **IMPORTANT:** Rules in this file **override** the Master file (`MASTER.md`).")
    lines.append("> Only deviations from the Master are documented here. For all other rules, refer to the Master.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Page-Specific Rules")
    lines.append("")

    for section, key in (("Layout Overrides", "layout"), ("Spacing Overrides", "spacing"),
                         ("Typography Overrides", "typography"), ("Color Overrides", "colors")):
        lines.append(f"### {section}")
        lines.append("")
        overrides = page_overrides.get(key, {})
        if overrides:
            for name, value in overrides.items():
                lines.append(f"- **{name}:** {value}")
        else:
            lines.append(f"- No overrides - use Master {key}")
        lines.append("")

    lines.append("### Component Overrides")
    lines.append("")
    components = page_overrides.get("components", [])
    if components:
        for comp in components:
            lines.append(f"- {comp}")
    else:
        lines.append("- No overrides - use Master component specs")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    for rec in page_overrides.get("recommendations", []):
        lines.append(f"- {rec}")
    lines.append("")
    lines += vault_nav_section([("design-system/MASTER", "Design Master")])
    return "\n".join(lines)


def _generate_intelligent_overrides(page_name: str, page_query: str, design_system: dict) -> dict:
    """Generate overrides for a page type using layered search (style + ux + landing)."""
    combined_context = f"{page_name.lower()} {(page_query or '').lower()}"

    style_results = search(combined_context, "style", max_results=1).get("results", [])
    ux_results = search(combined_context, "ux", max_results=3).get("results", [])
    landing_results = search(combined_context, "landing", max_results=1).get("results", [])

    page_type = _detect_page_type(combined_context, style_results)

    layout, spacing, typography, colors = {}, {}, {}, {}
    components, recommendations = [], []

    if style_results:
        style = style_results[0]
        keywords = style.get("Keywords", "").lower()
        effects = style.get("Effects & Animation", "")
        if any(kw in keywords for kw in ["data", "dense", "dashboard", "grid"]):
            layout["Max Width"] = "1400px or full-width"
            layout["Grid"] = "12-column grid for data flexibility"
            spacing["Content Density"] = "High - optimize for information display"
        elif any(kw in keywords for kw in ["minimal", "simple", "clean", "single"]):
            layout["Max Width"] = "800px (narrow, focused)"
            layout["Layout"] = "Single column, centered"
            spacing["Content Density"] = "Low - focus on clarity"
        else:
            layout["Max Width"] = "1200px (standard)"
            layout["Layout"] = "Full-width sections, centered content"
        if effects:
            recommendations.append(f"Effects: {effects}")

    for ux in ux_results:
        if ux.get("Do"):
            recommendations.append(f"{ux.get('Category', '')}: {ux.get('Do', '')}")
        dont_text = ux.get("Don't", "")
        if dont_text:
            components.append(f"Avoid: {dont_text}")

    if landing_results:
        landing = landing_results[0]
        if landing.get("Section Order"):
            layout["Sections"] = landing.get("Section Order")
        if landing.get("Primary CTA Placement"):
            recommendations.append(f"CTA Placement: {landing.get('Primary CTA Placement')}")
        if landing.get("Color Strategy"):
            colors["Strategy"] = landing.get("Color Strategy")

    if not layout:
        layout = {"Max Width": "1200px", "Layout": "Responsive grid"}
    if not recommendations:
        recommendations = ["Refer to MASTER.md for all design rules",
                           "Add specific overrides as needed for this page"]

    return {
        "page_type": page_type,
        "layout": layout,
        "spacing": spacing,
        "typography": typography,
        "colors": colors,
        "components": components,
        "recommendations": recommendations,
    }


def _detect_page_type(context: str, style_results: list) -> str:
    """Detect page archetype from context and search results."""
    context_lower = context.lower()
    page_patterns = [
        (["dashboard", "admin", "analytics", "data", "metrics", "stats", "monitor", "overview"], "Dashboard / Data View"),
        (["checkout", "payment", "cart", "purchase", "order", "billing"], "Checkout / Payment"),
        (["settings", "profile", "account", "preferences", "config"], "Settings / Profile"),
        (["landing", "marketing", "homepage", "hero", "home", "promo"], "Landing / Marketing"),
        (["login", "signin", "signup", "register", "auth", "password"], "Authentication"),
        (["pricing", "plans", "subscription", "tiers", "packages"], "Pricing / Plans"),
        (["blog", "article", "post", "news", "content", "story"], "Blog / Article"),
        (["product", "item", "detail", "pdp", "shop", "store"], "Product Detail"),
        (["search", "results", "browse", "filter", "catalog", "list"], "Search Results"),
        (["empty", "404", "error", "not found", "zero"], "Empty State"),
    ]
    for keywords, page_type in page_patterns:
        if any(kw in context_lower for kw in keywords):
            return page_type
    if style_results:
        best_for = style_results[0].get("Best For", "").lower()
        if "dashboard" in best_for or "data" in best_for:
            return "Dashboard / Data View"
        if "landing" in best_for or "marketing" in best_for:
            return "Landing / Marketing"
    return "General"


# ============ PERSISTENCE ============
def persist_design_system(design_system: dict, page: str = None, output_dir: str = None,
                          page_query: str = None, force: bool = False) -> dict:
    """Persist to <output_dir>/MASTER.md (+ pages/<page>.md).

    Default output_dir is the workspace convention workspace/docs/design-system.
    An existing MASTER.md is the project's source of truth: regenerating over
    it silently would discard curated rules, so this raises FileExistsError
    (before writing anything) unless force is True.
    """
    base_dir = Path(output_dir) if output_dir else Path(DEFAULT_OUTPUT_DIR)
    master_file = base_dir / "MASTER.md"
    if master_file.exists() and not force:
        raise FileExistsError(
            f"{master_file} already exists. Refusing to overwrite: modify it through the "
            "design-system entry's UPDATE mode, or re-run with --force-master to regenerate.")
    pages_dir = base_dir / "pages"
    base_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    created_files = []
    master_file.write_text(format_master_md(design_system), encoding="utf-8")
    created_files.append(str(master_file))

    if page:
        page_file = pages_dir / f"{page.lower().replace(' ', '-')}.md"
        page_file.write_text(format_page_override_md(design_system, page, page_query), encoding="utf-8")
        created_files.append(str(page_file))

    return {"status": "success", "design_system_dir": str(base_dir), "created_files": created_files}


# ============ MAIN ENTRY POINT ============
def generate_design_system(query: str, project_name: str = None, persist: bool = False,
                           page: str = None, output_dir: str = None,
                           style_emphasis: str = None, force_master: bool = False) -> str:
    """Generate (and optionally persist) a design system; returns markdown.

    Raises FileExistsError when persisting over an existing MASTER.md
    without force_master.
    """
    generator = DesignSystemGenerator()
    design_system = generator.generate(query, project_name, style_emphasis)
    if persist:
        persist_design_system(design_system, page, output_dir, query, force=force_master)
    return format_markdown(design_system)


# ============ CLI SUPPORT ============
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Design System")
    parser.add_argument("query", help="Search query (e.g., 'SaaS dashboard')")
    parser.add_argument("--project-name", "-p", type=str, default=None, help="Project name")
    parser.add_argument("--style-emphasis", type=str, default=None,
                        help="Style keywords to emphasize (divergent candidate seed)")
    args = parser.parse_args()

    print(generate_design_system(args.query, args.project_name, style_emphasis=args.style_emphasis))
