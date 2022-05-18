<a id="aea_swarm.analyse.abci.app_spec"></a>

# aea`_`swarm.analyse.abci.app`_`spec

Generates the specification for a given ABCI app in YAML/JSON/Mermaid format.

<a id="aea_swarm.analyse.abci.app_spec.DFASpecificationError"></a>

## DFASpecificationError Objects

```python
class DFASpecificationError(Exception)
```

Simple class to raise errors when parsing a DFA.

<a id="aea_swarm.analyse.abci.app_spec.DFA"></a>

## DFA Objects

```python
class DFA()
```

Simple specification of a deterministic finite automaton (DFA).

<a id="aea_swarm.analyse.abci.app_spec.DFA.OutputFormats"></a>

## OutputFormats Objects

```python
class OutputFormats()
```

Output formats.

<a id="aea_swarm.analyse.abci.app_spec.DFA.__init__"></a>

#### `__`init`__`

```python
def __init__(label: str, states: Set[str], default_start_state: str, start_states: Set[str], final_states: Set[str], alphabet_in: Set[str], transition_func: Dict[Tuple[str, str], str])
```

Initialize DFA object.

<a id="aea_swarm.analyse.abci.app_spec.DFA.is_transition_func_total"></a>

#### is`_`transition`_`func`_`total

```python
def is_transition_func_total() -> bool
```

Outputs True if the transition function of the DFA is total.

A transition function is total when it explicitly defines all the transitions
for all the possible pairs (state, input_symbol). By convention, when a transition
(state, input_symbol) is not defined for a certain input_symbol, it will be
automatically regarded as a self-transition to the same state.

**Returns**:

None

<a id="aea_swarm.analyse.abci.app_spec.DFA.get_transitions"></a>

#### get`_`transitions

```python
def get_transitions(input_sequence: List[str]) -> List[str]
```

Runs the DFA given the input sequence of symbols, and outputs the list of state transitions.

<a id="aea_swarm.analyse.abci.app_spec.DFA.__eq__"></a>

#### `__`eq`__`

```python
def __eq__(other: object) -> bool
```

Compares two DFAs

<a id="aea_swarm.analyse.abci.app_spec.DFA.dump"></a>

#### dump

```python
def dump(file: Path, output_format: str = "yaml") -> None
```

Dumps this DFA spec. to a file in YAML/JSON/Mermaid format.

<a id="aea_swarm.analyse.abci.app_spec.DFA.load"></a>

#### load

```python
@classmethod
def load(cls, fp: TextIO, input_format: str = "yaml") -> "DFA"
```

Loads a DFA JSON specification from file.

<a id="aea_swarm.analyse.abci.app_spec.DFA.abci_to_dfa"></a>

#### abci`_`to`_`dfa

```python
@classmethod
def abci_to_dfa(cls, abci_app_cls: Type[AbciApp], label: str = "") -> "DFA"
```

Translates an AbciApp class into a DFA.

<a id="aea_swarm.analyse.abci.app_spec.SpecCheck"></a>

## SpecCheck Objects

```python
class SpecCheck()
```

Class to represent abci spec checks.

<a id="aea_swarm.analyse.abci.app_spec.SpecCheck.check_one"></a>

#### check`_`one

```python
@staticmethod
def check_one(informat: str, infile: str, classfqn: str) -> bool
```

Check for one.

<a id="aea_swarm.analyse.abci.app_spec.SpecCheck.check_all"></a>

#### check`_`all

```python
@classmethod
def check_all(cls, packages_dir: Path) -> None
```

Check all the available definitions.

