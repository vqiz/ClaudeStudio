---
name: Bundle Optimization
category: performance
tags: [bundle, code-splitting, tree-shaking, performance, web-vitals, build]
scope: global
tokens: 360
version: 1.0.0
---

When optimizing a frontend JavaScript bundle, work in this order — measure, then cut, then split:

1. **Measure before changing anything.** Run a bundle analyzer (e.g. `rollup-plugin-visualizer`, `source-map-explorer`, `webpack-bundle-analyzer`) and identify the largest modules and duplicate dependencies. Set a budget (e.g. < 170 KB gzipped initial JS) and treat regressions as build failures.

2. **Tree-shake effectively.** Ship ESM, mark the package `"sideEffects": false` (or list the real side-effecting files), and import named members (`import { x } from 'lib'`), never whole namespaces or default-imported barrels that pull in everything.

3. **Code-split by route and interaction.** Lazy-load each route and any heavy, below-the-fold or behind-interaction component (`import()` / `React.lazy` / dynamic import). Keep the initial bundle to what is needed for first paint and interactivity.

4. **Defer and isolate third-party weight.** Audit large dependencies and prefer lighter alternatives (e.g. `date-fns`/`dayjs` over `moment`, native `Intl` over libraries). Load analytics/chat/marketing scripts with `async`/`defer` or after idle so they never block the main bundle.

5. **Avoid duplication.** Deduplicate transitive versions of the same library, extract a shared vendor/runtime chunk, and ensure polyfills are conditional (differential serving / `browserslist`) rather than always shipped.

6. **Optimize what remains.** Enable minification and compression (Brotli, then gzip fallback). Strip dev-only code via `process.env.NODE_ENV` guards and dead-code elimination. Inline only truly critical CSS; lazy-load the rest.

7. **Cache aggressively.** Emit content-hashed filenames, set long-lived immutable cache headers on hashed assets, and keep the entry HTML uncached so deploys are picked up instantly.

8. **Re-measure and guard.** Re-run the analyzer, confirm the budget holds, and wire a bundle-size check into CI so the win does not silently erode over time.
