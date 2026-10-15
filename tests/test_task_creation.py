"""Regression tests for creating one-off tasks from todo lists and create_task."""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

import custom_components.donetick as integration
from custom_components.donetick import todo
from custom_components.donetick.api import DonetickApiClient
from custom_components.donetick.model import DonetickMember, DonetickProject

TOKEN_OWNER = 1
CIRCLE_USERS = {1, 2, 3}
PROJECT_ID = 7
# HA configured for a zone behind UTC, where the eAPI's own date-only handling
# (the date plus the current UTC time) can land on the previous local day.
HA_TIME_ZONE = timezone(timedelta(hours=-7))
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _rfc3339(value: Any) -> datetime:
    """Parse like Go's time.Time JSON binding, which requires RFC3339."""
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError(f"cannot parse {value!r} as RFC3339")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientError(f"HTTP {self.status}: {self.body}")

    async def json(self) -> Any:
        return self.body


class FakeDonetick:
    """Donetick create, edit and delete endpoints with their request binding rules.

    version (0, 1, 80) follows the current server. (0, 1, 75) follows servers up
    to 0.1.75, whose full API reads the due date only from "dueDate" and whose
    eAPI has no projectId. The token owner is a plain circle member, not an admin.
    """

    def __init__(self, detail_status: int = 200, version: tuple[int, ...] = (0, 1, 80)) -> None:
        self.chores: dict[int, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.detail_status = detail_status
        self.version = version

    def _store(self, **chore: Any) -> dict[str, Any]:
        chore_id = 41 + len(self.chores)
        stored = {"id": chore_id, "frequencyType": "once", "isActive": True, **chore}
        self.chores[chore_id] = stored
        return stored

    def post(self, url: str, *, json: Any = None, **kwargs: Any) -> FakeResponse:
        path = urlsplit(url).path
        self.requests.append(("POST", path, json))
        if path == "/eapi/v1/chore":
            return self._eapi_create(json)
        if path == "/api/v1/chores/":
            return self._full_api_create(json)
        raise AssertionError(f"Unexpected POST {path}")

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        path = urlsplit(url).path
        self.requests.append(("GET", path, None))
        if path == "/eapi/v1/chore":
            return FakeResponse(200, list(self.chores.values()))
        detail = re.fullmatch(r"/api/v1/chores/(\d+)/details", path)
        if detail:
            if self.detail_status != 200:
                return FakeResponse(self.detail_status, {"error": "denied"})
            return FakeResponse(200, {"res": self.chores[int(detail[1])]})
        raise AssertionError(f"Unexpected GET {path}")

    def put(self, url: str, *, json: Any = None, **kwargs: Any) -> FakeResponse:
        """internal/chore/api.go UpdateChore: CanEdit allows the creator or an admin."""
        path = urlsplit(url).path
        self.requests.append(("PUT", path, json))
        chore = self.chores.get(int(path.removeprefix("/eapi/v1/chore/")))
        if chore is None:
            return FakeResponse(404, {"error": "Chore not found"})
        if chore["createdBy"] != TOKEN_OWNER:
            return FakeResponse(403, {"error": "You can only update your own chores"})
        due = self._eapi_due(json.get("dueDate"))
        if isinstance(due, FakeResponse):
            return due
        chore["name"] = json.get("name") or chore["name"]
        if json.get("description") is not None:
            chore["description"] = json["description"]
        if due is not None:
            chore["nextDueDate"] = _utc(due)
        return FakeResponse(200, chore)

    def delete(self, url: str, **kwargs: Any) -> FakeResponse:
        """internal/chore/api.go DeleteChore: only the creator, admins included."""
        path = urlsplit(url).path
        self.requests.append(("DELETE", path, None))
        chore_id = int(path.removeprefix("/eapi/v1/chore/"))
        if chore_id not in self.chores:
            return FakeResponse(404, {"error": "Chore not found"})
        if self.chores[chore_id]["createdBy"] != TOKEN_OWNER:
            return FakeResponse(403, {"error": "You can only delete your own chores"})
        del self.chores[chore_id]
        return FakeResponse(200, {"message": "Chore deleted successfully"})

    @staticmethod
    def _eapi_due(value: Any) -> datetime | FakeResponse | None:
        """ChoreLiteReq dueDate: RFC3339, or a date plus the current UTC time."""
        if not value:
            return None
        try:
            return _rfc3339(value)
        except ValueError:
            try:
                day = date.fromisoformat(value)
            except ValueError:
                return FakeResponse(400, {"error": "Invalid due date format"})
            return datetime.combine(day, datetime.now(timezone.utc).time(), timezone.utc)

    def _eapi_create(self, body: dict[str, Any]) -> FakeResponse:
        """internal/chore/api.go CreateChore (ChoreLiteReq)."""
        due = self._eapi_due(body.get("dueDate"))
        if isinstance(due, FakeResponse):
            return due
        created_by = body.get("createdBy", TOKEN_OWNER)
        if created_by not in CIRCLE_USERS:
            return FakeResponse(400, {"error": "Specified user not found in circle"})
        chore = self._store(
            name=body["name"],
            description=body.get("description"),
            nextDueDate=_utc(due) if due else None,
            createdBy=created_by,
            assignedTo=created_by,
            assignees=[{"userId": created_by}],
            assignStrategy="random",
            projectId=body.get("projectId") if self.version >= (0, 1, 80) else None,
        )
        return FakeResponse(201, chore)

    def _full_api_create(self, body: dict[str, Any]) -> FakeResponse:
        """internal/chore/handler.go CreateChore (ChoreReq, unknown keys dropped)."""
        if not body.get("name") or not body.get("frequencyType"):
            return FakeResponse(400, {"error": "Invalid request format"})
        if not body.get("assignStrategy"):
            return FakeResponse(400, {"error": "Invalid request format"})
        due = None
        # 0.1.76 and later bind *time.Time nextDueDate; earlier versions parse
        # the string dueDate with time.RFC3339. Gin drops the other key.
        due_key = "nextDueDate" if self.version >= (0, 1, 76) else "dueDate"
        if body.get(due_key):
            try:
                due = _rfc3339(body[due_key])
            except ValueError as err:
                return FakeResponse(400, {"error": "Invalid request format", "details": str(err)})
        assignees = body.get("assignees") or []
        allowed = [a["userId"] for a in assignees] or sorted(CIRCLE_USERS)
        if any(user not in CIRCLE_USERS for user in allowed):
            return FakeResponse(400, {"error": "Assignee not found in circle"})
        if body.get("assignedTo") is not None and body["assignedTo"] not in allowed:
            return FakeResponse(400, {"error": "Assigned to not found in assignees"})
        chore = self._store(
            name=body["name"],
            description=body.get("description"),
            nextDueDate=_utc(due) if due else None,
            createdBy=TOKEN_OWNER,
            assignedTo=body.get("assignedTo"),
            assignees=[{"userId": a["userId"]} for a in assignees],
            assignStrategy=body["assignStrategy"],
            projectId=body.get("projectId"),
        )
        return FakeResponse(200, {"res": chore["id"]})


class RefreshingCoordinator:
    """Coordinator that reloads tasks through the eAPI like the real one."""

    def __init__(self, client: DonetickApiClient) -> None:
        self.client = client
        self.data: list[Any] = []
        self.refresh_count = 0

    async def async_refresh(self) -> None:
        self.refresh_count += 1
        self.data = await self.client.async_get_tasks()


@pytest.fixture(autouse=True)
def ha_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", HA_TIME_ZONE)


@pytest.fixture
def server() -> FakeDonetick:
    return FakeDonetick()


def _client(server: FakeDonetick) -> DonetickApiClient:
    return DonetickApiClient("https://donetick.example", "token", server)  # type: ignore[arg-type]


def _list_entity(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick, cls: type, *args: Any
) -> Any:
    monkeypatch.setattr(todo, "async_get_clientsession", lambda hass: server)
    config_entry = type("Entry", (), {
        "entry_id": "entry-1",
        "data": {"url": "https://donetick.example", "token": "token", "show_due_in": 0},
    })()
    entity = cls(RefreshingCoordinator(_client(server)), config_entry, *args)
    entity.hass = None
    return entity


def _member(user_id: int) -> DonetickMember:
    # The circle membership id differs from the user id, as on a real server.
    return DonetickMember(id=100 + user_id, user_id=user_id, circle_id=1, role="member",
                          is_active=True, username=f"user{user_id}",
                          display_name=f"User {user_id}")


async def test_task_without_project_uses_eapi_with_creator_and_due_date(
    server: FakeDonetick,
) -> None:
    task = await _client(server).async_create_task(
        "Buy milk", "2L", "2026-10-20T10:00:00+02:00", created_by=2
    )

    assert server.requests == [("POST", "/eapi/v1/chore", {
        "name": "Buy milk", "dueDate": "2026-10-20T10:00:00+02:00",
        "createdBy": 2, "description": "2L",
    })]
    stored = server.chores[task.id]
    assert stored["nextDueDate"] == "2026-10-20T08:00:00Z"
    assert (stored["createdBy"], stored["assignedTo"], stored["assignees"]) == (
        2, 2, [{"userId": 2}]
    )


@pytest.mark.parametrize("project_id", [None, PROJECT_ID])
async def test_date_only_due_date_is_end_of_that_day_in_ha_time_zone(
    server: FakeDonetick, project_id: int | None
) -> None:
    await _client(server).async_create_task(
        "Buy milk", due_date="2026-10-20", project_id=project_id
    )

    [stored] = server.chores.values()
    # 2026-10-20T23:59:59-07:00, still on the chosen day in HA's time zone.
    assert stored["nextDueDate"] == "2026-10-21T06:59:59Z"
    assert stored["projectId"] == project_id


async def test_project_task_uses_full_api_without_follow_up_request() -> None:
    server = FakeDonetick(detail_status=403)
    due = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

    task = await _client(server).async_create_task(
        "Paint", "Hall", "2026-10-20T10:00:00+00:00", project_id=PROJECT_ID
    )

    assert server.requests == [("POST", "/api/v1/chores/", {
        "name": "Paint", "frequencyType": "once", "assignStrategy": "no_assignee",
        "projectId": PROJECT_ID, "nextDueDate": "2026-10-20T10:00:00+00:00",
        "dueDate": "2026-10-20T10:00:00+00:00", "description": "Hall",
    })]
    assert (task.id, task.name, task.project_id, task.next_due_date) == (
        41, "Paint", PROJECT_ID, due
    )
    assert server.chores[41]["nextDueDate"] == "2026-10-20T10:00:00Z"


async def test_project_task_for_a_member_is_assigned_to_them(server: FakeDonetick) -> None:
    task = await _client(server).async_create_task("Paint", created_by=2, project_id=PROJECT_ID)

    stored = server.chores[task.id]
    assert (stored["assignedTo"], stored["assignees"], stored["assignStrategy"]) == (
        2, [{"userId": 2}], "keep_last_assigned"
    )
    assert task.assigned_to == 2


async def test_member_list_item_is_assigned_to_member_and_stays_listed(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick
) -> None:
    entity = _list_entity(monkeypatch, server, todo.DonetickAssigneeTasksList, _member(2))

    await entity.async_create_todo_item(todo.TodoItem(summary="Buy milk", due=date(2026, 10, 20)))

    assert entity.coordinator.refresh_count == 1
    [stored] = server.chores.values()
    # The token owner stays the creator, so Home Assistant can still delete it.
    assert (stored["createdBy"], stored["assignedTo"], stored["assignees"]) == (
        TOKEN_OWNER, 2, [{"userId": 2}]
    )
    assert stored["nextDueDate"] == "2026-10-21T06:59:59Z"
    assert [item.summary for item in entity.todo_items] == ["Buy milk"]


_LISTS = [
    pytest.param(todo.DonetickAllTasksList, (), id="all"),
    pytest.param(todo.DonetickAssigneeTasksList, (_member(2),), id="member"),
    pytest.param(todo.DonetickProjectTasksList,
                 (DonetickProject(id=PROJECT_ID, name="House"),), id="project"),
]


@pytest.mark.parametrize(("cls", "args"), _LISTS)
async def test_list_item_can_be_renamed_and_deleted_after_creation(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick, cls: type, args: tuple[Any, ...]
) -> None:
    entity = _list_entity(monkeypatch, server, cls, *args)
    await entity.async_create_todo_item(todo.TodoItem(summary="Buy milk"))
    [item] = entity.todo_items

    await entity.async_update_todo_item(todo.TodoItem(
        uid=item.uid, summary="Buy oat milk", status=todo.TodoItemStatus.NEEDS_ACTION
    ))
    [item] = entity.todo_items
    assert item.summary == "Buy oat milk"

    await entity.async_delete_todo_items([item.uid])
    assert server.chores == {}
    assert entity.todo_items == []


@pytest.mark.parametrize(("cls", "args"), [
    param for param in _LISTS if param.id != "all"
])
@pytest.mark.parametrize(("due", "stored_due"), [
    (datetime(2026, 10, 20, 9, 30, tzinfo=HA_TIME_ZONE), "2026-10-20T16:30:00Z"),
    (date(2026, 10, 20), "2026-10-21T06:59:59Z"),
])
async def test_full_api_list_item_keeps_due_date_on_donetick_0_1_75(
    monkeypatch: pytest.MonkeyPatch, cls: type, args: tuple[Any, ...],
    due: date, stored_due: str,
) -> None:
    server = FakeDonetick(version=(0, 1, 75))
    entity = _list_entity(monkeypatch, server, cls, *args)

    await entity.async_create_todo_item(todo.TodoItem(summary="Paint", due=due))

    [stored] = server.chores.values()
    assert stored["nextDueDate"] == stored_due
    assert [item.summary for item in entity.todo_items] == ["Paint"]


async def test_rejected_member_list_item_raises_without_refresh(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick
) -> None:
    entity = _list_entity(monkeypatch, server, todo.DonetickAssigneeTasksList, _member(9))

    with pytest.raises(aiohttp.ClientError):
        await entity.async_create_todo_item(todo.TodoItem(summary="Buy milk"))

    assert server.chores == {}
    assert entity.coordinator.refresh_count == 0


@pytest.mark.parametrize(("cls", "args"), [
    param for param in _LISTS if param.id != "project"
])
async def test_date_only_due_date_edit_stays_on_that_local_day(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick, cls: type, args: tuple[Any, ...]
) -> None:
    entity = _list_entity(monkeypatch, server, cls, *args)
    await entity.async_create_todo_item(todo.TodoItem(summary="Buy milk"))
    [item] = entity.todo_items

    await entity.async_update_todo_item(todo.TodoItem(
        uid=item.uid, summary="Buy milk", status=todo.TodoItemStatus.NEEDS_ACTION,
        due=date(2026, 10, 20),
    ))

    [stored] = server.chores.values()
    # Same end-of-day value as a date-only create, not the date plus UTC time.
    assert stored["nextDueDate"] == "2026-10-21T06:59:59Z"


async def test_all_list_item_keeps_its_due_date_time(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick
) -> None:
    entity = _list_entity(monkeypatch, server, todo.DonetickAllTasksList)
    due = datetime(2026, 10, 20, 9, 30, tzinfo=HA_TIME_ZONE)

    await entity.async_create_todo_item(todo.TodoItem(summary="Call", due=due))

    [item] = entity.todo_items
    assert (item.summary, item.due) == ("Call", due)


async def test_project_list_item_is_created_when_detail_request_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeDonetick(detail_status=403)
    entity = _list_entity(monkeypatch, server, todo.DonetickProjectTasksList,
                          DonetickProject(id=PROJECT_ID, name="House"))

    await entity.async_create_todo_item(todo.TodoItem(summary="Paint", due=date(2026, 10, 20)))

    assert entity.coordinator.refresh_count == 1
    assert [item.summary for item in entity.todo_items] == ["Paint"]
    assert server.chores[41]["nextDueDate"] == "2026-10-21T06:59:59Z"


@pytest.mark.parametrize(("cls", "args"), [
    (todo.DonetickAllTasksList, ()),
    (todo.DonetickAssigneeTasksList, (_member(2),)),
    (todo.DonetickProjectTasksList, (DonetickProject(id=PROJECT_ID, name="House"),)),
])
async def test_creating_with_debug_logging_logs_cleanly(
    monkeypatch: pytest.MonkeyPatch, server: FakeDonetick,
    caplog: pytest.LogCaptureFixture, cls: type, args: tuple[Any, ...],
) -> None:
    caplog.set_level(logging.DEBUG)
    entity = _list_entity(monkeypatch, server, cls, *args)

    await entity.async_create_todo_item(todo.TodoItem(summary="Paint"))

    assert "Creating task 'Paint'" in caplog.text
    assert entity.coordinator.refresh_count == 1


class FakeEntries:
    def __init__(self, entry: Any) -> None:
        self.entry = entry

    def async_entries(self, domain: str) -> list[Any]:
        return [self.entry]


def _service_hass(server: FakeDonetick) -> Any:
    entry = type("Entry", (), {"entry_id": "entry-1", "domain": integration.DOMAIN})()
    hass = type("Hass", (), {})()
    hass.config_entries = FakeEntries(entry)
    hass.data = {integration.DOMAIN: {"entry-1": {
        "client": _client(server), "coordinator": DataUpdateCoordinator(),
    }}}
    return hass


@pytest.mark.parametrize(("due_date", "stored_due"), [
    ("2026-11-01T09:00:00Z", "2026-11-01T09:00:00Z"),
    ("2026-11-01", "2026-11-02T06:59:59Z"),
    ("2026-11-01T09:00:00", "2026-11-01T16:00:00Z"),
])
async def test_create_task_service_keeps_due_date_and_creator(
    due_date: str, stored_due: str
) -> None:
    # A detail request, if one were made, would be denied.
    server = FakeDonetick(detail_status=403)
    hass = _service_hass(server)
    call = ServiceCall(integration.CREATE_TASK_SCHEMA(
        {"name": "Milk", "due_date": due_date, "created_by": 2}
    ))

    await integration.async_create_task_service(hass, call)

    [stored] = server.chores.values()
    assert stored["nextDueDate"] == stored_due
    assert (stored["createdBy"], stored["assignedTo"]) == (2, 2)
    assert hass.data[integration.DOMAIN]["entry-1"]["coordinator"].refresh_count == 1


async def test_create_task_service_rejects_an_invalid_due_date(server: FakeDonetick) -> None:
    hass = _service_hass(server)

    with pytest.raises(ServiceValidationError, match="RFC3339 or YYYY-MM-DD"):
        await integration.async_create_task_service(
            hass, ServiceCall({"name": "Milk", "due_date": "next Tuesday"})
        )

    assert server.requests == []
