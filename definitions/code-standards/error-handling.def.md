---
name: Error Handling Standard
category: code-standards
tags: [errors, exceptions, resilience, logging, reliability]
scope: user
tokens: 340
version: 1.0.0
---

When writing or reviewing error-handling code, follow these rules:

1. **Fail fast on programmer errors, recover from operational errors.** Distinguish bugs (invalid arguments, broken invariants — crash/throw, do not swallow) from expected runtime conditions (network down, file missing, bad user input — handle and recover). Never use exceptions for normal control flow.

2. **Never silently swallow.** An empty `catch {}` is forbidden. If you catch, you must do one of: handle and recover, retry, translate-and-rethrow, or log-and-rethrow. Re-throwing without context is worse than not catching — always add context.

3. **Preserve the cause chain.** Wrap with the original error attached (`new Error(msg, { cause })`, `raise X from err`, `fmt.Errorf("...: %w", err)`). Never discard a stack trace.

4. **Throw typed, structured errors.** Use specific error types/classes carrying machine-readable fields (a `code`, the offending input, whether it is retryable) — not bare strings. Callers should branch on type/code, not on message text.

5. **Validate at the boundary.** Validate and normalize all external input (requests, env, files, IPC) at the entry point. Inside the trusted core, assume data is already valid and assert invariants instead of re-checking.

6. **Make retries safe and bounded.** Only retry idempotent operations. Use exponential backoff with jitter and a hard cap; wrap unreliable dependencies in timeouts and, where appropriate, a circuit breaker.

7. **Log once, at the boundary, with context.** Log the error where it is finally handled — not at every layer it passes through. Include correlation/request ids and the relevant inputs, but never log secrets or full PII.

8. **Clean up deterministically.** Release resources in `finally`/`defer`/`with`/RAII so they are freed on both success and failure paths.

9. **User-facing messages are safe and actionable.** Show users what went wrong and what to do next; keep stack traces and internals in logs, not in the UI or API response.
