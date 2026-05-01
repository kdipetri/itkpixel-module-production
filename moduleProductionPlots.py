import json
import re
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

#Reuse the module page to build inventory tables.
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from matplotlib.dates import DateFormatter

plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.labelsize": 16,
    "legend.fontsize": 14,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,
    "lines.linewidth": 5,
    "patch.linewidth": 2.0,
    "figure.figsize": (6, 4),
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})
plt.style.use("seaborn-v0_8-whitegrid")

STAGE_COLORS = {
    'MODULE/ASSEMBLY':           'C0',
    'MODULE/INITIAL_WARM':       'C1',
    'MODULE/PARYLENE_UNMASKING': 'C2',
    'MODULE/FINAL_COLD':         'C3',
}
BM_COLOR   = 'C4'
FLEX_COLOR = 'gray'


@lru_cache(maxsize=None)
def load_json(json_filename):
    metadata_file = Path(json_filename)
    with metadata_file.open("r") as file:
        return json.load(file)

def parquet_to_df(pq_filename, columns=None):
    df_file = Path(pq_filename)
    table = pq.read_table(df_file, columns=columns)
    table_md = table.schema.metadata
    df_date = datetime.fromisoformat(table_md[b'timestamp'].decode('utf-8')).date()
    return table.to_pandas(), df_date


@lru_cache(maxsize=None)
def _institution_lookup():
    cluster_map = load_json("metadata/cluster_maps_MODULE.json")
    return {inst: cluster
            for cluster, insts in cluster_map.items() if insts
            for inst in insts}


def map_institution_to_group(institution):
    return _institution_lookup().get(institution)


def assign_clusters_from_locations(locations_series, target_stages, fallback_series):
    lookup = _institution_lookup()
    result = []
    for locs, fallback in zip(locations_series, fallback_series):
        cluster = None
        for target_stage in target_stages:
            if cluster:
                break
            for loc in locs:
                if loc.get('stage') == target_stage:
                    cluster = lookup.get(loc['institution'])
                    if cluster:
                        break
        result.append(cluster if cluster else lookup.get(fallback))
    return result


def get_latest_stage_timestamp(stage_list, stage_name):
    timestamps = [s['dateTime'] for s in stage_list if s['code'] == stage_name]
    return max(timestamps) if timestamps else None


def get_earliest_stage_timestamp(stage_list, stage_name):
    if isinstance(stage_name, str):
        stage_name = [stage_name]
    timestamps = [s['dateTime'] for s in stage_list
                  if s.get('code') in stage_name or s.get('stage') in stage_name]
    return min(timestamps) if timestamps else None



def create_monthly_production(df, base_path, cluster='All Clusters'):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/PARYLENE_UNMASKING', 'MODULE/FINAL_COLD']
    fig = plt.figure(figsize=(8, 5))
    stage_times = []
    min_month = None
    max_month = None
    for stage in stages:
        times = df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage)
        times = pd.to_datetime(times[times.notna()].sort_values())
        months = times.dt.month
        years = times.dt.year
        norm_month = months + 12 * (years - 2024)
        if len(norm_month) > 0:
            if min_month is None or norm_month.min() < min_month:
                min_month = norm_month.min()
            if max_month is None or norm_month.max() > max_month:
                max_month = norm_month.max()
        stage_times.append(norm_month)
    if min_month is None:
        plt.close(fig)
        return
    bin_names = [ f'{mo % 12:2}/{2024+(mo//12)}' for mo in range(min_month, max_month+1) ]
    plt.hist([time for time in stage_times], bins=len(bin_names), range=(min_month, max_month+1),
             histtype='bar', label=[_fmt_stage(stage) for stage in stages],
             color=[STAGE_COLORS[s] for s in stages], stacked=False)
    plt.xticks(ticks = range(min_month,max_month+1), labels = bin_names, rotation=45)
    plt.legend()
    plt.xlabel('Sign-Off Month')
    plt.ylabel('# Modules / Month')
    fig.tight_layout()
    plt.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''), transform=plt.gca().transAxes, fontsize=16)
    plt.savefig(base_path)

