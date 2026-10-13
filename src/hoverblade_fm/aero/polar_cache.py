"""Polar caching (T083).

The analytic polar is cheap but the BEMT calls it once per annulus per iteration, and a table
or XFOIL source is thousands of times more expensive.  ``CachingPolar`` wraps any
:class:`~hoverblade_fm.aero.polar.PolarSource`, buckets the continuous local conditions onto a
finite grid, and serves repeats from memory or from a JSON file that can be committed with a
run so a regression test never re-runs an external solver.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from hoverblade_fm.aero.polar import PolarPoint, PolarSource


@dataclass
class Bucketing:
    """Precision of the cache grid; coarser buckets cache more aggressively."""

    alpha_deg: float = 0.25
    reynolds: float = 0.05  # relative
    mach: float = 0.01
    t_over_c: float = 0.005
    te_over_c: float = 0.002
    camber: float = 0.0025

    def key(
        self,
        alpha: float,
        reynolds: float,
        mach: float,
        t_over_c: float,
        te_over_c: float,
        camber: float,
    ) -> tuple[int, int, int, int, int, int]:
        def snap(value: float, step: float) -> int:
            return int(round(value / step))

        def rel(value: float, base: float) -> int:
            return int(round(math.log(max(value, 1.0)) / base))

        return (
            snap(alpha, self.alpha_deg),
            rel(reynolds, self.reynolds),
            snap(mach, self.mach),
            snap(t_over_c, self.t_over_c),
            snap(te_over_c, self.te_over_c),
            snap(camber, self.camber),
        )


@dataclass
class CachingPolar(PolarSource):
    """Memoised wrapper around any polar source."""

    inner: PolarSource
    bucketing: Bucketing = field(default_factory=Bucketing)
    cache: dict[tuple[int, int, int, int, int, int], tuple[float, float, float]] = field(default_factory=dict)
    disk_path: Path | None = None
    hits: int = 0
    misses: int = 0

    def __post_init__(self) -> None:
        if self.disk_path is not None and Path(self.disk_path).is_file():
            self.load(self.disk_path)

    def __call__(
        self,
        alpha_deg: np.ndarray | float,
        reynolds: np.ndarray | float,
        mach: np.ndarray | float,
        t_over_c: np.ndarray | float,
        te_over_c: np.ndarray | float,
        camber: np.ndarray | float,
    ) -> PolarPoint:
        a = np.atleast_1d(np.asarray(alpha_deg, dtype=float))
        re = np.broadcast_to(np.atleast_1d(np.asarray(reynolds, dtype=float)), a.shape)
        m = np.broadcast_to(np.atleast_1d(np.asarray(mach, dtype=float)), a.shape)
        tc = np.broadcast_to(np.atleast_1d(np.asarray(t_over_c, dtype=float)), a.shape)
        tec = np.broadcast_to(np.atleast_1d(np.asarray(te_over_c, dtype=float)), a.shape)
        cm = np.broadcast_to(np.atleast_1d(np.asarray(camber, dtype=float)), a.shape)

        cl = np.empty(a.shape)
        cd = np.empty(a.shape)
        cml = np.empty(a.shape)
        todo: list[int] = []
        keys: list[tuple[int, int, int, int, int, int]] = []
        for i in range(a.size):
            key = self.bucketing.key(a[i], re[i], m[i], tc[i], tec[i], cm[i])
            keys.append(key)
            found = self.cache.get(key)
            if found is None:
                todo.append(i)
            else:
                cl[i], cd[i], cml[i] = found
                self.hits += 1
        if todo:
            point = self.inner(a[todo], re[todo], m[todo], tc[todo], tec[todo], cm[todo])
            for j, i in enumerate(todo):
                cl[i] = float(np.asarray(point.cl)[j])
                cd[i] = float(np.asarray(point.cd)[j])
                cml[i] = float(np.asarray(point.cm)[j])
                self.cache[keys[i]] = (cl[i], cd[i], cml[i])
                self.misses += 1
        return PolarPoint(alpha_deg=a, cl=cl, cd=cd, cm=cml)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "entries": len(self.cache),
            "hit_rate": self.hit_rate,
        }

    def save(self, path: str | Path | None = None) -> Path:
        chosen = path if path is not None else self.disk_path
        if chosen is None:
            raise ValueError("no cache path given")
        target = Path(chosen)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bucketing": self.bucketing.__dict__,
            "entries": {"|".join(str(k) for k in key): value for key, value in self.cache.items()},
        }
        target.write_text(json.dumps(payload), encoding="utf-8")
        self.disk_path = target
        return target

    def load(self, path: str | Path) -> int:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        added = 0
        for raw_key, value in payload.get("entries", {}).items():
            parts = [int(part) for part in raw_key.split("|")]
            if len(parts) != 6:
                continue
            key = (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
            if key not in self.cache:
                added += 1
            self.cache[key] = (float(value[0]), float(value[1]), float(value[2]))
        return added

    def clear(self) -> None:
        self.cache.clear()
        self.hits = self.misses = 0


__all__ = ["Bucketing", "CachingPolar"]
