"""Featureflip Python SDK."""

__version__ = "2.0.0"

from featureflip.client import FeatureflipClient
from featureflip.config import Config
from featureflip.context import EvaluationContext
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.exceptions import (
    ConfigurationError,
    FeatureflipError,
    InitializationError,
)
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Segment,
    ServeConfig,
    ServeType,
    TargetingRule,
    Variation,
    WeightedVariation,
)

__all__: list[str] = [
    "Condition",
    "ConditionGroup",
    "ConditionLogic",
    "ConditionOperator",
    "Config",
    "ConfigurationError",
    "EvaluationContext",
    "EvaluationDetail",
    "EvaluationReason",
    "FeatureflipClient",
    "FeatureflipError",
    "FlagConfiguration",
    "FlagType",
    "InitializationError",
    "Segment",
    "ServeConfig",
    "ServeType",
    "TargetingRule",
    "Variation",
    "WeightedVariation",
    "__version__",
]
