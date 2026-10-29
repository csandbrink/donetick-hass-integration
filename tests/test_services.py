"""Tests for Donetick Home Assistant service integration."""

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

import custom_components.donetick as integration
from conftest import REGISTERED_ENTITIES


class FakeConfigEntry:
    entry_id = "entry-1"
    domain = "donetick"
    data: ClassVar[dict[str, str]] = {
        "url": "https://donetick.example",
        "token": "test-token",
    }

    def __init__(self) -> None:
        self.unload_callbacks: list[Any] = []

    def add_update_listener(self, listener: Any) -> Any:
        self.listener = listener
        return lambda: None

    def async_on_unload(self, func: Any) -> None:
        self.unload_callbacks.append(func)


class FakeConfigEntries:
    def __init__(self, entry: FakeConfigEntry, *others: Any) -> None:
        self.entry = entry
        self.all_entries = {e.entry_id: e for e in (entry, *others)}

    def async_get_entry(self, entry_id: str) -> Any:
        return self.all_entries.get(entry_id)

    def async_entries(self, domain: str) -> list[FakeConfigEntry]:
        return [self.entry]

    async def async_forward_entry_setups(self, entry: Any, platforms: Any) -> None:
        return None

    async def async_unload_platforms(self, entry: Any, platforms: Any) -> bool:
        return True


class FakeServices:
    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], tuple[Any, Any]] = {}

    def async_register(
        self,
        domain: str,
        service: str,
        handler: Any,
        *,
        schema: Any,
        supports_response: Any = None,
    ) -> None:
        self.registered[(domain, service)] = (handler, schema)

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_remove(self, domain: str, service: str) -> None:
        self.registered.pop((domain, service))


class FakeHass:
    def __init__(self, entry: FakeConfigEntry, *other_entries: Any) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = FakeConfigEntries(entry, *other_entries)
        self.services = FakeServices()
        self.http = FakeHttp()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class FakeHttp:
    def __init__(self) -> None:
        self.static_paths: list[Any] = []

    async def async_register_static_paths(self, configs: list[Any]) -> None:
        self.static_paths.extend(configs)


class RecordingClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def async_create_chore(self, payload: dict[str, Any]) -> int:
        self.payloads.append(payload)
        return 42


class CompletionClient:
    def __init__(self) -> None:
        self.completions: list[tuple[int, int, int | None]] = []

    async def async_complete_chore(
        self, chore_id: int, completed_by: int, assigned_to: int | None = None
    ) -> None:
        self.completions.append((chore_id, completed_by, assigned_to))


class FailingClient:
    async def async_create_chore(self, payload: dict[str, Any]) -> int:
        raise RuntimeError("Donetick unavailable")


class FailingCompletionClient:
    async def async_complete_chore(
        self, chore_id: int, completed_by: int, assigned_to: int | None = None
    ) -> None:
        raise RuntimeError("Donetick unavailable")


class FailingCoordinator:
    async def async_request_refresh(self) -> None:
        raise RuntimeError("refresh unavailable")


