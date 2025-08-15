# Development Setup

## Install
```bash
# with virtual environment .venv active
pip install -e ".[dev]" -c requirements.txt
```

## Update dependencies
```bash
pip-compile pyproject.toml
```

## After code changes
```bash
ruff check --fix
mypy ofd.py
```
