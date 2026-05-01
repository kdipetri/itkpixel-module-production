# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Scripts for tracking ITk Pixel Module production for the ATLAS detector (CERN). The main deliverable is a set of PNG plots and an HTML website to browse them.

## Scripts

**`moduleProductionPlots.py`** — Generates all PNG plots and saves them to `plots/`. Run from the repo root:
```bash
python3 moduleProductionPlots.py
```

**`publish.sh`** — Syncs `index.html` and `plots/` to `kdipetri@lxplus.cern.ch:/eos/user/k/kdipetri/www/moduleProduction` via SSH multiplexing (single auth prompt). Requires a valid Kerberos ticket (`kinit kdipetri@CERN.CH`) or SSH key on lxplus.

**`modules.py`** — Streamlit dashboard (separate from the plots pipeline). Requires `dashboard.apps.utils.*` from the `module-qc-statistical-tools` repo on CERN GitLab on `PYTHONPATH`.

## Data Files

All parquet files live under `data/`:
- `data/df_pixel_modules.parquet` — assembled module data
- `data/df_pixel_baremodules.parquet` — bare module data (includes `BARE_MODULE.batch_number`)
- `data/df_pixel_flex.parquet` — flex PCB data

Config files live under `metadata/`:
- `metadata/cluster_maps_MODULE.json` — maps institution codes (e.g. `KEK`, `CERN`) to cluster codes (e.g. `PIXEL_MODULE_JAPAN`)
- `metadata/cern_japan_config.json` — defines the `PIXEL_MODULE_CERN_JAPAN` virtual cluster (see below)

Parquet files carry a `timestamp` in schema metadata used as the "last update" date.

## Key data flow in `moduleProductionPlots.py`

1. `load_data()` — reads the three parquets with column pruning (`MODULE_COLS`, `BM_COLS`, `FLEX_COLS`)
2. `filter_components()` — filters to production modules (SN regex, BOM version, not pre-production); assigns `cluster_code` to modules via `MODULE.institution_code`, and to bare modules / flexes via `assign_clusters_from_locations()` (walks `MODULE.locations` history)
3. `apply_cern_japan_cluster()` — reclassifies Japan modules to `PIXEL_MODULE_CERN_JAPAN` per-module if `MODULE.locations` contains a CERN institution shipment that occurred before `MODULE/COMPLETE` (i.e. the module was sent to CERN mid-pipeline for final QC, not after finishing in Japan)
4. Per-cluster loop generates all plots into `plots/`

## CERN-JAPAN cluster (`metadata/cern_japan_config.json`)

A virtual cluster for Japan-assembled modules shipped to CERN before completing QC. Classification is per-module using `MODULE.locations`:
- **Include**: Japan module with any location entry at a CERN institution where `stage != MODULE/COMPLETE` (and not in `completion_stages`)
- **Exclude**: modules shipped to CERN only after reaching `MODULE/COMPLETE` or `MODULE/FINAL_COLD` (those finished QC in Japan)

The config keys: `cluster_code`, `source_cluster`, `cern_cluster` (used to look up CERN institutions from `cluster_maps_MODULE.json`), `completion_stages`.

## Plots generated

All plots go to `plots/`. Per-cluster plots use the suffix `_PIXEL_MODULE_<CLUSTER>.png`.

| File pattern | Function | Notes |
|---|---|---|
| `monthly_modules[_CLUSTER].png` | `create_monthly_production` | Bar chart, stages as colors defined in `STAGE_COLORS` |
| `cumulative_pipeline[_CLUSTER].png` | `_plot_cumulative_pipeline` / `create_cumulative_all_clusters` | Line chart; BM/Flex dotted, stages solid |
| `stage_duration_summary[_short]_CLUSTER.png` | `create_stage_duration_summary` | Median ± IQR (asymmetric Q1/Q3 error bars) |
| `batch_duration_CLUSTER.png` | `create_batch_duration_plot` | Median ± IQR per batch; fraction-completed overlay on right axis |
| `duration_STAGEA_STAGEB.png` | `create_stage_duration_histograms` | Overlaid KDE/histogram per cluster |

Global style: `savefig.dpi=300`, `savefig.bbox=tight`, seaborn-v0_8-whitegrid. Stage colors defined in `STAGE_COLORS` dict (C0–C3 for ASSEMBLY/INITIAL_WARM/PARYLENE_UNMASKING/FINAL_COLD).

## Website (`index.html`)

Static HTML/CSS/JS. Three top-level modes:
- **Production Overview** — all-clusters monthly bar chart + cumulative pipeline line chart side by side
- **By Cluster** — cluster picker → tabs: *Number of Modules* (monthly + cumulative), *Time to Completion* (stage duration summary short + batch duration)
- **Compare Clusters** — Monthly Production (all clusters) and Stage Duration Histograms grid

Cluster buttons use `data-cluster` attributes for exact-match active state (avoids substring collision between `PIXEL_MODULE_CERN` and `PIXEL_MODULE_CERN_JAPAN`).

## Domain concepts

- **Component hierarchy**: Flex PCB + Bare Module → assembled Module
- **Production types**: Quad (primary focus) vs. Triplet/Single
- **Stage pipeline**: `MODULE/INIT` → `MODULE/ASSEMBLY` → `MODULE/WIREBONDING` → `MODULE/INITIAL_WARM` → `MODULE/PARYLENE_MASKING` → `MODULE/PARYLENE_COATING` → `MODULE/PARYLENE_UNMASKING` → `MODULE/THERMAL_CYCLES` → `MODULE/FINAL_WARM` → `MODULE/FINAL_COLD`
- **Clusters**: Japan, CERN-Japan, France, Germany BMBF, Germany MPI, UK, USA, Italy, CERN
- **Japan note**: starting April 2026, batches of ~7 Japan modules systematically skipped `MODULE/ASSEMBLY` sign-off; cumulative plots fall back to `MODULE.cts` (creation timestamp) when assembly is absent
