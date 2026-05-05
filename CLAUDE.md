# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Scripts for tracking ITk Pixel Module production for the ATLAS detector (CERN). Two main deliverables:
1. PNG plots + HTML website (`moduleProductionPlots.py` + `publish.sh`)
2. HTML inventory table injected into `index.html` (`make_inventory.py`)

## Scripts

**`moduleProductionPlots.py`** — Generates all PNG plots into `plots/`:
```bash
python3 moduleProductionPlots.py
```

**`make_inventory.py`** — Generates component inventory tables (Unasm. Flex / Unasm. BM / per-stage module counts) and per-cluster location summary tables, injects into `index.html`. Also writes CSVs to `inventory/`:
```bash
python3 make_inventory.py
```

**`publish.sh`** — Syncs `index.html` and `plots/` to `kdipetri@lxplus.cern.ch:/eos/user/k/kdipetri/www/moduleProduction`. Requires valid Kerberos ticket (`kinit kdipetri@CERN.CH`).

**`modules.py`** — Streamlit dashboard (separate pipeline). Requires `dashboard.apps.utils.*` from `module-qc-statistical-tools` repo on `PYTHONPATH`.

## Data Files

Parquets under `data/`:
- `data/df_pixel_modules.parquet` — assembled module data
- `data/df_pixel_baremodules.parquet` — bare module data (includes `BARE_MODULE.batch_number`)
- `data/df_pixel_flex.parquet` — flex PCB data

Config under `metadata/`:
- `cluster_maps_MODULE.json` — maps institution codes → cluster codes (e.g. `KEK` → `PIXEL_MODULE_JAPAN`)
- `cern_japan_config.json` — defines the `PIXEL_MODULE_CERN_JAPAN` virtual cluster
- `excluded_bms.json` — individual BM serial numbers excluded from unassembled BM count (stragglers, pre-production test registrations, broken units)
- `excluded_bm_batches.json` — full BM batches excluded from all counts (pre-production batches)

Parquet files carry a `timestamp` in schema metadata used as the "last update" date.

## Key data flow in `moduleProductionPlots.py`

1. `load_data()` — reads the three parquets with column pruning (`MODULE_COLS`, `BM_COLS`, `FLEX_COLS`)
2. `filter_components()` — filters to production modules (SN regex, BOM version, not pre-production); assigns `cluster_code` to modules via `MODULE.institution_code`, and to bare modules / flexes via `assign_clusters_from_locations()` (walks location history for BAREMODULERECEPTION/MODULE/INIT/MODULE/ASSEMBLY events)
3. `apply_cern_japan_cluster()` — reclassifies Japan modules shipped to CERN before `MODULE/COMPLETE` into the `PIXEL_MODULE_CERN_JAPAN` virtual cluster
4. Per-cluster loop generates all plots into `plots/`

## Key data flow in `make_inventory.py`

The inventory tallies component counts per cluster. Important logic:

- `_current_stage()` — determines a module's current stage by looking at the **chronologically last stage** in its history that matches `STAGE_RANK`. (Previously used max-rank which counted historical UNHAPPY/GRAVEYARD even if later resolved.)
- Unassembled BM counting: uses `BARE_MODULE.currentLocation_code` for cluster assignment, filters by `UNASM_BM_STAGES` and exclusion lists.
- Unassembled flex counting: two cases — base (COMPLETE/PCB_RECEPTION_MODULE_SITE) and PFM (PCB_READY_FOR_MODULE at assembly sites). France assembly sites are restricted to `{IRFU, LPNHE}` via `CLUSTER_FLEX_ASSEMBLY_SITES`.

## make_inventory.py — Component Inventory Logic

Produces a per-cluster tally of: Unasm. Flex | Unasm. BM | ASSEMBLY | INITIAL_WARM | PARYLENE | OBWP | THERMAL_CYCLES | FINAL_QC | COMPLETE | UNHAPPY | GRAVEYARD | Total.

### Unassembled BM counting

