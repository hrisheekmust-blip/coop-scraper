"""Adapter registry. Order matters only for overlapping hosts; the generic adapter always matches last.

Adapters can be switched off without deleting queued jobs: settings key 'disabled_adapters' (list of names);
a disabled adapter's pages fall through to the generic flow.
"""
from __future__ import annotations

from .base import GenericAdapter, PortalAdapter
from .guest import Ashby, Greenhouse, Lever

_REGISTRY: list[PortalAdapter] = [Greenhouse(), Ashby(), Lever()]
GENERIC = GenericAdapter()
DISABLED: set[str] = set()


def register(adapter: PortalAdapter):
    _REGISTRY.insert(0, adapter)


def pick_adapter(url: str) -> PortalAdapter:
    for a in _REGISTRY:
        if a.name not in DISABLED and a.matches(url):
            return a
    return GENERIC


def names():
    return [a.name for a in _REGISTRY] + [GENERIC.name]


from .icims import ICIMS  # noqa: E402
from .oracle import Oracle  # noqa: E402
from .successfactors import SuccessFactors  # noqa: E402
from .workday import Workday  # noqa: E402

_REGISTRY += [Workday(), SuccessFactors(), Oracle(), ICIMS()]
