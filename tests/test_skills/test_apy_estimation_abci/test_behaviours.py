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

"""Tests for valory/apy_estimation_abci skill's behaviours."""
import binascii
import json
import logging
import os
import time
from datetime import datetime
from enum import Enum
from itertools import product
from multiprocessing.pool import AsyncResult
from pathlib import Path, PosixPath
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    cast,
)
from unittest import mock
from uuid import uuid4

import numpy as np
import optuna
import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from aea.skills.tasks import TaskManager
from hypothesis import given, settings
from hypothesis import strategies as st

from packages.valory.protocols.abci import AbciMessage  # noqa: F401
from packages.valory.skills.abstract_round_abci.base import AbciApp, AbciAppDB
from packages.valory.skills.abstract_round_abci.behaviour_utils import (
    BaseBehaviour,
    IPFSBehaviour,
)
from packages.valory.skills.abstract_round_abci.io.store import SupportedFiletype
from packages.valory.skills.abstract_round_abci.models import ApiSpecs, BenchmarkTool
from packages.valory.skills.abstract_round_abci.test_tools.base import (
    FSMBehaviourBaseCase,
)
from packages.valory.skills.apy_estimation_abci.behaviours import (
    APYEstimationBaseBehaviour,
    CycleResetBehaviour,
    EstimateBehaviour,
    EstimatorRoundBehaviour,
    FetchBatchBehaviour,
    FetchBehaviour,
    FreshModelResetBehaviour,
    OptimizeBehaviour,
    PrepareBatchBehaviour,
    PreprocessBehaviour,
    RandomnessBehaviour,
)
from packages.valory.skills.apy_estimation_abci.behaviours import (
    TestBehaviour as _TestBehaviour,
)
from packages.valory.skills.apy_estimation_abci.behaviours import (
    TrainBehaviour,
    TransformBehaviour,
    UpdateForecasterBehaviour,
)
from packages.valory.skills.apy_estimation_abci.ml.forecasting import (
    PoolIdToForecasterType,
    PoolIdToTestReportType,
)
from packages.valory.skills.apy_estimation_abci.ml.optimization import (
    PoolToHyperParamsWithStatusType,
)
from packages.valory.skills.apy_estimation_abci.ml.preprocessing import (
    prepare_pair_data,
)
from packages.valory.skills.apy_estimation_abci.models import SubgraphsMixin
from packages.valory.skills.apy_estimation_abci.rounds import Event, SynchronizedData
from packages.valory.skills.apy_estimation_abci.tools.etl import ResponseItemType
from packages.valory.skills.apy_estimation_abci.tools.queries import SAFE_BLOCK_TIME

from tests.conftest import ROOT_DIR
from tests.test_skills.test_apy_estimation_abci.conftest import DummyPipeline


SLEEP_TIME_TWEAK = 0.01

ipfs_daemon = pytest.mark.usefixtures("ipfs_daemon")


class DummyAsyncResult(object):
    """Dummy class for AsyncResult."""

    def __init__(
        self,
        task_result: Any,
        ready: bool = True,
    ) -> None:
        """Initialize class."""

        self.id = uuid4()
        self._ready = ready
        self._task_result = task_result

    def ready(
        self,
    ) -> bool:
        """Returns bool"""
        return self._ready

    def get(
        self,
    ) -> Any:
        """Returns task result."""
        return self._task_result


@ipfs_daemon
class APYEstimationFSMBehaviourBaseCase(FSMBehaviourBaseCase):
    """Base case for testing APYEstimation FSMBehaviour."""

    path_to_skill = Path(
        ROOT_DIR, "packages", "valory", "skills", "apy_estimation_abci"
    )

    behaviour: EstimatorRoundBehaviour
    behaviour_class: Type[APYEstimationBaseBehaviour]
    next_behaviour_class: Type[APYEstimationBaseBehaviour]
    synchronized_data: SynchronizedData

    @classmethod
    def setup(cls, **kwargs: Any) -> None:
        """Set up the test class."""
        super().setup(
            param_overrides={"ipfs_domain_name": "/dns/localhost/tcp/5001/http"}
        )
        cls.synchronized_data = SynchronizedData(
            AbciAppDB(
                setup_data={"full_training": [False]},
            )
        )

    def end_round(self, done_event: Enum = Event.DONE) -> None:
        """Ends round early to cover `wait_for_end` generator."""
        super().end_round(done_event)


class TestFetchProgress:
    """Test `FetchBehaviour`'s `Progress`."""

    @pytest.mark.parametrize(
        "current_timestamp, current_dex_name, expected",
        (
            (None, None, False),
            (None, "test", False),
            (0, None, False),
            (0, "test", True),
        ),
    )
    def test_can_continue(
        self,
        current_timestamp: Optional[int],
        current_dex_name: Optional[str],
        expected: bool,
    ) -> None:
        """Test `can_continue` property."""
        progress = FetchBehaviour.Progress(
            current_timestamp=current_timestamp, current_dex_name=current_dex_name
        )
        assert progress.can_continue is expected


