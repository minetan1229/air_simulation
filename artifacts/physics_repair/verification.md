# Physics repair verification

Generated UTC: 2026-10-10T03:25:00.441612+00:00

GPU trajectories: unverified. CPU rigid-body simulation with optional stress backend reported in logs.

Fixed-step convergence: not_converged (relative tolerance 1%).

Adaptive dt-cap convergence: not_converged. Any step rollback fails convergence validity.

Full-flight targets are verification goals, not parameter-fitting constraints.

Tool glide criterion is stricter than engine diagnostic: speed1.5..8 m/s, |alpha|<=10deg, |pitch rate|<=0.5rad/s, sink0..2m/s and >=1 continuous second. Both threshold sets are saved.

| Case | Mode | Time s | Max height m | Stable glide s | Target |
|---|---|---:|---:|---:|---|
| unite_reference | full_flight | 1.700 | 10.800 | 0.000 | unmet |
| unite_reference | full_flight | 1.740 | 10.465 | 0.000 | unmet |
| unite_reference | full_flight | 1.755 | 10.416 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 1.720 | 10.414 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 1.720 | 10.414 | 0.000 | unmet |
| unite_reference_adaptive | full_flight | 1.745 | 10.414 | 0.000 | unmet |
| unite_31s | full_flight | 1.680 | 10.754 | 0.000 | unmet |
| unite_31s | apex_glide | 60.000 | 17.314 | 32.920 | unmet |
| wind_voyager_ur_29_5s | full_flight | 1.720 | 10.626 | 0.000 | unmet |
| wind_voyager_ur_29_5s | apex_glide | 60.000 | 16.500 | 47.160 | unmet |
| toda_29_2s | full_flight | 1.760 | 9.935 | 0.000 | unmet |
| toda_29_2s | apex_glide | 60.000 | 16.134 | 49.680 | unmet |
| blackburn_27_6s | full_flight | 2.240 | 10.695 | 0.000 | unmet |
| blackburn_27_6s | apex_glide | 19.780 | 11.800 | 19.760 | unmet |
| unite_V25_angle75_h1.8 | full_flight | 1.760 | 10.718 | 0.000 | unmet |
| unite_V25_angle75_h2 | full_flight | 1.760 | 10.918 | 0.000 | unmet |
| unite_V25_angle78_h1.8 | full_flight | 1.720 | 10.384 | 0.000 | unmet |
| unite_V25_angle78_h2 | full_flight | 1.720 | 10.584 | 0.000 | unmet |
| unite_V25_angle80_h1.8 | full_flight | 1.700 | 10.162 | 0.000 | unmet |
| unite_V25_angle80_h2 | full_flight | 1.700 | 10.362 | 0.000 | unmet |
| unite_V26_angle75_h1.8 | full_flight | 1.720 | 11.146 | 0.000 | unmet |
| unite_V26_angle75_h2 | full_flight | 1.740 | 11.346 | 0.000 | unmet |
| unite_V26_angle78_h1.8 | full_flight | 1.700 | 10.800 | 0.000 | unmet |
| unite_V26_angle78_h2 | full_flight | 1.700 | 10.999 | 0.000 | unmet |
| unite_V26_angle80_h1.8 | full_flight | 1.680 | 10.554 | 0.000 | unmet |
| unite_V26_angle80_h2 | full_flight | 1.680 | 10.754 | 0.000 | unmet |
| unite_V28_angle75_h1.8 | full_flight | 1.700 | 11.627 | 0.000 | unmet |
| unite_V28_angle75_h2 | full_flight | 1.700 | 11.827 | 0.000 | unmet |
| unite_V28_angle78_h1.8 | full_flight | 1.680 | 11.242 | 0.000 | unmet |
| unite_V28_angle78_h2 | full_flight | 1.680 | 11.442 | 0.000 | unmet |
| unite_V28_angle80_h1.8 | full_flight | 1.660 | 10.996 | 0.000 | unmet |
| unite_V28_angle80_h2 | full_flight | 1.660 | 11.196 | 0.000 | unmet |
