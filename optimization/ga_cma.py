"""A real CMA-ES (covariance matrix adaptation evolution strategy).

The engine implements the standard ``(mu/mu_w, lambda)``-CMA-ES of Hansen
(2006) / Hansen et al. (2016):

* weighted recombination of the ``mu`` best offspring (``m <- sum w_i x_i``),
* the evolution paths ``p_sigma`` (a step-size path) and ``p_c`` (a
  covariance path) with the ``h_sigma`` heavy-tail correction,
* the rank-one update ``C <- (1-c1-cmu) C + c1 p_c p_c^T + cmu sum w_i y_i y_i^T``,
* the step-size adaptation ``sigma <- sigma exp(c_sigma/d_sigma (||p_sigma||/chi_N - 1))``,
* the negative weights of the oldest offspring that keep the rank-one and
  rank-mu updates apart.

The strategy works on the **continuous** part of the search space of the
research engine: the fold angles of the genome (main wing fold angle,
elevon angle), the centre-of-gravity offset and the throw parameters.  The
discrete part of the genome (which creases exist, in which order) is handled
by the genetic operators of :mod:`physics.ga_domain`; the covariance matrix
therefore describes the *continuous* linkage between the fold angles and the
throw, which is exactly what the research question asks for.

The normalised search space
---------------------------
The strategy searches the **normalised unit box** ``[0, 1]^n``.  Every gene
is mapped into the box with

    ``x_norm = (x_raw - low) / (high - low)``

before the strategy sees it and is mapped back with the inverse when a sample
leaves the strategy.  *All* the internal state -- the mean, the step size,
the covariance matrix and the two evolution paths -- is expressed in that
normalised space, so the stored covariance is directly interpretable:

* the diagonal of ``C`` is the variance of a gene **as a fraction of its own
  range** (a value of ``1e-4`` means "the gene moves by one percent of its
  range"), and
* the off diagonal entries are the linkage between two genes, i.e. the
  correlation the strategy has discovered between them.

The step size ``sigma`` is a fraction of a gene range as well, so
``sigma0 = 0.15`` means "the first samples spread over 15 % of the range of
every gene".  Working in the raw gene units instead -- as an earlier
version of this module did -- is pathological: the ranges of the genes of the
engine differ by three orders of magnitude (the centre-of-gravity offset
spans 0.1 m, the main wing fold angle spans 360 deg), so a single raw step
size pins the narrow genes at their bounds and never moves the wide ones.

The whole state of the strategy -- the mean, the step size, the covariance
matrix, both evolution paths and the random generator -- is serialisable in
that normalised space, so a checkpoint can restore a run bit for bit.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["CMAES", "default_lambda", "default_mu", "NORMALISED_SPACE"]

#: The name of the coordinate system the state of the strategy lives in.
#: It is written into every snapshot, so a checkpoint always says in which
#: units its covariance matrix is expressed.
NORMALISED_SPACE: str = "normalised"


def default_lambda(dimension: int) -> int:
    """The standard offspring count ``4 + floor(3 ln n)``."""
    n = max(int(dimension), 1)
    return max(4, 4 + int(math.floor(3.0 * math.log(n))))


def default_mu(lam: int) -> int:
    """The standard parent count ``floor(lambda / 2)``."""
    return max(2, int(lam) // 2)


class CMAES:
    """The covariance matrix adaptation strategy.

    Parameters
    ----------
    x0:
        The initial mean of the search distribution, in the **raw** gene
        units.  It is clipped into the bounds.
    sigma0:
        The initial step size, expressed in the *normalised* units of the
        search space: a fraction of the range of a gene.  It therefore means
        the same thing for every gene.
    bounds:
        One ``(low, high)`` pair per dimension, in the raw gene units.  The
        samples are clipped into the box, which is the cheapest bound
        handling that keeps the covariance update unbiased for the interior
        of the box.
    seed:
        Seed of the internal ``numpy`` generator.  The same seed always
        produces the same sequence of samples.
    lam:
        Number of offspring per generation (``0`` -> the standard rule).
    mu:
        Number of parents (``0`` -> ``lambda // 2``).
    """

    def __init__(
        self,
        x0: Sequence[float],
        sigma0: float,
        bounds: Sequence[Tuple[float, float]],
        seed: int = 0,
        lam: int = 0,
        mu: int = 0,
    ) -> None:
        self.dimension = int(len(x0))
        if self.dimension < 1:
            raise ValueError("CMA-ES needs at least one dimension")
        self.bounds: List[Tuple[float, float]] = [
            (float(low), float(high)) for low, high in bounds
        ]
        if len(self.bounds) != self.dimension:
            raise ValueError("one bound pair per dimension is required")

        # The normalisation of the box: ``x_norm = (x_raw - low) / span``.
        self._low = np.asarray(
            [pair[0] for pair in self.bounds], dtype=np.float64
        )
        high = np.asarray([pair[1] for pair in self.bounds], dtype=np.float64)
        span = high - self._low
        # A gene without a range would divide by zero; it is treated as a
        # constant gene (a span of one raw unit).
        self._span = np.where(span > 0.0, span, 1.0)

        self.lam = int(lam) if int(lam) > 0 else default_lambda(self.dimension)
        self.mu = int(mu) if int(mu) > 0 else default_mu(self.lam)
        if self.mu > self.lam:
            raise ValueError("mu must not exceed lambda")

        self._mean_norm = self._to_norm(np.asarray(x0, dtype=np.float64))
        self.sigma = float(max(sigma0, 1.0e-12))
        self.covariance = np.eye(self.dimension, dtype=np.float64)
        self._eigenvectors = np.eye(self.dimension, dtype=np.float64)
        self._sqrt_covariance = np.eye(self.dimension, dtype=np.float64)
        self._inv_sqrt_covariance = np.eye(self.dimension, dtype=np.float64)
        self.path_sigma = np.zeros(self.dimension, dtype=np.float64)
        self.path_c = np.zeros(self.dimension, dtype=np.float64)
        self.generation = 0
        self._rng = np.random.default_rng(int(seed))
        self._pending: List[np.ndarray] = []
        self._pending_scores: List[float] = []
        self._weights = self._compute_weights()
        self._update_constants()

    # ------------------------------------------------------------------
    # The normalisation of the box
    # ------------------------------------------------------------------
    def _to_norm(self, values: np.ndarray) -> np.ndarray:
        """Map raw gene values into the normalised unit box."""
        out = (np.asarray(values, dtype=np.float64) - self._low) / self._span
        return np.clip(out, 0.0, 1.0)

    def _to_raw(self, values: np.ndarray) -> np.ndarray:
        """Map normalised values back into the raw gene units."""
        return self._low + np.asarray(values, dtype=np.float64) * self._span

    @property
    def mean(self) -> np.ndarray:
        """The centre of the search distribution, in the raw gene units."""
        return self._to_raw(self._mean_norm)

    @mean.setter
    def mean(self, values: Sequence[float]) -> None:
        self._mean_norm = self._to_norm(np.asarray(values, dtype=np.float64))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _compute_weights(self) -> np.ndarray:
        raw = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1, dtype=np.float64))
        raw = raw / raw.sum()
        return raw

    def _update_constants(self) -> None:
        n = float(self.dimension)
        mu_eff = float(1.0 / np.sum(self._weights ** 2))
        self.mu_eff = mu_eff
        self.c_sigma = (mu_eff + 2.0) / (n + mu_eff + 3.0)
        self.d_sigma = (
            1.0
            + 2.0 * max(0.0, math.sqrt((mu_eff - 1.0) / (n + 1.0)) - 1.0)
            + self.c_sigma
        )
        self.c_c = (mu_eff + 2.0) / (n + mu_eff + 5.0)
        self.c_1 = 2.0 / ((n + 1.3) ** 2 + mu_eff)
        self.c_mu = min(
            1.0 - self.c_1,
            2.0 * (mu_eff - 2.0 + 1.0 / mu_eff) / ((n + 2.0) ** 2 + mu_eff),
        )
        self.chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))
        self.expected_step = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------
    def _clip(self, vector: np.ndarray) -> np.ndarray:
        """Clip a normalised vector into the unit box."""
        return np.clip(np.asarray(vector, dtype=np.float64), 0.0, 1.0)

    def ask(self) -> List[np.ndarray]:
        """Draw ``lambda`` offspring from the current search distribution.

        The samples are returned in the **raw** gene units (the caller never
        has to know about the normalisation), but they are drawn in the
        normalised box, so every gene is explored with the same relative
        step size.
        """
        if self._pending:
            return [self._to_raw(sample) for sample in self._pending]
        samples: List[np.ndarray] = []
        for _ in range(self.lam):
            normal = self._rng.standard_normal(self.dimension)
            raw = self._mean_norm + self.sigma * (self._sqrt_covariance @ normal)
            samples.append(self._clip(raw))
        self._pending = samples
        self._pending_scores = []
        return [self._to_raw(sample) for sample in samples]

    def set_mean(self, values: Sequence[float]) -> None:
        """Move the centre of the search distribution (raw gene units)."""
        if len(values) != self.dimension:
            raise ValueError("the mean needs one value per dimension")
        self._mean_norm = self._to_norm(np.asarray(values, dtype=np.float64))

    def tell(self, scores: Sequence[float]) -> None:
        """Feed the fitness of the offspring drawn by :meth:`ask` back in.

        ``scores`` are *maximised*.  The offspring are ranked, the ``mu`` best
        ones are recombined and the covariance matrix, both evolution paths
        and the step size are updated with the standard rules.  Everything
        happens in the normalised space of the strategy.
        """
        if not self._pending:
            raise RuntimeError("tell() was called without a preceding ask()")
        if len(scores) != len(self._pending):
            raise ValueError("one score per sampled offspring is required")

        samples = np.asarray(self._pending, dtype=np.float64)
        values = np.asarray([float(s) for s in scores], dtype=np.float64)
        order = np.argsort(-values, kind="stable")
        selected = samples[order[: self.mu]]
        weights = self._weights

        old_mean = self._mean_norm.copy()
        new_mean = weights @ selected
        self._mean_norm = self._clip(new_mean)

        y_w = (new_mean - old_mean) / max(self.sigma, 1.0e-12)
        self.path_sigma = (1.0 - self.c_sigma) * self.path_sigma + math.sqrt(
            self.c_sigma * (2.0 - self.c_sigma) * self.mu_eff
        ) * (self._inv_sqrt_covariance @ y_w)

        denominator = math.sqrt(max(1.0 - (1.0 - self.c_sigma) ** (2.0 * (self.generation + 1)), 1.0e-12))
        h_sigma = 1.0 if (np.linalg.norm(self.path_sigma) / denominator) < (
            (1.4 + 2.0 / (self.dimension + 1.0)) * self.chi_n
        ) else 0.0
        self.path_c = (1.0 - self.c_c) * self.path_c + h_sigma * math.sqrt(
            self.c_c * (2.0 - self.c_c) * self.mu_eff
        ) * y_w

        y_selected = (selected - old_mean) / max(self.sigma, 1.0e-12)
        rank_mu = (weights[:, None] * y_selected).T @ y_selected
        delta_h = (1.0 - h_sigma) * self.c_c * (2.0 - self.c_c)
        self.covariance = (
            (1.0 - self.c_1 - self.c_mu) * self.covariance
            + self.c_1 * (np.outer(self.path_c, self.path_c) + delta_h * self.covariance)
            + self.c_mu * rank_mu
        )
        self.covariance = 0.5 * (self.covariance + self.covariance.T)
        self._regularise()
        self._decompose()

        self.sigma *= math.exp(
            (self.c_sigma / self.d_sigma) * (np.linalg.norm(self.path_sigma) / self.chi_n - 1.0)
        )
        self.sigma = float(min(max(self.sigma, 1.0e-12), 1.0e6))
        self.generation += 1
        self._pending = []
        self._pending_scores = []

    # ------------------------------------------------------------------
    # Linear algebra
    # ------------------------------------------------------------------
    def _regularise(self) -> None:
        """Keep the covariance matrix symmetric positive definite."""
        matrix = self.covariance
        matrix[range(self.dimension), range(self.dimension)] += 1.0e-12
        try:
            np.linalg.cholesky(matrix)
        except np.linalg.LinAlgError:
            matrix = matrix + 1.0e-8 * np.eye(self.dimension)
        self.covariance = matrix

    def _decompose(self) -> None:
        """Refresh the eigenvectors and the (inverse) square root of ``C``."""
        values, vectors = np.linalg.eigh(self.covariance)
        floor = max(float(np.max(values)) * 1.0e-12, 1.0e-20)
        values = np.clip(values, floor, None)
        self._eigenvectors = vectors
        self._sqrt_covariance = vectors @ np.diag(np.sqrt(values)) @ vectors.T
        self._inv_sqrt_covariance = vectors @ np.diag(1.0 / np.sqrt(values)) @ vectors.T

    # ------------------------------------------------------------------
    # Diagnostics and persistence
    # ------------------------------------------------------------------
    def correlation_matrix(self) -> np.ndarray:
        """The correlation matrix implied by the covariance matrix."""
        sigma = np.sqrt(np.clip(np.diag(self.covariance), 1.0e-30, None))
        return self.covariance / np.outer(sigma, sigma)

    def off_diagonal_mass(self) -> float:
        """A scalar measure of how much the genes are coupled.

        It is the mean of the absolute off-diagonal entries of the
        correlation matrix: ``0`` for an uncorrelated strategy, larger than
        zero as soon as the covariance adaptation discovered a linkage.
        """
        if self.dimension < 2:
            return 0.0
        correlation = self.correlation_matrix()
        mask = ~np.eye(self.dimension, dtype=bool)
        return float(np.mean(np.abs(correlation[mask])))

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable snapshot of the whole strategy state.

        The snapshot lives in the **normalised** space of the strategy: the
        mean, the step size, the covariance matrix and the evolution paths
        are all normalised, and the raw bounds are stored next to them so
        that the mapping back into the gene units is reproducible.
        """
        return {
            "space": NORMALISED_SPACE,
            "dimension": int(self.dimension),
            "lam": int(self.lam),
            "mu": int(self.mu),
            "bounds": [[float(low), float(high)] for low, high in self.bounds],
            "mean": [float(value) for value in self._mean_norm],
            "mean_raw": [float(value) for value in self.mean],
            "sigma": float(self.sigma),
            "covariance": [[float(value) for value in row] for row in self.covariance],
            "path_sigma": [float(value) for value in self.path_sigma],
            "path_c": [float(value) for value in self.path_c],
            "generation": int(self.generation),
            "rng_state": _jsonify(self._rng.bit_generator.state),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CMAES":
        """Rebuild a strategy from the snapshot of :meth:`to_dict`."""
        dimension = int(data["dimension"])
        bounds = [(float(low), float(high)) for low, high in data["bounds"]]
        instance = cls(
            x0=[float(value) for value in data.get("mean_raw") or data["mean"]],
            sigma0=float(data["sigma"]),
            bounds=bounds,
            seed=0,
            lam=int(data["lam"]),
            mu=int(data["mu"]),
        )
        space = str(data.get("space", NORMALISED_SPACE))
        if space == NORMALISED_SPACE:
            instance._mean_norm = np.clip(
                np.asarray([float(value) for value in data["mean"]], dtype=np.float64),
                0.0,
                1.0,
            )
            instance.covariance = np.asarray(
                [[float(value) for value in row] for row in data["covariance"]],
                dtype=np.float64,
            )
        else:
            # A snapshot of the older raw-unit engine: the covariance is
            # converted into the normalised space with ``D^-1 C D^-1``
            # (``D`` = the diagonal of the gene ranges) so that the restored
            # strategy keeps the same correlation structure.
            instance._mean_norm = instance._to_norm(
                np.asarray([float(value) for value in data["mean"]], dtype=np.float64)
            )
            raw = np.asarray(
                [[float(value) for value in row] for row in data["covariance"]],
                dtype=np.float64,
            )
            scale = np.diag(1.0 / instance._span)
            instance.covariance = scale @ raw @ scale
        instance.sigma = float(data["sigma"])
        instance.path_sigma = np.asarray(
            [float(value) for value in data["path_sigma"]], dtype=np.float64
        )
        instance.path_c = np.asarray(
            [float(value) for value in data["path_c"]], dtype=np.float64
        )
        instance.generation = int(data.get("generation", 0))
        instance._regularise()
        instance._decompose()
        state = data.get("rng_state")
        if state:
            instance._rng.bit_generator.state = _unjsonify(state)
        return instance


def _jsonify(value: Any) -> Any:
    """Convert numpy scalars and arrays into JSON friendly values."""
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def _unjsonify(value: Any) -> Any:
    """The inverse of :func:`_jsonify` for the bit generator state."""
    if isinstance(value, dict):
        return {str(key): _unjsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unjsonify(item) for item in value]
    return value
