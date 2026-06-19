---
name: TypeScript Strict Standard
category: code-standards
tags: [typescript, types, strict, type-safety, lint]
scope: user
tokens: 330
version: 1.0.0
---

When writing TypeScript, assume the strictest compiler settings and follow these rules:

1. **Strict mode is non-negotiable.** Code must compile under `strict: true` plus `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, and `noImplicitOverride`. Treat type errors as build failures.

2. **No `any`.** Never introduce `any`, explicitly or implicitly. When a type is genuinely unknown, use `unknown` and narrow it before use. The only acceptable escape hatch is a single, commented `// eslint-disable` with a justification.

3. **Avoid unsafe assertions.** Do not paper over types with `as` casts or non-null `!`. Prove the type with a runtime check or a type guard. Reserve `as const` (good) and narrow, justified assertions for genuine interop edges only.

4. **Validate external data at runtime.** Data from the network, storage, env, or user input is `unknown` until validated. Parse it with a schema (e.g. Zod/Valibot) and derive the static type from the schema, rather than casting a raw `JSON.parse` result.

5. **Model state precisely.** Use discriminated unions over loose optional flags (`{ status: 'loading' } | { status: 'error'; error: E } | { status: 'ok'; data: T }`). Make illegal states unrepresentable; prefer `readonly` and exhaustive `switch` with a `never` default.

6. **Prefer `type`/inference over redundant annotations.** Let inference do the work for locals and return values when it is clear; annotate public/exported API surfaces explicitly. Use `satisfies` to check object literals against a type without widening them.

7. **Be explicit about nullability.** Distinguish `undefined` (absent) from `null` (intentionally empty) consistently across the codebase, and handle both at the boundary.

8. **No floating promises.** Every promise is awaited, returned, or explicitly marked `void`. Async functions declare a precise return type.