- `hasParent_MODULE.isna()` (no parent module)
- Last stage in history is in `UNASM_BM_STAGES = {'BAREMODULERECEPTION', 'BAREMODULEASSEMBLY', 'MODULE/INIT'}`
- Not in `excluded_bms.json` or in a batch from `excluded_bm_batches.json`
- Cluster assigned by **currentLocation_code** (not history), guarded by `CLUSTER_BM_ASSEMBLY_SITES` (currently `{}` = all sites allowed)

### Unassembled flex counting — `_flex_current_stage` rule

`_flex_current_stage(stages)` returns the **chronologically last stage that is not `UNHAPPY`**. This mirrors the reference DB's `currentStage_code` field:
- UNHAPPY flexes where the last non-UNHAPPY stage is `COMPLETE` → counted as COMPLETE ✓
- Flexes ending with `PCB_QC` (went back for re-QC), `MODULE/NOTCONSIDEREDINYIELDS`, `PCB_RECEPTION` → excluded ✓

**Currently implemented (Japan-correct, France/UK incomplete):**
- `hasParent_MODULE.isna()` (strict no parent)
- `currentStage in {PCB_RECEPTION_MODULE_SITE, COMPLETE}` (no PCB_READY_FOR_MODULE)
- Not `PCB/GRAVEYARD` in stage history

**Reference target values and status (as of 2026-05-04):**
| Cluster | Unasm. Flex | Status |
|---|---|---|
| Japan | 280 | ✓ verified |
| France | 108 | ✗ currently 38 — needs PCB_READY_FOR_MODULE |
| UK | 353 | ✗ currently 209 — needs PCB_READY_FOR_MODULE |
| Others | not yet verified | — |

### France/UK flex fix — in progress

**France (target 108 = 38 + 70):**
- The 70 missing come from `PCB_READY_FOR_MODULE` flexes
- Rule: at `{IRFU, LPNHE}` only (not IJCLAB — IJCLAB is module QC/testing site, not assembly), `hasParent_MODULE.isna()`, not UNHAPPY
- IRFU: 23 (all non-UNHAPPY), LPNHE non-UNHAPPY no-parent: 47 → 70 ✓
- IJCLAB is excluded because French modules are assembled at IRFU/LPNHE and shipped to IJCLAB for electrical testing; IJCLAB `PCB_READY_FOR_MODULE` flexes are QC spares, not assembly-ready stock

**UK (target 353 = 209 + 144):**
- The 144 = all 142 no-parent `PCB_READY_FOR_MODULE` (EDI=87, GL=5, OX=50; all non-UNHAPPY) + 2 flexes whose parent modules are past `MODULE/WIREBONDING` (at `MODULE/PARYLENE_MASKING`) or in `MODULE/GRAVEYARD`
- Reference seems to include PCB_READY_FOR_MODULE flexes with parents if the parent module is past wirebonding / failed — possible data inconsistency in DB

**Needed implementation:**
1. Add `CLUSTER_FLEX_ASSEMBLY_SITES` dict (analogous to `CLUSTER_BM_ASSEMBLY_SITES`), defining per-cluster which institutions to accept for PCB_READY_FOR_MODULE. France = `{IRFU, LPNHE}`; others empty (all sites allowed).
2. Extend `flex_ready` to include `PCB_READY_FOR_MODULE` with: at assembly site, not UNHAPPY, no parent (OR parent past MODULE/WIREBONDING or in GRAVEYARD)
3. Verify all other clusters after France/UK are fixed.

## CERN-JAPAN cluster (`metadata/cern_japan_config.json`)

A virtual cluster for Japan-assembled modules shipped to CERN before completing QC. Classification is per-module using `MODULE.locations`:
- **Include**: Japan module with any location entry at a CERN institution where `stage != MODULE/COMPLETE` (and not in `completion_stages`)
- **Exclude**: modules shipped to CERN only after reaching `MODULE/COMPLETE` or `MODULE/FINAL_COLD`

Config keys: `cluster_code`, `source_cluster`, `cern_cluster`, `completion_stages`.

