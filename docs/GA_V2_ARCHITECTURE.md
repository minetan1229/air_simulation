# GA v2: the ultra large genetic search with the multi GPU DNS/FSI

This document describes the second generation of the genetic search: the
hybrid fold genome, the four stage evaluation pipeline, the hardware
scaling profiles and the infrastructure that makes a run survive months of
continuous operation and move from a laptop to a GPU server without losing
its state.

The code lives in `physics/ga2/`.

```
physics/ga2/
  scaling.py    the three hardware tiers and the budget of every one
  genome.py     the variable length hybrid fold programme (3 to 8 folds)
  stage0.py     the geometry and topology filter
  stage1.py     the batched 6-DoF screen with a captured CUDA graph
  stage2.py     the quasi static structural screen
  stage3.py     the multi GPU task parallel driver of the flow solver
  lbm.py        the D3Q19 lattice Boltzmann solver with the immersed boundary
  archive.py    the MAP-Elites archive and the Pareto front
  surrogate.py  the accumulated flow database and the correction model
  vram.py       the fixed VRAM pool of the generation loop
  checkpoint.py the atomic generation checkpoint and the hot migration
  runner.py     the generation loop that ties the stages together
```

## 1. One code base, three budgets

The physics must not depend on the hardware, otherwise a run on the laptop
says nothing about the run on the server.  Only the *budget* changes, and
the budget is described by one object: `ScalingProfile`
(`physics/ga2/scaling.py`).

| parameter | `small_long` (1x 8 GB) | `workstation` (1-2x 24-48 GB) | `cluster_20m` (4-8x 48-94 GB) |
|---|---|---|---|
| population per generation | 10 000 | 30 000 | 100 000 |
| target generations | 2 000 - 3 000 | 2 500 - 3 000 | 3 000 - 5 000 |
| stage 1 chunk | 5 000 x 2 | 15 000 x 2 | 25 000 x 4 |
| stage 2 pass slots | 500 | 1 500 | 5 000 |
| stage 3 pass slots | 10 | 25 | 80 - 100 |
| stage 3 lattice | 5 - 8 M cells (WMLES) | 25 M cells (LES + FSI) | 100 - 150 M cells (local DNS + FSI) |
| multi GPU strategy | one GPU, sequential | one GPU per individual | one GPU per individual |

The tier is detected from the CUDA devices of the host: three or more GPUs
always mean the cluster tier, two only do when they are server class, and a
single GPU is a workstation as soon as it holds at least 12 GiB.  The
profile can also be pinned explicitly, so that a run stays reproducible on a
machine with a different GPU.

`ScalingProfile.max_grid_cells()` caps the lattice at what the VRAM can
hold (19 populations in two buffers, four bytes per lattice value), so a
small card gets a smaller grid instead of an out of memory crash.

## 2. The hybrid fold genome

`physics/ga2/genome.py` holds a programme of 3 to 8 *hybrid fold genes*.
A gene carries:

| field | meaning |
|---|---|
| `enabled` | whether the fold is part of the programme at all |
| `origin`, `crease_angle_deg`, `length_ratio` | where the crease starts, which way it runs and how long it is |
| `fold_angle_deg` | the fold angle, -180 to +180 degrees, the sign carries the mountain and valley sense |
| `target_layers` | the layers the crease acts on: empty cuts the whole sheet, otherwise the groups the earlier folds created |
| `creates_pair` | whether the fold splits its moving half into a left and a right group |
| `creates_group`, `join_group` | whether the fold creates a new group or joins an existing one |
| `symmetry` | the left and right symmetry constraint |
| `mountain` | the sense of the fold, kept separately so that a zero degree fold keeps its sense |

The layer reference is what makes the nose pleats, the fuselage ribs, the
winglets, the dihedral and the trailing edge trim expressible: a fold can
act on a layer that is already folded over.

