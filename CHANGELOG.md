# Changelog

## Unreleased

### Added

- **Prerequisite flag support.** Flags can declare other flags as prerequisites; the flag's rules and fallthrough only run when every prerequisite serves the expected variation, otherwise the off variation is served with `EvaluationReason.PREREQUISITE_FAILED` and `prerequisite_key` set on the `EvaluationDetail`. Resolution is recursive (depth-capped at 10) with per-call memoisation. New types: `Prerequisite`, `EvaluationReason.PREREQUISITE_FAILED`. New `EvaluationDetail` fields: `variation_key`, `prerequisite_key`. New batch-eval entry point: `FlagEvaluator.evaluate_with_shared_memo(...)`.

## 2.0.0 — 2026-04-08

### BREAKING (observable behavior)

- **Two `FeatureflipClient(sdk_key="x")` calls now return distinct handle objects that share one underlying refcounted client.** Previously each construction created a completely independent client with its own HTTP connection, background thread pool, and event processor. Now, calls with the same SDK key share one shared core. Closing one handle when another is still alive does not shut down the core — the real shutdown runs only when the last handle is closed.

  **Migration:** The API is unchanged. Existing code continues to work exactly as before — constructors, context managers (`with FeatureflipClient(sdk_key="x") as c:`), `close()`, `for_testing(flags)`, and all evaluation/tracking methods behave identically from the caller's perspective. The only observable difference is that constructing multiple clients with the **same** SDK key is now cheap and safe: no duplicate connections, no duplicate polling threads.

  **Edge case:** If you were relying on the old behavior to get two fully-independent clients for the same SDK key (e.g., to test shutdown behavior in isolation), you now need to use distinct SDK keys.

- **Config mismatch on subsequent construction is logged as a warning** but does not raise. The cached instance's config is preserved; the passed config is ignored.

### Added

- New private module `featureflip/_core.py` containing `_SharedFeatureflipCore`, `_get_or_create_core`, and the process-wide `_LIVE_CORES` cache.
- Internal `_reset_for_testing()` helper for test isolation (clears the cache and force-shuts-down all cores).

### Changed

- `FeatureflipClient` is now a thin handle (~222 lines, down from ~583). All evaluation, tracking, flush, and close operations delegate to the shared core.
- `FeatureflipClient.for_testing(flags)` still bypasses the cache entirely — test stubs are independent per call and do not interfere with production cores.

## 1.0.1

Previous release.

## 1.0.0

Initial stable release.
