"""Featureflip Python SDK."""

from featureflip._version import __version__
from featureflip.client import FeatureflipClient
from featureflip.config import Config
from featureflip.context import EvaluationContext
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.exceptions import (
    ConfigurationError,
    FeatureflipError,
    InitializationError,
)
from featureflip.inspector import EvaluationEvent, EvaluationInspector
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Prerequisite,
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
    "EvaluationEvent",
    "EvaluationInspector",
    "EvaluationReason",
    "FeatureflipClient",
    "FeatureflipError",
    "FlagConfiguration",
    "FlagType",
    "InitializationError",
    "Prerequisite",
    "Segment",
    "ServeConfig",
    "ServeType",
    "TargetingRule",
    "Variation",
    "WeightedVariation",
    "__version__",
]
