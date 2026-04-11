# Featureflip Python SDK

Python SDK for [Featureflip](https://featureflip.io) - evaluate feature flags locally with near-zero latency.

## Installation

```bash
pip install featureflip
```

## Quick Start

```python
from featureflip import FeatureflipClient

# Initialize the client (blocks until flags are loaded)
client = FeatureflipClient(sdk_key="your-sdk-key")

# Evaluate a feature flag
enabled = client.variation("my-feature", {"user_id": "user-123"}, default=False)

if enabled:
    print("Feature is enabled!")
else:
    print("Feature is disabled")

# Clean shutdown
client.close()
```

> **Lifetime:** Multiple `FeatureflipClient(sdk_key="x")` calls with the same SDK key return distinct handles sharing one underlying client — you cannot accidentally open duplicate streaming connections by constructing multiple clients. `close()` is refcounted; the real shutdown runs only when the last handle is closed.

## Configuration

```python
from featureflip import FeatureflipClient, Config

client = FeatureflipClient(
    sdk_key="your-sdk-key",
    config=Config(
        base_url="https://eval.featureflip.io",  # Evaluation API URL
        streaming=True,           # Use SSE for real-time updates (default)
        poll_interval=30.0,       # Polling interval if streaming=False
        send_events=True,         # Enable analytics event tracking
        flush_interval=30.0,      # Event flush interval in seconds
        init_timeout=10.0,        # Max seconds to wait for initialization
    )
)
```

The SDK key can also be set via the `FEATUREFLIP_SDK_KEY` environment variable.

## Context Manager

```python
with FeatureflipClient(sdk_key="your-sdk-key") as client:
    enabled = client.variation("my-feature", {"user_id": "123"}, default=False)
# Automatically closes and flushes events on exit
```

## Evaluation

```python
# Boolean flag
enabled = client.variation("feature-key", {"user_id": "123"}, default=False)

# String flag
tier = client.variation("pricing-tier", {"user_id": "123"}, default="free")

# Number flag
limit = client.variation("rate-limit", {"user_id": "123"}, default=100)

# JSON flag
config = client.variation("ui-config", {"user_id": "123"}, default={"theme": "light"})
```

### Detailed Evaluation

```python
detail = client.variation_detail("feature-key", {"user_id": "123"}, default=False)

print(detail.value)    # The evaluated value
print(detail.reason)   # "RULE_MATCH", "FALLTHROUGH", "FLAG_DISABLED", etc.
print(detail.rule_id)  # Rule ID if reason is RULE_MATCH
```

## Event Tracking

```python
# Track custom events
client.track("checkout-completed", {"user_id": "123"}, metadata={"total": 99.99})

# Identify users for segment building
client.identify({"user_id": "123", "email": "user@example.com", "plan": "pro"})

# Force flush pending events
client.flush()
```

## Testing

Use the test client for deterministic unit tests:

```python
from featureflip import FeatureflipClient

# Create a test client with fixed values (no network calls)
client = FeatureflipClient.for_testing({
    "my-feature": True,
    "pricing-tier": "pro",
})

# Evaluations return the configured values
assert client.variation("my-feature", {}, default=False) is True
assert client.variation("pricing-tier", {}, default="free") == "pro"

# Unknown flags return the default
assert client.variation("unknown", {}, default="fallback") == "fallback"

# Update values at runtime
client.set_test_value("my-feature", False)
```

## Features

- **Client-side evaluation** - Near-zero latency after initialization
- **Real-time updates** - SSE streaming with polling fallback
- **Event tracking** - Automatic batching and flushing of analytics events
- **Test support** - `for_testing()` factory for deterministic unit tests
- **Type-safe** - Full type hints with mypy strict mode compliance

## Requirements

- Python 3.10+

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check src/featureflip tests

# Run type checking
mypy src/featureflip --strict
```

## License

MIT