The conversion to and from the fold programme of `physics.origami_genome`
is lossless: the classic dart, the Nakamura lock and the Sky King come out
of the round trip with the same planform area, the same span and the same
mass (`tests/test_ga2_genome.py`).

The warm start follows the fractions of the specification: 2 % of the first
population are the known good designs, 8 % are mutations of them and 90 %
are random programmes.

## 3. The four stage pipeline

### Stage 0 - the geometry filter (`stage0.py`)

The cheap 2D screen runs over the whole population: the fold count, the
crease geometry, the planform area of the largest remaining flap, the
number of folds that act on the base sheet and the stack depth.  Only the
survivors get the exact 3D fold, where the self penetration, the tear
risk, the wing area, the span and the CG against the CP are checked.

### Stage 1 - the batched 6-DoF screen (`stage1.py`)

The screen flies the population in one batch.  Three rules apply:

* **no host round trip** - the wind of the indoor case and the perturbation
  of the throw ensemble are drawn on the device, the state lives in device
  memory and the summary is reduced on the device.  There is no `.get()`
  inside the integration loop.
* **no data dependent control flow** - the number of steps is fixed, the
  cases that landed or crashed are masked instead of removed.
* **one captured step** - the whole step (the RK4 sweep, the contact
  detection, the summary accumulation) is captured into a CUDA graph and
  replayed.  On the reference laptop the graph is about eighty times faster
  than the eager loop, because a step is about forty kernel launches and the
  launch overhead dominates the arithmetic.

The graph capture needed three things:

* **no host to device copy inside the capture** - the identity quaternion of
  `physics/gpu_dynamics.py` is built on the device now (a host side array
  would be an implicit copy, and a copy is forbidden during a capture).  The
  numbers are unchanged.
* **no cuBLAS call inside the capture** - CuPy lowers `einsum`, `matmul`
  and `tensordot` to cuBLAS, and a cuBLAS call cannot be captured.  The
  *shared* kernel is **not** changed: the rotation of the capture is
  contracted element by element instead, and the two forms differ by at most
  one unit in the last place.  The chaotic 4g dart of the project
  amplifies that difference into the existing parity tests, so the shared
  physics wins over the speed: the elementwise form is used by the captured
  graph alone, and the ranking of the search is decided by the reference
  re-flight of the survivors, which uses the shared kernel.
* **the relaxed capture mode** - `begin_capture(mode=2)` is the mode that
  tolerates the asynchronous copies CuPy emits.

Every accumulator of the step is updated **in place**.  A graph replays the
kernels it captured, so a summary that was bound to a new array inside the
step would only ever hold the value of the capture: the distance of a flight
would be the distance of one step.

The screen reports the coverage of the flight: the folded airframes are
stiff, so the adaptive step of the reference integrator is small and a
fixed number of steps covers the first part of the flight.  The survivors
are therefore flown again with the reference integrator (the adaptive step,
the exact geometry) before the ranking is decided: **the screen filters,
the reference decides**.

The captured graph is released before the stages that allocate a lot of
device memory.  A graph keeps the memory of the temporaries it captured
alive, and the next replay would read the wrong addresses if that memory
was handed out again; releasing it costs one capture per generation.

### Stage 2 - the quasi static structural screen (`stage2.py`)

The stage answers three questions about the best individuals of stage 1:

* does the root section of the folded body stay below the allowable stress
  of the paper?  The moment and the section modulus are the ones of the
  shared structural model, so the number is the number the flight path
  reports for the same load case.
* does the compressed ply at the root buckle?  The critical stress of the
  plate is compared with the root stress.
* does the elastic deformation destroy the pitch stability?  The skin of a
  paper wing is a tensioned membrane - the flight load pulls it taut and
  the tension stiffens the panel - so the deflection is that of a tensioned
  plate, and the twist it produces eats the static margin.

The classic dart passes with a margin of about eighteen, a tip deflection
of six millimetres and an unchanged static margin.

