# Strabs Packages

PEP 420 namespace packages from Structured Abstraction.

## Packages

- [strabs-doit](packages/strabs-doit/) - Task runner with parallel execution
- [strabs-helm](packages/strabs-helm/) - Type-safe Helm chart operations
- [strabs-juggernaut](packages/strabs-juggernaut/) - Coming soon

## Install

```bash
pip install strabs-doit
pip install strabs-helm
```

## Usage

```python
from strabs.doit import doit, run
from strabs.helm import HelmChart, HelmRepo
```
