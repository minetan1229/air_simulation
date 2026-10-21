"""Configuration contract of the autonomous genetic research engine.

Every number that decides *how much* work the engine does lives here: the
trial budget of the whole run, the size of the three multi-fidelity tiers,
the population and island counts, the seed.  None of them is ever written
into the search logic itself -- the engine always reads them from a
:class:`GAConfig` instance, so a run can be resized from four different
places (the YAML file, the command line, the runtime API and a checkpoint
resume) without touching a single line of the algorithm.

The YAML reader
---------------
``requirements.txt`` does **not** list PyYAML, so this module ships its own
deliberately small YAML-subset reader instead of adding a dependency:

* nested block mappings (indentation based),
* block sequences (``- item``) of scalars or of mappings,
* inline flow sequences (``[a, b, c]``) and flow mappings (``{a: 1}``),
* the scalar types ``int``, ``float``, ``bool``, ``null`` and ``str``,
* ``#`` comments and blank lines.

That is exactly the subset ``config/genetic_research.yaml`` uses.  If PyYAML
happens to be installed the reader is not used at all -- the file is parsed
by the loader below, so the behaviour never depends on an optional package.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, fields, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_MAX_TRIALS",
    "DEFAULT_TOP_K",
    "DEFAULT_TIER1_TRIALS",
    "DEFAULT_TIER2_TRIALS",
    "DEFAULT_TIER3_TRIALS",
    "DEFAULT_BOOTSTRAP_GENE_SPREAD",
    "DEFAULT_GA_OFFSPRING_FRACTION",
    "DEFAULT_CMA_MUTATION_RATE",
    "DEFAULT_MUTATION_ANGLE_SIGMA_DEG",
    "DEFAULT_MUTATION_CREASE_SHIFT",
    "DEFAULT_FOLD_FLIP_RATE",
    "DEFAULT_SYMMETRY_CHANGE_RATE",
    "DEFAULT_BROKEN_GENOME_PENALTY",
    "DEFAULT_STAT_ELITE_MAX_FRACTION",
    "DEFAULT_PROGRESS_HISTORY_LIMIT",
    "DEFAULT_CMA_SIGMA0",
    "OBJECTIVE_DISTANCE",
    "OBJECTIVE_AIRTIME",
    "OBJECTIVES",
    "STAT_MODE_MEAN",
    "STAT_MODE_MEDIAN",
    "STAT_MODE_HYBRID",
    "STAT_MODE_BEST",
    "STAT_MODES",
    "GAConfig",
    "config_from_mapping",
    "load_config_file",
    "load_yaml_mapping",
    "parse_yaml",
]


# --------------------------------------------------------------------------
# Defaults -- the single place where the trial budget of a run is defined
# --------------------------------------------------------------------------
#: Total number of flight evaluations (all fidelity tiers together) a run
#: may spend.  Every other tier budget is a *subset* of it.
DEFAULT_MAX_TRIALS: int = 100_000

#: Number of elites kept per island and written into ``hall_of_fame.json``.
DEFAULT_TOP_K: int = 1_000

#: Number of individuals that reach the low fidelity (surrogate) tier.
DEFAULT_TIER1_TRIALS: int = 20

#: Number of individuals that reach the intermediate (short reference 6-DoF) tier.
DEFAULT_TIER2_TRIALS: int = 1_000

#: Number of individuals that reach the full fidelity (turbulent, jittered
#: reference 6-DoF) tier.
DEFAULT_TIER3_TRIALS: int = 100_000


# --------------------------------------------------------------------------
# Defaults of the search operators
# --------------------------------------------------------------------------
# The numbers below are the *tunable* constants of the search: they decide
# how the population is built and how the repair mutation behaves.  They used
# to live as bare literals inside the search logic of ``physics.ga_domain``;
# they live here now, so every one of them is documented, overridable and
# greppable in a single place.

#: The spread of the seeded generation-0 genes, as a fraction of the range of
#: every gene.  ``0.15`` means "the genes of the seed population are drawn
#: around the default value with a standard deviation of 15 % of the range".
DEFAULT_BOOTSTRAP_GENE_SPREAD: float = 0.15

#: The share of the offspring of one generation that is produced by the
#: linkage block crossover; the rest comes from the CMA-ES.
DEFAULT_GA_OFFSPRING_FRACTION: float = 0.5

#: The probability that the genome of a CMA-ES offspring is repaired by the
#: repair mutation (the CMA-ES offspring keep the crossover genome otherwise).
DEFAULT_CMA_MUTATION_RATE: float = 0.35

#: The standard deviation of the fold angle jitter of the repair mutation,
#: in degrees.
DEFAULT_MUTATION_ANGLE_SIGMA_DEG: float = 6.0

#: The standard deviation of the crease line translation of the repair
#: mutation, in normalised paper units (the sheet spans ``[0, 1]``).
DEFAULT_MUTATION_CREASE_SHIFT: float = 0.02

#: The probability that the repair mutation flips the sense (mountain /
#: valley) of a fold.
DEFAULT_FOLD_FLIP_RATE: float = 0.08

#: The probability that the repair mutation changes the symmetry of a fold.
DEFAULT_SYMMETRY_CHANGE_RATE: float = 0.08

#: The grip penalty of a genome that could not be folded at all.  It is large
#: enough to keep such a candidate out of every ranking.
DEFAULT_BROKEN_GENOME_PENALTY: float = 1.0e6

#: The largest share of a population the elite block of the selected
#: population statistic may take.  The block is what the statistic keeps
#: alive; the rest of the population is new blood, so the search can never
#: freeze.
DEFAULT_STAT_ELITE_MAX_FRACTION: float = 0.9

#: The number of generations the live progress curve keeps.
DEFAULT_PROGRESS_HISTORY_LIMIT: int = 500

#: The initial step size of the CMA-ES, expressed as a fraction of the range
#: of a gene (the strategy works in the normalised unit box ``[0, 1]^n``).
DEFAULT_CMA_SIGMA0: float = 0.15


# --------------------------------------------------------------------------
# Objective and statistic selectors
# --------------------------------------------------------------------------
#: Maximise the horizontal flight distance (metres).
OBJECTIVE_DISTANCE: str = "distance"

#: Maximise the hang time (seconds).
OBJECTIVE_AIRTIME: str = "airtime"

#: The two mutually exclusive objectives of the engine.
OBJECTIVES: Tuple[str, ...] = (OBJECTIVE_DISTANCE, OBJECTIVE_AIRTIME)

#: Population mean of the objective metric.
STAT_MODE_MEAN: str = "mean"

#: Population median of the objective metric.
STAT_MODE_MEDIAN: str = "median"

#: Weighted blend of the population median and mean (the default).
STAT_MODE_HYBRID: str = "hybrid_median_mean"

#: The best individual of the population.
STAT_MODE_BEST: str = "best"

#: Every selectable fitness statistic.
STAT_MODES: Tuple[str, ...] = (
    STAT_MODE_MEAN,
    STAT_MODE_MEDIAN,
    STAT_MODE_HYBRID,
    STAT_MODE_BEST,
)


# --------------------------------------------------------------------------
# The YAML subset reader
# --------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment that is not inside a quoted string."""
    out: List[str] = []
    quote: Optional[str] = None
    for index, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out)


