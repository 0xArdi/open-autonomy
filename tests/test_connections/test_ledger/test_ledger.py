# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2021-2022 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""This module contains the tests of the ledger connection module."""

import asyncio
from asyncio import Task
from typing import Any, List
from unittest import mock

import pytest
from aea.configurations.base import ConnectionConfig
from aea.connections.base import ConnectionStates

from packages.valory.connections.ledger.connection import LedgerConnection


def dummy_task_wrapper(waiting_time: float, result: Any) -> Task:
    """Create a dummy task that simply waits for a given `waiting_time` and returns a given `result`."""

    async def dummy_task(*_: Any) -> None:
        """Wait for the given `waiting_time` and return the given `result`."""
        await asyncio.sleep(waiting_time, result)

    task = asyncio.create_task(dummy_task())
    return task


class TestLedgerConnection:
    """Test `LedgerConnection` class."""

    @pytest.mark.asyncio
    async def test_not_hanging(self) -> None:
        """Test that the connection does not hang and that the tasks cannot get blocked."""
        # create configurations for the test, re bocking and non-blocking tasks' waiting times
        blocking_time = 100
        wait_time_among_tasks = 0.1
        non_blocking_time = 1
        tolerance = 0.1
        assert (
            non_blocking_time < blocking_time
        ), "`non_blocking_time` should be much smaller than the `blocking_time`."
        assert (
            tolerance * 10 < blocking_time
        ), "`tolerance` should be much smaller than the `blocking_time`."

        # setup a dummy ledger connection
        ledger_connection = LedgerConnection(
            configuration=ConnectionConfig("ledger", "valory", "0.1.0"),
            data_dir="test_data_dir",
        )

        # connect() is called by the multiplexer
        await ledger_connection.connect()

        # once a connection is ready
        # receive() is called by the multiplexer
        receive_task = asyncio.ensure_future(ledger_connection.receive())

        # create a blocking task lasting `blocking_time` secs
        blocking_task = dummy_task_wrapper(blocking_time, "blocking_task")
        dummy_envelope = mock.MagicMock()
        with mock.patch.object(
            LedgerConnection, "_schedule_request", return_value=blocking_task
        ):
            await ledger_connection.send(dummy_envelope)

        # create a non-blocking task lasting `non_blocking_time` secs, after `wait_time_among_tasks`
        await asyncio.sleep(wait_time_among_tasks)
        normal_task = dummy_task_wrapper(non_blocking_time, "normal_task")
        dummy_envelope = mock.MagicMock()
        with mock.patch.object(
            LedgerConnection, "_schedule_request", return_value=normal_task
        ):
            await ledger_connection.send(dummy_envelope)

        # sleep for `non_blocking_time + tolerance`
        await asyncio.sleep(non_blocking_time + tolerance)

        # the normal task should be finished
        assert normal_task.done(), "Normal task should be done at this point."

        # the blocking task should not be done
        assert not blocking_task.done(), "Blocking task should be still running."

        # the receive task should not be done
        # because it is only awaiting the `blocking_task`
        assert not receive_task.done(), "Receive task is blocked by blocking task!"
