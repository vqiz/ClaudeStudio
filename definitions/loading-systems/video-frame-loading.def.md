---
name: Video Frame Loading
category: loading-systems
tags: [video, streaming, frames, lazy-loading, performance, media]
scope: global
tokens: 360
version: 1.0.0
---

When implementing video playback or frame-accurate scrubbing, follow these loading rules:

1. **Stream, never preload the whole file.** Use adaptive streaming (HLS/DASH) for anything over a few seconds. For a single MP4, rely on HTTP range requests and `preload="metadata"` so only the moov/atom and the first frames load until playback starts.

2. **Decode lazily, keep a bounded cache.** When rendering frames yourself (canvas/WebGL scrubbing, timelines), decode on demand around the playhead. Keep a small LRU cache of decoded frames (e.g. ±15 frames) and evict aggressively — decoded RGBA frames are huge (width × height × 4 bytes each).

3. **Prefetch in the direction of travel.** Predict the next frames from playback/scrub direction and velocity, and warm the decoder ahead of the playhead. Cancel in-flight prefetches the moment direction reverses or the user seeks elsewhere.

4. **Show a placeholder immediately.** Render a poster image or the nearest already-decoded frame instantly, then swap in the exact frame when it is ready. Never block the UI thread on decode — do it off the main thread (WebCodecs, `requestVideoFrameCallback`, or a worker).

5. **Throttle scrub seeks.** During fast scrubbing, snap to keyframes (I-frames) for instant feedback and only decode the exact inter-frame once the user pauses on a position. Debounce the precise seek by ~100ms.

6. **Free GPU/memory deterministically.** Release `VideoFrame`/texture objects explicitly (`.close()`, delete textures) as they leave the cache window; do not rely on GC for media memory.

7. **Degrade gracefully.** Detect codec/`WebCodecs` support and fall back to a native `<video>` element when hardware-accelerated decode is unavailable. Surface buffering state to the user rather than freezing.

Always measure time-to-first-frame and dropped-frame count; these are the metrics that define perceived video quality.