def _parse_scalar(text: str) -> Any:
    """Convert a YAML scalar token into a Python value."""
    token = text.strip()
    if not token:
        return None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    lowered = token.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~"):
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        value = float(token)
    except ValueError:
        return token
    if not math.isfinite(value):
        return token
    return value


def _split_flow(body: str) -> List[str]:
    """Split a flow collection body on the top level commas."""
    parts: List[str] = []
    depth = 0
    quote: Optional[str] = None
    current: List[str] = []
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [part.strip() for part in parts if part.strip()]


def _parse_flow(token: str) -> Any:
    """Parse an inline ``[a, b]`` / ``{a: 1}`` collection."""
    token = token.strip()
    if token.startswith("[") and token.endswith("]"):
        return [_parse_flow(part) for part in _split_flow(token[1:-1])]
    if token.startswith("{") and token.endswith("}"):
        mapping: Dict[str, Any] = {}
        for part in _split_flow(token[1:-1]):
            key, sep, value = part.partition(":")
            if not sep:
                raise ValueError(f"malformed flow mapping entry: {part!r}")
            mapping[str(_parse_scalar(key))] = _parse_flow(value)
        return mapping
    return _parse_scalar(token)


@dataclass
class _Line:
    """One significant line of the YAML document."""

    indent: int
    text: str
    content_indent: int


