# Changelog

## 2.10.0 — 2026-10-26

### Added

- The `Evaluation` analytics event now carries `reason` (`rule-match`, `fallthrough`, `flag-disabled`, `prerequisite-failed`, `error` or `flag-not-found`), plus `ruleId` when the reason is `rule-match` and `prerequisiteKey` when a prerequisite failed, so the Analytics tab can show where flag traffic came from. Both are omitted when absent. The public API is unchanged. (#4268)

### Fixed

- `variation_detail()` now records an `Evaluation` event, exactly as `variation()` does. It recorded none before, so a flag read only through it (which includes every flag read through the OpenFeature provider) looked never evaluated to the Analytics tab, stale-flag detection and the archive guard. Pass `track=False` to skip the event, as with `variation()`. (#4300)
- A `decimal.Decimal` rollout bucket value or `user_id` of 1e21 or more now converts to a float, like a non-integral one, instead of expanding to its full integer digits. A huge exponent such as `Decimal("1E+999999999")` no longer builds a billion-digit integer on every evaluation, and a value over 4300 digits no longer raises `ValueError`. A context bucketed on an integral `Decimal` at or above 1e21 may get a different variant. (#4246)

## 2.9.0 — 2026-10-22

### Changed

- A percentage rollout that can't bucket the context (no `user_id`, or no value for its bucket-by attribute) now skips the rule and evaluation continues with the next rule or the fallthrough. Previously the SDK served the rollout's first variation, which is the variation being rolled out. A warning is logged once per flag and rule. (#4236)
- Boolean values used as a rollout bucket value now hash as `true`/`false` (previously `True`/`False`), matching the Evaluation API, so the same context gets the same variant in every SDK. Only a context whose user id is a boolean is affected. (This also changes how a boolean `user_id` reads in conditions on `user_id`.) (#4236)
- `EvaluationContext.from_dict` keeps a `user_id` of `0` or `False` instead of treating the context as anonymous, so it is now bucketed in percentage rollouts. (#4236)
- Decimal numbers used as a rollout bucket value now hash in one canonical spelling shared with the Evaluation API (for example `1.0` hashes as `1`), so the same context gets the same variant in every SDK. Integer values are unchanged. A context whose user id is a non-integer number may get a different variant. A `decimal.Decimal` value with no fractional part hashes as its exact integer digits; any other `Decimal` is converted to a float first. (#4236)
- Malformed variation references now evaluate to reason `ERROR` the way the Evaluation API does. A rule or fallthrough that serves a key the flag doesn't define (the detail already reported `ERROR` and the default) no longer reports the rule id, and a served empty-string key with no empty-string variation is now an `ERROR` too. A flag whose prerequisite serves an undefined variation, or whose own off variation is undefined, now reports `ERROR` (still serving its off variation) instead of `PREREQUISITE_FAILED` or `FLAG_DISABLED`. (#4236)
- `EvaluationContext.from_dict` formats a float or `Decimal` `user_id` canonically (for example `1.0` becomes `1`), so it buckets like the Evaluation API and every other SDK. (#4250)

## 2.8.0 — 2026-10-20

### Added

- `Config.wrapper_name` and `wrapper_version`, for libraries that wrap this SDK, such as the OpenFeature provider. The wrapper's token follows the SDK's in the User-Agent on the flag fetch, the event post and the SSE stream, e.g. `featureflip-python/<version> my-wrapper/1.0.0`, so usage through the wrapper can be counted. A name that is not a valid HTTP product token is ignored with a warning rather than failing construction, and the two fields do not trigger the config-mismatch warning. (#4153)

## 2.7.0 — 2026-08-28

### Added

- `FeatureflipClient.on_update(listener)` subscribes to flag-configuration changes, returning a callable that unsubscribes. The listener receives the flag keys whose configuration changed, batched into one call per update. This is the change channel the OpenFeature provider needs to emit `PROVIDER_CONFIGURATION_CHANGED`, and it brings Python level with the js/node SDKs (2.2.0) and the C# SDK. (#2586)

  The initial flag load does not fire — a cold start is not a change. Beyond flags whose own configuration changed, an update also names the flags whose evaluated value moves as a result: those referencing an edited segment (a segment edit alters targeting without bumping any flag's version), and those depending on a changed flag through a prerequisite. A listener that raises is logged and isolated, affecting neither flag delivery nor the other listeners.

  Listeners fire on the SDK's streaming or polling thread, so they must not block.

### Fixed

- The distribution now ships the PEP 561 `py.typed` marker. `pyproject.toml` has advertised the `Typing :: Typed` classifier since the first release, but without the marker file a type checker in a consuming project ignores the SDK's inline annotations entirely and reports `Skipping analyzing "featureflip": module is installed, but missing library stubs or py.typed marker`. Every annotation the SDK carries was invisible downstream. (#2586)

## 2.6.0 — 2026-08-26

### Changed

- A long-lived SSE stream severed mid-frame is now logged at `debug` instead of `warning`. Such a sever is routine behind a CDN or reverse proxy: the client reconnects and the server replays a full `sync` snapshot, so no configuration is missed and nothing is degraded — but reporting it at `warning` turned ordinary operation into a recurring alarm. A stream that never delivers an event is still logged at `warning`, and repeated failures still escalate to the polling fallback, so a genuinely broken stream stays visible. (#2457)

### Fixed

- A date operand whose fractional second has 1, 2, 4 or 5 digits (`2024-01-31T09:30:00.5Z`) matched **nothing on Python 3.10** while resolving normally on 3.11+, so the same flag config evaluated differently depending on the customer's interpreter. 3.10's `datetime.fromisoformat` reads the fraction as a fixed-width field and accepts exactly 3 or exactly 6 digits; the SDK truncated a long fraction to 6 but never padded a short one. Fractions are now normalized to exactly six digits, the width every supported version accepts, which is value-preserving (`.5` and `.500000` are the same 500ms). Same defect class as the 3.11 ISO-basic divergence fixed in 2.4.2. (#2480)

- An explicit `client.flush()` no longer opens a second drain loop while one is already running. The in-flight latch added for #2456 guarded only the batch-size trigger, so the periodic flush, an explicit `client.flush()` and a size-triggered flush could enter the loop together — two request streams against an endpoint the backoff gate exists to protect, and worse, a success in one cleared the gate a failure in the other had just armed, re-opening the one-request-per-evaluation behaviour outright. A caller arriving while a drain is running now waits for it and returns, matching the js and node SDKs. Shutdown still bypasses coalescing, because it is the last drain there will ever be. (#2477)

- A `Before`/`After` date operand that resolves outside `datetime`'s range no longer raises `OverflowError` out of flag evaluation, and now matches nothing instead. Shifting such an operand to UTC raises `OverflowError` — **not** `ValueError`, the only exception the parser caught — so `0001-01-01T00:00:00+05:00` or `9999-12-31T23:59:59-05:00` in a saved rule propagated an exception into the calling application rather than returning a value. This SDK was the only one where such an operand raised rather than merely diverging. (#2500)

- The same applies to a context **attribute** carrying such a value, which is the side an application does not control. (#2500)

- Rejecting these also aligns the SDK with the evaluation engine, whose accepted range is the same (`DateTimeOffset.TryParse` bounds it at 0001-01-01T00:00:00Z to 9999-12-31T23:59:59.999Z). Year 0 was already rejected here; the offset-crossed shapes are what raised. (#2500)

- The first SSE reconnect after a healthy stream drops is now jittered to `[d/2, d]`, like every other backoff level. The drops this absorbs are fleet-wide — a single edge event severs every stream at once — so every client re-entered the backoff together and waited an identical delay, republishing the drop's own synchronisation as a reconnect spike one backoff later. Measured in production: a drop spread across 2.5–3.0 ms produced a reconnect spread of 26–46 ms. The delay never exceeds the previous one and stays strictly positive, so a stream that fails immediately still cannot busy-loop. (#2508)

## 2.5.1 — 2026-08-24

### Fixed

- A date operand is now trimmed of exactly the whitespace the evaluation engine trims (tab, newline, vertical tab, form feed, carriage return and space), and is rejected outright if it still carries a NUL, another control character, or a non-ASCII whitespace character. Each SDK had been relying on its own language's `trim`, and no two of those cover the same set, so the same operand could match on one SDK and match nothing on another. (#2468)
- The ISO-8601 grammar no longer shifts with the interpreter version. `fromisoformat` now only ever sees a canonicalised string, so a nanosecond fraction and a basic `+0500` offset parse identically on 3.10 and 3.13 — previously they were accepted on newer interpreters and rejected on 3.10. (#2468)
- An ISO-8601 operand naming hour 24 (`2024-01-01T24:00:00`) matches nothing, rather than rolling over to the next day. (#2468)

## 2.5.0 — 2026-08-24

### Fixed

- Analytics events survive a transient failure of the events endpoint. The queue was emptied before the POST and the batch discarded on any error, so every 503 the edge returned silently lost a batch. A retryable failure — any 5xx, a 429, or a network error or timeout — now puts the batch back at the front of the queue for the next flush. (#2456)
- A permanently rejected batch is still dropped, and now says so. A 400, 401 or 403 fails identically however often it is resent, so the batch is discarded with a warning naming it as non-retryable instead of being retried forever at the expense of every later event. (#2456)
- A failing endpoint no longer receives one request per tracked event. A re-queued batch leaves the queue at its flush threshold, so the batch-size trigger would otherwise have fired on every subsequent event; it now backs off for one flush interval after a retryable failure, leaving the periodic flush to do the retrying. (#2456)
- `stop()` makes a single final flush attempt rather than retrying, so shutdown cannot hang while the endpoint is down. (#2456)

### Changed

- A flush sends at most `flush_batch_size` events per request, looping until the queue is empty, rather than posting the whole queue in one body. Now that failed batches are kept, the queue can sit at its bound through an outage, and a body that large invites a 413 — which is not retryable, so the entire backlog would have been dropped by the path meant to preserve it. (#2456)
- The event queue is bounded at 10,000 events, overridable with `EventProcessor(max_queue_size=...)`. Past the bound the oldest events are shed and the number dropped is logged, so an extended outage sheds the stalest analytics instead of growing the queue without limit. (#2456)

## 2.4.3 — 2026-08-23

### Fixed

- An unrecognised `FlagType` no longer fails the entire config fetch. One unknown value in one flag discarded the whole payload, so a single new server-side type could blank out every flag the SDK served. (#2401)
- An unrecognised condition operator now fails closed instead of matching every user. The default arm returned `false`, which a negated condition then inverted to `true` — so a config naming an operator the SDK did not know could silently target everyone. (#2262)
- An unrecognised condition operator is tolerated at deserialization instead of discarding the whole config, matching the string-typed SDKs. (#2372)
- `identify()` and `track()` put the same payload on the wire as every other server SDK. The field set and shapes had drifted per language, so the same call produced different events depending on which SDK sent it. (#2359)

## 2.4.2 — 2026-08-05

### Fixed

- `LICENSE` is now the verbatim Apache-2.0 text. Three phrases in the operative sections had been reworded and the appendix dropped, which left automated license scanners unable to identify it. The license itself is unchanged; the file now says what it always claimed to.
- The README's License section said MIT. `LICENSE`, `pyproject.toml` and the PyPI listing have always said Apache-2.0, which is the actual license.

## 2.4.1 — 2026-08-02

### Fixed

- The `User-Agent` reports the SDK's real version, read from the installed package metadata rather than a hardcoded literal. It had been pinned to `0.1.0` since the first release, so every request from a 2.x client identified itself as pre-1.0 (#2141).
- `featureflip.__version__` reports the real version too. It had been stuck at `2.0.0`.

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
