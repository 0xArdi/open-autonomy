# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2022 Valory AG
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

"""Utils to support on-chain contract interactions."""


import os
from typing import Dict, List, Optional, Tuple

import requests
import web3


SERVICE_REGISTRY_ABI = "https://abi-server.staging.autonolas.tech/autonolas-registries/ServiceRegistry.json"
DEFAULT_STAGING_CHAIN = "https://chain.staging.autonolas.tech"

CHAIN_CONFIG: Dict[str, Dict[str, Optional[str]]] = {
    "staging": {
        "rpc": os.environ.get("STAGING_CHAIN_RPC", DEFAULT_STAGING_CHAIN),
        "service_contract_address": os.environ.get(
            "SERVICE_ADDRESS_STAGING", "0x0DCd1Bf9A1b36cE34237eEaFef220932846BCD82"
        ),
    },
    "ethereum": {
        "rpc": os.environ.get("ETHEREUM_CHAIN_RPC"),
        "service_contract_address": os.environ.get(
            "SERVICE_ADDRESS_ETHEREUM", "0x48b6af7B12C71f09e2fC8aF4855De4Ff54e775cA"
        ),
    },
    "goerli": {
        "rpc": os.environ.get("GOERLI_CHAIN_RPC"),
        "service_contract_address": os.environ.get(
            "SERVICE_ADDRESS_GOERLI", "0x1cEe30D08943EB58EFF84DD1AB44a6ee6FEff63a"
        ),
    },
}


def get_abi(url: str) -> Dict:
    """Get ABI from provided URL"""

    r = requests.get(url=url)
    return r.json().get("abi")


class ServiceRegistry:
    """Class to represent on-chain service registry."""

    def __init__(
        self,
        chain_type: str = "staging",
        rpc_url: Optional[str] = None,
        service_contract_address: Optional[str] = None,
    ) -> None:
        """Initialize object."""

        if chain_type not in CHAIN_CONFIG:
            raise ValueError(f"{chain_type} Currently not supported.")

        rpc_url = rpc_url or CHAIN_CONFIG.get(chain_type, {}).get("rpc")
        if rpc_url is None:
            raise ValueError(
                f"RPC url for {chain_type} is not set, please set value for {chain_type.upper()}_CHAIN_RPC"
            )

        self.w3 = web3.Web3(
            provider=web3.HTTPProvider(endpoint_uri=rpc_url),
        )
        self.abi = get_abi(SERVICE_REGISTRY_ABI)

        self.service_contract_address = service_contract_address or CHAIN_CONFIG.get(
            chain_type, {}
        ).get("service_contract_address")
        if self.service_contract_address is None:
            raise ValueError(
                f"RPC url for {chain_type} is not set, please set value for SERVICE_ADDRESS_{chain_type.upper()}"
            )

        self.contract = self.w3.eth.contract(
            address=self.service_contract_address, abi=self.abi
        )

    def resolve_token_id(
        self,
        token_id: int,
    ) -> Dict:
        """Resolve token id using on-chain contracts."""
        url = self.contract.functions.tokenURI(token_id).call()
        return requests.get(url).json()

    def get_agent_instances(self, token_id: int) -> Tuple[int, List[str]]:
        """Get the list of agent instances."""
        agent_instances = self.contract.functions.getAgentInstances(token_id).call()
        return agent_instances
