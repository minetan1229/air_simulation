"""Incremental surrogate learning of the screening physics.

The search of :mod:`physics.ga2` evaluates most of its individuals with the
cheap screening physics -- a reduced order model that is fast enough for a
population of 100000 -- and promotes only a few of them to the expensive
DNS/FSI reference.  The screening model is systematically wrong, and the way
it is wrong depends on the shape of the airplane: a wide, heavy sheet and a
small, light one do not fail in the same way.

This module learns that bias instead of ignoring it.  Every individual that
reached the reference stage contributes one sample to a SQLite database: the
descriptors of the design (wing loading, aspect ratio, fold count, ...) and
the *correction* that the reference stage revealed, namely the reference
value minus the screening value.  A ridge regression on the z-scored
descriptors then predicts the correction of a new individual, so the search
can correct the screening physics before it decides what to promote.

The database is append only and survives a restart of the process, because
the samples of the previous run are the most expensive data the search owns.
The schema follows the data: a descriptor that appears for the first time
in the middle of a run gets a new column, and the rows that were written
before keep the value they had.

The regression is a plain ridge regression on NumPy alone.  The design
matrix carries a bias column, the features are z-scored with the statistics
of the training set, and the normal equations

``(X^T X + lambda I) W = X^T Y``

are solved directly; a singular system falls back to the least squares
solution.  The model is deliberately small and inspectable: its coefficients
are part of the generation checkpoint, so a run can be audited without
replaying it.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "SurrogateDatabase",
    "CorrectionModel",
    "train_correction_model",
    "apply_correction",
]


#: The number of inserts between two commits of the sample database.
DEFAULT_COMMIT_EVERY: int = 64

#: The smallest standard deviation that is treated as a real spread.
_MIN_SCALE: float = 1.0e-12


def _quote_identifier(name: str) -> str:
    """Quote a column name so that it can be used in a SQL statement."""
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


class SurrogateDatabase:
    """The append-only store of the screening-to-reference corrections.

    Parameters
    ----------
    path:
        The file of the SQLite database.  The directory is created when it
        does not exist yet.
    commit_every:
        The number of inserts between two commits.  The commit is the
        expensive part of a SQLite write, so the inserts are batched; a
        value of one commits every insert and is only useful for a test.
    """

    def __init__(self, path: str, commit_every: int = DEFAULT_COMMIT_EVERY) -> None:
        self.path = str(path)
        self.commit_every: int = max(1, int(commit_every))
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=60.0,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._columns: List[str] = []
        self._insert_cache: Dict[Tuple[str, ...], str] = {}
        self._pending: int = 0
        self._closed: bool = False
        self._create_table()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _create_table(self) -> None:
        """Create the sample table when it does not exist yet."""
        with self._lock:
            self._connection.execute(
                'CREATE TABLE IF NOT EXISTS samples ('
                '"generation" INTEGER NOT NULL, '
                '"key" TEXT NOT NULL)'
            )
            self._connection.commit()
            self._columns = self._read_columns()

    def _read_columns(self) -> List[str]:
        """The descriptor columns of the table, in the order of the table."""
        rows = self._connection.execute("PRAGMA table_info(samples)").fetchall()
        names = [str(row[1]) for row in rows]
        return [name for name in names if name not in ("generation", "key")]

    def _ensure_columns(self, columns: Sequence[str]) -> None:
        """Add a column for every descriptor that the table does not know yet."""
        missing = [name for name in columns if name not in self._columns]
        if not missing:
            return
        for name in missing:
            self._connection.execute(
                f"ALTER TABLE samples ADD COLUMN {_quote_identifier(name)} REAL"
            )
        self._columns.extend(missing)
        self._connection.commit()

    def _insert_sql(self, columns: Tuple[str, ...]) -> str:
        """The prepared insert statement for a set of descriptor columns."""
        statement = self._insert_cache.get(columns)
        if statement is None:
            names = ["generation", "key"] + list(columns)
            placeholders = ", ".join("?" for _ in names)
            statement = (
                f"INSERT INTO samples ({', '.join(_quote_identifier(name) for name in names)}) "
                f"VALUES ({placeholders})"
            )
            self._insert_cache[columns] = statement
        return statement

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def record(
        self,
        generation: int,
        key: str,
        features: Dict[str, float],
        targets: Dict[str, float],
    ) -> None:
        """Store one sample of the search.

        The features describe the individual, the targets are the
        corrections that the reference stage revealed.  A descriptor that
        appears for the first time gets a new column of the table.
        """
        values: Dict[str, float] = {}
        for name, value in dict(features).items():
            values[str(name)] = float(value)
        for name, value in dict(targets).items():
            text = str(name)
            if text in values:
                raise ValueError(
                    f"'{text}' is used by a feature and by a target; the names have to differ"
                )
            values[text] = float(value)
        if not values:
            raise ValueError("a sample needs at least one feature or target")

        columns = tuple(values.keys())
        with self._lock:
            self._ensure_columns(columns)
            statement = self._insert_sql(columns)
            self._connection.execute(
                statement,
                [int(generation), str(key)] + [values[name] for name in columns],
            )
            self._pending += 1
            if self._pending >= self.commit_every:
                self._connection.commit()
                self._pending = 0

    def flush(self) -> None:
        """Commit the pending inserts."""
        with self._lock:
            if self._closed:
                return
            self._connection.commit()
            self._pending = 0

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def _select(self, include_key: bool, limit: int) -> List[Dict[str, Any]]:
        """The stored samples, oldest first."""
        with self._lock:
            selected = ["generation"]
            if include_key:
                selected.append("key")
            selected.extend(self._columns)
            query = (
                f"SELECT {', '.join(_quote_identifier(name) for name in selected)} "
                "FROM samples ORDER BY rowid"
            )
            parameters: Tuple[Any, ...] = ()
            if limit > 0:
                query += " LIMIT ?"
                parameters = (int(limit),)
            rows = self._connection.execute(query, parameters).fetchall()
        samples: List[Dict[str, Any]] = []
        for row in rows:
            sample: Dict[str, Any] = {}
            for name, value in zip(selected, row):
                if value is None:
                    continue
                sample[name] = value
            samples.append(sample)
        return samples

    def samples(self, limit: int = 0) -> List[Dict[str, float]]:
        """The stored samples as plain numbers.

        Every sample carries the generation it was recorded in and one entry
        per descriptor of the table.  A limit of zero returns all of them.
        """
        return [
            {name: float(value) for name, value in sample.items() if name != "key"}
            for sample in self._select(False, limit)
        ]

    def rows(self, limit: int = 0) -> List[Dict[str, Any]]:
        """The stored samples including the key of the individual."""
        return self._select(True, limit)

    def count(self) -> int:
        """The number of stored samples."""
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) FROM samples").fetchone()
        return int(row[0]) if row is not None else 0

    def columns(self) -> List[str]:
        """The descriptor columns of the table."""
        with self._lock:
            return list(self._columns)

    # ------------------------------------------------------------------
    # Life cycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Commit the pending inserts and close the database."""
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.commit()
            finally:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "SurrogateDatabase":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class CorrectionModel:
    """A ridge regression of the screening-to-reference correction.

    The features are z-scored with the statistics of the training set, the
    design matrix carries a bias column, and the coefficients solve

    ``(X^T X + lambda I) W = X^T Y``

    with a small ridge term that keeps the system solvable when the
    descriptors are correlated.
    """

    def __init__(
        self,
        feature_names: Sequence[str],
        target_names: Sequence[str],
        ridge: float = 1.0e-3,
    ) -> None:
        self.feature_names: Tuple[str, ...] = tuple(str(name) for name in feature_names)
        self.target_names: Tuple[str, ...] = tuple(str(name) for name in target_names)
        self.ridge: float = float(ridge)
        self._mean: Optional[np.ndarray] = None
        self._scale: Optional[np.ndarray] = None
        self._weights: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def fitted(self) -> bool:
        """True when the model holds coefficients and can predict."""
        return self._weights is not None

    @property
    def coefficients(self) -> Optional[np.ndarray]:
        """The regression coefficients, bias row first."""
        return self._weights

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------
    def _as_matrix(self, data: Any, names: Sequence[str]) -> np.ndarray:
        """Turn the training data into a two dimensional float matrix."""
        if isinstance(data, Mapping):
            data = [data]
        rows = list(data)
        if rows and isinstance(rows[0], Mapping):
            matrix = np.asarray(
                [[float(row.get(name, 0.0)) for name in names] for row in rows],
                dtype=np.float64,
            )
        else:
            matrix = np.asarray(data, dtype=np.float64)
            if matrix.ndim == 1:
                matrix = matrix.reshape(1, -1)
        if matrix.ndim != 2:
            raise ValueError("the training data has to be a matrix")
        if matrix.shape[1] != len(names):
            raise ValueError(
                f"the data has {matrix.shape[1]} columns, expected {len(names)}"
            )
        return matrix

    def fit(self, features: Any, targets: Any) -> "CorrectionModel":
        """Fit the model and return it.

        A model that is fitted again starts from scratch.  When the data
        holds fewer samples than features the model stays unfitted instead
        of raising, because a run that has not collected enough reference
        evaluations yet is a normal state and not an error.
        """
        self._mean = None
        self._scale = None
        self._weights = None
        if not self.feature_names or not self.target_names:
            raise ValueError("the model needs at least one feature and one target")

        design = self._as_matrix(features, self.feature_names)
        response = self._as_matrix(targets, self.target_names)
        if design.shape[0] != response.shape[0]:
            raise ValueError("the features and the targets have to have the same length")

        samples = design.shape[0]
        if samples < len(self.feature_names):
            return self

        mean = design.mean(axis=0)
        scale = design.std(axis=0)
        scale = np.where(scale < _MIN_SCALE, 1.0, scale)
        normalised = (design - mean) / scale
        matrix = np.hstack([np.ones((samples, 1), dtype=np.float64), normalised])

        gram = matrix.T @ matrix + self.ridge * np.eye(matrix.shape[1], dtype=np.float64)
        moment = matrix.T @ response
        try:
            weights = np.linalg.solve(gram, moment)
        except np.linalg.LinAlgError:
            weights = np.linalg.lstsq(gram, moment, rcond=None)[0]

        self._mean = mean
        self._scale = scale
        self._weights = weights
        return self

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def _normalised_vector(self, sample: Mapping[str, float]) -> np.ndarray:
        """The z-scored features of one sample.

        A feature that the sample does not carry contributes the mean of the
        training set, which is the neutral assumption for a missing
        descriptor.
        """
        if self._mean is None or self._scale is None:
            raise RuntimeError("the correction model is not fitted")
        values = np.zeros(len(self.feature_names), dtype=np.float64)
        for index, name in enumerate(self.feature_names):
            if name in sample:
                values[index] = (float(sample[name]) - self._mean[index]) / self._scale[index]
        return values

    def predict(self, features: Any) -> np.ndarray:
        """Predict the correction of one or more designs.

        A mapping is one design and gives one row of corrections, a vector
        of features is one design as well, and a matrix of designs gives one
        row per design.
        """
        if self._mean is None or self._scale is None or self._weights is None:
            raise RuntimeError("the correction model is not fitted")
        if isinstance(features, Mapping):
            vector = self._normalised_vector(features)
            matrix = np.hstack([np.ones((1, 1), dtype=np.float64), vector.reshape(1, -1)])
            return np.asarray(matrix @ self._weights).reshape(-1)
        design = np.asarray(features, dtype=np.float64)
        if design.ndim == 1:
            design = design.reshape(1, -1)
            single = True
        else:
            single = False
        if design.ndim != 2:
            raise ValueError("the features have to be a matrix")
        if design.shape[1] != len(self.feature_names):
            raise ValueError(
                f"the data has {design.shape[1]} columns, expected {len(self.feature_names)}"
            )
        normalised = (design - self._mean) / self._scale
        matrix = np.hstack([np.ones((design.shape[0], 1), dtype=np.float64), normalised])
        prediction = np.asarray(matrix @ self._weights)
        if single:
            return prediction.reshape(-1)
        return prediction

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the model."""
        return {
            "kind": "correction_model",
            "feature_names": list(self.feature_names),
            "target_names": list(self.target_names),
            "ridge": float(self.ridge),
            "fitted": bool(self.fitted),
            "mean": None if self._mean is None else [float(value) for value in self._mean],
            "scale": None if self._scale is None else [float(value) for value in self._scale],
            "weights": None
            if self._weights is None
            else [[float(value) for value in row] for row in self._weights],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CorrectionModel":
        """Rebuild a model from its serialised form."""
        data = dict(payload or {})
        model = cls(
            feature_names=[str(name) for name in data.get("feature_names", ())],
            target_names=[str(name) for name in data.get("target_names", ())],
            ridge=float(data.get("ridge", 1.0e-3)),
        )
        mean = data.get("mean")
        scale = data.get("scale")
        weights = data.get("weights")
        if mean is None or scale is None or weights is None:
            return model
        mean_array = np.asarray(mean, dtype=np.float64)
        scale_array = np.asarray(scale, dtype=np.float64)
        weight_array = np.asarray(weights, dtype=np.float64)
        features = len(model.feature_names)
        if mean_array.shape != (features,) or scale_array.shape != (features,):
            return model
        if weight_array.shape != (features + 1, len(model.target_names)):
            return model
        model._mean = mean_array
        model._scale = scale_array
        model._weights = weight_array
        return model


def train_correction_model(
    database: SurrogateDatabase,
    feature_names: Sequence[str],
    target_names: Sequence[str],
    ridge: float = 1.0e-3,
) -> CorrectionModel:
    """Fit a correction model on the samples of the database.

    A sample that does not carry every feature or every target is skipped.
    When the database holds fewer samples than features the returned model
    is unfitted, because there is nothing to learn from yet.
    """
    names = tuple(str(name) for name in feature_names)
    targets = tuple(str(name) for name in target_names)
    model = CorrectionModel(names, targets, ridge)
    if not names or not targets:
        return model

    samples = database.samples()
    if len(samples) < len(names):
        return model

    rows: List[List[float]] = []
    responses: List[List[float]] = []
    for sample in samples:
        try:
            features = [float(sample[name]) for name in names]
            corrections = [float(sample[target]) for target in targets]
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in features + corrections):
            continue
        rows.append(features)
        responses.append(corrections)
    if len(rows) < len(names):
        return model

    return model.fit(
        np.asarray(rows, dtype=np.float64),
        np.asarray(responses, dtype=np.float64),
    )


def apply_correction(
    results: Sequence[Dict[str, Any]],
    model: CorrectionModel,
    feature_names: Sequence[str],
) -> List[Dict[str, Any]]:
    """Add the predicted correction to the named keys of every result.

    The inputs are not modified: every result is copied and the correction
    is added to the copy.  A target that the result does not carry is left
    alone, and a result without any of the features keeps the correction of
    the mean design.
    """
    names = tuple(str(name) for name in feature_names)
    if not model.fitted or not names:
        return [dict(result) for result in results]

    corrected: List[Dict[str, Any]] = []
    for result in results:
        features = {name: float(result[name]) for name in names if name in result}
        prediction = np.asarray(model.predict(features)).reshape(-1)
        updated = dict(result)
        for index, target in enumerate(model.target_names):
            if target in updated and index < prediction.size:
                updated[target] = float(updated[target]) + float(prediction[index])
        corrected.append(updated)
    return corrected
