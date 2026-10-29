"""Tests for the Donetick todo list entity."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.todo import TodoItem, TodoItemStatus

from custom_components.donetick import todo


class RecordingTaskClient:
    def __init__(self, *args: Any) -> None:
        self.completions: list[tuple[int, int | None]] = []
        self.updates: list[dict[str, Any]] = []
        RecordingTaskClient.instances.append(self)

    instances: list["RecordingTaskClient"] = []

    async def async_complete_task(self, task_id: int, completed_by: int | None):
        self.completions.append((task_id, completed_by))
        return SimpleNamespace(
            id=task_id,
            name="Laundry",
            frequency_type="weekly",
            next_due_date=datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc),
        )

    async def async_get_tasks(self) -> list[Any]:
        return [SimpleNamespace(id=5, assigned_to=3, is_active=True)]

    async def async_update_task(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeCoordinator:
    def __init__(self, data: list[Any]) -> None:
        self.data = data
        self.refresh_count = 0

    async def async_refresh(self) -> None:
        self.refresh_count += 1


@pytest.fixture
def entity(monkeypatch: pytest.MonkeyPatch) -> todo.DonetickAllTasksList:
    RecordingTaskClient.instances.clear()
    monkeypatch.setattr(todo, "DonetickApiClient", RecordingTaskClient)
    coordinator = FakeCoordinator([SimpleNamespace(id=5, assigned_to=3)])
    config_entry = SimpleNamespace(
        entry_id="entry-1",
        data={"url": "https://donetick.example", "token": "test-token"},
    )
    list_entity = todo.DonetickAllTasksList(coordinator, config_entry)
    list_entity.hass = None
    return list_entity


async def test_completing_recurring_task_does_not_issue_follow_up_update(
    entity: todo.DonetickAllTasksList,
) -> None:
    item = TodoItem(
        summary="Laundry",
        uid="5--2026-10-07 18:00:00+00:00",
        status=TodoItemStatus.COMPLETED,
    )

    await entity.async_update_todo_item(item)

    (client,) = RecordingTaskClient.instances
    assert client.completions == [(5, 3)]
    assert client.updates == []
    assert entity.coordinator.refresh_count == 1


async def test_editing_open_task_updates_its_properties(
    entity: todo.DonetickAllTasksList,
) -> None:
    item = TodoItem(
        summary="Laundry",
        uid="5--2026-10-07 18:00:00+00:00",
        status=TodoItemStatus.NEEDS_ACTION,
        description="Whites only",
    )

    await entity.async_update_todo_item(item)

    (client,) = RecordingTaskClient.instances
    assert client.completions == []
    assert client.updates == [
        {
            "task_id": 5,
            "name": "Laundry",
            "description": "Whites only",
            "due_date": None,
        }
    ]
    assert entity.coordinator.refresh_count == 1
