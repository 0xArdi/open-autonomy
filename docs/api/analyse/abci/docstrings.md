<a id="aea_swarm.analyse.abci.docstrings"></a>

# aea`_`swarm.analyse.abci.docstrings

Analyse ABCI app definitions for docstrings.

<a id="aea_swarm.analyse.abci.docstrings.check_working_tree_is_dirty"></a>

#### check`_`working`_`tree`_`is`_`dirty

```python
def check_working_tree_is_dirty() -> None
```

Check if the current Git working tree is dirty.

<a id="aea_swarm.analyse.abci.docstrings.docstring_abci_app"></a>

#### docstring`_`abci`_`app

```python
def docstring_abci_app(abci_app: Any) -> str
```

Generate a docstring for an ABCI app

This ensures that documentation aligns with the actual implementation

**Arguments**:

- `abci_app`: abci app object.

**Returns**:

docstring

<a id="aea_swarm.analyse.abci.docstrings.update_docstrings"></a>

#### update`_`docstrings

```python
def update_docstrings(module_path: Path, docstring: str, abci_app_name: str) -> Optional[str]
```

Update docstrings.

<a id="aea_swarm.analyse.abci.docstrings.process_module"></a>

#### process`_`module

```python
def process_module(module_path: Path) -> Optional[str]
```

Process module.

