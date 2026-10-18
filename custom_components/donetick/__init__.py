"""The Donetick integration."""
import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import DonetickApiClient, normalize_due_date
from .chore_payload import (
    ASSIGNMENT_STRATEGIES,
    FREQUENCY_TYPES,
    build_create_chore_payload,
)
from .const import (
    CONF_REFRESH_INTERVAL,
    CONF_SHOW_DUE_IN,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)
from .frontend import async_setup_frontend
from .completion_history import async_completed_occurrences
from .model import DonetickMember

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.TODO, Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.TEXT, Platform.CALENDAR]


SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_COMPLETE_CHORE = "complete_chore"
SERVICE_CREATE_TASK = "create_task"
SERVICE_CREATE_CHORE = "create_chore"
SERVICE_UPDATE_TASK = "update_task"
SERVICE_DELETE_TASK = "delete_task"
SERVICE_SKIP_TASK = "skip_task"
SERVICE_COMPLETE_ASSIGNED_TASK = "complete_assigned_task"
SERVICE_POSTPONE_TASK = "postpone_task"
SERVICE_NAMES = (
    SERVICE_COMPLETE_TASK,
    SERVICE_COMPLETE_CHORE,
    SERVICE_CREATE_TASK,
    SERVICE_CREATE_CHORE,
    SERVICE_UPDATE_TASK,
    SERVICE_DELETE_TASK,
    SERVICE_SKIP_TASK,
    SERVICE_COMPLETE_ASSIGNED_TASK,
    SERVICE_POSTPONE_TASK,
)

COMPLETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("completed_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

COMPLETE_CHORE_SCHEMA = vol.Schema({
    vol.Required("chore_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Required("completed_by"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("assigned_to"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("config_entry_id"): cv.string,
})

CREATE_TASK_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("created_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

CREATE_CHORE_SCHEMA = vol.Schema({
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
    vol.Optional("description"): cv.string,
    vol.Optional("next_due_date"): cv.string,
    vol.Optional("frequency_type", default="once"): vol.In(FREQUENCY_TYPES),
    vol.Optional("frequency", default=1): vol.All(
        vol.Coerce(int), vol.Range(min=1)
    ),
    vol.Optional("frequency_metadata"): dict,
    vol.Optional("assignee_ids"): vol.All(
        cv.ensure_list,
        [vol.All(vol.Coerce(int), vol.Range(min=1))],
    ),
    vol.Optional("assigned_to"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("assign_strategy"): vol.In(ASSIGNMENT_STRATEGIES),
    vol.Optional("priority", default=0): vol.Coerce(int),
    vol.Optional("is_rolling"): bool,
    vol.Optional("config_entry_id"): cv.string,
})

UPDATE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("force_unarchive", default=False): cv.boolean,
    vol.Optional("assigned_to"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=1))),
    vol.Optional("config_entry_id"): cv.string,
})

DELETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

