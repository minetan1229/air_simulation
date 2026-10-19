"""Ad-hoc investigation scripts.

These modules are one-off diagnostics used while tuning the flight model.
They are **not** part of the verification protocol, they are excluded from
``ruff check .`` (``ruff.toml`` -> ``exclude``) and they are never collected
by pytest (``pytest.ini`` -> ``norecursedirs``).  Several of them execute a
full 35 s simulation at import time, so importing them from a test module is
never safe.

Run them from the repository root, e.g.::

    python tools/experiments/analyze_glide_trim.py

Because they rely on ``sys.path.insert(0, '.')`` the current working directory
must be the repository root.
"""