def create_weekly_throughput(df, base_path, cluster='All Clusters'):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_WARM', 'MODULE/FINAL_COLD']
    fig = plt.figure()    
    for stage in stages:
        times = df['MODULE.stages'].apply(
            get_latest_stage_timestamp,
            stage_name=stage
        )

        # clean + convert
        times = pd.to_datetime(times[times.notna()])

        if len(times) == 0:
            continue

        # make it a time-indexed series for resampling
        s = pd.Series(1, index=times)

        # weekly counts (default: week ending Sunday)
        weekly_counts = s.resample('W').sum()

        plt.plot(
            weekly_counts.index,
            weekly_counts.values,
            linewidth=2.0,   # <-- change this
            label=_fmt_stage(stage)
        )

    plt.legend()
    plt.xlabel('Week')
    plt.ylabel('# Modules/Week')
    #plt.title('Weekly Production by Stage')

    # optional: nicer date formatting
    plt.xticks(rotation=45)

    plt.text(
        0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''),
        transform=plt.gca().transAxes,
        fontsize=16
    )

    plt.tight_layout()
    plt.savefig(base_path)


def _plot_cumulative_pipeline(ax, module_df, bm_df, flex_df):
    bm_times = pd.to_datetime(
        bm_df['BARE_MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='BAREMODULERECEPTION').dropna(),
        utc=True,
    )
    flex_times = pd.to_datetime(
        flex_df['PCB.stages'].apply(
            get_earliest_stage_timestamp,
            stage_name=['PCB_RECEPTION_MODULE_SITE', 'PCB_READY_FOR_MODULE', 'COMPLETE', 'MODULE/ASSEMBLY'],
        ).dropna(),
        utc=True,
    )
    assembly_times = pd.to_datetime(
        module_df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/ASSEMBLY')
            .fillna(module_df['MODULE.cts']).dropna(),
        utc=True,
    )
    pipeline = [
        ('Bare Modules',      bm_times,       BM_COLOR,                                ':'),
        ('Flexes',            flex_times,     FLEX_COLOR,                              ':'),
        ('Assembled',         assembly_times, STAGE_COLORS['MODULE/ASSEMBLY'],         '-'),
        ('Initial Warm',      pd.to_datetime(module_df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/INITIAL_WARM').dropna(),        utc=True), STAGE_COLORS['MODULE/INITIAL_WARM'],       '-'),
        ('Parylene Unmasked', pd.to_datetime(module_df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/PARYLENE_UNMASKING').dropna(), utc=True), STAGE_COLORS['MODULE/PARYLENE_UNMASKING'], '-'),
        ('Final Cold',        pd.to_datetime(module_df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/FINAL_COLD').dropna(),         utc=True), STAGE_COLORS['MODULE/FINAL_COLD'],         '-'),
    ]
    for label, times, color, ls in pipeline:
        if len(times) == 0:
            continue
        weekly = pd.Series(1, index=times).resample('W').sum().cumsum()
        ax.plot(weekly.index, weekly.values, label=label, color=color, linestyle=ls, linewidth=2)
    ax.set_xlabel('Week')
    ax.set_ylabel('# Modules')
    ax.legend(fontsize=11, loc='upper left')
    ax.tick_params(axis='x', rotation=45)


def create_cumulative_pipeline_plot(module_df, bm_df, flex_df, cluster, base_path):
    fig, ax = plt.subplots(figsize=(8, 5))
    _plot_cumulative_pipeline(
        ax,
        module_df[module_df['MODULE.cluster_code'] == cluster],
        bm_df[bm_df['BARE_MODULE.cluster_code'] == cluster],
        flex_df[flex_df['PCB.cluster_code'] == cluster],
    )
    ax.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''), transform=ax.transAxes, fontsize=16)
    fig.tight_layout()
    fig.savefig(f"{base_path}/cumulative_pipeline_{cluster}.png")
    plt.close(fig)


def create_cumulative_all_clusters(module_df, bm_df, flex_df, base_path):
    fig, ax = plt.subplots(figsize=(8, 5))
    _plot_cumulative_pipeline(ax, module_df, bm_df, flex_df)
    ax.text(0.05, 1.02, 'All Clusters', transform=ax.transAxes, fontsize=16)
    fig.tight_layout()
    fig.savefig(f"{base_path}/cumulative_pipeline.png")
    plt.close(fig)


def modules_last_month(df, clusters):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_WARM', 'MODULE/FINAL_COLD']

    print('cluster', "ASSEMBLY", "INITIAL_WARM", "FINAL_WARM", "FINAL_COLD")
    for cluster in clusters:
        df_cluster = df[df["MODULE.cluster_code"]==cluster]
        counts = []

        now = datetime.now(timezone.utc)
        min_date = now - timedelta(days=30)

        for stage in stages:
            times = df_cluster['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage)
            times = pd.to_datetime(times[times.notna()].sort_values(), utc=True)
            win_last_month = times[(min_date <= times) & (times <= now)]
            counts.append(len(win_last_month))

        print(cluster, counts[0], counts[1], counts[2], counts[3])


def _fmt_stage(name):
    return name.replace('MODULE/', '').replace('_', ' ')


STAGE_PAIRS = [
    ('MODULE/INIT',              'MODULE/ASSEMBLY'),
    ('MODULE/ASSEMBLY',          'MODULE/WIREBONDING'),
    ('MODULE/WIREBONDING',       'MODULE/INITIAL_WARM'),
    ('MODULE/INITIAL_WARM',      'MODULE/PARYLENE_MASKING'),
    ('MODULE/PARYLENE_MASKING',  'MODULE/PARYLENE_COATING'),
    ('MODULE/PARYLENE_COATING',  'MODULE/PARYLENE_UNMASKING'),
    ('MODULE/PARYLENE_UNMASKING','MODULE/THERMAL_CYCLES'),
    ('MODULE/THERMAL_CYCLES',    'MODULE/FINAL_WARM'),
    ('MODULE/FINAL_WARM',        'MODULE/FINAL_COLD'),
]


def _stage_durations_days(df, stage_from, stage_to):
    t1 = pd.to_datetime(df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage_from))
    t2 = pd.to_datetime(df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage_to))
    dt = (t2 - t1).dt.total_seconds() / 86400
    return dt[dt.notna() & (dt >= 0)]


def create_stage_duration_histograms(df, clusters, base_path):
    for stage_from, stage_to in STAGE_PAIRS:
        fig, ax = plt.subplots(figsize=(6.5, 5))

        all_dts = []
        cluster_dts = {}
        for cluster in clusters:
            dt = _stage_durations_days(df[df['MODULE.cluster_code'] == cluster], stage_from, stage_to)
            if len(dt) > 0:
                cluster_dts[cluster] = dt
                all_dts.extend(dt.values)

        if not all_dts:
            plt.close(fig)
            continue

        bins = np.linspace(0, np.percentile(all_dts, 95), 30)
        max_name = max(len(c.replace('PIXEL_MODULE_', '')) for c in cluster_dts)
        for cluster, dt in cluster_dts.items():
            name = cluster.replace('PIXEL_MODULE_', '')
            label = (f"{name:<{max_name}}  "
                     f"med={dt.median():>5.1f}d  rms={dt.std():>5.1f}d  n={len(dt):>4}")
            ax.hist(dt, bins=bins, histtype='step', label=label,
                    weights=np.ones(len(dt)) / len(dt))

        stage_label = f"{_fmt_stage(stage_from)} → {_fmt_stage(stage_to)}"
        ax.set_xlabel(f'Duration: {stage_label} [days]')
        ax.set_ylabel('Probability')
        ax.legend(
            fontsize='small',
            prop={'family': 'monospace'},
            labelspacing=0.15,
            bbox_to_anchor=(0.05, 1),
            bbox_transform=fig.transFigure,
            loc='lower left',
            borderaxespad=0,
            alignment='left',
        )
        fig.tight_layout()
        fig.canvas.draw()
        legend = ax.get_legend()
        legend_bottom = legend.get_window_extent(fig.canvas.get_renderer()).transformed(fig.transFigure.inverted()).y0
        pos = ax.get_position()
        new_left = 0.08
        ax.set_position([new_left, pos.y0, pos.x0 + pos.width - new_left, legend_bottom - pos.y0 - 0.02])
        slug = f"{stage_from.replace('MODULE/', '')}_{stage_to.replace('MODULE/', '')}"
        fig.savefig(f"{base_path}/duration_{slug}.png", bbox_inches='tight', pad_inches=0.2)
        plt.close(fig)


def create_stage_duration_summary(df, clusters, base_path, pairs=None, suffix=''):
    if pairs is None:
        pairs = STAGE_PAIRS
    stage_labels = [
        f"{_fmt_stage(f)} →\n{_fmt_stage(t)}"
        for f, t in pairs
    ] + ['INIT →\nFINAL COLD']
    x = np.arange(len(stage_labels))

    for cluster in clusters:
        df_cluster = df[df['MODULE.cluster_code'] == cluster]
        medians, err_lo, err_hi = [], [], []
        for stage_from, stage_to in pairs:
            dt = _stage_durations_days(df_cluster, stage_from, stage_to)
            if len(dt) >= 2:
                med = dt.median()
                medians.append(med)
                err_lo.append(med - dt.quantile(0.25))
                err_hi.append(dt.quantile(0.75) - med)
            else:
                medians.append(np.nan)
                err_lo.append(np.nan)
                err_hi.append(np.nan)
        total = _stage_durations_days(df_cluster, 'MODULE/INIT', 'MODULE/FINAL_COLD')
        if len(total) >= 2:
            med = total.median()
            medians.append(med)
            err_lo.append(med - total.quantile(0.25))
            err_hi.append(total.quantile(0.75) - med)
        else:
            medians.append(np.nan)
            err_lo.append(np.nan)
            err_hi.append(np.nan)

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.errorbar(x, medians, yerr=[err_lo, err_hi], fmt='o', capsize=6, markersize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(stage_labels, fontsize=10, rotation=45, ha='right')
        ax.set_ylabel('Duration [days]')
        ax.set_ylim(bottom=0)
        ax.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''),
                transform=ax.transAxes, fontsize=16)
        fig.tight_layout()
        fig.savefig(f"{base_path}/stage_duration_summary{suffix}_{cluster}.png")
        plt.close(fig)


def _shorten_batch_name(name):
    date_match = re.search(r'\(([^)]+)\)', name)
    date = date_match.group(1) if date_match else ''
    for full, abbr in [
        ('January','Jan'), ('February','Feb'), ('March','Mar'), ('April','Apr'),
        ('May','May'), ('June','Jun'), ('July','Jul'), ('August','Aug'),
        ('September','Sep'), ('October','Oct'), ('November','Nov'), ('December','Dec'),
    ]:
        date = date.replace(full, abbr)

    batch_match = re.search(r'batch (\d+)', name)
    batch_num = batch_match.group(1) if batch_match else ''

    vendor_raw = name.split('Quad')[0].strip()
    vendor_raw = re.sub(r'HPK-HPK in-kind', 'HPK', vendor_raw)
    vendor_raw = re.sub(r'\bV2\b|\bthin\b|\bplanar\b', '', vendor_raw)
    vendor = ' '.join(vendor_raw.split())
    if vendor.startswith('Advafab'):
        vendor = 'Advafab'

    parts = [vendor]
    if batch_num:
        parts.append(batch_num)
    if date:
        parts.append(f"({date})")
    return ' '.join(parts)


def create_batch_duration_plot(module_df, clusters, base_path):
    bm_extra = pq.read_table(
        Path("data/df_pixel_baremodules.parquet"),
        columns=['BARE_MODULE.serialNumber', 'BARE_MODULE.batch_number', 'BARE_MODULE.cts'],
    ).to_pandas()

    df = module_df.copy()
    df['t_init'] = pd.to_datetime(
        df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/INIT'), utc=True)
    df['t_cold'] = pd.to_datetime(
        df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/FINAL_COLD'), utc=True)
    df['duration'] = (df['t_cold'] - df['t_init']).dt.total_seconds() / 86400
    df.loc[df['duration'] < 0, 'duration'] = float('nan')

    df = df.merge(
        bm_extra.rename(columns={
            'BARE_MODULE.serialNumber':   'BARE_MODULE_0.serialNumber',
            'BARE_MODULE.batch_number':   'batch_number',
            'BARE_MODULE.cts':            'bm_cts',
        }),
        on='BARE_MODULE_0.serialNumber', how='left',
    )
    df = df[df['batch_number'].notna()]

    for cluster in clusters:
        df_cluster = df[df['MODULE.cluster_code'] == cluster]
        if len(df_cluster) == 0:
            continue

        batch_stats = df_cluster.groupby('batch_number').agg(
            median_duration=('duration', 'median'),
            q1_duration=('duration', lambda x: x.quantile(0.25)),
            q3_duration=('duration', lambda x: x.quantile(0.75)),
            batch_min_cts=('bm_cts', 'min'),
            n_completed=('duration', 'count'),
            n_total=('duration', 'size'),
        ).reset_index()
        batch_stats['fraction_completed'] = batch_stats['n_completed'] / batch_stats['n_total']
        batch_stats['batch_min_cts'] = pd.to_datetime(batch_stats['batch_min_cts'])
        batch_stats = batch_stats.sort_values('batch_min_cts')

        x = np.arange(len(batch_stats))
        labels = [_shorten_batch_name(n) for n in batch_stats['batch_number']]

        fig, ax = plt.subplots(figsize=(8, 5))
        ax2 = ax.twinx()

        ax2.bar(x, batch_stats['fraction_completed'], alpha=0.25, color='steelblue', width=0.6, zorder=1)
        ax2.set_ylabel('Fraction completed', color='steelblue')
        ax2.tick_params(axis='y', labelcolor='steelblue', color='steelblue')
        ax2.spines['right'].set_color('steelblue')
        ax2.spines['right'].set_linestyle('--')
        ax2.yaxis.grid(True, color='steelblue', linestyle='--', linewidth=0.8, alpha=0.4, zorder=0)
        ax2.set_ylim(0, 1)

        err_lo = (batch_stats['median_duration'] - batch_stats['q1_duration']).clip(lower=0)
        err_hi = (batch_stats['q3_duration'] - batch_stats['median_duration']).clip(lower=0)
        ax.errorbar(x, batch_stats['median_duration'], yerr=[err_lo, err_hi],
                    fmt='o', capsize=4, markersize=6, zorder=2)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=9)
        ax.set_ylabel('INIT → FINAL_COLD [days]')
        ax.set_ylim(bottom=0)
        ax.set_zorder(ax2.get_zorder() + 1)
        ax.patch.set_visible(False)
        ax.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''),
                transform=ax.transAxes, fontsize=16)
        fig.tight_layout()
        fig.savefig(f"{base_path}/batch_duration_{cluster}.png")
        plt.close(fig)


MODULE_COLS = [
    'MODULE.serialNumber', 'MODULE.IS_PREPRODUCTION_MODULE',
    'PCB_0.PCB_BOM_VERSION', 'MODULE.institution_code', 'MODULE.type_code',
    'MODULE.stages', 'MODULE.cts', 'MODULE.locations',
    'BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber',
]
BM_COLS = [
    'BARE_MODULE.serialNumber', 'BARE_MODULE.type_code',
    'BARE_MODULE.hasParent_MODULE',
    'BARE_MODULE.locations', 'BARE_MODULE.currentLocation_code',
    'BARE_MODULE.stages',
]
FLEX_COLS = [
    'PCB.PCB_BOM_VERSION', 'PCB.PCB_DESIGN_VERSION',
    'PCB.locations', 'PCB.currentLocation_code',
    'PCB.stages',
]


def load_data():
    module_df, _ = parquet_to_df(Path("data/df_pixel_modules.parquet"), columns=MODULE_COLS)
    bm_df, _     = parquet_to_df(Path("data/df_pixel_baremodules.parquet"), columns=BM_COLS)
    flex_df, _   = parquet_to_df(Path("data/df_pixel_flex.parquet"), columns=FLEX_COLS)
    return module_df, bm_df, flex_df


def filter_components(module_df, bm_df, flex_df):
    preprod_mods       = module_df[module_df['MODULE.IS_PREPRODUCTION_MODULE'] == True]
    preprod_module_sns = set(preprod_mods['MODULE.serialNumber'])
    preprod_bm_sns     = set()
    for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
        preprod_bm_sns.update(preprod_mods[col].dropna())

    sn_mask      = module_df['MODULE.serialNumber'].str.contains('20UP[IG]M[0125S][345]', na=False)
    preprod_mask = module_df['MODULE.IS_PREPRODUCTION_MODULE'].ne(True)  # False and NaN → production
    bom_mask     = module_df['PCB_0.PCB_BOM_VERSION'].str.startswith('2', na=False)
    module_df = module_df[sn_mask & preprod_mask & bom_mask].copy()
    module_df['MODULE.cluster_code'] = module_df['MODULE.institution_code'].apply(map_institution_to_group)

    bm_df = bm_df[
        bm_df['BARE_MODULE.serialNumber'].str.contains('20UPGB[124]3', na=False) &
        (bm_df['BARE_MODULE.type_code'] == 'QUAD_BARE_MODULE') &
        ~bm_df['BARE_MODULE.hasParent_MODULE'].isin(preprod_module_sns) &
        ~bm_df['BARE_MODULE.serialNumber'].isin(preprod_bm_sns)
    ].copy()
    bm_df['BARE_MODULE.cluster_code'] = assign_clusters_from_locations(
        bm_df['BARE_MODULE.locations'],
        ('BAREMODULERECEPTION', 'MODULE/INIT', 'MODULE/ASSEMBLY'),
        bm_df['BARE_MODULE.currentLocation_code'],
    )

    flex_df = flex_df[
        flex_df['PCB.PCB_BOM_VERSION'].str.startswith('2', na=False) &
        flex_df['PCB.PCB_DESIGN_VERSION'].isin(['4', '5'])
    ].copy()
    flex_df['PCB.cluster_code'] = assign_clusters_from_locations(
        flex_df['PCB.locations'],
        ('PCB_RECEPTION_MODULE_SITE', 'MODULE/INIT', 'MODULE/ASSEMBLY'),
        flex_df['PCB.currentLocation_code'],
    )

    return module_df, bm_df, flex_df


def apply_cern_japan_cluster(module_df, bm_df, config_path="metadata/cern_japan_config.json"):
    cfg = load_json(config_path)
    target = cfg["cluster_code"]
    source = cfg["source_cluster"]

    # institutions that belong to the CERN cluster
    cern_insts = set(load_json("metadata/cluster_maps_MODULE.json")[cfg["cern_cluster"]])

    # per-module: Japan module shipped to CERN before completing QC there
    completion_stages = set(cfg.get("completion_stages", ["MODULE/COMPLETE", "MODULE/FINAL_COLD"]))

    def _shipped_to_cern_before_completion(locations):
        cern_entries = [loc for loc in locations if loc.get('institution', '') in cern_insts]
        if not cern_entries:
            return False
        # exclude if every CERN shipment happened only after full QC completion
        return any(loc.get('stage', '') not in completion_stages for loc in cern_entries)

    module_df = module_df.copy()
    japan_idx = module_df.index[module_df['MODULE.cluster_code'] == source]
    cern_visit = module_df.loc[japan_idx, 'MODULE.locations'].apply(_shipped_to_cern_before_completion)
    mod_mask = cern_visit[cern_visit].index
    module_df.loc[mod_mask, 'MODULE.cluster_code'] = target

    # reclassify the bare modules that belong to reclassified modules
    reclassified_bm_sns = set()
    for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
        reclassified_bm_sns.update(module_df.loc[mod_mask, col].dropna())
    bm_df = bm_df.copy()
    bm_mask = (bm_df['BARE_MODULE.cluster_code'] == source) & \
              (bm_df['BARE_MODULE.serialNumber'].isin(reclassified_bm_sns))
    bm_df.loc[bm_mask, 'BARE_MODULE.cluster_code'] = target

    return module_df, bm_df


def main():
    module_df, bm_df, flex_df = load_data()
    module_df, bm_df, flex_df = filter_components(module_df, bm_df, flex_df)
    module_df, bm_df = apply_cern_japan_cluster(module_df, bm_df)

    clusters = [
        "PIXEL_MODULE_JAPAN",
        "PIXEL_MODULE_CERN_JAPAN",
        "PIXEL_MODULE_FRANCE",
        "PIXEL_MODULE_GERMANY_BMBF",
        "PIXEL_MODULE_UK",
        "PIXEL_MODULE_USA",
        "PIXEL_MODULE_ITALY",
        "PIXEL_MODULE_CERN",
        "PIXEL_MODULE_GERMANY_MPI",
    ]

    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)]
    create_monthly_production(quad_df, "plots/monthly_modules.png")
    create_cumulative_all_clusters(module_df, bm_df, flex_df, "plots")

    for cluster in clusters:
        print(cluster)
        df_cluster = quad_df[quad_df['MODULE.cluster_code'] == cluster]
        create_monthly_production(df_cluster, f"plots/monthly_modules_{cluster}.png", cluster=cluster)
        create_weekly_throughput(df_cluster, f"plots/weekly_modules_{cluster}.png", cluster=cluster)
        create_cumulative_pipeline_plot(module_df, bm_df, flex_df, cluster, "plots")

    create_batch_duration_plot(quad_df, clusters, "plots")
    modules_last_month(quad_df, clusters)
    create_stage_duration_histograms(quad_df, clusters, "plots")
    create_stage_duration_summary(quad_df, clusters, "plots")
    create_stage_duration_summary(quad_df, clusters, "plots",
        pairs=[
            ('MODULE/INIT',              'MODULE/WIREBONDING'),
            ('MODULE/WIREBONDING',       'MODULE/INITIAL_WARM'),
            ('MODULE/INITIAL_WARM',      'MODULE/PARYLENE_UNMASKING'),
            ('MODULE/PARYLENE_UNMASKING','MODULE/FINAL_COLD'),
        ],
        suffix='_short',
    )

if __name__ == "__main__":
    main()
