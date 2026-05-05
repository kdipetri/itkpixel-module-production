from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
import pandas as pd

from data import apply_cern_japan_cluster, filter_components, load_data
from plot_duration import (
    STAGE_PAIRS,
    create_batch_duration_plot,
    create_stage_duration_histograms,
    create_stage_duration_summary,
)
from plot_throughput import (
    create_cumulative_all_clusters,
    create_cumulative_pipeline_plot,
    create_monthly_production,
    create_weekly_throughput,
)
from utils import get_latest_stage_timestamp

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

CLUSTERS = [
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

SHORT_STAGE_PAIRS = [
    ('MODULE/INIT',              'MODULE/WIREBONDING'),
    ('MODULE/WIREBONDING',       'MODULE/INITIAL_WARM'),
    ('MODULE/INITIAL_WARM',      'MODULE/PARYLENE_UNMASKING'),
    ('MODULE/PARYLENE_UNMASKING','MODULE/FINAL_COLD'),
]


def modules_last_month(df, clusters):
    stages = ['MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM', 'MODULE/FINAL_WARM', 'MODULE/FINAL_COLD']
    print('cluster', "ASSEMBLY", "INITIAL_WARM", "FINAL_WARM", "FINAL_COLD")
    now      = datetime.now(timezone.utc)
    min_date = now - timedelta(days=30)
    for cluster in clusters:
        df_cluster = df[df["MODULE.cluster_code"] == cluster]
        counts = []
        for stage in stages:
            times = pd.to_datetime(
                df_cluster['MODULE.stages'].apply(get_latest_stage_timestamp, stage_name=stage).dropna(),
                utc=True,
            )
            counts.append(len(times[(min_date <= times) & (times <= now)]))
        print(cluster, *counts)


def main():
    module_df, bm_df, flex_df = load_data()
    module_df, bm_df, flex_df = filter_components(module_df, bm_df, flex_df)
    module_df, bm_df          = apply_cern_japan_cluster(module_df, bm_df)

    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)]

    create_monthly_production(quad_df, "plots/monthly_modules.png")
    create_cumulative_all_clusters(module_df, bm_df, flex_df, "plots")

    for cluster in CLUSTERS:
        print(cluster)
        df_cluster = quad_df[quad_df['MODULE.cluster_code'] == cluster]
        create_monthly_production(df_cluster, f"plots/monthly_modules_{cluster}.png", cluster=cluster)
        create_weekly_throughput(df_cluster, f"plots/weekly_modules_{cluster}.png", cluster=cluster)
        create_cumulative_pipeline_plot(module_df, bm_df, flex_df, cluster, "plots")

    create_batch_duration_plot(quad_df, CLUSTERS, "plots")
    create_batch_duration_plot(quad_df, ['ALL'], "plots")
    modules_last_month(quad_df, CLUSTERS)
    create_stage_duration_histograms(quad_df, CLUSTERS, "plots")
    create_stage_duration_summary(quad_df, CLUSTERS, "plots")
    create_stage_duration_summary(quad_df, CLUSTERS, "plots", pairs=SHORT_STAGE_PAIRS, suffix='_short')
    create_stage_duration_summary(quad_df, ['ALL'], "plots", pairs=SHORT_STAGE_PAIRS, suffix='_short')


if __name__ == "__main__":
    main()
