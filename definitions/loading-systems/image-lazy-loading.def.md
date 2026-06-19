---
name: Image Lazy Loading
category: loading-systems
tags: [images, lazy-loading, performance, lcp, responsive, media]
scope: global
tokens: 340
version: 1.0.0
---

When loading images on the web, apply these rules to maximize perceived performance and avoid layout shift:

1. **Reserve space first.** Always set explicit `width`/`height` (or an `aspect-ratio`) so the browser reserves the box before the image arrives. This eliminates Cumulative Layout Shift (CLS).

2. **Lazy-load below the fold, eager-load the hero.** Add `loading="lazy"` to off-screen images. For the Largest Contentful Paint image (hero/above the fold), use `loading="eager"` and `fetchpriority="high"`, and never lazy-load it.

3. **Serve responsive sources.** Use `srcset` + `sizes` so each device downloads an appropriately sized image, and `<picture>` with modern formats (AVIF, then WebP, then a JPEG/PNG fallback). Let the browser pick the smallest sufficient asset.

4. **Use a lightweight placeholder.** Show a blurred low-quality image placeholder (LQIP), a dominant-color block, or a tiny base64 thumbnail, then fade to the full image on `load`. Decode off the main thread with `decoding="async"`.

5. **Use a generous IntersectionObserver margin.** When implementing lazy loading manually, trigger loads with a `rootMargin` of a few hundred pixels so images are ready just before they scroll into view, not after.

6. **Cap concurrency and cancel.** Limit simultaneous decodes/downloads; cancel requests for images that scroll back out of view before they finish (e.g. clear `src` / abort the fetch) so a fast scroll does not saturate the network.

7. **Preconnect and preload critical assets.** `preconnect` to the image CDN and `preload` the LCP image so it starts downloading during HTML parse.

Measure LCP and CLS on real devices; a beautiful blur-up that delays LCP is a regression, not a feature.
