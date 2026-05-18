from pathlib import Path

from utils import (
    assign_clusters_from_locations,
    load_json,
    map_institution_to_group,
    parquet_to_df,
    _institution_lookup,
)

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
    'BARE_MODULE.stages', 'BARE_MODULE.batch_number', 'BARE_MODULE.cts',
]
FLEX_COLS = [
    'PCB.serialNumber', 'PCB.type_code',
    'PCB.PCB_BOM_VERSION', 'PCB.PCB_DESIGN_VERSION',
    'PCB.locations', 'PCB.currentLocation_code',
    'PCB.stages', 'PCB.hasParent_MODULE',
]


def load_data():
    module_df, data_date = parquet_to_df(Path("data/df_pixel_modules.parquet"), columns=MODULE_COLS)
    bm_df, _             = parquet_to_df(Path("data/df_pixel_baremodules.parquet"), columns=BM_COLS)
    flex_df, _           = parquet_to_df(Path("data/df_pixel_flex.parquet"), columns=FLEX_COLS)
    return module_df, bm_df, flex_df, data_date


def filter_components(module_df, bm_df, flex_df):
    preprod_mods   = module_df[module_df['MODULE.IS_PREPRODUCTION_MODULE'] == True]
    preprod_bm_sns = set()
    for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
        preprod_bm_sns.update(preprod_mods[col].dropna())
    preprod_module_sns = set(preprod_mods['MODULE.serialNumber'])

    sn_mask      = module_df['MODULE.serialNumber'].str.contains('20UP[IG]M[0125S][345]', na=False)
    preprod_mask = module_df['MODULE.IS_PREPRODUCTION_MODULE'].ne(True)
    bom_mask     = module_df['PCB_0.PCB_BOM_VERSION'].str.startswith('2', na=False)
    module_df = module_df[sn_mask & preprod_mask & bom_mask].copy()
    module_df['MODULE.cluster_code'] = module_df['MODULE.institution_code'].apply(map_institution_to_group)

    excluded_batches = {
        e['batch_number']
        for e in load_json("metadata/excluded_bm_batches.json")
    }
    excluded_batch_bm_sns = set(
        bm_df.loc[bm_df['BARE_MODULE.batch_number'].isin(excluded_batches), 'BARE_MODULE.serialNumber']
    )
    bm_df = bm_df[
        bm_df['BARE_MODULE.serialNumber'].str.contains('20UPGB[124]3', na=False) &
        (bm_df['BARE_MODULE.type_code'] == 'QUAD_BARE_MODULE') &
        ~bm_df['BARE_MODULE.batch_number'].isin(excluded_batches)
    ].copy()

    # Exclude modules that contain BMs from excluded batches; track their SNs to drop flexes too
    has_excluded_bm = (
        module_df['BARE_MODULE_0.serialNumber'].isin(excluded_batch_bm_sns) |
        module_df['BARE_MODULE_1.serialNumber'].isin(excluded_batch_bm_sns) |
        module_df['BARE_MODULE_2.serialNumber'].isin(excluded_batch_bm_sns)
    )
    excluded_module_sns = set(module_df.loc[has_excluded_bm, 'MODULE.serialNumber'])
    module_df = module_df[~has_excluded_bm].copy()
    bm_df['BARE_MODULE.cluster_code'] = assign_clusters_from_locations(
        bm_df['BARE_MODULE.locations'],
        ('BAREMODULERECEPTION', 'MODULE/INIT', 'MODULE/ASSEMBLY'),
        bm_df['BARE_MODULE.currentLocation_code'],
    )

    flex_df = flex_df[
        (flex_df['PCB.type_code'] == 'QUAD_PCB') &
        flex_df['PCB.PCB_BOM_VERSION'].str.startswith('2', na=False) &
        flex_df['PCB.PCB_DESIGN_VERSION'].isin(['4', '5']) &
        ~flex_df['PCB.hasParent_MODULE'].isin(excluded_module_sns)
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

    cern_insts        = set(load_json("metadata/cluster_maps_MODULE.json")[cfg["cern_cluster"]])
    completion_stages = set(cfg.get("completion_stages", ["MODULE/COMPLETE", "MODULE/FINAL_COLD"]))

    def _shipped_to_cern_before_completion(locations):
        cern_entries = [loc for loc in locations if loc.get('institution', '') in cern_insts]
        if not cern_entries:
            return False
        return any(loc.get('stage', '') not in completion_stages for loc in cern_entries)

    module_df = module_df.copy()
    japan_idx = module_df.index[module_df['MODULE.cluster_code'] == source]
    cern_visit = module_df.loc[japan_idx, 'MODULE.locations'].apply(_shipped_to_cern_before_completion)
    mod_mask = cern_visit[cern_visit].index
    module_df.loc[mod_mask, 'MODULE.cluster_code'] = target

    reclassified_bm_sns = set()
    for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
        reclassified_bm_sns.update(module_df.loc[mod_mask, col].dropna())
    bm_df = bm_df.copy()
    bm_mask = (
        (bm_df['BARE_MODULE.cluster_code'] == source) &
        (bm_df['BARE_MODULE.serialNumber'].isin(reclassified_bm_sns))
    )
    bm_df.loc[bm_mask, 'BARE_MODULE.cluster_code'] = target

    return module_df, bm_df