### Stage 3 - the non stationary flow solver (`stage3.py`, `lbm.py`)

The solver is a D3Q19 lattice Boltzmann code with an immersed boundary
representation of the folded wing, a modal structural model (a spanwise
bending mode and a torsion mode) and the coupling between the two: the
momentum exchange at the boundary is the load of the structure, and the
velocity of the structure is the moving wall of the bounce back.  The
momentum budget between the inflow plane and the outflow plane gives the
aerodynamic force, the pitch mode gives the damping and the flutter
margin.

The multi GPU strategy is the task parallel one of the specification: one
independent process per GPU, each of them seeing exactly one device
(`CUDA_VISIBLE_DEVICES`), pulling the individuals from one shared queue.
There is no MPI, no peer access and no domain decomposition: the flow
domain of an individual is never split.  When only one GPU is present, or
when the multiprocessing is not available, the same solver runs in process.

*Known limitation*: the local solver resolves the shape of the flow, not
the absolute force of the aeroplane.  The coefficients are clamped into the
physical range a paper wing can reach, and stage 3 is used as a refinement
of the ranking and as the data source of the surrogate, not as the
reference of the physics.  The 6-DoF path of the project remains the
reference.

## 4. The infrastructure

### The archive (`archive.py`)

The MAP-Elites archive keeps the best individual per cell of the grid
"wing loading x aspect ratio x fold count".  The insertion is a dictionary
lookup, and the eviction only sorts when the archive is over its capacity,
so the maintenance of a generation stays far below one second even after
thousands of generations.  The Pareto front keeps the non dominated
individuals of the three objectives (distance, lift to drag ratio, root
stress).

### The surrogate (`surrogate.py`)

The database (SQLite) accumulates the aerodynamic answer of stage 3
together with the screening value of the same individual.  Every ten
generations a ridge regression (NumPy only) is trained on the accumulated
samples and predicts the correction of the screening coefficients.

### The VRAM pool (`vram.py`)

The pool owns one flat buffer that is allocated once, before the generation
loop starts, and hands out named workspaces as views into it.  The loop
itself allocates nothing.

### The checkpoint (`checkpoint.py`)

Every generation is written atomically: a temporary file in the same
directory, `flush`, `fsync` and `os.replace`, so a crash never leaves a
half written checkpoint.  The state carries the islands, the archive, the
Pareto front, the best individual, the statistics and the state of the
random generator.

The hot migration of the specification is implemented by `migrate_state`: a
checkpoint written on the laptop is loaded on the server, the profile is
replaced by the one of the server, the archive and the elites are kept and
the island populations are padded to the new population size.  The run then
continues with 100 000 individuals and a 100 million cell lattice.

## 5. Running the search

From the command line:

```
python tools/run_ga_v2.py --tier small_long --population 200 --generations 3 \
    --out runs/ga_v2_small
python tools/run_ga_v2.py --tier cluster_20m --generations 1000 \
    --resume runs/ga_v2_small/generation_000003.json --out runs/ga_v2_small
```

From Python:

```python
from physics.ga2.runner import EvolutionRunner
from physics.ga2.scaling import ScalingProfile, detect_profile

profile = detect_profile()          # or ScalingProfile.cluster_20m(8, 80 * 2**30)
runner = EvolutionRunner(profile=profile, out_dir="runs/ga2")
for report in runner.run(100):
    print(report.to_dict())
```

A run that is interrupted is continued with

```python
runner = EvolutionRunner(profile=profile, out_dir="runs/ga2")
runner.load("runs/ga2/generation_000042.json")
runner.run(1)
```

## 6. The throw model

The search flies its designs with a lofted indoor throw (8 m/s at 25
degrees from 1.5 m).  The flat and fast throw of the default launch hits
the floor before the airframe can glide, which would make every design look
like a crash and would leave the search without a signal.  The throw is a
property of the search harness, not of the physics: `flight_sim` and
`gpu_dynamics` are untouched.