def _significant_lines(text: str) -> List[_Line]:
    """Collect the significant (non blank, non comment) lines of a document."""
    lines: List[_Line] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" \t"))
        body = stripped.strip()
        content_indent = indent
        if body.startswith("-"):
            rest = body[1:]
            offset = len(rest) - len(rest.lstrip(" "))
            content_indent = indent + 1 + offset
            body = rest.strip()
            if not body:
                body = ""
        lines.append(_Line(indent=indent, text=body, content_indent=content_indent))
    return lines


def _parse_block(lines: Sequence[_Line], index: int, indent: int) -> Tuple[Any, int]:
    """Parse one block (mapping or sequence) and return it with the next index."""
    if index >= len(lines):
        return None, index
    if lines[index].text.startswith("-"):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: Sequence[_Line], index: int, indent: int) -> Tuple[Dict[str, Any], int]:
    mapping: Dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise ValueError(f"unexpected indentation at {line.text!r}")
        if line.text.startswith("-"):
            break
        key, sep, rest = line.text.partition(":")
        if not sep:
            raise ValueError(f"malformed mapping entry: {line.text!r}")
        key = str(_parse_scalar(key))
        rest = rest.strip()
        index += 1
        if rest:
            mapping[key] = _parse_flow(rest)
            continue
        if index < len(lines) and lines[index].indent > indent:
            value, index = _parse_block(lines, index, lines[index].indent)
            mapping[key] = value
        else:
            mapping[key] = None
    return mapping, index


def _parse_sequence(lines: Sequence[_Line], index: int, indent: int) -> Tuple[List[Any], int]:
    items: List[Any] = []
    while index < len(lines):
        line = lines[index]
        if line.indent != indent or not line.text.startswith("-"):
            break
        body = line.text[1:].strip()
        item_indent = line.content_indent
        index += 1
        if body:
            if ":" in body and not body.startswith(("[", "{")):
                # A mapping that starts on the dash line: re-read it as a block.
                key, sep, rest = body.partition(":")
                if sep:
                    synthetic = [_Line(indent=item_indent, text=body, content_indent=item_indent)]
                    while index < len(lines) and lines[index].indent > indent:
                        synthetic.append(lines[index])
                        index += 1
                    value, consumed = _parse_mapping(synthetic, 0, item_indent)
                    if consumed < len(synthetic):
                        raise ValueError(f"malformed sequence mapping entry: {body!r}")
                    items.append(value)
                    continue
            items.append(_parse_flow(body))
            continue
        if index < len(lines) and lines[index].indent > indent:
            value, index = _parse_block(lines, index, lines[index].indent)
            items.append(value)
        else:
            items.append(None)
    return items, index


def parse_yaml(text: str) -> Any:
    """Parse the YAML subset described in the module docstring."""
    lines = _significant_lines(text)
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0].indent)
    if index != len(lines):
        raise ValueError("could not parse the whole YAML document")
    return value


