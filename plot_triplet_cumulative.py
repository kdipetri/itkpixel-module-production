from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
import pandas as pd

from data import load_data
from utils import _fmt_stage, add_data_timestamp, get_earliest_stage_timestamp, get_latest_stage_timestamp

plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.labelsize": 16,
    "legend.fontsize": 14,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})
plt.style.use("seaborn-v0_8-whitegrid")

STAGE_COLORS = {
    'MODULE/ASSEMBLY':     'C0',
    'MODULE/INITIAL_WARM': 'C1',
    'MODULE/FINAL_COLD':   'C3',
}

# Production filter per flavor:
#   STAVE: IS_PREPRODUCTION_MODULE == False (BOM often unset, so BOM filter drops 8 real modules)
#   RING0 / RING0.5: BOM version starts with '2' (IS_PREPRODUCTION_MODULE is None for all)
FLAVORS = [
    ('L0',  'TRIPLET_L0_STAVE_MODULE',   'C0', lambda df: df['MODULE.IS_PREPRODUCTION_MODULE'] == False),
    ('R0',  'TRIPLET_L0_RING0_MODULE',   'C1', lambda df: df['PCB_0.PCB_BOM_VERSION'].str.startswith('2', na=False)),
    ('R05', 'TRIPLET_L0_RING0.5_MODULE', 'C2', lambda df: df['PCB_0.PCB_BOM_VERSION'].str.startswith('2', na=False)),
]


def _all_triplets(module_df):
    frames = []
    for _, type_code, _, prod_filter in FLAVORS:
        df = module_df[module_df['MODULE.type_code'] == type_code]
        frames.append(df[prod_filter(df)])
    return pd.concat(frames) if frames else pd.DataFrame()


def create_monthly_triplets(module_df, data_date=None):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_COLD']
    df = _all_triplets(module_df)

    fig = plt.figure(figsize=(8, 5))
    stage_times = []
    min_month = max_month = None

    for stage in stages:
        if stage == 'MODULE/ASSEMBLY':
            times = pd.to_datetime(df['MODULE.cts'].dropna().sort_values())
        else:
            times = df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage)
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
    plt.text(0.05, 1.02, 'Triplet Modules', transform=plt.gca().transAxes, fontsize=16)
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    out = 'plots/monthly_triplets.png'
    plt.savefig(out)
    plt.close(fig)
    print(f'Saved {out}')


def triplets_last_month(module_df):
    stages    = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_COLD']
    col_names = ['ASSEMBLY',        'INITIAL_WARM',        'FINAL_COLD']
    now      = datetime.now(timezone.utc)
    min_date = now - timedelta(days=30)
    rows = {}
    for label, type_code, _, prod_filter in FLAVORS:
        df = module_df[module_df['MODULE.type_code'] == type_code]
        df = df[prod_filter(df)]
        counts = []
        for stage in stages:
            if stage == 'MODULE/ASSEMBLY':
                times = pd.to_datetime(df['MODULE.cts'].dropna(), utc=True)
            else:
                times = df['MODULE.stages'].apply(get_earliest_stage_timestamp, stage_name=stage)
                times = pd.to_datetime(times[times.notna()], utc=True)
            counts.append(len(times[(min_date <= times) & (times <= now)]))
        rows[label] = counts
    result = pd.DataFrame.from_dict(rows, orient='index', columns=col_names)
    print('flavor', *col_names)
    for flavor, row in result.iterrows():
        print(f'{flavor:4s}', *row.tolist())
    return result


def main():
    module_df, bm_df, flex_df, data_date = load_data()

    fig, ax = plt.subplots(figsize=(8, 5))

    for label, type_code, color, prod_filter in FLAVORS:
        df = module_df[module_df['MODULE.type_code'] == type_code]
        df = df[prod_filter(df)]
        times = pd.to_datetime(
            df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/INIT').dropna(),
            utc=True,
        )
        if len(times) == 0:
            continue
        weekly = pd.Series(1, index=times).resample('W').sum().cumsum()
        ax.plot(weekly.index, weekly.values, label=f"{label} ({len(times)})", color=color, linewidth=2)
        print(f"{label:4s}  {len(times):4d} modules  latest {times.max().date()}")

    ax.set_xlabel('Week')
    ax.set_ylabel('# Modules')
    ax.set_title('Assembled Triplet Modules')
    ax.legend()
    ax.tick_params(axis='x', rotation=45)
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    out = "plots/cumulative_triplets.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"Saved {out}")

    create_monthly_triplets(module_df, data_date)
    triplets_last_month(module_df)


if __name__ == "__main__":
    main()
