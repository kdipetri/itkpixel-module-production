import matplotlib.pyplot as plt
import pandas as pd

from utils import _fmt_stage, add_data_timestamp, get_earliest_stage_timestamp, get_latest_stage_timestamp, get_stage_institution

STAGE_COLORS = {
    'MODULE/ASSEMBLY':           'C0',
    'MODULE/INITIAL_WARM':       'C1',
    'MODULE/PARYLENE_UNMASKING': 'C2',
    'MODULE/FINAL_COLD':         'C3',
}
BM_COLOR   = 'C4'
FLEX_COLOR = 'gray'


def create_monthly_production(df, base_path, cluster='All Clusters', data_date=None):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/PARYLENE_UNMASKING', 'MODULE/FINAL_COLD']
    fig = plt.figure(figsize=(8, 5))
    stage_times = []
    min_month = None
    max_month = None
    for stage in stages:
        if stage == 'MODULE/ASSEMBLY':
            times = pd.to_datetime(df['MODULE.cts'].dropna().sort_values())
        elif stage == 'MODULE/PARYLENE_UNMASKING':
            is_japan = df['MODULE.cluster_code'] == 'PIXEL_MODULE_JAPAN'
            parylene_ts = df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/PARYLENE_UNMASKING')
            final_warm_ts = df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/FINAL_WARM')
            times = pd.to_datetime(parylene_ts.where(~is_japan, final_warm_ts).fillna(final_warm_ts)[lambda s: s.notna()].sort_values())
        else:
            times = df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name=stage)
            times = pd.to_datetime(times[times.notna()].sort_values())
        norm_month = times.dt.month + 12 * (times.dt.year - 2024)
        if len(norm_month) > 0:
            if min_month is None or norm_month.min() < min_month:
                min_month = norm_month.min()
            if max_month is None or norm_month.max() > max_month:
                max_month = norm_month.max()
        stage_times.append(norm_month)
    if min_month is None:
        plt.close(fig)
        return
    bin_names = [f'{mo % 12:2}/{2024 + (mo // 12)}' for mo in range(min_month, max_month + 1)]
    plt.hist(stage_times, bins=len(bin_names), range=(min_month, max_month + 1),
             histtype='bar', label=[_fmt_stage(s) for s in stages],
             color=[STAGE_COLORS[s] for s in stages], stacked=False)
    plt.xticks(ticks=range(min_month, max_month + 1), labels=bin_names, rotation=45)
    plt.legend()
    plt.xlabel('Sign-Off Month')
    plt.ylabel('# Modules / Month')
    fig.tight_layout()
    plt.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''), transform=plt.gca().transAxes, fontsize=16)
    add_data_timestamp(fig, data_date)
    plt.savefig(base_path)
    plt.close(fig)


def create_weekly_throughput(df, base_path, cluster='All Clusters', data_date=None):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_WARM', 'MODULE/FINAL_COLD']
    fig = plt.figure()
    for stage in stages:
        times = pd.to_datetime(
            df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name=stage).dropna()
        )
        if len(times) == 0:
            continue
        weekly_counts = pd.Series(1, index=times).resample('W').sum()
        plt.plot(weekly_counts.index, weekly_counts.values, linewidth=2.0, label=_fmt_stage(stage))
    plt.legend()
    plt.xlabel('Week')
    plt.ylabel('# Modules/Week')
    plt.xticks(rotation=45)
    plt.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''), transform=plt.gca().transAxes, fontsize=16)
    plt.tight_layout()
    add_data_timestamp(fig, data_date)
    plt.savefig(base_path)
    plt.close(fig)


def _flex_last_stage(stages):
    for s in reversed(stages):
        c = s.get('code') or s.get('stage', '')
        if c:
            return c
    return None