def load_yaml_mapping(path: str) -> Dict[str, Any]:
    """Read a YAML (subset) file and return its top level mapping."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    data = parse_yaml(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the configuration file must contain a mapping")
    return data


# --------------------------------------------------------------------------
# The configuration object
# --------------------------------------------------------------------------
@dataclass
class GAConfig:
    """Every knob of the research engine.

    The object is the *only* source of the trial counts: the YAML file, the
    command line, the runtime API and a checkpoint resume all end up in a
    :class:`GAConfig`, and the search logic never sees a bare number.
    """

    # --- objective and fitness statistic --------------------------------
    objective: str = OBJECTIVE_DISTANCE
    stat_mode: str = STAT_MODE_HYBRID
    #: Weights of the two statistics blended by ``hybrid_median_mean``.
    stat_weights: Dict[str, float] = field(default_factory=lambda: {"mean": 0.5, "median": 0.5})

    # --- budgets --------------------------------------------------------
    #: Total number of flight evaluations the whole run may spend.
    max_trials: int = DEFAULT_MAX_TRIALS
    #: Number of elites kept per island and stored in the hall of fame.
    top_k: int = DEFAULT_TOP_K
    #: Number of individuals promoted into the surrogate tier.
    tier1_trials: int = DEFAULT_TIER1_TRIALS
    #: Number of individuals promoted into the short reference 6-DoF tier.
    tier2_trials: int = DEFAULT_TIER2_TRIALS
    #: Number of individuals promoted into the turbulent reference 6-DoF tier.
    tier3_trials: int = DEFAULT_TIER3_TRIALS

    # --- population ------------------------------------------------------
    seed: int = 7
    generations: int = 20
    islands: int = 4
    population_size: int = 16
    max_fold_steps: int = 8
    max_fold_steps_limit: int = 14
    #: The spread of the seeded generation-0 genes (a fraction of the range
    #: of every gene).
    bootstrap_gene_spread: float = DEFAULT_BOOTSTRAP_GENE_SPREAD
    #: The share of the offspring produced by the linkage block crossover.
    ga_offspring_fraction: float = DEFAULT_GA_OFFSPRING_FRACTION
    #: The probability that a CMA-ES offspring is repaired by the mutation.
    cma_mutation_rate: float = DEFAULT_CMA_MUTATION_RATE
    #: The fold angle jitter of the repair mutation, in degrees.
    mutation_angle_sigma_deg: float = DEFAULT_MUTATION_ANGLE_SIGMA_DEG
    #: The crease line translation of the repair mutation (paper units).
    mutation_crease_shift: float = DEFAULT_MUTATION_CREASE_SHIFT
    #: The probability that the repair mutation flips the sense of a fold.
    fold_flip_rate: float = DEFAULT_FOLD_FLIP_RATE
    #: The probability that the repair mutation changes the symmetry of a fold.
    symmetry_change_rate: float = DEFAULT_SYMMETRY_CHANGE_RATE

    # --- physics fidelity -------------------------------------------------
    #: Duration of the intermediate tier; ``0`` selects the objective default.
    tier2_duration_s: float = 0.0
    #: Duration of the full fidelity tier; ``0`` selects the objective default.
    tier3_duration_s: float = 0.0
    dt_max: float = 2.0e-2
    paper_gsm: float = 100.0
    launch_height_m: float = 1.2
    #: Monte-Carlo draws per individual in the full fidelity tier.
    tier3_monte_carlo_samples: int = 1
    #: Worker processes of the batch evaluator (``0`` -> automatic).
    processes: int = 0

    # --- fitness ------------------------------------------------------------
    #: The grip penalty of a genome that could not be folded at all.
    broken_genome_penalty: float = DEFAULT_BROKEN_GENOME_PENALTY
    #: The largest share of a population the elite block of the selected
    #: population statistic may take (the rest of the population is new
    #: blood, so the search can never freeze).
    stat_elite_max_fraction: float = DEFAULT_STAT_ELITE_MAX_FRACTION
    #: The number of generations the live progress curve keeps.
    progress_history_limit: int = DEFAULT_PROGRESS_HISTORY_LIMIT

    # --- turbulence and throw jitter (full fidelity tier) -----------------
    tier3_wind_speed: float = 2.0
    tier3_gust_intensity: float = 0.25
    tier3_thermal_strength: float = 0.5
    tier3_turbulence_scale: float = 0.5
    jitter_speed_mps: float = 0.5
    jitter_elevation_deg: float = 2.0
    jitter_pitch_rate_radps: float = 1.0
    jitter_roll_deg: float = 3.0
    jitter_yaw_deg: float = 3.0

    # --- autonomous meta control ------------------------------------------
    stagnation_generations: int = 10
    migration_interval: int = 2
    migration_elites: int = 2
    micro_flaps: bool = False

    # --- CMA-ES ------------------------------------------------------------
    #: The initial step size of the CMA-ES, as a fraction of the range of a
    #: gene (the strategy searches the normalised unit box).
    cma_sigma0: float = DEFAULT_CMA_SIGMA0
    cma_lambda: int = 0  # 0 -> the standard 4 + 3 ln(n) rule
    cma_mu: int = 0  # 0 -> lambda / 2

    # --- seeding of the initial population ---------------------------------
    archetype_seed_count: int = 4
    random_seed_count: int = 12
    genome_strategy: str = "families"

    # --- grip filter thresholds (kept configurable end to end) --------------
    grip_thresholds: Dict[str, float] = field(default_factory=dict)

    # --- input / output ------------------------------------------------------
    out_dir: str = "runs/ga_research"
    checkpoint_dir: str = ""
    journal_path: str = ""
    checkpoint_interval: int = 1
    resume_from: str = ""

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the configuration."""
        return {
            spec.name: getattr(self, spec.name)
            for spec in fields(self)
        }

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "GAConfig":
        """Build a configuration from a (possibly partial) mapping."""
        data = dict(payload or {})
        known = {spec.name for spec in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(
                f"unknown research configuration key(s): {', '.join(unknown)}"
            )
        kwargs: Dict[str, Any] = {}
        for spec in fields(cls):
            if spec.name not in data:
                continue
            value = data[spec.name]
            if spec.type in ("int", int):
                kwargs[spec.name] = int(value)
            elif spec.type in ("float", float):
                kwargs[spec.name] = float(value)
            elif spec.type in ("bool", bool):
                kwargs[spec.name] = bool(value)
            elif spec.name in ("stat_weights", "grip_thresholds"):
                kwargs[spec.name] = {str(k): float(v) for k, v in dict(value or {}).items()}
            else:
                kwargs[spec.name] = value
        return cls(**kwargs)

    def merged(self, overrides: Optional[Dict[str, Any]]) -> "GAConfig":
        """Return a copy with ``overrides`` applied on top of this configuration."""
        if not overrides:
            return replace(self)
        data = self.to_dict()
        for key, value in overrides.items():
            if value is None:
                continue
            data[key] = value
        return GAConfig.from_dict(data)

    # ------------------------------------------------------------------
    def validate(self) -> "GAConfig":
        """Check the configuration and return it (raises ``ValueError``)."""
        if self.objective not in OBJECTIVES:
            raise ValueError(
                f"unknown objective '{self.objective}'; expected one of {', '.join(OBJECTIVES)}"
            )
        if self.stat_mode not in STAT_MODES:
            raise ValueError(
                f"unknown stat mode '{self.stat_mode}'; expected one of {', '.join(STAT_MODES)}"
            )
        if self.max_trials < 1:
            raise ValueError("'max_trials' must be at least 1")
        if self.top_k < 1:
            raise ValueError("'top_k' must be at least 1")
        for name in ("tier1_trials", "tier2_trials", "tier3_trials"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"'{name}' must not be negative")
        if self.islands < 1:
            raise ValueError("'islands' must be at least 1")
        if self.generations < 1:
            raise ValueError("'generations' must be at least 1")
        if self.population_size < 2:
            raise ValueError("'population_size' must be at least 2")
        if self.max_fold_steps < 1:
            raise ValueError("'max_fold_steps' must be at least 1")
        if self.max_fold_steps_limit < self.max_fold_steps:
            raise ValueError("'max_fold_steps_limit' must not be below 'max_fold_steps'")
        if self.stagnation_generations < 1:
            raise ValueError("'stagnation_generations' must be at least 1")
        if self.migration_interval < 1:
            raise ValueError("'migration_interval' must be at least 1")
        if self.migration_elites < 0:
            raise ValueError("'migration_elites' must not be negative")
        if self.tier3_monte_carlo_samples < 1:
            raise ValueError("'tier3_monte_carlo_samples' must be at least 1")
        if self.processes < 0:
            raise ValueError("'processes' must not be negative")
        if not (0.0 <= self.ga_offspring_fraction <= 1.0):
            raise ValueError("'ga_offspring_fraction' must be within [0, 1]")
        if not (0.0 <= self.cma_mutation_rate <= 1.0):
            raise ValueError("'cma_mutation_rate' must be within [0, 1]")
        if not (0.0 <= self.fold_flip_rate <= 1.0):
            raise ValueError("'fold_flip_rate' must be within [0, 1]")
        if not (0.0 <= self.symmetry_change_rate <= 1.0):
            raise ValueError("'symmetry_change_rate' must be within [0, 1]")
        if self.bootstrap_gene_spread <= 0.0:
            raise ValueError("'bootstrap_gene_spread' must be positive")
        if self.mutation_angle_sigma_deg < 0.0:
            raise ValueError("'mutation_angle_sigma_deg' must not be negative")
        if self.mutation_crease_shift < 0.0:
            raise ValueError("'mutation_crease_shift' must not be negative")
        if self.broken_genome_penalty <= 0.0:
            raise ValueError("'broken_genome_penalty' must be positive")
        if not (0.0 < self.stat_elite_max_fraction <= 1.0):
            raise ValueError("'stat_elite_max_fraction' must be within (0, 1]")
        if self.progress_history_limit < 1:
            raise ValueError("'progress_history_limit' must be at least 1")
        if not (0.0 < self.cma_sigma0 <= 1.0):
            raise ValueError("'cma_sigma0' must be within (0, 1]")
        if not (0.0 < self.paper_gsm <= 1000.0):
            raise ValueError("'paper_gsm' must be within (0, 1000]")
        if self.launch_height_m <= 0.0:
            raise ValueError("'launch_height_m' must be positive")
        if self.dt_max <= 0.0:
            raise ValueError("'dt_max' must be positive")
        return self

    # ------------------------------------------------------------------
    def resolved_checkpoint_dir(self) -> str:
        """The directory the checkpoints are written to."""
        return self.checkpoint_dir or os.path.join(self.out_dir, "checkpoints")

    def resolved_journal_path(self) -> str:
        """The path of the decision journal."""
        return self.journal_path or os.path.join(self.out_dir, "research_journal.jsonl")


def config_from_mapping(payload: Optional[Dict[str, Any]]) -> GAConfig:
    """Build a validated :class:`GAConfig` from a mapping."""
    return GAConfig.from_dict(payload).validate()


def load_config_file(path: str, overrides: Optional[Dict[str, Any]] = None) -> GAConfig:
    """Read a YAML configuration file and apply ``overrides`` on top of it."""
    mapping = load_yaml_mapping(path)
    base = GAConfig.from_dict(mapping).validate()
    return base.merged(overrides).validate()


def _dump_json(payload: Any) -> str:
    """Serialise a payload as pretty printed JSON (used by the CLI echo)."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)
