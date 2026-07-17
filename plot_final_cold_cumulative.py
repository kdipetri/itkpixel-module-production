import matplotlib.pyplot as plt
import pandas as pd

from data import apply_cern_japan_cluster, filter_components, load_data
from utils import add_data_timestamp, get_latest_stage_timestamp

plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.labelsize": 16,
    "legend.fontsize": 14,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,
    "lines.linewidth": 2,
    "figure.figsize": (6, 5),
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})
plt.style.use("seaborn-v0_8-whitegrid")

Y_MAX = 2500


def main():
    module_df, bm_df, flex_df, data_date = load_data()
    module_df, bm_df, flex_df = filter_components(module_df, bm_df, flex_df)
    module_df, bm_df          = apply_cern_japan_cluster(module_df, bm_df)

    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)]

    times = pd.to_datetime(
        quad_df['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name='MODULE/FINAL_COLD').dropna(),
        utc=True,
    )

    weekly = pd.Series(1, index=times).resample('W').sum().cumsum()

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(weekly.index, weekly.values, linewidth=2)
    ax.set_xlabel('Week')
    ax.set_ylabel('# Modules')
    ax.set_ylim(0, Y_MAX)
    ax.set_title('All Quad Clusters —  Final Cold')
    ax.tick_params(axis='x', rotation=45)
    fig.tight_layout()
    add_data_timestamp(fig, data_date)
    out = "plots/cumulative_final_cold.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"Saved {out}  ({len(times)} modules, latest {times.max().date()})")


if __name__ == "__main__":
    main()
