<a id="autonomy.deploy.chain"></a>

# autonomy.deploy.chain

Utils to support on-chain contract interactions.

<a id="autonomy.deploy.chain.get_abi"></a>

#### get`_`abi

```python
def get_abi(url: str) -> Dict
```

Get ABI from provided URL

<a id="autonomy.deploy.chain.ServiceRegistry"></a>

## ServiceRegistry Objects

```python
class ServiceRegistry()
```

Class to represent on-chain service registry.

<a id="autonomy.deploy.chain.ServiceRegistry.__init__"></a>

#### `__`init`__`

```python
def __init__(chain_type: str = "staging", rpc_url: Optional[str] = None, service_contract_address: Optional[str] = None) -> None
```

Initialize object.

<a id="autonomy.deploy.chain.ServiceRegistry.resolve_token_id"></a>

#### resolve`_`token`_`id

```python
def resolve_token_id(token_id: int) -> Dict
```

Resolve token id using on-chain contracts.

<a id="autonomy.deploy.chain.ServiceRegistry.get_agent_instances"></a>

#### get`_`agent`_`instances

```python
def get_agent_instances(token_id: int) -> Tuple[int, List[str]]
```

Get the list of agent instances.