## Plots generated

All plots go to `plots/`. Per-cluster plots use suffix `_PIXEL_MODULE_<CLUSTER>.png`.

| File pattern | Function | Notes |
|---|---|---|
| `monthly_modules[_CLUSTER].png` | `create_monthly_production` | Bar chart, stages as colors in `STAGE_COLORS` |
| `cumulative_pipeline[_CLUSTER].png` | `_plot_cumulative_pipeline` / `create_cumulative_all_clusters` | Line chart; BM/Flex dotted |
| `stage_duration_summary[_short]_CLUSTER.png` | `create_stage_duration_summary` | Median ± IQR error bars |
| `batch_duration_CLUSTER.png` | `create_batch_duration_plot` | Median ± IQR per batch; fraction-completed right axis |
| `duration_STAGEA_STAGEB.png` | `create_stage_duration_histograms` | Overlaid KDE/histogram per cluster |

Global style: `savefig.dpi=300`, `savefig.bbox=tight`, seaborn-v0_8-whitegrid.

## Website (`index.html`)

Static HTML/CSS/JS. Three top-level modes:
- **Production Overview** — all-clusters monthly bar chart + cumulative pipeline
- **By Cluster** — cluster picker → tabs: *Number of Modules*, *Time to Completion*
- **Compare Clusters** — monthly production and stage duration histograms

Cluster buttons use `data-cluster` attributes for exact-match active state (avoids substring collision between `PIXEL_MODULE_CERN` and `PIXEL_MODULE_CERN_JAPAN`).

Inventory tables are injected between `<!-- INV-TABLE-START -->` / `<!-- INV-TABLE-END -->` (all-clusters stage summary), per-cluster location tables between `<!-- LOC-TABLE-{CLUSTER}-START -->` / `<!-- LOC-TABLE-{CLUSTER}-END -->`, and per-cluster batch tables between `<!-- BATCH-TABLE-{CLUSTER}-START -->` / `<!-- BATCH-TABLE-{CLUSTER}-END -->` comment markers. The "Location Summary" tab (renamed from "Stage Summary") shows all-clusters stage table when "All Quad Clusters" is selected, and per-cluster location tables when a specific cluster is selected.

## Domain concepts

- **Component hierarchy**: Flex PCB + Bare Module → assembled Module
- **Production types**: Quad (primary focus) vs. Triplet/Single
- **Stage pipeline**: `MODULE/INIT` → `MODULE/ASSEMBLY` → `MODULE/WIREBONDING` → `MODULE/INITIAL_WARM` → `MODULE/PARYLENE_MASKING` → `MODULE/PARYLENE_COATING` → `MODULE/PARYLENE_UNMASKING` → `MODULE/THERMAL_CYCLES` → `MODULE/FINAL_WARM` → `MODULE/FINAL_COLD`
- **Clusters**: Japan, CERN-Japan, France, Germany BMBF, Germany MPI, UK, USA, Italy, CERN
- **Japan note**: starting April 2026, batches of ~7 Japan modules systematically skipped `MODULE/ASSEMBLY` sign-off; cumulative plots fall back to `MODULE.cts` (creation timestamp) when assembly is absent
- **Institution codes in cluster_maps_MODULE.json**: Japan = `{HR, KEK}`, France = `{IJCLAB, IRFU, LPNHE}`, UK = `{EDI, GL, LIV, OX}`, CERN = `{CERN}`, etc.

## Active TODOs

1. ~~Fix France/UK flex counts~~ (completed in separate session) — France=108, UK=353 verified.
2. **Verify remaining cluster flex counts** after #1 — request reference exports from user for Germany/Italy/USA/CERN if needed
3. **Prune tmp files** (`tmp_french_bm.txt`, `tmp_germany_bmbf_bm.txt`, `japan_missing_assembly.txt`) — investigation artifacts, can be deleted
4. **Location Summary tables** added — "Stage Summary" renamed to "Location Summary"; per-cluster location tables injected (rows = current institution codes).
