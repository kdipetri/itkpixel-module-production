"""
List ITkPix pixel bare modules (v2 quads + all production singles) whose last
stage is NOT_USED, currently located at a module assembly site, with their flags.
"""
from pathlib import Path

import pandas as pd

from utils import load_json, parquet_to_df

# v2 quads: 20UPGB43... / v2 singles: 20UPGB13... (already captured by [124]3)
ITKPIX_V2_QUAD_SN_RE = r'20UPGB[124]3'

BM_COLS = [
    'BARE_MODULE.serialNumber',
    'BARE_MODULE.type_code',
    'BARE_MODULE.stages',
    'BARE_MODULE.flags',
    'BARE_MODULE.currentLocation_code',
    'BARE_MODULE.currentLocation_name',
    'BARE_MODULE.hasParent_MODULE',
    'BARE_MODULE.batch_number',
]


def _last_stage(stages) -> str | None:
    if stages is None or len(stages) == 0:
        return None
    return stages[-1]['code']


def main():
    bm_df, data_date = parquet_to_df(Path('data/df_pixel_baremodules.parquet'), columns=BM_COLS)

    cluster_maps = load_json('metadata/cluster_maps_MODULE.json')
    module_site_institutions: set[str] = set()
    for institutions in cluster_maps.values():
        module_site_institutions.update(institutions)

    # Production quads (v2 SN filter) + all production singles
    is_quad   = bm_df['BARE_MODULE.serialNumber'].str.contains(ITKPIX_V2_QUAD_SN_RE, na=False)
    is_single = bm_df['BARE_MODULE.type_code'] == 'SINGLE_BARE_MODULE'
    prod = bm_df[is_quad | is_single].copy()

    prod['last_stage'] = prod['BARE_MODULE.stages'].apply(_last_stage)

    mask = (
        (prod['last_stage'] == 'NOT_USED') &
        prod['BARE_MODULE.currentLocation_code'].isin(module_site_institutions)
    )
    result = prod[mask].copy()

    print(f'Data date: {data_date}')
    print(f'ITkPix BMs (quads v2 + singles) in NOT_USED stage at a module site: {len(result)}\n')

    for _, row in result.sort_values(['BARE_MODULE.currentLocation_code', 'BARE_MODULE.serialNumber']).iterrows():
        sn       = row['BARE_MODULE.serialNumber']
        tc       = row['BARE_MODULE.type_code']
        loc_code = row['BARE_MODULE.currentLocation_code']
        loc_name = row['BARE_MODULE.currentLocation_name']
        flags    = list(row['BARE_MODULE.flags']) if row['BARE_MODULE.flags'] is not None else []
        parent   = row['BARE_MODULE.hasParent_MODULE']
        batch    = row['BARE_MODULE.batch_number']
        print(f'  SN:       {sn}  [{tc}]')
        print(f'  Batch:    {batch}')
        print(f'  Location: {loc_code} ({loc_name})')
        print(f'  Parent:   {parent if pd.notna(parent) else "none"}')
        print(f'  Flags:    {flags if flags else "(none)"}')
        print()


if __name__ == '__main__':
    main()
