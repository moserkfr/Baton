"""Find what a student's code keeps on the GPU, so they don't have to call `track()` by hand.

Walks a namespace (a notebook's variables, or a script's globals) and returns every model, optimizer
and GPU tensor in it, also one level inside lists, tuples and dicts (`models = [a, b]`, `state = {...}`).

Skipped on purpose:
* names starting with `_` (IPython's output history `_1`, `__`, ... would keep old results alive);
* tensor views (`x[0]`, `x.T`): parking swaps a tensor's storage, which would detach a view from its
  base, so only the base tensor is parked. A view's memory belongs to its base anyway.
"""
from __future__ import annotations

from typing import Any, Iterator

import torch

MAX_ITEMS = 1000   # don't walk huge Python lists of numbers


def _candidates(name: str, value: Any) -> Iterator[tuple[str, Any]]:
    yield name, value
    if isinstance(value, (list, tuple)) and len(value) <= MAX_ITEMS:
        for i, v in enumerate(value):
            yield f"{name}[{i}]", v
    elif isinstance(value, dict) and len(value) <= MAX_ITEMS:
        for k, v in value.items():
            yield f"{name}[{k!r}]", v


def discover(namespace: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """(kind, name, object) for every module, optimizer and base GPU tensor found."""
    found: list[tuple[str, str, Any]] = []
    seen: set[int] = set()
    for name, value in list(namespace.items()):
        if name.startswith("_"):
            continue
        for cname, obj in _candidates(name, value):
            if id(obj) in seen:
                continue
            if isinstance(obj, torch.nn.Module):
                kind = "module"
            elif isinstance(obj, torch.optim.Optimizer):
                kind = "optimizer"
            elif torch.is_tensor(obj) and obj.is_cuda and obj._base is None:
                kind = "tensor"
            else:
                continue
            seen.add(id(obj))
            found.append((kind, cname, obj))
    return found