def _plot_cumulative_pipeline(ax, module_df, bm_df, flex_df):
    flex_last = flex_df['PCB.stages'].apply(_flex_last_stage)
    flex_df = flex_df[~flex_last.isin({'UNHAPPY', 'PCB/GRAVEYARD'})]

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
    assembly_times = pd.to_datetime(module_df['MODULE.cts'].dropna(), utc=True)
    pipeline = [
        ('Bare Modules',      bm_times,       BM_COLOR,                                ':'),
        ('Flexes',            flex_times,     FLEX_COLOR,                              ':'),
        ('Assembled',         assembly_times, STAGE_COLORS['MODULE/ASSEMBLY'],         '-'),
        ('Initial Warm',      pd.to_datetime(module_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/INITIAL_WARM').dropna(),        utc=True), STAGE_COLORS['MODULE/INITIAL_WARM'],       '-'),
        ('Parylene Unmasked', pd.to_datetime(
            module_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/PARYLENE_UNMASKING')
                .where(module_df['MODULE.cluster_code'] != 'PIXEL_MODULE_JAPAN',
                       module_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/FINAL_WARM'))
                .fillna(module_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/FINAL_WARM'))
                .dropna(),
            utc=True), STAGE_COLORS['MODULE/PARYLENE_UNMASKING'], '-'),
        ('Final Cold',        pd.to_datetime(module_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/FINAL_COLD').dropna(),         utc=True), STAGE_COLORS['MODULE/FINAL_COLD'],         '-'),
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


def create_cumulative_pipeline_plot(module_df, bm_df, flex_df, cluster, base_path, data_date=None):
    fig, ax = plt.subplots(figsize=(8, 5))
    _plot_cumulative_pipeline(
        ax,
        module_df[module_df['MODULE.cluster_code'] == cluster],
        bm_df[bm_df['BARE_MODULE.cluster_code'] == cluster],
        flex_df[flex_df['PCB.cluster_code'] == cluster],
    )
    ax.text(0.05, 1.02, cluster.replace('PIXEL_MODULE_', ''), transform=ax.transAxes, fontsize=16)
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    fig.savefig(f"{base_path}/cumulative_pipeline_{cluster}.png")
    plt.close(fig)


_SITE_STAGES = [
    ('MODULE/ASSEMBLY',           'Assembled',         STAGE_COLORS['MODULE/ASSEMBLY']),
    ('MODULE/INITIAL_WARM',       'Initial Warm',      STAGE_COLORS['MODULE/INITIAL_WARM']),
    ('MODULE/PARYLENE_UNMASKING', 'Parylene Unmasked', STAGE_COLORS['MODULE/PARYLENE_UNMASKING']),
    ('MODULE/FINAL_COLD',         'Final Cold',        STAGE_COLORS['MODULE/FINAL_COLD']),
]


def _stage_institution_series(df, stage):
    """Series mapping each module to the institution where stage was signed off."""
    if stage == 'MODULE/ASSEMBLY':
        return df['MODULE.institution_code']
    return pd.Series(
        [get_stage_institution(inst, locs, stage)
         for inst, locs in zip(df['MODULE.institution_code'], df['MODULE.locations'])],
        index=df.index,
    )


def _site_stage_times(df, stage, site):
    """Timestamps for modules where stage was signed off at site."""
    mask = _stage_institution_series(df, stage) == site
    site_df = df[mask]
    if stage == 'MODULE/ASSEMBLY':
        return pd.to_datetime(site_df['MODULE.cts'].dropna())
    if stage == 'MODULE/PARYLENE_UNMASKING':
        is_japan = site_df['MODULE.cluster_code'] == 'PIXEL_MODULE_JAPAN'
        parylene_ts = site_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/PARYLENE_UNMASKING')
        final_warm_ts = site_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name='MODULE/FINAL_WARM')
        return pd.to_datetime(parylene_ts.where(~is_japan, final_warm_ts).fillna(final_warm_ts).dropna())
    return pd.to_datetime(site_df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name=stage).dropna())


def _all_sites(df):
    """Union of institutions that appear at any of the four pipeline stages."""
    sites = set()
    for stage, _, _ in _SITE_STAGES:
        sites.update(_stage_institution_series(df, stage).dropna().unique())
    return sorted(sites)


def create_monthly_by_site(df, base_path, cluster='', data_date=None):
    for site in _all_sites(df):
        stage_times, min_month, max_month = [], None, None
        for stage, _, _ in _SITE_STAGES:
            times = _site_stage_times(df, stage, site)
            norm = times.dt.month + 12 * (times.dt.year - 2024)
            if len(norm):
                min_month = norm.min() if min_month is None else min(min_month, norm.min())
                max_month = norm.max() if max_month is None else max(max_month, norm.max())
            stage_times.append(norm)
        if min_month is None:
            continue
        fig = plt.figure(figsize=(8, 5))
        bin_names = [f'{mo % 12:2}/{2024 + (mo // 12)}' for mo in range(min_month, max_month + 1)]
        plt.hist(stage_times, bins=len(bin_names), range=(min_month, max_month + 1),
                 histtype='bar', label=[lbl for _, lbl, _ in _SITE_STAGES],
                 color=[c for _, _, c in _SITE_STAGES], stacked=False)
        plt.xticks(ticks=range(min_month, max_month + 1), labels=bin_names, rotation=45)
        plt.legend()
        plt.xlabel('Sign-Off Month')
        plt.ylabel('# Modules / Month')
        fig.tight_layout()
        plt.text(0.05, 1.02, f'{cluster.replace("PIXEL_MODULE_", "")} — {site}',
                 transform=plt.gca().transAxes, fontsize=16)
        add_data_timestamp(fig, data_date)
        plt.savefig(f'{base_path}/monthly_by_site_{cluster}_{site}.png')
        plt.close(fig)


def create_cumulative_by_site(df, base_path, cluster='', data_date=None):
    for site in _all_sites(df):
        fig, ax = plt.subplots(figsize=(8, 5))
        any_data = False
        for stage, label, color in _SITE_STAGES:
            times = pd.to_datetime(_site_stage_times(df, stage, site), utc=True)
            if len(times) == 0:
                continue
            any_data = True
            weekly = pd.Series(1, index=times).resample('W').sum().cumsum()
            ax.plot(weekly.index, weekly.values, label=label, color=color, linewidth=2)
        if not any_data:
            plt.close(fig)
            continue
        ax.set_xlabel('Week')
        ax.set_ylabel('# Modules')
        ax.legend(fontsize=11, loc='upper left')
        ax.tick_params(axis='x', rotation=45)
        ax.text(0.05, 1.02, f'{cluster.replace("PIXEL_MODULE_", "")} — {site}',
                transform=ax.transAxes, fontsize=16)
        fig.tight_layout()
        add_data_timestamp(fig, data_date)
        fig.savefig(f'{base_path}/cumulative_by_site_{cluster}_{site}.png')
        plt.close(fig)


def create_cumulative_all_clusters(module_df, bm_df, flex_df, base_path, data_date=None):
    fig, ax = plt.subplots(figsize=(8, 5))
    _plot_cumulative_pipeline(ax, module_df, bm_df, flex_df)
    ax.text(0.05, 1.02, 'All Clusters', transform=ax.transAxes, fontsize=16)
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    fig.savefig(f"{base_path}/cumulative_pipeline.png")
    plt.close(fig)
