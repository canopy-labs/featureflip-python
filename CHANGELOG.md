# Changelog

## 2.4.0 — 2026-07-29

### Added

- **`onEvaluation` inspector callback.** `inspectors` config option registering in-process observers fired on every evaluation, receiving flag key, context, value, variation key, reason, rule id and prerequisite key. Also threaded through `FeatureflipClient.for_testing(flags, inspectors=...)`, which bypasses `__init__` (#1914).

### Fixed

- A served variation key the flag does not define now reports reason `ERROR` with the caller's default, instead of a misleading success reason (#1989).

## 2.3.1 — 2026-07-14

### Fixed

- Analytics events are sent in the backend's `SdkEventDto` wire shape (#1920).

## 2.3.0 — 2026-07-13

### Fixed

- Outage-recovery hardening: polling fallback, reconnect backoff, and SSE `sync` applied as a full store replace (#1868, #1896).
- The streaming fallback poller is reaped once the stream is healthy, instead of continuing to poll alongside it (#1895).

## 2.2.0 — 2026-06-19

### Added

- **Semantic-version condition operators.** Five new operators — `SemverEquals`, `SemverGreaterThan`, `SemverGreaterThanOrEqual`, `SemverLessThan`, `SemverLessThanOrEqual` — compare an attribute against the condition value(s) as a semantic version (https://semver.org) rather than a decimal, so `2.10.1` correctly satisfies `>= 2.0` (the numeric path mis-parsed multi-segment versions). Tolerant of a leading `v`, `+build` metadata, missing trailing segments (`2.0` == `2.0.0`), and `-prerelease` precedence; unparseable versions match nothing. A rule matches when any supplied condition value satisfies the operator, mirroring the numeric/date operators and the evaluation engine. New module: `featureflip/_semver.py` (#1409).

### Fixed

- **Multi-word condition operators sent by the server now parse correctly.** The evaluation API serializes operators in PascalCase (e.g. `GreaterThan`, `SemverGreaterThanOrEqual`); operator parsing previously lower-cased the wire string, which collapsed the word boundaries (`GreaterThan` → `greaterthan`) and failed to resolve any multi-word operator. `ConditionOperator` now normalizes PascalCase → snake_case via `_missing_` (#1429).
- Relational operators match if the attribute satisfies the operator against **any** supplied condition value (#1443).
- `MatchesRegex` and semver prerelease comparison are now case-sensitive, matching the engine (#1453, #1454).
- `Before`/`After` date operators aligned with the engine (#1455).
- Type-aware numeric coercion for `Equals`/`In` (#1458).
- Keyless rollouts serve the control variation deterministically (#1457).
- Segment-keyed rules with no segment source fail closed (#1459).
- Present-but-null attributes are treated as absent (#1460).
- Environment-level percentage rollouts with no variations no longer throw (#1469).

## 2.1.0 — 2026-05-27

### Added

- **Prerequisite flag support.** Flags can declare other flags as prerequisites; the flag's rules and fallthrough only run when every prerequisite serves the expected variation, otherwise the off variation is served with `EvaluationReason.PREREQUISITE_FAILED` and `prerequisite_key` set on the `EvaluationDetail`. Resolution is recursive (depth-capped at 10) with per-call memoisation. New types: `Prerequisite`, `EvaluationReason.PREREQUISITE_FAILED`. New `EvaluationDetail` fields: `variation_key`, `prerequisite_key`. New batch-eval entry point: `FlagEvaluator.evaluate_with_shared_memo(...)` (#1108).

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
