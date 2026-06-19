---
name: Color Token System
category: ui-design
tags: [design-tokens, color, theming, dark-mode, accessibility, design-system]
scope: global
tokens: 350
version: 1.0.0
---

When defining or using colors in a UI, work through a token system, never raw hex values:

1. **Three tiers of tokens.** Separate (a) **primitives** — the raw palette (`blue-500`, `gray-900`), (b) **semantic tokens** — role-based aliases that reference primitives (`color-bg-surface`, `color-text-primary`, `color-border-subtle`, `color-accent`, `color-danger`), and (c) **component tokens** where needed (`button-primary-bg`). Components consume only semantic/component tokens, never primitives or literals.

2. **Theme by remapping semantics.** Light and dark themes (and brand variants) are just different mappings of semantic tokens to primitives. Define them as CSS custom properties under a `[data-theme]` selector / `prefers-color-scheme`, so switching themes never touches component code.

3. **Encode state in tokens.** Provide tokens for interactive states — `-hover`, `-active`, `-disabled`, `-focus` — rather than computing them inline. This keeps state transitions consistent across the product.

4. **Guarantee contrast.** Every text/background semantic pair must meet WCAG AA (4.5:1 for body text, 3:1 for large text and UI boundaries). Verify both themes. Pick pairings at the token level so contrast can never silently regress in a component.

5. **Don't convey meaning by color alone.** Pair semantic color (success/warning/danger) with an icon, label, or shape so the UI is usable for color-blind users.

6. **Name by role, not by appearance.** `color-text-primary`, not `dark-gray`. A token named after its hue breaks the moment the theme changes.

7. **Single source of truth.** Define tokens once (a tokens file / JSON exported to CSS vars and platform constants) and generate platform outputs from it. Never hardcode a hex value in a component; if you reach for one, a token is missing — add it.

8. **Respect transparency and elevation deliberately.** Use dedicated overlay/scrim and elevation tokens rather than ad-hoc `rgba()`; layering should be systematic.
