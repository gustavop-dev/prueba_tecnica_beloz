import os

from hypothesis import HealthCheck, settings

# Database-backed properties are slow per example: no deadline, and fixtures are reused on purpose.
_COMMON = {
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
}
settings.register_profile("ci", max_examples=200, stateful_step_count=25, **_COMMON)
settings.register_profile("dev", max_examples=30, stateful_step_count=15, **_COMMON)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