SKIP_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("completed_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

ASSIGNED_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("config_entry_id"): cv.string,
})
POSTPONE_TASK_SCHEMA = ASSIGNED_TASK_SCHEMA
# Only completion can be credited to someone else; postponing records no performer.
COMPLETE_ASSIGNED_SCHEMA = ASSIGNED_TASK_SCHEMA.extend({
    vol.Optional("completed_by"): vol.All(vol.Coerce(int), vol.Range(min=1)),
})

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Donetick from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await async_setup_frontend(hass)

    session = async_get_clientsession(hass)
    client = DonetickApiClient(
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        session,
    )

    refresh_interval_seconds = entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
    async def async_update_tasks():
        tasks = await client.async_get_tasks()
        coordinator.completed_tasks = await async_completed_occurrences(client, tasks)
        return tasks

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_chores",
        update_method=async_update_tasks,
        update_interval=timedelta(seconds=refresh_interval_seconds),
    )
    await coordinator.async_config_entry_first_refresh()

    # Fetch circle members for resolving user IDs to names
    circle_members = []
    try:
        circle_members = await client.async_get_circle_members()
    except Exception as e:
        _LOGGER.warning("Failed to fetch circle members: %s", e)

    hass.data[DOMAIN][entry.entry_id] = {
        CONF_URL: entry.data[CONF_URL],
        CONF_TOKEN: entry.data[CONF_TOKEN],
        CONF_SHOW_DUE_IN: entry.data.get(CONF_SHOW_DUE_IN, 7),
        "coordinator": coordinator,
        "client": client,
        "circle_members": circle_members,
    }
    
    # Register services before setting up platforms
    async def complete_task_handler(call: ServiceCall) -> None:
        await async_complete_task_service(hass, call)

    async def complete_chore_handler(call: ServiceCall) -> None:
        await async_complete_chore_service(hass, call)

    async def create_task_handler(call: ServiceCall) -> None:
        await async_create_task_service(hass, call)

    async def create_chore_handler(call: ServiceCall) -> None:
        await async_create_chore_service(hass, call)
    
    async def update_task_handler(call: ServiceCall) -> None:
        await async_update_task_service(hass, call)
    
    async def delete_task_handler(call: ServiceCall) -> None:
        await async_delete_task_service(hass, call)

    async def skip_task_handler(call: ServiceCall) -> None:
        await async_skip_task_service(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPLETE_TASK,
        complete_task_handler,
        schema=COMPLETE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPLETE_CHORE,
        complete_chore_handler,
        schema=COMPLETE_CHORE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TASK,
        create_task_handler,
        schema=CREATE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_CHORE,
        create_chore_handler,
        schema=CREATE_CHORE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TASK,
        update_task_handler,
        schema=UPDATE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_TASK,
        delete_task_handler,
        schema=DELETE_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SKIP_TASK,
        skip_task_handler,
        schema=SKIP_TASK_SCHEMA,
    )
    async def complete_assigned_handler(call: ServiceCall) -> None:
        await async_task_row_action(hass, call, complete=True)

    async def postpone_handler(call: ServiceCall) -> None:
        await async_task_row_action(hass, call, complete=False)

    hass.services.async_register(DOMAIN, SERVICE_COMPLETE_ASSIGNED_TASK,
                                 complete_assigned_handler, schema=COMPLETE_ASSIGNED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_POSTPONE_TASK,
                                 postpone_handler, schema=POSTPONE_TASK_SCHEMA)
    _LOGGER.debug("Registered services: %s",
                  ", ".join(f"{DOMAIN}.{name}" for name in SERVICE_NAMES))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True

async def async_complete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the complete_task service call."""
    task_id = call.data["task_id"]
    completed_by = call.data.get("completed_by")
    config_entry_id = call.data.get("config_entry_id")
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return

    # Get API client and coordinator
    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        result = await client.async_complete_task(task_id, completed_by)
        _LOGGER.info("Task %d completed successfully by user %s", task_id, completed_by or "default")
        await coordinator.async_request_refresh()

    except Exception as e:
        _LOGGER.error("Failed to complete task %d: %s", task_id, e)


async def async_complete_chore_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Complete a chore while recording the actual circle member who did it."""
    config_entry_id = call.data.get("config_entry_id")
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        raise HomeAssistantError("No matching Donetick config entry found")

    try:
        config = hass.data[DOMAIN][entry.entry_id]
    except KeyError as err:
        raise HomeAssistantError(
            f"Donetick config entry {entry.entry_id} is not loaded"
        ) from err

    await _validate_circle_members(
        config,
        completed_by=call.data["completed_by"],
        assigned_to=call.data.get("assigned_to"),
    )

    try:
        await config["client"].async_complete_chore(
            call.data["chore_id"],
            call.data["completed_by"],
            assigned_to=call.data.get("assigned_to"),
        )
    except Exception as err:
        raise HomeAssistantError(f"Failed to complete Donetick chore: {err}") from err

    try:
        await config["coordinator"].async_request_refresh()
    except Exception as err:
        raise HomeAssistantError(
            "Donetick chore was completed, but Home Assistant refresh failed: "
            f"{err}"
        ) from err


