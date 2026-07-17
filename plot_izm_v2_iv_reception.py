import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq
from matplotlib.lines import Line2D

from utils import add_data_timestamp

plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.labelsize": 16,
    "legend.fontsize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 14,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})
plt.style.use("seaborn-v0_8-whitegrid")

FECHIP_V2 = '3'
IV_PREFIX = 'BARE_MODULE.BAREMODULERECEPTION.BARE_MODULE_SENSOR_IV.'
IV_PASSED_COL = IV_PREFIX + 'passed'
NO_BREAKDOWN_SENTINEL = -999

# (BARE_MODULE.VENDOR code, title prefix, output filename prefix)
VENDORS = [
    ('2', 'IZM',            'izm_v2'),
    ('0', 'Advafab',        'advafab_v2'),
    ('4', 'HPK-CERN Order', 'hpk_cern_v2'),
]

# (column suffix, y-axis label, output filename slug, histogram binning: 'log' | 'linear' | (lo, hi) for a
# fixed linear range with overflow/underflow bins)
METRIC_PLOTS = [
    ('LEAK_PER_AREA_TEST',      'Leakage Current / Area\n(LEAK_PER_AREA_TEST)',      'leak_per_area',      'log'),
    ('LEAK_CURRENT_TEST',       'Leakage Current\n(LEAK_CURRENT_TEST)',              'leak_current',       (0, 10)),
    ('BREAKDOWN_VOLTAGE_TEST',  'Breakdown Voltage [V]\n(BREAKDOWN_VOLTAGE_TEST)',   'breakdown_voltage',  'linear'),
    ('BREAKDOWN_REDUCTION_TEST','Breakdown Reduction\n(BREAKDOWN_REDUCTION_TEST)',   'breakdown_reduction','linear'),
]

COLS = [
    'BARE_MODULE.serialNumber',
    'BARE_MODULE.VENDOR',
    'BARE_MODULE.FECHIP_VERSION',
    'BARE_MODULE.type_code',
    'BARE_MODULE.batch_number',
    'BARE_MODULE.cts',
    IV_PASSED_COL,
] + [IV_PREFIX + suffix for suffix, _, _, _ in METRIC_PLOTS]


def _shorten_batch_name(name):
    date_match = re.search(r'\(([^)]+)\)', name)
    date = date_match.group(1) if date_match else ''
    for full, abbr in [
        ('January', 'Jan'), ('February', 'Feb'), ('March', 'Mar'), ('April', 'Apr'),
        ('May', 'May'), ('June', 'Jun'), ('July', 'Jul'), ('August', 'Aug'),
        ('September', 'Sep'), ('October', 'Oct'), ('November', 'Nov'), ('December', 'Dec'),
    ]:
        date = date.replace(full, abbr)
    flavor = 'Quad' if 'Quad' in name else 'Single' if 'Single' in name else ''
    batch_match = re.search(r'batch (\d+)', name)
    batch_num = batch_match.group(1) if batch_match else ''
    parts = [p for p in [flavor, batch_num] if p]
    label = ' '.join(parts)
    return f"{label} ({date})" if date else label


def plot_metric_by_batch(df, metric_suffix, ylabel, filename_slug, title_prefix, filename_prefix, data_date):
    col = IV_PREFIX + metric_suffix
    metric = df[['BARE_MODULE.batch_number', 'BARE_MODULE.cts', IV_PASSED_COL, col]].copy()
    metric = metric[metric[col].notna() & (metric[col] != NO_BREAKDOWN_SENTINEL)]
    if metric.empty:
        return

    batch_min_cts = metric.groupby('BARE_MODULE.batch_number')['BARE_MODULE.cts'].min()
    batches = batch_min_cts.sort_values().index.tolist()
    labels = [_shorten_batch_name(b) for b in batches]
    batch_x = {b: i for i, b in enumerate(batches)}

    rng = np.random.default_rng(0)
    x = metric['BARE_MODULE.batch_number'].map(batch_x) + rng.uniform(-0.15, 0.15, size=len(metric))
    colors = np.where(metric[IV_PASSED_COL] == True, 'green', 'red')  # noqa: E712

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.scatter(x, metric[col], c=colors, alpha=0.8, edgecolor='black', linewidth=0.5)
    ax.set_xticks(np.arange(len(batches)))
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_ylabel(ylabel)
    ax.set_title(f'{title_prefix} Quad Sensor {_fmt_metric_title(metric_suffix)} at Bare Module Reception, by Batch')
    ax.legend(handles=[
        Line2D([0], [0], marker='o', color='w', markerfacecolor='green', markeredgecolor='black', label='Pass'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='red', markeredgecolor='black', label='Fail'),
    ])
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    fig.savefig(f'plots/{filename_prefix}_{filename_slug}_by_batch.png')
    plt.close(fig)


