from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from utils import _fmt_stage, _shorten_batch_name, add_data_timestamp, get_latest_stage_timestamp

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


def create_stage_duration_histograms(df, clusters, base_path, data_date=None):
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
            name  = cluster.replace('PIXEL_MODULE_', '')
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
        add_data_timestamp(fig, data_date)
        fig.savefig(f"{base_path}/duration_{slug}.png", bbox_inches='tight', pad_inches=0.2)
        plt.close(fig)


def create_stage_duration_summary(df, clusters, base_path, pairs=None, suffix='', data_date=None):
    if pairs is None:
        pairs = STAGE_PAIRS
    stage_labels = [
        f"{_fmt_stage(f)} →\n{_fmt_stage(t)}"
        for f, t in pairs
    ] + ['INIT →\nFINAL COLD']
    x = np.arange(len(stage_labels))

    for cluster in clusters:
        df_cluster = df if cluster == 'ALL' else df[df['MODULE.cluster_code'] == cluster]
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

        label = 'All Clusters' if cluster == 'ALL' else cluster.replace('PIXEL_MODULE_', '')
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.errorbar(x, medians, yerr=[err_lo, err_hi], fmt='o', capsize=6, markersize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(stage_labels, fontsize=10, rotation=45, ha='right')
        ax.set_ylabel('Duration [days]')
        ax.set_ylim(bottom=0)
        ax.text(0.05, 1.02, label, transform=ax.transAxes, fontsize=16)
        fig.tight_layout()
        add_data_timestamp(fig, data_date)
        fig.savefig(f"{base_path}/stage_duration_summary{suffix}_{cluster}.png")
        plt.close(fig)


def create_batch_duration_plot(module_df, clusters, base_path, data_date=None):
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
            'BARE_MODULE.serialNumber': 'BARE_MODULE_0.serialNumber',
            'BARE_MODULE.batch_number': 'batch_number',
            'BARE_MODULE.cts':          'bm_cts',
        }),
        on='BARE_MODULE_0.serialNumber', how='left',
    )
    df = df[df['batch_number'].notna()]

    for cluster in clusters:
        df_cluster = df if cluster == 'ALL' else df[df['MODULE.cluster_code'] == cluster]
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

        x      = np.arange(len(batch_stats))
        labels = [_shorten_batch_name(n) for n in batch_stats['batch_number']]
        label  = 'All Clusters' if cluster == 'ALL' else cluster.replace('PIXEL_MODULE_', '')

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
        ax.text(0.05, 1.02, label, transform=ax.transAxes, fontsize=16)
        fig.tight_layout()
        add_data_timestamp(fig, data_date)
        fig.savefig(f"{base_path}/batch_duration_{cluster}.png")
        plt.close(fig)