async def async_create_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the create_task service call."""
    name = call.data["name"]
    description = call.data.get("description")
    created_by = call.data.get("created_by")
    config_entry_id = call.data.get("config_entry_id")
    try:
        due_date = normalize_due_date(call.data.get("due_date"))
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return
    
    # Get API client and coordinator
    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        result = await client.async_create_task(name, description, due_date, created_by)
    except Exception as e:
        _LOGGER.error("Failed to create task '%s': %s", name, e)
        return
    _LOGGER.info("Task '%s' created successfully with ID %s", name, result.id)
    await coordinator.async_request_refresh()


async def async_create_chore_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Create a chore through Donetick's full API."""
    config_entry_id = call.data.get("config_entry_id")
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        raise HomeAssistantError("No matching Donetick config entry found")

    try:
        config = hass.data[DOMAIN][entry.entry_id]
    except KeyError as err:
        raise HomeAssistantError(
            f"Donetick config entry {entry.entry_id} is not loaded"
        ) from err
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        payload = build_create_chore_payload(call.data)
    except ValueError as err:
        raise ServiceValidationError(f"Invalid Donetick chore: {err}") from err

    await _validate_circle_members(
        config,
        assigned_to=call.data.get("assigned_to"),
        assignee_ids=call.data.get("assignee_ids"),
    )

    try:
        chore_id = await client.async_create_chore(payload)
    except Exception as err:
        raise HomeAssistantError(f"Failed to create Donetick chore: {err}") from err

    try:
        await coordinator.async_request_refresh()
    except Exception as err:
        raise HomeAssistantError(
            f"Donetick chore was created with ID {chore_id}, "
            f"but Home Assistant refresh failed: {err}"
        ) from err

    _LOGGER.info(
        "Chore '%s' created successfully with ID %d", payload["name"], chore_id
    )


def _find_unknown_member(
    circle_members: list[DonetickMember], fields: dict[str, int | list[int] | None]
) -> tuple[str, int] | None:
    """Return the first (field, user ID) that is not an active circle member."""
    active_ids = {member.user_id for member in circle_members if member.is_active}
    for field, value in fields.items():
        user_ids = value if isinstance(value, list) else [value]
        for user_id in user_ids:
            if user_id is not None and user_id not in active_ids:
                return field, user_id
    return None


async def _validate_circle_members(
    config: dict[str, Any], **fields: int | list[int] | None
) -> None:
    """Reject user IDs that are not active members of the Donetick circle.

    Field values may be a single user ID or a list of IDs. The member list in
    ``config["circle_members"]`` is cached at setup; when an ID is unknown it
    is re-fetched once so members who joined later are accepted. The check is
    skipped when no member list is available, leaving Donetick as the only
    authority in that case.
    """
    circle_members = config.get("circle_members")
    if not circle_members:
        return
    unknown = _find_unknown_member(circle_members, fields)
    if unknown is None:
        return

    try:
        circle_members = await config["client"].async_get_circle_members()
    except Exception as err:
        _LOGGER.warning(
            "Could not refresh Donetick circle members to validate %s %s: %s",
            unknown[0], unknown[1], err,
        )
        return
    if not circle_members:
        _LOGGER.warning(
            "Donetick returned no circle members; skipping validation of %s %s",
            unknown[0], unknown[1],
        )
        return
    config["circle_members"] = circle_members

    unknown = _find_unknown_member(circle_members, fields)
    if unknown is not None:
        raise ServiceValidationError(
            f"{unknown[0]} {unknown[1]} is not an active Donetick circle member"
        )


async def async_update_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the update_task service call."""
    task_id = call.data["task_id"]
    name = call.data.get("name")
    description = call.data.get("description")
    due_date = call.data.get("due_date")
    force_unarchive = call.data.get("force_unarchive", False)
    assigned_to = call.data.get("assigned_to")
    config_entry_id = call.data.get("config_entry_id")

    if not any(field in call.data for field in ("name", "description", "due_date", "assigned_to")) and not force_unarchive:
        raise HomeAssistantError("Provide at least one task field to update")
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        raise HomeAssistantError("No matching Donetick integration found for update_task")
    
    # Get API client and coordinator
    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        result = None
        if force_unarchive or any(field in call.data for field in ("name", "description", "due_date")):
            result = await client.async_update_task(task_id, name, description, due_date, force_unarchive)
        if "assigned_to" in call.data:
            await client.async_assign_task(task_id, assigned_to)
        _LOGGER.info("Task %d updated successfully", task_id)
        await coordinator.async_request_refresh()

    except Exception as e:
        _LOGGER.error("Failed to update task %d: %s", task_id, e)
        raise HomeAssistantError(f"Failed to update Donetick task {task_id}: {e}") from e

    if result is not None and not result.is_active:
        raise HomeAssistantError(
            f"Donetick task {task_id} was updated but remains archived. "
            "Set force_unarchive to true to reactivate it."
        )

async def async_delete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the delete_task service call."""
    task_id = call.data["task_id"]
    config_entry_id = call.data.get("config_entry_id")
    
    # Find the config entry to use
    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        raise HomeAssistantError("No matching Donetick integration found for delete_task")
    
    # Get API client and coordinator
    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        success = await client.async_delete_task(task_id)
        if success:
            _LOGGER.info("Task %d deleted successfully", task_id)
            await coordinator.async_request_refresh()
        else:
            raise HomeAssistantError(f"Donetick did not delete task {task_id}")

    except Exception as e:
        _LOGGER.error("Failed to delete task %d: %s", task_id, e)
        raise HomeAssistantError(f"Failed to delete Donetick task {task_id}: {e}") from e

