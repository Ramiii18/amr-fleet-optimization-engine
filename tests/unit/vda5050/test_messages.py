"""Tests for the VDA 5050 message types (D30), against the official 3.0.0 schemas."""

import dataclasses
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from amr_optimizer.vda5050 import (
    Action,
    ActionState,
    Connection,
    Edge,
    EdgeState,
    Header,
    HeaderCounter,
    InstantActions,
    Node,
    NodePosition,
    NodeState,
    Order,
    State,
    format_timestamp,
    parse_timestamp,
    topic,
)

Check = Callable[[str, dict[str, Any]], None]
AT = datetime(2026, 11, 6, 8, 30, 1, 234567, tzinfo=UTC)
HEADER = Header(7, AT, "synthetic", "R01")
PICK = Action("pick", "O1-pick", "HARD", (("loadId", "O1"),))

ORDER = Order(
    HEADER,
    "O1",
    0,
    (
        Node("A", 0, True, NodePosition(0.0, 1.0, "large")),
        Node("B", 2, True, NodePosition(2.0, 1.0, "large"), (PICK,)),
        Node("C", 4, False, NodePosition(2.0, 0.0, "large")),
    ),
    (Edge("A-B", 1, True, 2.0), Edge("B-C", 3, False, 1.0)),
)
STATE = State(
    HEADER,
    "O1",
    0,
    "B",
    2,
    (NodeState("C", 4, False),),
    (EdgeState("B-C", 3, False),),
    False,
    (ActionState("O1-pick", "RUNNING", "pick"),),
)


def test_the_topic_layout_follows_the_specification() -> None:
    assert topic("synthetic", "R01", "order") == "vda5050/v3/synthetic/R01/order"


def test_timestamps_are_utc_with_milliseconds() -> None:
    text = format_timestamp(AT.astimezone(timezone(timedelta(hours=2))))

    assert text == "2026-11-06T08:30:01.234Z"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", text)
    assert parse_timestamp(text) == AT.replace(microsecond=234000)


def test_header_ids_count_per_topic() -> None:
    counter = HeaderCounter("synthetic", "R01")

    ids = [counter.next(name, AT).header_id for name in ("state", "state", "connection", "state")]

    assert ids == [0, 1, 0, 2]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("order", ORDER.to_json()),
        ("state", STATE.to_json()),
        ("instantActions", InstantActions(HEADER, (Action("startPause", "p1", "NONE"),)).to_json()),
        ("connection", Connection(HEADER, "ONLINE").to_json()),
    ],
    ids=["order", "state", "instantActions", "connection"],
)
def test_messages_are_valid_against_the_official_schemas(
    name: str, message: dict[str, Any], check_message: Check
) -> None:
    check_message(name, message)


def test_the_check_catches_invalid_and_invented_fields(check_message: Check) -> None:
    invalid = ORDER.to_json() | {"orderUpdateId": -1}
    invented = ORDER.to_json() | {"priority": 3}

    with pytest.raises(AssertionError):
        check_message("order", invalid)
    with pytest.raises(AssertionError):
        check_message("order", invented)


def test_orders_and_states_survive_a_round_trip_through_json() -> None:
    header = Header(7, AT.replace(microsecond=234000), "synthetic", "R01")
    order = dataclasses.replace(ORDER, header=header)
    state = dataclasses.replace(STATE, header=header)

    assert Order.from_json(order.to_json()) == order
    assert State.from_json(state.to_json()) == state