class TestFetchAndBatchBehaviours(APYEstimationFSMBehaviourBaseCase):
    """Test FetchBehaviour and FetchBatchBehaviour."""

    behaviour_class = FetchBehaviour
    next_behaviour_class = TransformBehaviour

    @pytest.mark.parametrize(
        "progress_initialized, current_dex_name, expected_pair_ids",
        (
            (False, "spooky_subgraph", []),
            (True, "spooky_subgraph", ["0xec454eda10accdd66209c57af8c12924556f3abd"]),
            (True, "uniswap_subgraph", ["0x00004ee988665cdda9a1080d5792cecd16dc1220"]),
        ),
    )
    def test_current_pair_ids(
        self,
        progress_initialized: bool,
        current_dex_name: str,
        expected_pair_ids: List[str],
        pairs_ids: Dict[str, List[str]],
    ) -> None:
        """Test `current_pair_ids`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour._progress.initialized = progress_initialized
        behaviour._progress.current_dex_name = current_dex_name
        behaviour.params.pair_ids = pairs_ids

        assert expected_pair_ids == behaviour.current_pair_ids

    @pytest.mark.parametrize(
        "progress_initialized, current_dex_name, expected",
        (
            (False, "test", "default"),
            (False, "other", "default"),
            (True, "test", "test"),
            (True, "other", "other"),
        ),
    )
    @mock.patch.object(
        SubgraphsMixin, "get_subgraph", return_value="get_subgraph_output"
    )
    def test_current_dex(
        self,
        get_subgraph_mock: mock.Mock,
        progress_initialized: bool,
        current_dex_name: str,
        expected: str,
    ) -> None:
        """Test `current_dex`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour._progress.initialized = progress_initialized
        behaviour._progress.current_dex_name = current_dex_name

        actual = behaviour.current_dex
        get_subgraph_mock.assert_called_once_with(expected)
        assert actual == "get_subgraph_output"

    def test_current_chain(self) -> None:
        """Test `current_chain`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.context.default = mock.MagicMock(
            chain_subgraph_name="chain_subgraph_name"
        )
        behaviour.context.chain_subgraph_name = mock.MagicMock(
            api_id="test_current_chain"
        )

        assert (
            behaviour.current_chain.api_id == "test_current_chain"
        ), "current chain is incorrect"

    @pytest.mark.parametrize(
        "current_dex_name", ("uniswap_subgraph", "spooky_subgraph")
    )
    @pytest.mark.parametrize(
        "progress_initialized, pairs_hist, n_fetched, expected",
        (
            (False, [], 0, 0),
            (False, ["test"], 100, 0),
            (True, ["test"], 1, 0),
            (True, [i for i in range(100)], 10, 90),
            (True, [i for i in range(123)], 67, 56),
        ),
    )
    def test_currently_downloaded(
        self,
        current_dex_name: str,
        progress_initialized: bool,
        pairs_hist: ResponseItemType,
        n_fetched: int,
        expected: int,
        pairs_ids: Dict[str, List[str]],
    ) -> None:
        """Test `currently_downloaded`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        behaviour._pairs_hist = pairs_hist
        behaviour._progress.initialized = progress_initialized
        behaviour._progress.current_dex_name = current_dex_name
        behaviour._progress.n_fetched = n_fetched

        assert behaviour.currently_downloaded == expected

    @given(st.lists(st.integers()))
    @settings(deadline=5000)
    def test_total_downloaded(
        self,
        pairs_hist: List,
    ) -> None:
        """Test `total_downloaded`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour._pairs_hist = pairs_hist

        assert behaviour.total_downloaded == len(pairs_hist)

    @given(st.lists(st.booleans(), max_size=50))
    @settings(deadline=5000)
    def test_retries_exceeded(
        self,
        is_exceeded_per_subgraph: List[bool],
    ) -> None:
        """Test `retries_exceeded`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        # set up dummy subgraphs with their `is_retries_exceeded` returning whatever hypothesis selected to
        for i in range(len(is_exceeded_per_subgraph)):
            subgraph = mock.MagicMock()
            subgraph.is_retries_exceeded.return_value = is_exceeded_per_subgraph[i]
            behaviour._utilized_subgraphs[f"test{i}"] = subgraph

        # we expect `retries_exceeded` to return True if any of the subgraphs' `is_retries_exceeded` returns `True`
        expected = any(is_exceeded_per_subgraph)
        assert behaviour.retries_exceeded == expected

    @pytest.mark.parametrize("batch_flag", (True, False))
    def test_setup(self, monkeypatch: MonkeyPatch, batch_flag: bool) -> None:
        """Test behaviour setup."""
        self.skill.skill_context.state.round_sequence.abci_app._last_timestamp = (
            datetime.now()
        )

        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        cast(FetchBehaviour, self.behaviour.current_behaviour).batch = batch_flag

        monkeypatch.setattr(os.path, "join", lambda *_: "")
        cast(APYEstimationBaseBehaviour, self.behaviour.current_behaviour).setup()

    @staticmethod
    def mocked_handle_response_wrapper(
        res: Optional[Any],
    ) -> Callable[[Any, Any], Generator[None, None, Optional[Any]]]:
        """A wrapper to a mocked version of the `_handle_response` method, which returns the given `fetched_pairs`."""

        def mocked_handle_response(
            *_: Any, **__: Any
        ) -> Generator[None, None, Optional[Any]]:
            """A mocked version of the `_handle_response` method, which returns the given `fetched_pairs`."""
            yield
            return res

        return mocked_handle_response

    @pytest.mark.parametrize(
        "fetched_pairs, expected",
        (
            (None, False),
            ([{"id": "test"}, {"id": "test"}], False),
            (
                [{"id": "test"}, {"id": "0x00004ee988665cdda9a1080d5792cecd16dc1220"}],
                False,
            ),
            (
                [{"id": "0xec454eda10accdd66209c57af8c12924556f3abd"}, {"id": "test"}],
                False,
            ),
            (
                [
                    {"id": "0xec454eda10accdd66209c57af8c12924556f3abd"},
                    {"id": "0x00004ee988665cdda9a1080d5792cecd16dc1220"},
                ],
                True,
            ),
        ),
    )
    def test_check_given_pairs(
        self,
        fetched_pairs: Optional[List[Dict[str, str]]],
        expected: bool,
        pairs_ids: Dict[str, List[str]],
    ) -> None:
        """Test `_check_given_pairs` method."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour.get_subgraph = mock.MagicMock()  # type: ignore
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour.get_http_response = mock.MagicMock()  # type: ignore
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._handle_response = TestFetchAndBatchBehaviours.mocked_handle_response_wrapper(  # type: ignore
            fetched_pairs
        )  # type: ignore

        gen = behaviour._check_given_pairs()
        gen.send(None)
        gen.send(None)

        try:
            gen.send(None)
        except StopIteration:
            assert behaviour._pairs_exist is expected
        else:
            raise AssertionError(
                "Test did not finish as expected. `_check_given_pairs` should have reached to its end."
            )

    @pytest.mark.parametrize("batch", (True, False))
    @given(
        st.integers(min_value=0),
        st.integers(min_value=1),
        st.integers(min_value=1, max_value=50),
    )
    @settings(deadline=5000)
    def test_reset_timestamps_iterator(
        self,
        batch: bool,
        start: int,
        interval: int,
        end: int,
    ) -> None:
        """Test `_reset_timestamps_iterator` method."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.batch = batch
        behaviour.params.start = start
        behaviour.params.interval = interval
        behaviour.params.end = end

        behaviour._reset_timestamps_iterator()

        expected = [end] if batch else [i for i in range(start, end, interval)]
        assert behaviour._progress.timestamps_iterator is not None
        assert list(behaviour._progress.timestamps_iterator) == expected

    @given(
        st.booleans(),
        st.booleans(),
        st.integers(max_value=50),
        st.integers(),
        st.booleans(),
        st.text(min_size=1),
        st.integers(),
        st.lists(st.text(min_size=1)),
    )
    @settings(deadline=5000)
    def test_set_current_progress(
        self,
        pairs_ids: Dict[str, List[str]],
        retries_exceeded: bool,
        call_failed: bool,
        currently_downloaded: int,
        target_per_pool: int,
        batch: bool,
        expected_dex_name: str,
        expected_timestamp: int,
        pairs_hist: ResponseItemType,
    ) -> None:
        """Test `_set_current_progress`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour._utilized_subgraphs["test"] = ApiSpecs(
            name="",
            skill_context=mock.MagicMock(),
            url="test",
            api_id="test",
            method="test",
        )
        assert behaviour._utilized_subgraphs["test"] is not None
        behaviour.batch = batch

        # start of setting the `currently_downloaded`
        # we cannot simply mock because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour.params.pair_ids = pairs_ids
        behaviour._progress.current_dex_name = "uniswap_subgraph"
        behaviour._pairs_hist = [{"test": "test"} for _ in range(currently_downloaded)]
        # end of setting the `currently_downloaded`

        behaviour._target_per_pool = target_per_pool
        behaviour._pairs_hist = pairs_hist
        behaviour._progress.call_failed = call_failed
        behaviour._progress.dex_names_iterator = (
            iter((expected_dex_name,)) if expected_dex_name is not None else iter(())
        )
        behaviour._progress.timestamps_iterator = (
            iter((expected_timestamp,)) if expected_timestamp is not None else iter(())
        )
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._reset_timestamps_iterator = mock.MagicMock()  # type: ignore
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour.clean_up = mock.MagicMock()  # type: ignore

        if retries_exceeded:
            # exceed the retries before calling the method
            for _ in range(behaviour._utilized_subgraphs["test"]._retries + 1):
                behaviour._utilized_subgraphs["test"].increment_retries()
            # call the tested method
            behaviour._set_current_progress()
            # assert its results
            assert behaviour._progress.current_timestamp is None
            assert behaviour._progress.current_dex_name is None
            behaviour.clean_up.assert_called_once()

        elif not call_failed:
            # call the tested method
            behaviour._set_current_progress()

            if (
                currently_downloaded == 0
                or currently_downloaded == target_per_pool
                or batch
            ):
                # assert its results
                assert behaviour._progress.current_dex_name == expected_dex_name
                behaviour._reset_timestamps_iterator.assert_called_once()
                assert behaviour._progress.n_fetched == len(pairs_hist)

            assert behaviour._progress.current_timestamp == expected_timestamp

        else:
            # call the tested method
            behaviour._set_current_progress()

        assert behaviour._progress.initialized

    def test_handle_response(self, caplog: LogCaptureFixture) -> None:
        """Test `handle_response`."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        cast(
            FetchBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK

        # test with empty response.
        specs = ApiSpecs(
            url="test",
            api_id="test",
            method="GET",
            name="test",
            skill_context=self.behaviour.context,
        )

        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            handling_generator = cast(
                FetchBehaviour, self.behaviour.current_behaviour
            )._handle_response(None, "test_context", ("", 0), specs)
            next(handling_generator)
            time.sleep(SLEEP_TIME_TWEAK + 0.01)

            try:
                next(handling_generator)
            except StopIteration as res:
                assert res.value is None

            assert (
                "[test_agent_name] Could not get test_context from test" in caplog.text
            )
            assert specs._retries_attempted == 1

        caplog.clear()
        with caplog.at_level(
            logging.INFO,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            handling_generator = cast(
                FetchBehaviour, self.behaviour.current_behaviour
            )._handle_response({"test": [4, 5]}, "test", ("test", 0), specs)
            try:
                next(handling_generator)
            except StopIteration as res:
                assert res.value == 4
            assert "[test_agent_name] Retrieved test: 4." in caplog.text
            assert specs._retries_attempted == 0

    def test_fetch_behaviour(
        self,
        block_from_timestamp_q: str,
        timestamp_gte: str,
        timestamp_lte: str,
        eth_price_usd_q: str,
        spooky_pairs_q: str,
        uni_pairs_q: str,
        pairs_ids: Dict[str, List[str]],
        pool_fields: Tuple[str, ...],
    ) -> None:
        """Run tests."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        total_iterations = 30
        # for every subgraph and every iteration that will be performed, we test fetching a single batch
        for subgraph_name in ("uniswap_subgraph", "spooky_subgraph"):
            for _ in range(total_iterations):
                behaviour.act_wrapper()
                subgraph = getattr(behaviour.context, subgraph_name)
                request_kwargs: Dict[str, Union[str, bytes]] = dict(
                    method="POST",
                    url=subgraph.url,
                    headers="Content-Type: application/json\r\n",
                    version="",
                )
                response_kwargs = dict(
                    version="",
                    status_code=200,
                    status_text="",
                    headers="",
                )

                # block request.
                assert behaviour._progress.current_timestamp is not None
                block_query = block_from_timestamp_q.replace(
                    timestamp_gte, str(behaviour._progress.current_timestamp)
                )
                block_query = block_query.replace(
                    timestamp_lte,
                    str(behaviour._progress.current_timestamp + SAFE_BLOCK_TIME),
                )
                block_subgraph = getattr(
                    behaviour.context, subgraph.chain_subgraph_name
                )
                request_kwargs["url"] = block_subgraph.url
                request_kwargs["body"] = json.dumps({"query": block_query}).encode(
                    "utf-8"
                )
                res = {"data": {"blocks": [{"timestamp": "1", "number": "15178691"}]}}
                response_kwargs["body"] = json.dumps(res).encode("utf-8")
                self.mock_http_request(request_kwargs, response_kwargs)

                # ETH price request.
                request_kwargs["url"] = subgraph.url
                request_kwargs["body"] = json.dumps({"query": eth_price_usd_q}).encode(
                    "utf-8"
                )
                res = {"data": {"bundles": [{"ethPrice": "0.8973548"}]}}
                response_kwargs["body"] = json.dumps(res).encode("utf-8")
                behaviour.act_wrapper()
                self.mock_http_request(request_kwargs, response_kwargs)

                # top pairs data.
                pairs_q = (
                    uni_pairs_q
                    if subgraph_name == "uniswap_subgraph"
                    else spooky_pairs_q
                )
                request_kwargs["body"] = json.dumps({"query": pairs_q}).encode("utf-8")
                res = {
                    "data": {"pairs": [{field: "dummy_value" for field in pool_fields}]}
                }
                response_kwargs["body"] = json.dumps(res).encode("utf-8")
                behaviour.act_wrapper()
                self.mock_http_request(request_kwargs, response_kwargs)

        behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == TransformBehaviour.behaviour_id

    def test_invalid_pairs(self, caplog: LogCaptureFixture) -> None:
        """Test the behaviour when the given pairs do not exist at the subgraphs."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._progress.call_failed = True
        # set the retries to the max allowed for any subgraph (chose SpookySwap randomly)
        behaviour.context.spooky_subgraph._retries_attempted = (
            behaviour.context.spooky_subgraph._retries
        )

        behaviour.act_wrapper()
        # exceed the retries
        behaviour.context.spooky_subgraph.increment_retries()
        behaviour._pairs_exist = False
        behaviour._progress.call_failed = True

        # test empty retrieved history.
        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()
        assert "Could not download any historical data!" in caplog.text

        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == TransformBehaviour.behaviour_id

    @pytest.mark.parametrize("is_non_indexed_res", (True, False))
    def test_fetch_behaviour_non_indexed_block(
        self,
        is_non_indexed_res: bool,
        block_from_timestamp_q: str,
        block_from_number_q: str,
        eth_price_usd_q: str,
        uni_pairs_q: str,
        pairs_ids: Dict[str, List[str]],
        pool_fields: Tuple[str, ...],
        caplog: LogCaptureFixture,
    ) -> None:
        """Run tests for fetch behaviour when a block has not been indexed yet."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        request_kwargs: Dict[str, Union[str, bytes]] = dict(
            method="POST",
            url=behaviour.context.uniswap_subgraph.url,
            headers="Content-Type: application/json\r\n",
            version="",
        )
        response_kwargs = dict(
            version="",
            status_code=200,
            status_text="",
            headers="",
        )

        # block request.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_timestamp_q}).encode(
            "utf-8"
        )
        res: Dict[str, Union[List[Dict[str, str]], Dict[str, List[Dict[str, str]]]]] = {
            "data": {"blocks": [{"timestamp": "1", "number": "15178691"}]}
        }
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # ETH price request for non-indexed block.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps({"query": eth_price_usd_q}).encode("utf-8")

        if is_non_indexed_res:
            res = {
                "errors": [
                    {
                        "message": "Failed to decode `block.number` value: `subgraph "
                        "QmPJbGjktGa7c4UYWXvDRajPxpuJBSZxeQK5siNT3VpthP has only indexed up to block number 3730367 "
                        "and data for block number 15178691 is therefore not yet available`"
                    }
                ]
            }
        else:
            res = {"errors": [{"message": "another error"}]}

        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        if not is_non_indexed_res:
            with caplog.at_level(
                logging.WARNING,
                logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
            ):
                behaviour.act_wrapper()

            assert (
                "Attempted to handle an indexing error, but could not extract the latest indexed block!"
                in caplog.text
            )
            return

        # indexed block request.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_number_q}).encode(
            "utf-8"
        )
        res = {"data": {"blocks": [{"timestamp": "1", "number": "3730360"}]}}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # ETH price request for indexed block.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps(
            {"query": eth_price_usd_q.replace("15178691", "3730360")}
        ).encode("utf-8")
        res = {"data": {"bundles": [{"ethPrice": "0.8973548"}]}}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # top pairs data.
        request_kwargs["body"] = json.dumps(
            {"query": uni_pairs_q.replace("15178691", "3730360")}
        ).encode("utf-8")
        res = {
            "data": {
                "pairs": [
                    {field: dummy_value for field in pool_fields}
                    for dummy_value in ("dum1", "dum2")
                ]
            }
        }
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == TransformBehaviour.behaviour_id

    @pytest.mark.parametrize("none_at_step", (0, 1, 2))
    def test_fetch_behaviour_non_indexed_block_none_res(
        self,
        none_at_step: int,
        block_from_timestamp_q: str,
        block_from_number_q: str,
        eth_price_usd_q: str,
        uni_pairs_q: str,
        pairs_ids: Dict[str, List[str]],
        pool_fields: Tuple[str, ...],
        caplog: LogCaptureFixture,
    ) -> None:
        """Test `async_act` when we receive `None` responses in `_check_non_indexed_block`."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        behaviour.params.sleep_time = SLEEP_TIME_TWEAK
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        request_kwargs: Dict[str, Union[str, bytes]] = dict(
            method="POST",
            url=behaviour.context.uniswap_subgraph.url,
            headers="Content-Type: application/json\r\n",
            version="",
        )
        response_kwargs = dict(
            version="",
            status_code=200,
            status_text="",
            headers="",
        )

        # block request.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_timestamp_q}).encode(
            "utf-8"
        )
        res: Dict[str, Union[List[Dict[str, str]], Dict[str, List[Dict[str, str]]]]] = {
            "data": {"blocks": [{"timestamp": "1", "number": "15178691"}]}
        }
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # ETH price request for non-indexed block.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps({"query": eth_price_usd_q}).encode("utf-8")

        res = (
            {
                "errors": [
                    {
                        "message": "Failed to decode `block.number` value: `subgraph "
                        "QmPJbGjktGa7c4UYWXvDRajPxpuJBSZxeQK5siNT3VpthP has only indexed up to block number 3730367 "
                        "and data for block number 15178691 is therefore not yet available`"
                    }
                ]
            }
            if none_at_step
            else {}
        )

        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        if none_at_step == 0:
            time.sleep(SLEEP_TIME_TWEAK + 0.01)
            behaviour.act_wrapper()
            return

        # indexed block request.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_number_q}).encode(
            "utf-8"
        )
        res = (
            {"data": {"blocks": [{"timestamp": "1", "number": "3730360"}]}}
            if none_at_step != 1
            else {}
        )
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        if none_at_step == 1:
            time.sleep(SLEEP_TIME_TWEAK + 0.01)
            behaviour.act_wrapper()
            return

        # ETH price request for indexed block.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps(
            {"query": eth_price_usd_q.replace("15178691", "3730360")}
        ).encode("utf-8")
        res = {}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        behaviour.act_wrapper()

    def test_fetch_behaviour_retries_exceeded(self, caplog: LogCaptureFixture) -> None:
        """Run tests for exceeded retries."""
        self.skill.skill_context.state.round_sequence.abci_app._last_timestamp = (
            datetime.now()
        )

        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        assert behaviour is not None
        assert behaviour.behaviour_id == FetchBehaviour.behaviour_id
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        retries = None
        for subgraph_name in (
            "spooky_subgraph",
            "fantom_subgraph",
            "uniswap_subgraph",
            "ethereum_subgraph",
        ):
            subgraph = getattr(behaviour.context, subgraph_name)
            subgraph_retries = subgraph._retries

            if retries is None:
                retries = subgraph_retries
            else:
                assert retries == subgraph_retries

        assert retries is not None
        for i in range(retries + 1):
            for subgraph_name in (
                "spooky_subgraph",
                "fantom_subgraph",
                "uniswap_subgraph",
                "ethereum_subgraph",
            ):
                subgraph = getattr(behaviour.context, subgraph_name)
                subgraph.increment_retries()

                if i == retries:
                    assert subgraph.is_retries_exceeded()

        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()

        assert (
            "Retries were exceeded while downloading the historical data!"
            in caplog.text
        )
        for subgraph_name in (
            "spooky_subgraph",
            "fantom_subgraph",
            "uniswap_subgraph",
            "ethereum_subgraph",
        ):
            subgraph = getattr(behaviour.context, subgraph_name)
            assert not subgraph.is_retries_exceeded()

        assert behaviour._hist_hash == ""

        self.mock_a2a_transaction()
        self._test_done_flag_set()

    def test_fetch_value_none(
        self,
        caplog: LogCaptureFixture,
        block_from_timestamp_q: str,
        eth_price_usd_q: str,
        uni_pairs_q: str,
        pairs_ids: Dict[str, List[str]],
        pool_fields: Tuple[str, ...],
    ) -> None:
        """Test when fetched value is none."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        behaviour.params.pair_ids = pairs_ids
        behaviour.params.sleep_time = SLEEP_TIME_TWEAK
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        request_kwargs: Dict[str, Union[str, bytes]] = dict(
            method="POST",
            url=behaviour.context.spooky_subgraph.url,
            headers="Content-Type: application/json\r\n",
            version="",
            body=b"",
        )
        response_kwargs = dict(
            version="",
            status_code=200,
            status_text="",
            headers="",
            body=b"",
        )

        # block request with None response.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_timestamp_q}).encode(
            "utf-8"
        )
        response_kwargs["body"] = b""

        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()
            self.mock_http_request(request_kwargs, response_kwargs)
        assert "[test_agent_name] Could not get block from eth" in caplog.text

        caplog.clear()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        behaviour.act_wrapper()

        # block request.
        request_kwargs["body"] = json.dumps({"query": block_from_timestamp_q}).encode(
            "utf-8"
        )
        res = {"data": {"blocks": [{"timestamp": "1", "number": "15178691"}]}}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # ETH price request with None response.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps({"query": eth_price_usd_q}).encode("utf-8")
        response_kwargs["body"] = b""

        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()
            self.mock_http_request(request_kwargs, response_kwargs)
        assert (
            "[test_agent_name] Could not get ETH price for block "
            "{'timestamp': '1', 'number': '15178691'} from uniswap" in caplog.text
        )

        caplog.clear()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        behaviour.act_wrapper()

        # block request.
        request_kwargs["url"] = behaviour.context.ethereum_subgraph.url
        request_kwargs["body"] = json.dumps({"query": block_from_timestamp_q}).encode(
            "utf-8"
        )
        res = {"data": {"blocks": [{"timestamp": "1", "number": "15178691"}]}}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # ETH price request.
        request_kwargs["url"] = behaviour.context.uniswap_subgraph.url
        request_kwargs["body"] = json.dumps({"query": eth_price_usd_q}).encode("utf-8")
        res = {"data": {"bundles": [{"ethPrice": "0.8973548"}]}}
        response_kwargs["body"] = json.dumps(res).encode("utf-8")
        behaviour.act_wrapper()
        self.mock_http_request(request_kwargs, response_kwargs)

        # top pairs data with None response.
        request_kwargs["body"] = json.dumps({"query": uni_pairs_q}).encode("utf-8")
        response_kwargs["body"] = b""

        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()
            self.mock_http_request(request_kwargs, response_kwargs)
        assert (
            "[test_agent_name] Could not get pool data for block {'timestamp': '1', 'number': '15178691'} "
            "from uniswap" in caplog.text
        )

        caplog.clear()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        behaviour.act_wrapper()

    @pytest.mark.parametrize("target_per_pool", (0, 3))
    def test_fetch_behaviour_stop_iteration(
        self,
        tmp_path: PosixPath,
        caplog: LogCaptureFixture,
        no_action: Callable[[Any], None],
        target_per_pool: int,
    ) -> None:
        """Test `FetchBehaviour`'s `async_act` after all the timestamps have been generated."""
        self.skill.skill_context.state.round_sequence.abci_app._last_timestamp = (
            datetime.now()
        )

        # fast-forward to fetch behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        # set history end to a negative value in order to raise a `StopIteration`.
        behaviour.params.end = -1
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        # test empty retrieved history.
        with caplog.at_level(
            logging.ERROR,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            behaviour.act_wrapper()
        assert "Could not download any historical data!" in caplog.text
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        # fast-forward to fetch behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )

        # test with partly fetched history and valid save path.
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)
        # we do this because of https://github.com/valory-xyz/open-autonomy/pull/646
        behaviour._check_given_pairs = mock.MagicMock()  # type: ignore
        behaviour._pairs_exist = True

        behaviour._pairs_hist = [{"pool1": "test"}, {"pool2": "test"}]
        behaviour._target_per_pool = target_per_pool
        behaviour.context._agent_context._data_dir = tmp_path  # type: ignore
        behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == TransformBehaviour.behaviour_id

        # fast-forward to fetch behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )

    def test_clean_up(
        self,
    ) -> None:
        """Test clean-up."""
        self.fast_forward_to_behaviour(
            self.behaviour, FetchBehaviour.behaviour_id, self.synchronized_data
        )
        assert self.behaviour.current_behaviour is not None
        behaviour = cast(FetchBehaviour, self.behaviour.current_behaviour)

        for subgraph_name in (
            "spooky_subgraph",
            "fantom_subgraph",
            "uniswap_subgraph",
            "ethereum_subgraph",
        ):
            subgraph = getattr(behaviour.context, subgraph_name)
            subgraph._retries_attempted = 1

        for subgraph_name in (
            "spooky_subgraph",
            "fantom_subgraph",
            "uniswap_subgraph",
            "ethereum_subgraph",
        ):
            subgraph = getattr(behaviour.context, subgraph_name)
            self.behaviour.current_behaviour.clean_up()
            assert subgraph._retries_attempted == 0


class TestTransformBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test TransformBehaviour."""

    behaviour_class = TransformBehaviour
    next_behaviour_class = PreprocessBehaviour

    def _fast_forward(self, tmp_path: PosixPath, ipfs_succeed: bool = True) -> None:
        """Setup `TestTransformBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Send historical data to IPFS and get the hash.
        if ipfs_succeed:
            hash_ = cast(BaseBehaviour, self.behaviour.current_behaviour).send_to_ipfs(
                os.path.join(
                    tmp_path,
                    f"historical_data_period_{self.synchronized_data.period_count}.json",
                ),
                {"test": "test"},
                filetype=SupportedFiletype.JSON,
            )
        else:
            hash_ = "test"

        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=dict(
                        most_voted_randomness=[0], most_voted_history=[hash_]
                    ),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    def test_setup(self, tmp_path: PosixPath) -> None:
        """Test behaviour setup."""
        self._fast_forward(tmp_path)
        self.behaviour.context.task_manager.start()
        cast(TransformBehaviour, self.behaviour.current_behaviour).setup()

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        caplog: LogCaptureFixture,
        tmp_path: PosixPath,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
    ) -> None:
        """Run test for `transform_behaviour` when task result is not ready."""
        self._fast_forward(tmp_path)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(
                transformed_historical_data_no_datetime_conversion, ready=False
            ),
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            self.behaviour.context.task_manager.start()

            cast(
                TransformBehaviour, self.behaviour.current_behaviour
            ).params.sleep_time = SLEEP_TIME_TWEAK
            self.behaviour.act_wrapper()

            # Sleep to wait for the behaviour that is also sleeping.
            time.sleep(SLEEP_TIME_TWEAK + 0.01)

            # Continue the `async_act` after the sleep of the Behaviour.
            self.behaviour.act_wrapper()

        assert "[test_agent_name] Entered in the 'transform' behaviour" in caplog.text

        assert (
            "[test_agent_name] The transform task is not finished yet." in caplog.text
        )

        self.end_round()

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_transform_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `transform_behaviour`."""
        self._fast_forward(tmp_path, ipfs_succeed)

        monkeypatch.setattr(
            "packages.valory.skills.apy_estimation_abci.tasks.transform_hist_data",
            lambda _: transformed_historical_data_no_datetime_conversion,
        )
        monkeypatch.setattr(
            self._skill._skill_context._agent_context._task_manager,  # type: ignore
            "get_task_result",
            lambda *_: DummyAsyncResult(
                transformed_historical_data_no_datetime_conversion
            ),
        )
        monkeypatch.setattr(
            self._skill._skill_context._agent_context._task_manager,  # type: ignore
            "enqueue_task",
            lambda *_, **__: 3,
        )

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestPreprocessBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test PreprocessBehaviour."""

    behaviour_class = PreprocessBehaviour
    next_behaviour_class = RandomnessBehaviour

    def _fast_forward(
        self,
        data_found: bool,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
    ) -> pd.DataFrame:
        """Fast-forward to behaviour."""
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore
        if data_found:
            monkeypatch.setattr(
                IPFSBehaviour, "get_from_ipfs", lambda *_, **__: {"test": "test"}
            )
        # Increase the amount of dummy data for the train-test split,
        # as many times as the threshold in `group_and_filter_pair_data`.
        transformed_historical_data = pd.DataFrame(
            np.repeat(
                transformed_historical_data_no_datetime_conversion.values, 5, axis=0
            ),
            columns=transformed_historical_data_no_datetime_conversion.columns,
        )

        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(AbciAppDB(setup_data=dict(most_voted_transform=["test"]))),
        )
        behaviour = cast(PreprocessBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.behaviour_class.behaviour_id

        behaviour.params.sleep_time = SLEEP_TIME_TWEAK

        return transformed_historical_data

    @pytest.mark.parametrize(
        "data_found, task_ready", ((True, True), (True, False), (False, False))
    )
    def test_preprocess_behaviour(
        self,
        data_found: bool,
        task_ready: bool,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
    ) -> None:
        """Run test for `preprocess_behaviour`."""
        transformed_historical_data = self._fast_forward(
            data_found,
            transformed_historical_data_no_datetime_conversion,
            monkeypatch,
            tmp_path,
        )

        # Convert the `blockTimestamp` to a pandas datetime.
        transformed_historical_data["blockTimestamp"] = pd.to_datetime(
            transformed_historical_data["blockTimestamp"], unit="s"
        )
        monkeypatch.setattr(
            self._skill._skill_context._agent_context._task_manager,  # type: ignore
            "get_task_result",
            lambda *_: DummyAsyncResult(
                prepare_pair_data(transformed_historical_data), task_ready
            ),
        )
        monkeypatch.setattr(
            self._skill._skill_context._agent_context._task_manager,  # type: ignore
            "enqueue_task",
            lambda *_, **__: 3,
        )

        self.behaviour.act_wrapper()

        if data_found:
            assert (
                cast(PreprocessBehaviour, self.behaviour.current_behaviour)._pairs_hist
                is not None
            ), "Pairs history could not be loaded!"

        if task_ready:
            self.mock_a2a_transaction()
            self._test_done_flag_set()
            self.end_round()
            behaviour = cast(PreprocessBehaviour, self.behaviour.current_behaviour)
            assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id

        else:
            self.behaviour.act_wrapper()
            time.sleep(SLEEP_TIME_TWEAK + 0.01)
            self.behaviour.act_wrapper()
            behaviour = cast(PreprocessBehaviour, self.behaviour.current_behaviour)
            assert behaviour.behaviour_id == self.behaviour_class.behaviour_id


class TestPrepareBatchBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test `PrepareBatchBehaviour`."""

    behaviour_class = PrepareBatchBehaviour
    next_behaviour_class = UpdateForecasterBehaviour

    def _fast_forward(
        self,
        tmp_path: PosixPath,
        transformed_historical_data: pd.DataFrame,
        batch: ResponseItemType,
        ipfs_succeed: bool = True,
    ) -> None:
        """Setup `PrepareBatchBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore
        current_behaviour = cast(
            APYEstimationBaseBehaviour, self.behaviour.current_behaviour
        )

        # Create a dictionary with all the dummy data to send to IPFS.
        data_to_send = {
            "hist": {
                "filepath": os.path.join(
                    tmp_path,
                    f"latest_observations_period_{self.synchronized_data.period_count - 1}.csv",
                ),
                "obj": transformed_historical_data.iloc[[0, 2]].reset_index(drop=True),
                "filetype": SupportedFiletype.CSV,
            },
            "batch": {
                "filepath": os.path.join(
                    tmp_path,
                    f"historical_data_batch_{current_behaviour.params.end}"
                    f"_period_{self.synchronized_data.period_count}.json",
                ),
                "obj": batch,
                "filetype": SupportedFiletype.JSON,
            },
        }

        # Send dummy data to IPFS and get the hashes.
        if ipfs_succeed:
            hashes = {}
            for item_name, item_args in data_to_send.items():
                hashes[item_name] = cast(
                    BaseBehaviour, self.behaviour.current_behaviour
                ).send_to_ipfs(**item_args)
        else:
            hashes = {item_name: "test" for item_name, _ in data_to_send.items()}

        # fast-forward to the `PrepareBatchBehaviour` behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=AbciAppDB.data_to_lists(
                        dict(
                            latest_observation_hist_hash=hashes["hist"],
                            most_voted_batch=hashes["batch"],
                        )
                    ),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    def test_prepare_batch_behaviour_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        batch: ResponseItemType,
        prepare_batch_task_result: Dict[str, pd.DataFrame],
    ) -> None:
        """Test behaviour setup."""
        self._fast_forward(
            tmp_path, transformed_historical_data_no_datetime_conversion, batch
        )

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(prepare_batch_task_result),
        )

        current_behaviour = cast(
            PrepareBatchBehaviour, self.behaviour.current_behaviour
        )
        current_behaviour.setup()
        assert not any(batch is None for batch in current_behaviour._batches)

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        batch: ResponseItemType,
        prepare_batch_task_result: Dict[str, pd.DataFrame],
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(
            tmp_path,
            transformed_historical_data_no_datetime_conversion,
            batch,
        )

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(prepare_batch_task_result, ready=False),
        )

        cast(
            PrepareBatchBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_prepare_batch_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        transformed_historical_data_no_datetime_conversion: pd.DataFrame,
        batch: ResponseItemType,
        prepare_batch_task_result: Dict[str, pd.DataFrame],
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `prepare_behaviour`."""
        self._fast_forward(
            tmp_path,
            transformed_historical_data_no_datetime_conversion,
            batch,
            ipfs_succeed,
        )

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(prepare_batch_task_result),
        )

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.behaviour_class.behaviour_id

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestRandomnessBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test RandomnessBehaviour."""

    randomness_behaviour_class = RandomnessBehaviour  # type: ignore
    next_behaviour_class = OptimizeBehaviour

    drand_response = {
        "round": 1416669,
        "randomness": "f6be4bf1fa229f22340c1a5b258f809ac4af558200775a67dacb05f0cb258a11",
        "signature": (
            "b44d00516f46da3a503f9559a634869b6dc2e5d839e46ec61a090e3032172954929a5"
            "d9bd7197d7739fe55db770543c71182562bd0ad20922eb4fe6b8a1062ed21df3b68de"
            "44694eb4f20b35262fa9d63aa80ad3f6172dd4d33a663f21179604"
        ),
        "previous_signature": (
            "903c60a4b937a804001032499a855025573040cb86017c38e2b1c3725286756ce8f33"
            "61188789c17336beaf3f9dbf84b0ad3c86add187987a9a0685bc5a303e37b008fba8c"
            "44f02a416480dd117a3ff8b8075b1b7362c58af195573623187463"
        ),
    }

    def test_randomness_behaviour(
        self,
    ) -> None:
        """Test RandomnessBehaviour."""

        self.fast_forward_to_behaviour(
            self.behaviour,
            self.randomness_behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        assert (
            cast(
                BaseBehaviour,
                cast(BaseBehaviour, self.behaviour.current_behaviour),
            ).behaviour_id
            == self.randomness_behaviour_class.behaviour_id
        )
        self.behaviour.act_wrapper()
        self.mock_http_request(
            request_kwargs=dict(
                method="GET",
                headers="",
                version="",
                body=b"",
                url="https://drand.cloudflare.com/public/latest",
            ),
            response_kwargs=dict(
                version="",
                status_code=200,
                status_text="",
                headers="",
                body=json.dumps(self.drand_response).encode("utf-8"),
            ),
        )

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id

    def test_invalid_drand_value(
        self,
    ) -> None:
        """Test invalid drand values."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.randomness_behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        assert (
            cast(
                BaseBehaviour,
                cast(BaseBehaviour, self.behaviour.current_behaviour),
            ).behaviour_id
            == self.randomness_behaviour_class.behaviour_id
        )
        self.behaviour.act_wrapper()

        drand_invalid = self.drand_response.copy()
        drand_invalid["randomness"] = binascii.hexlify(b"randomness_hex").decode()
        self.mock_http_request(
            request_kwargs=dict(
                method="GET",
                headers="",
                version="",
                body=b"",
                url="https://drand.cloudflare.com/public/latest",
            ),
            response_kwargs=dict(
                version="",
                status_code=200,
                status_text="",
                headers="",
                body=json.dumps(drand_invalid).encode(),
            ),
        )

    def test_invalid_response(
        self,
    ) -> None:
        """Test invalid json response."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.randomness_behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        assert (
            cast(
                BaseBehaviour,
                cast(BaseBehaviour, self.behaviour.current_behaviour),
            ).behaviour_id
            == self.randomness_behaviour_class.behaviour_id
        )
        cast(
            RandomnessBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK

        self.behaviour.act_wrapper()

        self.mock_http_request(
            request_kwargs=dict(
                method="GET",
                headers="",
                version="",
                body=b"",
                url="https://drand.cloudflare.com/public/latest",
            ),
            response_kwargs=dict(
                version="", status_code=200, status_text="", headers="", body=b""
            ),
        )
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

    def test_max_retries_reached(
        self,
    ) -> None:
        """Test with max retries reached."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.randomness_behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        assert (
            cast(
                BaseBehaviour,
                cast(BaseBehaviour, self.behaviour.current_behaviour),
            ).behaviour_id
            == self.randomness_behaviour_class.behaviour_id
        )
        with mock.patch.object(
            self.behaviour.context.randomness_api,
            "is_retries_exceeded",
            return_value=True,
        ):
            self.behaviour.act_wrapper()
            behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
            assert (
                behaviour.behaviour_id == self.randomness_behaviour_class.behaviour_id
            )
            self._test_done_flag_set()

    def test_clean_up(
        self,
    ) -> None:
        """Test when `observed` value is none."""
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.randomness_behaviour_class.behaviour_id,
            self.synchronized_data,
        )
        assert (
            cast(
                BaseBehaviour,
                cast(BaseBehaviour, self.behaviour.current_behaviour),
            ).behaviour_id
            == self.randomness_behaviour_class.behaviour_id
        )
        self.behaviour.context.randomness_api._retries_attempted = 1
        assert self.behaviour.current_behaviour is not None
        self.behaviour.current_behaviour.clean_up()
        assert self.behaviour.context.randomness_api._retries_attempted == 0


class TestOptimizeBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test OptimizeBehaviour."""

    behaviour_class = OptimizeBehaviour
    next_behaviour_class = TrainBehaviour

    def _fast_forward(
        self,
        tmp_path: PosixPath,
        ipfs_succeed: bool = True,
    ) -> None:
        """Setup `OptimizeBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Create a dictionary with all the dummy data to send to IPFS.
        data_to_send = {}
        for split in ("train", "test"):
            data_to_send[split] = {
                "filepath": os.path.join(
                    tmp_path,
                    f"y_{split}",
                    f"period_{self.synchronized_data.period_count}",
                ),
                "obj": {
                    f"{split}_{i}": pd.DataFrame([i for i in range(5)])
                    for i in range(3)
                },
                "multiple": True,
                "filetype": SupportedFiletype.CSV,
            }

        # Send dummy data to IPFS and get the hashes.
        if ipfs_succeed:
            hashes = {}
            for item_name, item_args in data_to_send.items():
                hashes[item_name] = cast(
                    BaseBehaviour, self.behaviour.current_behaviour
                ).send_to_ipfs(**item_args)
        else:
            hashes = {item_name: "non_existing" for item_name in data_to_send.keys()}

        # fast-forward to the `OptimizeBehaviour` behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=dict(
                        most_voted_randomness=[0],
                        most_voted_split=[hashes["train"] + hashes["test"]],
                    ),
                )
            ),
        )

        assert (
            cast(OptimizeBehaviour, self.behaviour.current_behaviour).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    def test_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        optimize_task_result_empty: optuna.Study,
    ) -> None:
        """Test behaviour setup."""
        self._fast_forward(tmp_path)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(optimize_task_result_empty),
        )
        current_behaviour = cast(OptimizeBehaviour, self.behaviour.current_behaviour)
        current_behaviour.setup()
        assert current_behaviour._y is not None

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        optimize_task_result_empty: optuna.Study,
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(tmp_path)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(optimize_task_result_empty, ready=False),
        )

        cast(
            OptimizeBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_optimize_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        caplog: LogCaptureFixture,
        optimize_task_result: PoolToHyperParamsWithStatusType,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `optimize_behaviour`."""
        self._fast_forward(tmp_path, ipfs_succeed)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 3)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(optimize_task_result),
        )

        with caplog.at_level(
            logging.WARNING,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            # note: this will capture the warning only if the first item in the `optimize_task_result` dict
            # is the one representing the non-finished trial, i.e., `False`.
            self.behaviour.act_wrapper()

        if ipfs_succeed:
            for _ in range(len(optimize_task_result)):
                self.behaviour.act_wrapper()
            assert (
                "The optimization could not be done for pool `test1`! "
                "Please make sure that there is a sufficient number of data for the optimization procedure. "
                "Parameters have been set randomly!"
            ) in caplog.text

        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestTrainBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test TrainBehaviour."""

    behaviour_class = TrainBehaviour
    next_behaviour_class = _TestBehaviour

    def _fast_forward(
        self,
        tmp_path: PosixPath,
        ipfs_succeed: bool = True,
        full_training: bool = False,
    ) -> None:
        """Setup `TestTrainBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Create a dictionary with all the dummy data to send to IPFS.
        data_to_send = {
            "params": {
                "filepath": os.path.join(
                    tmp_path,
                    "best_params",
                    f"period_{self.synchronized_data.period_count}",
                ),
                "obj": {
                    "pool1.json": {"p": 1, "q": 1, "d": 1, "m": 1},
                    "pool2.json": {"p": 2, "q": 2, "d": 1, "m": 1},
                },
                "multiple": True,
                "filetype": SupportedFiletype.JSON,
            }
        }
        for split in ("train", "test"):
            data_to_send[split] = {
                "filepath": os.path.join(
                    tmp_path,
                    f"y_{split}",
                    f"period_{self.synchronized_data.period_count}",
                ),
                "obj": {
                    f"pool{i}.csv": pd.DataFrame([i for i in range(5)])
                    for i in range(3)
                },
                "multiple": True,
                "filetype": SupportedFiletype.CSV,
            }

        # Send dummy data to IPFS and get the hashes.
        if ipfs_succeed:
            hashes = {}
            for item_name, item_args in data_to_send.items():
                hashes[item_name] = cast(
                    BaseBehaviour, self.behaviour.current_behaviour
                ).send_to_ipfs(**item_args)
        else:
            hashes = {item_name: "non_existing" for item_name in data_to_send.keys()}

        # fast-forward to the `TrainBehaviour` behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=AbciAppDB.data_to_lists(
                        dict(
                            full_training=full_training,
                            most_voted_params=hashes["params"],
                            most_voted_split=hashes["train"] + hashes["test"],
                        )
                    ),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize(
        "full_training, ipfs_succeed", product((True, False), repeat=2)
    )
    def test_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        no_action: Callable[[Any], None],
        ipfs_succeed: bool,
        full_training: bool,
    ) -> None:
        """Test behaviour setup."""
        self._fast_forward(tmp_path, ipfs_succeed, full_training)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(TaskManager, "get_task_result", no_action)

        current_behaviour = cast(TrainBehaviour, self.behaviour.current_behaviour)
        current_behaviour.setup()
        if ipfs_succeed:
            assert not any(
                arg is None
                for arg in (current_behaviour._y, current_behaviour._best_params)
            )
        else:
            assert all(
                arg is None
                for arg in (current_behaviour._y, current_behaviour._best_params)
            )

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(tmp_path)

        self.behaviour.context.task_manager.start()

        monkeypatch.setattr(AsyncResult, "ready", lambda *_: False)
        cast(
            TrainBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_train_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        train_task_result: PoolIdToForecasterType,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `train_behaviour`."""
        self._fast_forward(tmp_path, ipfs_succeed)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 3)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(train_task_result),
        )

        # act.
        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestTestBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test TestBehaviour."""

    behaviour_class = _TestBehaviour
    next_behaviour_class = TrainBehaviour

    def _fast_forward(
        self,
        tmp_path: PosixPath,
        ipfs_succeed: bool = True,
    ) -> None:
        """Setup `TestTrainBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Create a dictionary with all the dummy data to send to IPFS.
        data_to_send = {
            "model": {
                "filepath": os.path.join(
                    tmp_path,
                    "forecasters",
                    f"period_{self.synchronized_data.period_count}",
                ),
                "obj": {f"pool{i}.joblib": DummyPipeline() for i in range(3)},
                "multiple": True,
                "filetype": SupportedFiletype.PM_PIPELINE,
            }
        }
        for split in ("train", "test"):
            data_to_send[split] = {
                "filepath": os.path.join(
                    tmp_path,
                    f"y_{split}",
                    f"period_{self.synchronized_data.period_count}",
                ),
                "obj": {
                    f"pool{i}.csv": pd.DataFrame([i for i in range(5)])
                    for i in range(3)
                },
                "multiple": True,
                "filetype": SupportedFiletype.CSV,
            }

        # Send dummy data to IPFS and get the hashes.
        if ipfs_succeed:
            hashes = {}
            for item_name, item_args in data_to_send.items():
                hashes[item_name] = cast(
                    BaseBehaviour, self.behaviour.current_behaviour
                ).send_to_ipfs(**item_args)
        else:
            hashes = {item_name: "non_existing" for item_name in data_to_send.keys()}

        # fast-forward to the `TestBehaviour` behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=dict(
                        most_voted_models=[hashes["model"]],
                        most_voted_split=[hashes["train"] + hashes["test"]],
                    ),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        ipfs_succeed: bool,
        no_action: Callable[[Any], None],
    ) -> None:
        """Test behaviour setup."""
        self._fast_forward(tmp_path, ipfs_succeed)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(TaskManager, "get_task_result", no_action)

        current_behaviour = cast(_TestBehaviour, self.behaviour.current_behaviour)
        current_behaviour.setup()

        is_none = (
            arg is None
            for arg in (
                getattr(current_behaviour, arg_name)
                for arg_name in ("_y_train", "_y_test", "_forecasters")
            )
        )
        if ipfs_succeed:
            assert not any(is_none)
        else:
            assert all(is_none)

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(tmp_path)

        self.behaviour.context.task_manager.start()
        monkeypatch.setattr(AsyncResult, "ready", lambda *_: False)
        cast(
            _TestBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_test_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        ipfs_succeed: bool,
        _test_task_result: PoolIdToTestReportType,
    ) -> None:
        """Run test for `test_behaviour`."""
        self._fast_forward(tmp_path, ipfs_succeed)

        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 3)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(_test_task_result),
        )

        # test act.
        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestUpdateForecasterBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test `UpdateForecasterBehaviour`."""

    behaviour_class = UpdateForecasterBehaviour
    next_behaviour_class = EstimateBehaviour

    def _fast_forward(
        self,
        tmp_path: PosixPath,
        prepare_batch_task_result: pd.DataFrame,
        ipfs_succeed: bool = True,
    ) -> None:
        """Setup `TestUpdateForecasterBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Create a dictionary with all the dummy data to send to IPFS.
        data_to_send = {
            "model": {
                "filepath": os.path.join(
                    tmp_path,
                    "fully_trained_forecasters",
                    f"period_{self.synchronized_data.period_count - 1}",
                ),
                "obj": {f"pool{i}.joblib": DummyPipeline() for i in range(3)},
                "multiple": True,
                "filetype": SupportedFiletype.PM_PIPELINE,
            },
            "observation": {
                "filepath": os.path.join(
                    tmp_path,
                    f"latest_observations_period_{self.synchronized_data.period_count}.csv",
                ),
                "obj": prepare_batch_task_result,
                "filetype": SupportedFiletype.CSV,
            },
        }

        # Send dummy data to IPFS and get the hashes.
        if ipfs_succeed:
            hashes = {}
            for item_name, item_args in data_to_send.items():
                hashes[item_name] = cast(
                    BaseBehaviour, self.behaviour.current_behaviour
                ).send_to_ipfs(**item_args)
        else:
            hashes = {
                item_name: "non_existing" for item_name, _ in data_to_send.items()
            }

        # fast-forward to the `TestBehaviour` behaviour.
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=dict(
                        most_voted_models=[hashes["model"]],
                        latest_observation_hist_hash=[hashes["observation"]],
                    ),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_update_forecaster_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        no_action: Callable[[Any], None],
        prepare_batch_task_result: pd.DataFrame,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `UpdateForecasterBehaviour`'s setup method."""
        self._fast_forward(tmp_path, prepare_batch_task_result, ipfs_succeed)
        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(TaskManager, "get_task_result", no_action)

        current_behaviour = cast(
            UpdateForecasterBehaviour, self.behaviour.current_behaviour
        )
        current_behaviour.setup()

        is_none = (
            arg is None
            for arg in (
                getattr(current_behaviour, arg_name)
                for arg_name in ("_y", "_forecasters")
            )
        )
        if ipfs_succeed:
            assert not any(is_none)
        else:
            assert all(is_none)

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        prepare_batch_task_result: pd.DataFrame,
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(tmp_path, prepare_batch_task_result)

        self.behaviour.context.task_manager.start()

        monkeypatch.setattr(AsyncResult, "ready", lambda *_: False)
        cast(
            TrainBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_update_forecaster_behaviour(
        self,
        tmp_path: PosixPath,
        monkeypatch: MonkeyPatch,
        prepare_batch_task_result: pd.DataFrame,
        train_task_result: PoolIdToForecasterType,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `UpdateForecasterBehaviour`."""
        self._fast_forward(tmp_path, prepare_batch_task_result, ipfs_succeed)
        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 3)
        monkeypatch.setattr(
            TaskManager,
            "get_task_result",
            lambda *_: DummyAsyncResult(train_task_result),
        )

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(UpdateForecasterBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestEstimateBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test EstimateBehaviour."""

    behaviour_class = EstimateBehaviour
    next_behaviour_class = FreshModelResetBehaviour

    def _fast_forward(self, tmp_path: PosixPath, ipfs_succeed: bool = True) -> None:
        """Setup `TestTransformBehaviour`."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Send dummy forecasters to IPFS and get the hash.
        if ipfs_succeed:
            hash_ = cast(BaseBehaviour, self.behaviour.current_behaviour).send_to_ipfs(
                os.path.join(
                    tmp_path,
                    "fully_trained_forecasters",
                    f"period_{self.synchronized_data.period_count}",
                ),
                {f"pool{i}.joblib": DummyPipeline() for i in range(3)},
                multiple=True,
                filetype=SupportedFiletype.PM_PIPELINE,
            )
        else:
            hash_ = "non_existing"

        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(
                AbciAppDB(
                    setup_data=dict(most_voted_models=[hash_]),
                )
            ),
        )

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_estimate_setup(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        no_action: Callable[[Any], None],
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `EstimateBehaviour`'s setup method."""
        self._fast_forward(tmp_path, ipfs_succeed)
        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(TaskManager, "get_task_result", no_action)

        current_behaviour = cast(
            UpdateForecasterBehaviour, self.behaviour.current_behaviour
        )
        current_behaviour.setup()

        if ipfs_succeed:
            assert current_behaviour._forecasters is not None
        else:
            assert current_behaviour._forecasters is None

    def test_task_not_ready(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
    ) -> None:
        """Run test for behaviour when task result is not ready."""
        self._fast_forward(tmp_path)

        self.behaviour.context.task_manager.start()

        monkeypatch.setattr(AsyncResult, "ready", lambda *_: False)
        cast(
            TrainBehaviour, self.behaviour.current_behaviour
        ).params.sleep_time = SLEEP_TIME_TWEAK
        self.behaviour.act_wrapper()
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        assert (
            cast(
                APYEstimationBaseBehaviour, self.behaviour.current_behaviour
            ).behaviour_id
            == self.behaviour_class.behaviour_id
        )

    @pytest.mark.parametrize("ipfs_succeed", (True, False))
    def test_estimate_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        ipfs_succeed: bool,
    ) -> None:
        """Run test for `EstimateBehaviour`."""
        self._fast_forward(tmp_path, ipfs_succeed)
        monkeypatch.setattr(TaskManager, "enqueue_task", lambda *_, **__: 0)
        monkeypatch.setattr(
            TaskManager, "get_task_result", lambda *_: DummyAsyncResult(pd.DataFrame())
        )

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestCycleResetBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test CycleResetBehaviour."""

    behaviour_class = CycleResetBehaviour
    next_behaviour_class = FetchBatchBehaviour

    @pytest.mark.parametrize(
        "ipfs_succeed, log_level, log_message",
        (
            (True, logging.INFO, "Finalized estimates:"),
            (
                False,
                logging.ERROR,
                "There was an error while trying to fetch and load the estimations from IPFS!",
            ),
        ),
    )
    def test_reset_behaviour(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: PosixPath,
        caplog: LogCaptureFixture,
        no_action: Callable[[Any], None],
        ipfs_succeed: bool,
        log_level: int,
        log_message: str,
    ) -> None:
        """Test reset behaviour."""
        # Set data directory to a temporary path for tests.
        self.behaviour.context._agent_context._data_dir = tmp_path  # type: ignore

        # Send dummy forecasters to IPFS and get the hash.
        if ipfs_succeed:
            hash_ = cast(BaseBehaviour, self.behaviour.current_behaviour).send_to_ipfs(
                os.path.join(
                    tmp_path,
                    f"estimations_period_{self.synchronized_data.period_count}.csv",
                ),
                pd.DataFrame({"pool1": [1.435, 4.234], "pool2": [3.45, 23.64]}),
                filetype=SupportedFiletype.CSV,
            )
        else:
            hash_ = "non_existing"

        self.fast_forward_to_behaviour(
            behaviour=self.behaviour,
            behaviour_id=self.behaviour_class.behaviour_id,
            synchronized_data=SynchronizedData(
                AbciAppDB(setup_data=dict(most_voted_estimate=[hash_]))
            ),
        )
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.behaviour_class.behaviour_id

        monkeypatch.setattr(BenchmarkTool, "save", lambda _: no_action)
        monkeypatch.setattr(AbciApp, "last_timestamp", datetime.now())
        cast(
            CycleResetBehaviour, self.behaviour.current_behaviour
        ).params.observation_interval = SLEEP_TIME_TWEAK
        with caplog.at_level(
            log_level,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            self.behaviour.act_wrapper()
        assert log_message in caplog.text
        time.sleep(SLEEP_TIME_TWEAK + 0.01)
        self.behaviour.act_wrapper()

        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id

    def test_reset_behaviour_without_most_voted_estimate(
        self,
        monkeypatch: MonkeyPatch,
        no_action: Callable[[Any], None],
        caplog: LogCaptureFixture,
    ) -> None:
        """Test reset behaviour without most voted estimate."""
        self.fast_forward_to_behaviour(
            behaviour=self.behaviour,
            behaviour_id=self.behaviour_class.behaviour_id,
            synchronized_data=SynchronizedData(
                AbciAppDB(setup_data=dict(most_voted_estimate=[None]))
            ),
        )
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.behaviour_class.behaviour_id

        monkeypatch.setattr(BenchmarkTool, "save", lambda _: no_action)
        monkeypatch.setattr(AbciApp, "last_timestamp", datetime.now())

        self.behaviour.context.params.observation_interval = 0.1

        with caplog.at_level(
            logging.INFO,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            self.behaviour.act_wrapper()
            cast(
                CycleResetBehaviour, self.behaviour.current_behaviour
            ).params.sleep_time = SLEEP_TIME_TWEAK
            time.sleep(SLEEP_TIME_TWEAK + 0.01)
            self.behaviour.act_wrapper()

        assert "[test_agent_name] Entered in the 'cycle_reset' behaviour" in caplog.text
        assert (
            "[test_agent_name] Finalized estimate not available. Resetting!"
            in caplog.text
        )

        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id


class TestFreshModelResetBehaviour(APYEstimationFSMBehaviourBaseCase):
    """Test FreshModelResetBehaviour."""

    behaviour_class = FreshModelResetBehaviour
    next_behaviour_class = FetchBehaviour

    def test_fresh_model_reset_behaviour(self, caplog: LogCaptureFixture) -> None:
        """Run test for `ResetBehaviour`."""
        self.fast_forward_to_behaviour(
            behaviour=self.behaviour,
            behaviour_id=self.behaviour_class.behaviour_id,
            synchronized_data=SynchronizedData(AbciAppDB(setup_data={})),
        )
        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.behaviour_class.behaviour_id

        with caplog.at_level(
            logging.INFO,
            logger="aea.test_agent_name.packages.valory.skills.apy_estimation_abci",
        ):
            self.behaviour.act_wrapper()

        assert (
            "[test_agent_name] Entered in the 'fresh_model_reset' behaviour"
            in caplog.text
        )
        assert (
            "[test_agent_name] Resetting to create a fresh forecasting model!"
            in caplog.text
        )

        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round()

        behaviour = cast(BaseBehaviour, self.behaviour.current_behaviour)
        assert behaviour.behaviour_id == self.next_behaviour_class.behaviour_id
