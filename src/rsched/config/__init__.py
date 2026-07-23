"""Server config (~/.config/routine-scheduler/config.yaml) and routine.yaml loading.

Both loaders validate through pydantic, leniently: every invalid key is reported into a
problems list (so callers — registry, `rsched validate` — can show all of them at once)
and falls back to its default instead of failing the whole load.

A package since the overhaul (base vocabulary / transport catalog / server / routine),
re-exporting the same public names the old single module carried — `from rsched.config
import X` is unchanged for every consumer.
"""

from .base import (
    CONVERSATION_DELIBERATION,
    DEFAULT_BUDGETS,
    DEFAULT_CAPABILITIES,
    DEFAULT_DELIBERATION,
    DEFAULT_MODEL_MAX_TOKENS,
    DEFAULT_PERMISSIONS,
    DEFAULT_TRAITS,
    DELIBERATION_LEVELS,
    ENDPOINT_KINDS,
    KEY_VAR_DEFAULTS,
    MODEL_KINDS,
    NATIVE_MM_KINDS,
    SCHEMA_MODES,
    BlankableStr,
    EndpointKind,
    HomePath,
    SchemaMode,
)
from .modelconf import EndpointConfig, MachineConfig, ModelConfig, ModelRef
from .routine import RoutineConfig, load_routine, load_tuning, write_tuning
from .server import LibrarySyncConfig, ServerConfig, load_server_config

__all__ = [
    "CONVERSATION_DELIBERATION",
    "DEFAULT_BUDGETS",
    "DEFAULT_CAPABILITIES",
    "DEFAULT_DELIBERATION",
    "DEFAULT_MODEL_MAX_TOKENS",
    "DEFAULT_PERMISSIONS",
    "DEFAULT_TRAITS",
    "DELIBERATION_LEVELS",
    "ENDPOINT_KINDS",
    "KEY_VAR_DEFAULTS",
    "MODEL_KINDS",
    "NATIVE_MM_KINDS",
    "SCHEMA_MODES",
    "BlankableStr",
    "EndpointConfig",
    "EndpointKind",
    "HomePath",
    "LibrarySyncConfig",
    "MachineConfig",
    "ModelConfig",
    "ModelRef",
    "RoutineConfig",
    "SchemaMode",
    "ServerConfig",
    "load_routine",
    "load_server_config",
    "load_tuning",
    "write_tuning",
]
