# Cooperative parking benchmark (RTX 3050 Laptop, WDDM)

Device memory before CUDA init: 373 MiB; after context init: 459 MiB (CUDA context ~87 MiB, stays while parked). Generated 2026-10-24 03:44.

| Data tensor | State moved (GiB) | First park (s, incl. pinned alloc) | Park (s, reused buffers) | Restore (s) | VRAM running (MiB) | VRAM parked (MiB) | Left above context (MiB) | Checksum ok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5 GiB | 0.56 | 0.54 | 0.26 | 0.08 | 1111 | 477 | 18 | True |
| 1.0 GiB | 1.06 | 0.87 | 0.30 | 0.12 | 1623 | 477 | 18 | True |
| 2.0 GiB | 2.06 | 1.74 | 0.42 | 0.25 | 2647 | 477 | 18 | True |

Device memory at the end of the benchmark (process still alive): 475 MiB.
