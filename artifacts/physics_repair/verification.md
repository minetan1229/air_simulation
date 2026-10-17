# Physics repair verification

Generated UTC: 2026-10-17T04:28:49.360521+00:00

GPU trajectories: unverified. CPU rigid-body simulation with optional stress backend reported in logs.

Fixed-step convergence: not_converged (relative tolerance 1%).

Adaptive dt-cap convergence: converged. Any step rollback fails convergence validity.

Full-flight targets are verification goals, not parameter-fitting constraints.

Tool glide criterion is stricter than engine diagnostic: speed1.5..8 m/s, |alpha|<=10deg, |pitch rate|<=0.5rad/s, sink0..2m/s and >=1 continuous second. Both threshold sets are saved.

| Case | Mode | Time s | Max height m | Stable glide s | Target |
|---|---|---:|---:|---:|---|
| unite_reference | full_flight | 15.280 | 15.661 | 0.000 | unmet |
| unite_reference | full_flight | 15.190 | 15.583 | 0.000 | unmet |
| unite_reference | full_flight | 15.150 | 15.564 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 15.180 | 15.563 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 15.170 | 15.563 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 15.150 | 15.563 | 0.000 | unmet |
| unite_31s | full_flight | 45.440 | 15.698 | 0.960 | unmet |
| unite_31s | apex_glide | 60.000 | 17.391 | 0.920 | unmet |
| wind_voyager_ur_29_5s | full_flight | 41.520 | 15.634 | 0.360 | unmet |
| wind_voyager_ur_29_5s | apex_glide | 60.000 | 16.547 | 1.080 | unmet |
| toda_29_2s | full_flight | 35.620 | 12.455 | 7.800 | unmet |
| toda_29_2s | apex_glide | 60.000 | 16.194 | 1.000 | unmet |
| blackburn_27_6s | full_flight | 16.760 | 17.077 | 0.000 | unmet |
| blackburn_27_6s | apex_glide | 31.980 | 11.800 | 31.880 | unmet |