def plot_metric_histogram(df, metric_suffix, xlabel, filename_slug, binning, title_prefix, filename_prefix, data_date):
    col = IV_PREFIX + metric_suffix
    metric = df[[IV_PASSED_COL, col]].copy()
    metric = metric[metric[col].notna() & (metric[col] != NO_BREAKDOWN_SENTINEL)]
    if metric.empty:
        return

    log_x = binning == 'log'
    fixed_range = isinstance(binning, tuple)

    if fixed_range:
        lo, hi = binning
        binwidth = (hi - lo) / 30
        flank = binwidth * 3  # wider overflow/underflow bins, visually distinct from in-range bins
        bins = np.concatenate([[lo - flank], np.linspace(lo, hi, 31), [hi + flank]])
        metric[col] = metric[col].clip(lower=lo - flank / 2, upper=hi + flank / 2)
    elif log_x:
        # zeros can't be log-binned; floor them to half the smallest positive value
        positive = metric[col][metric[col] > 0]
        floor = positive.min() / 2 if len(positive) else 1e-3
        metric[col] = metric[col].clip(lower=floor)
        hi = np.percentile(metric[col], 99)
        bins = np.logspace(np.log10(floor), np.log10(max(hi, floor * 10)), 30)
    else:
        lo, hi = metric[col].min(), np.percentile(metric[col], 99)
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        bins = np.linspace(lo, hi, 30)

    passed = metric[metric[IV_PASSED_COL] == True][col]   # noqa: E712
    failed = metric[metric[IV_PASSED_COL] == False][col]  # noqa: E712

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(passed, bins=bins, color='green', alpha=0.6, label=f'Pass (n={len(passed)})')
    ax.hist(failed, bins=bins, color='red', alpha=0.6, label=f'Fail (n={len(failed)})')
    if log_x:
        ax.set_xscale('log')
    if fixed_range:
        lo, hi = binning
        inner_ticks = np.linspace(lo, hi, 6)[1:-1]  # drop lo/hi: the boundary lines mark them instead
        ax.set_xticks([lo - flank / 2, *inner_ticks, hi + flank / 2])
        ax.set_xticklabels([f'<{lo:g}', *[f'{t:g}' for t in inner_ticks], f'>{hi:g}'])
        ax.axvline(lo, color='gray', linestyle='--', linewidth=1)
        ax.axvline(hi, color='gray', linestyle='--', linewidth=1)
        ax.set_xlim(lo - flank * 1.5, hi + flank * 1.5)
    ax.set_xlabel(xlabel.replace('\n', ' '))
    ax.set_ylabel('# Bare Modules')
    ax.set_title(f'{title_prefix} Quad Sensor {_fmt_metric_title(metric_suffix)} at Bare Module Reception')
    ax.legend()
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    fig.savefig(f'plots/{filename_prefix}_{filename_slug}_hist.png')
    plt.close(fig)


def _fmt_metric_title(metric_suffix):
    return metric_suffix.replace('_TEST', '').replace('_', ' ').title()


def make_vendor_plots(full_df, vendor_code, title_prefix, filename_prefix, data_date):
    df = full_df[
        (full_df['BARE_MODULE.VENDOR'] == vendor_code) &
        (full_df['BARE_MODULE.FECHIP_VERSION'] == FECHIP_V2) &
        (full_df['BARE_MODULE.type_code'] == 'QUAD_BARE_MODULE') &
        full_df['BARE_MODULE.batch_number'].notna()
    ]
    if df.empty:
        return

    batch_min_cts = df.groupby('BARE_MODULE.batch_number')['BARE_MODULE.cts'].min()
    batches = batch_min_cts.sort_values().index.tolist()

    passed  = df[IV_PASSED_COL] == True   # noqa: E712
    failed  = df[IV_PASSED_COL] == False  # noqa: E712
    pending = df[IV_PASSED_COL].isna()

    pass_counts    = df[passed].groupby('BARE_MODULE.batch_number').size()
    fail_counts    = df[failed].groupby('BARE_MODULE.batch_number').size()
    pending_counts = df[pending].groupby('BARE_MODULE.batch_number').size()

    pass_vals    = np.array([pass_counts.get(b, 0) for b in batches])
    fail_vals    = np.array([fail_counts.get(b, 0) for b in batches])
    pending_vals = np.array([pending_counts.get(b, 0) for b in batches])
    labels       = [_shorten_batch_name(b) for b in batches]

    x = np.arange(len(batches))
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x, pass_vals, label='Pass', color='green', alpha=0.8)
    ax.bar(x, fail_vals, label='Fail', color='red', alpha=0.8, bottom=pass_vals)
    ax.bar(x, pending_vals, label='Not yet tested', color='gray', alpha=0.5,
           bottom=pass_vals + fail_vals)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_ylabel('# Bare Modules')
    ax.set_title(f'{title_prefix} Quad Sensor IV Result at Bare Module Reception, by Batch')
    ax.legend()
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    fig.savefig(f'plots/{filename_prefix}_iv_reception_by_batch.png')
    plt.close(fig)

    for metric_suffix, ylabel, filename_slug, binning in METRIC_PLOTS:
        plot_metric_by_batch(df, metric_suffix, ylabel, filename_slug, title_prefix, filename_prefix, data_date)
        plot_metric_histogram(df, metric_suffix, ylabel, filename_slug, binning, title_prefix, filename_prefix, data_date)


def main():
    table = pq.read_table(Path("data/df_pixel_baremodules.parquet"), columns=COLS)
    data_date = datetime.fromisoformat(
        table.schema.metadata[b'timestamp'].decode('utf-8')
    ).date()
    full_df = table.to_pandas()

    for vendor_code, title_prefix, filename_prefix in VENDORS:
        make_vendor_plots(full_df, vendor_code, title_prefix, filename_prefix, data_date)


if __name__ == '__main__':
    main()