async def test_complete_chore_service_records_actual_performer_and_refreshes() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    client = CompletionClient()
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = client

    handler, schema = hass.services.registered[(integration.DOMAIN, "complete_chore")]
    await handler(
        SimpleNamespace(
            data=schema(
                {
                    "chore_id": 42,
                    "completed_by": 7,
                    "assigned_to": 3,
                    "config_entry_id": entry.entry_id,
                }
            )
        )
    )

    assert client.completions == [(42, 7, 3)]
    coordinator = hass.data[integration.DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.refresh_count == 1


async def test_complete_chore_service_surfaces_api_failure_without_refresh() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = FailingCompletionClient()
    handler, schema = hass.services.registered[(integration.DOMAIN, "complete_chore")]

    with pytest.raises(HomeAssistantError, match="Donetick unavailable") as error:
        await handler(
            SimpleNamespace(data=schema({"chore_id": 42, "completed_by": 7}))
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    coordinator = hass.data[integration.DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.refresh_count == 0


async def test_complete_chore_service_rejects_unloaded_config_entry() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)

    with pytest.raises(HomeAssistantError, match="No matching Donetick config entry"):
        await integration.async_complete_chore_service(
            hass,
            SimpleNamespace(
                data={
                    "chore_id": 42,
                    "completed_by": 7,
                    "config_entry_id": entry.entry_id,
                }
            ),
        )


async def test_complete_chore_service_reports_remote_success_when_refresh_fails() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    client = CompletionClient()
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = client
    hass.data[integration.DOMAIN][entry.entry_id]["coordinator"] = FailingCoordinator()
    handler, schema = hass.services.registered[(integration.DOMAIN, "complete_chore")]

    with pytest.raises(
        HomeAssistantError,
        match="chore was completed, but Home Assistant refresh failed",
    ) as error:
        await handler(
            SimpleNamespace(data=schema({"chore_id": 42, "completed_by": 7}))
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert client.completions == [(42, 7, None)]


async def test_create_chore_service_calls_full_api_and_refreshes_coordinator() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    client = RecordingClient()
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = client

    handler, schema = hass.services.registered[(integration.DOMAIN, "create_chore")]
    call_data = schema(
        {
            "name": "Clean kitchen",
            "next_due_date": "2026-09-07T18:00:00+02:00",
            "frequency_type": "weekly",
            "frequency": 2,
            "assignee_ids": [2, 3],
            "assigned_to": 2,
            "assign_strategy": "round_robin",
            "priority": 1,
            "config_entry_id": entry.entry_id,
        }
    )

    await handler(SimpleNamespace(data=call_data))

    assert client.payloads == [
        {
            "name": "Clean kitchen",
            "nextDueDate": "2026-09-07T18:00:00+02:00",
            "frequencyType": "weekly",
            "frequency": 2,
            "assignees": [{"userId": 2}, {"userId": 3}],
            "assignedTo": 2,
            "assignStrategy": "round_robin",
            "priority": 1,
        }
    ]
    coordinator = hass.data[integration.DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.refresh_count == 1


async def test_create_chore_service_surfaces_api_failure_without_refresh() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = FailingClient()
    handler, schema = hass.services.registered[(integration.DOMAIN, "create_chore")]

    with pytest.raises(HomeAssistantError, match="Donetick unavailable") as error:
        await handler(SimpleNamespace(data=schema({"name": "Clean kitchen"})))

    assert isinstance(error.value.__cause__, RuntimeError)
    coordinator = hass.data[integration.DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.refresh_count == 0


async def test_create_chore_service_rejects_unloaded_config_entry() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)

    with pytest.raises(HomeAssistantError, match="No matching Donetick config entry"):
        await integration.async_create_chore_service(
            hass,
            SimpleNamespace(
                data={
                    "name": "Clean kitchen",
                    "config_entry_id": entry.entry_id,
                }
            ),
        )


async def test_create_chore_service_reports_create_success_when_refresh_fails() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    client = RecordingClient()
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = client
    hass.data[integration.DOMAIN][entry.entry_id]["coordinator"] = FailingCoordinator()
    handler, schema = hass.services.registered[
        (integration.DOMAIN, integration.SERVICE_CREATE_CHORE)
    ]

    with pytest.raises(
        HomeAssistantError,
        match="created with ID 42, but Home Assistant refresh failed",
    ) as error:
        await handler(SimpleNamespace(data=schema({"name": "Partial success"})))

    assert isinstance(error.value.__cause__, RuntimeError)
    assert client.payloads == [
        {
            "name": "Partial success",
            "frequencyType": "once",
            "frequency": 1,
            "assignStrategy": "no_assignee",
            "priority": 0,
        }
    ]


async def test_unloading_final_entry_removes_create_chore_service() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    service_key = (integration.DOMAIN, integration.SERVICE_CREATE_CHORE)
    assert service_key in hass.services.registered

    assert await integration.async_unload_entry(hass, entry)

    assert service_key not in hass.services.registered


def _member(user_id: int, *, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, is_active=is_active)


async def test_setup_ties_update_listener_to_entry_unload() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)

    await integration.async_setup_entry(hass, entry)

    assert len(entry.unload_callbacks) == 1


async def test_complete_chore_service_rejects_non_member_performer() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    config = hass.data[integration.DOMAIN][entry.entry_id]
    client = CompletionClient()
    config["client"] = client
    config["circle_members"] = [_member(7), _member(8, is_active=False)]
    handler, schema = hass.services.registered[(integration.DOMAIN, "complete_chore")]

    with pytest.raises(ServiceValidationError, match="completed_by 99"):
        await handler(
            SimpleNamespace(data=schema({"chore_id": 42, "completed_by": 99}))
        )
    with pytest.raises(ServiceValidationError, match="assigned_to 8"):
        await handler(
            SimpleNamespace(
                data=schema({"chore_id": 42, "completed_by": 7, "assigned_to": 8})
            )
        )

    assert client.completions == []
    assert config["coordinator"].refresh_count == 0


async def test_create_chore_service_rejects_non_member_assignee() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    config = hass.data[integration.DOMAIN][entry.entry_id]
    client = RecordingClient()
    config["client"] = client
    config["circle_members"] = [_member(2)]
    handler, schema = hass.services.registered[(integration.DOMAIN, "create_chore")]

    with pytest.raises(ServiceValidationError, match="assignee_ids 3"):
        await handler(
            SimpleNamespace(
                data=schema({"name": "Clean kitchen", "assignee_ids": [2, 3]})
            )
        )

    assert client.payloads == []


async def test_create_chore_service_reports_invalid_input_as_validation_error() -> None:
    entry = FakeConfigEntry()
    hass = FakeHass(entry)
    await integration.async_setup_entry(hass, entry)
    client = RecordingClient()
    hass.data[integration.DOMAIN][entry.entry_id]["client"] = client
    handler, schema = hass.services.registered[(integration.DOMAIN, "create_chore")]

    with pytest.raises(ServiceValidationError, match="is_rolling requires"):
        await handler(
            SimpleNamespace(data=schema({"name": "Laundry", "is_rolling": True}))
        )

    assert client.payloads == []


async def test_config_entry_lookup_ignores_foreign_todo_entities() -> None:
    entry = FakeConfigEntry()
    foreign = SimpleNamespace(entry_id="shopping-1", domain="shopping_list")
    hass = FakeHass(entry, foreign)
    hass.data[integration.DOMAIN] = {entry.entry_id: {}}
    REGISTERED_ENTITIES["todo.shopping_list"] = SimpleNamespace(
        platform="shopping_list", config_entry_id=foreign.entry_id
    )
    REGISTERED_ENTITIES["todo.all_tasks"] = SimpleNamespace(
        platform="donetick", config_entry_id=entry.entry_id
    )
    try:
        assert await integration._get_config_entry(hass, "todo.shopping_list") is None
        assert await integration._get_config_entry(hass, "todo.all_tasks") is entry
    finally:
        REGISTERED_ENTITIES.clear()