async def async_skip_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the skip_task service call."""
    task_id = call.data["task_id"]
    completed_by = call.data.get("completed_by")
    config_entry_id = call.data.get("config_entry_id")

    entry = await _get_config_entry(hass, config_entry_id)
    if not entry:
        return

    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    coordinator = config["coordinator"]

    try:
        result = await client.async_skip_task(task_id, completed_by)
        _LOGGER.info("Task %d skipped successfully", task_id)
        await coordinator.async_request_refresh()

    except Exception as e:
        _LOGGER.error("Failed to skip task %d: %s", task_id, e)

async def _get_config_entry(hass: HomeAssistant, config_entry_id: str = None) -> ConfigEntry:
    """Get the config entry to use for the service call."""
    entry = None
    if config_entry_id:
        # Check if it's a config entry ID
        entry = hass.config_entries.async_get_entry(config_entry_id)
        if entry and entry.domain != DOMAIN:
            entry = None

        # If not found, check if it's an entity ID and extract config entry from it
        if not entry and config_entry_id.startswith("todo."):
            entity_registry = er.async_get(hass)
            entity_entry = entity_registry.async_get(config_entry_id)
            # Only accept todo entities that belong to this integration.
            if entity_entry and entity_entry.platform == DOMAIN:
                entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        
        if not entry:
            _LOGGER.error("Config entry not found for: %s", config_entry_id)
            return None
    else:
        # Use the first Donetick integration if no specific entry provided
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)]
        if not entries:
            _LOGGER.error("No Donetick integration found")
            return None
        entry = entries[0]
    
    if entry.domain != DOMAIN:
        _LOGGER.error("Config entry %s belongs to %s, expected %s", entry.entry_id, entry.domain, DOMAIN)
        return None
    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.error("Config entry %s is not loaded", entry.entry_id)
        return None

    return entry

async def async_task_row_action(hass: HomeAssistant, call: ServiceCall, *, complete: bool) -> None:
    """Resolve the current server assignment/date before a dashboard action."""
    entry = await _get_config_entry(hass, call.data.get("config_entry_id"))
    if entry is None:
        raise HomeAssistantError("No matching Donetick integration found")
    config = hass.data[DOMAIN][entry.entry_id]
    client = config["client"]
    try:
        tasks = await client.async_get_tasks()
        task = next((task for task in tasks if task.id == call.data["task_id"] and task.is_active), None)
        if task is None:
            raise HomeAssistantError("Task is no longer active or visible; refresh the list")
        if complete:
            # Without completed_by the current assignee is credited; None leaves
            # attribution to Donetick's authenticated API token owner.
            await client.async_complete_task(
                task.id, completed_by=call.data.get("completed_by", task.assigned_to))
        else:
            if task.frequency_type in ("once", "no_repeat", "trigger", "always"):
                raise HomeAssistantError("This task has no next scheduled occurrence")
            await client.async_reschedule_task(task.id)

    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError(f"Donetick task action failed: {err}") from err
    finally:
        await config["coordinator"].async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove services if this is the last config entry
        if not hass.data[DOMAIN]:
            for service_name in SERVICE_NAMES:
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            _LOGGER.debug("Removed all Donetick services")
    
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
