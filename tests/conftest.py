"""Minimal Home Assistant stubs for isolated integration unit tests."""

import sys
import types
from datetime import timezone
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class HomeAssistant:
    """Home Assistant test stub."""


class ServiceCall:
    """Service call test stub."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class SupportsResponse:
    """Service response support test stub."""

    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


class ConfigEntry:
    """Config entry test stub."""


class DataUpdateCoordinator:
    """Coordinator test stub."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.refresh_count = 0

    async def async_config_entry_first_refresh(self) -> None:
        return None

    async def async_request_refresh(self) -> None:
        self.refresh_count += 1


class HomeAssistantError(Exception):
    """Error surfaced to Home Assistant service callers."""


class ServiceValidationError(HomeAssistantError):
    """Error caused by invalid service call input."""


class Platform:
    """Platform constants used by the integration."""

    TODO = "todo"
    SENSOR = "sensor"
    SWITCH = "switch"
    NUMBER = "number"
    TEXT = "text"
    CALENDAR = "calendar"


class StaticPathConfig:
    """Static path registration test stub."""

    def __init__(self, url_path: str, path: str, cache_headers: bool) -> None:
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


class TodoItem:
    """Todo item test stub."""


class TodoItemStatus:
    """Todo status test stub."""


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    return value


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


homeassistant = cast(Any, types.ModuleType("homeassistant"))
core = cast(Any, types.ModuleType("homeassistant.core"))
config_entries = cast(Any, types.ModuleType("homeassistant.config_entries"))
ha_const = cast(Any, types.ModuleType("homeassistant.const"))
exceptions = cast(Any, types.ModuleType("homeassistant.exceptions"))
helpers = cast(Any, types.ModuleType("homeassistant.helpers"))
config_validation = cast(
    Any, types.ModuleType("homeassistant.helpers.config_validation")
)
aiohttp_client = cast(Any, types.ModuleType("homeassistant.helpers.aiohttp_client"))
update_coordinator = cast(
    Any, types.ModuleType("homeassistant.helpers.update_coordinator")
)
entity_registry = cast(Any, types.ModuleType("homeassistant.helpers.entity_registry"))
components = cast(Any, types.ModuleType("homeassistant.components"))
todo = cast(Any, types.ModuleType("homeassistant.components.todo"))
frontend = cast(Any, types.ModuleType("homeassistant.components.frontend"))
http = cast(Any, types.ModuleType("homeassistant.components.http"))
util = cast(Any, types.ModuleType("homeassistant.util"))
dt_util = cast(Any, types.ModuleType("homeassistant.util.dt"))

core.HomeAssistant = HomeAssistant
core.ServiceCall = ServiceCall
core.ServiceResponse = dict[str, Any] | None
core.SupportsResponse = SupportsResponse
config_entries.ConfigEntry = ConfigEntry
ha_const.Platform = Platform
exceptions.HomeAssistantError = HomeAssistantError
exceptions.ServiceValidationError = ServiceValidationError
config_validation.string = _string
config_validation.ensure_list = _ensure_list
config_validation.boolean = bool
aiohttp_client.async_get_clientsession = lambda hass: None
update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
entity_registry.async_get = lambda hass: None
todo.TodoItem = TodoItem
todo.TodoItemStatus = TodoItemStatus
frontend.add_extra_js_url = lambda hass, url: None
http.StaticPathConfig = StaticPathConfig
dt_util.get_default_time_zone = lambda: timezone.utc

homeassistant.core = core
homeassistant.config_entries = config_entries
homeassistant.const = ha_const
homeassistant.exceptions = exceptions
homeassistant.helpers = helpers
homeassistant.components = components
homeassistant.util = util
helpers.config_validation = config_validation
helpers.aiohttp_client = aiohttp_client
helpers.update_coordinator = update_coordinator
helpers.entity_registry = entity_registry
components.todo = todo
components.frontend = frontend
components.http = http
util.dt = dt_util

for name, module in {
    "homeassistant": homeassistant,
    "homeassistant.core": core,
    "homeassistant.config_entries": config_entries,
    "homeassistant.const": ha_const,
    "homeassistant.exceptions": exceptions,
    "homeassistant.helpers": helpers,
    "homeassistant.helpers.config_validation": config_validation,
    "homeassistant.helpers.aiohttp_client": aiohttp_client,
    "homeassistant.helpers.update_coordinator": update_coordinator,
    "homeassistant.helpers.entity_registry": entity_registry,
    "homeassistant.components": components,
    "homeassistant.components.todo": todo,
    "homeassistant.components.frontend": frontend,
    "homeassistant.components.http": http,
    "homeassistant.util": util,
    "homeassistant.util.dt": dt_util,
}.items():
    sys.modules[name] = module
