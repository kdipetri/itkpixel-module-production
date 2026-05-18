import re
from pathlib import Path

import pandas as pd

from data import apply_cern_japan_cluster, filter_components, load_data
from moduleProductionPlots import modules_last_month
from utils import load_json, map_institution_to_group, _shorten_batch_name

CLUSTERS = [
    "PIXEL_MODULE_JAPAN",
    "PIXEL_MODULE_CERN_JAPAN",
    "PIXEL_MODULE_FRANCE",
    "PIXEL_MODULE_GERMANY_BMBF",
    "PIXEL_MODULE_GERMANY_MPI",
    "PIXEL_MODULE_UK",
    "PIXEL_MODULE_USA",
    "PIXEL_MODULE_ITALY",
    "PIXEL_MODULE_CERN",
]

CLUSTER_LABELS = {
    "PIXEL_MODULE_JAPAN":        "Japan",
    "PIXEL_MODULE_CERN_JAPAN":   "CERN–Japan",
    "PIXEL_MODULE_FRANCE":       "France",
    "PIXEL_MODULE_GERMANY_BMBF": "Germany BMBF",
    "PIXEL_MODULE_GERMANY_MPI":  "Germany MPI",
    "PIXEL_MODULE_UK":           "UK",
    "PIXEL_MODULE_USA":          "USA",
    "PIXEL_MODULE_ITALY":        "Italy",
    "PIXEL_MODULE_CERN":         "CERN",
}

CLUSTER_BM_ASSEMBLY_SITES = {}

# Per-cluster allowed institutions for PCB_READY_FOR_MODULE flexes.
# France: only IRFU/LPNHE are assembly sites; IJCLAB is electrical QC only.
# Clusters absent from this dict allow all sites.
CLUSTER_FLEX_ASSEMBLY_SITES = {
    'PIXEL_MODULE_FRANCE': {'IRFU', 'LPNHE'},
}

STAGE_ORDER = [
    'MODULE/INIT',
    'MODULE/ASSEMBLY',
    'MODULE/WIREBONDING',
    'MODULE/INITIAL_WARM',
    'MODULE/INITIAL_COLD',           # merged → INITIAL_WARM
    'MODULE/QC_CROSSCHECK',          # merged → INITIAL_WARM
    'MODULE/PARYLENE_MASKING',
    'MODULE/PARYLENE_COATING',
    'MODULE/PARYLENE_UNMASKING',
    'MODULE/WIREBOND_PROTECTION',
    'MODULE/POST_PARYLENE_WARM',     # merged → THERMAL_CYCLES
    'MODULE/POST_PARYLENE_COLD',     # merged → THERMAL_CYCLES
    'MODULE/THERMAL_CYCLES',
    'MODULE/LONG_TERM_STABILITY',    # merged → THERMAL_CYCLES
    'MODULE/FINAL_WARM',
    'MODULE/FINAL_COLD',
    'MODULE/FINAL_METROLOGY',        # merged → COMPLETE
    'MODULE/QC_STATUS',              # merged → COMPLETE
    'MODULE/COMPLETE',
    'MODULE/UNHAPPY',
    'MODULE/GRAVEYARD',
]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}

STAGE_MERGE = {
    'MODULE/INIT':                'MODULE/ASSEMBLY',
    'MODULE/WIREBONDING':         'MODULE/ASSEMBLY',
    'MODULE/PARYLENE_MASKING':    'MODULE/PARYLENE',
    'MODULE/PARYLENE_COATING':    'MODULE/PARYLENE',
    'MODULE/PARYLENE_UNMASKING':  'MODULE/PARYLENE',
    'MODULE/INITIAL_COLD':        'MODULE/INITIAL_WARM',
    'MODULE/QC_CROSSCHECK':       'MODULE/INITIAL_WARM',
    'MODULE/POST_PARYLENE_WARM':  'MODULE/THERMAL_CYCLES',
    'MODULE/POST_PARYLENE_COLD':  'MODULE/THERMAL_CYCLES',
    'MODULE/LONG_TERM_STABILITY': 'MODULE/THERMAL_CYCLES',
    'MODULE/FINAL_WARM':          'MODULE/FINAL_QC',
    'MODULE/FINAL_COLD':          'MODULE/FINAL_QC',
    'MODULE/FINAL_METROLOGY':     'MODULE/COMPLETE',
    'MODULE/QC_STATUS':           'MODULE/COMPLETE',
}
STAGE_DISPLAY = [
    'MODULE/ASSEMBLY', 'MODULE/INITIAL_WARM',
    'MODULE/PARYLENE',
    'MODULE/WIREBOND_PROTECTION',
    'MODULE/THERMAL_CYCLES', 'MODULE/FINAL_QC', 'MODULE/COMPLETE',
    'MODULE/UNHAPPY', 'MODULE/GRAVEYARD',
]
STAGE_SHORT = {s: s.replace('MODULE/', '') for s in STAGE_DISPLAY}
STAGE_SHORT['MODULE/WIREBOND_PROTECTION'] = 'OBWP'
STAGE_SHORT['BM_UNASM']   = 'Unasm. BM'
STAGE_SHORT['FLEX_UNASM'] = 'Unasm. Flex'

UNASM_BM_STAGES = {'BAREMODULERECEPTION', 'BAREMODULEASSEMBLY', 'MODULE/INIT'}


def _current_stage(stage_list):
    if any(s.get('code', '').startswith('OB_LOADED_') for s in stage_list):
        return 'MODULE/COMPLETE'
    # Use the last stage in chronological order
    for s in reversed(stage_list):
        code = s.get('code')
        if code in STAGE_RANK:
            return code
    return None


def make_stage_tally(module_df, bm_df, flex_df):
    df = module_df.copy()
    df['current_stage'] = df['MODULE.stages'].apply(_current_stage).replace(STAGE_MERGE)
    df = df[df['current_stage'].notna() & df['MODULE.cluster_code'].notna()]

    tally = (
        df.groupby(['MODULE.cluster_code', 'current_stage'])
        .size()
        .unstack(fill_value=0)
    )
    tally = tally.reindex(
        index=[c for c in CLUSTERS if c in tally.index],
        columns=[s for s in STAGE_DISPLAY if s in tally.columns],
        fill_value=0,
    )

    # unassembled bare modules: no parent module, last stage in pre-assembly pool
    def _bm_last_code(stages):
        return next((s.get('code') for s in reversed(stages) if s.get('code')), None)

    bm_last         = bm_df['BARE_MODULE.stages'].apply(_bm_last_code)
    bm_no_parent    = bm_df['BARE_MODULE.hasParent_MODULE'].isna()
    bm_is_unasm     = bm_last.isin(UNASM_BM_STAGES)
    excluded_sns    = {e['serialNumber'] for e in load_json('metadata/excluded_bms.json')}
    bm_not_excluded = ~bm_df['BARE_MODULE.serialNumber'].isin(excluded_sns)

    # Use current location for cluster — avoids misclassification from location-history routing
    bm_candidates = bm_df[bm_no_parent & bm_is_unasm & bm_not_excluded].copy()
    bm_candidates['unasm_cluster'] = bm_candidates['BARE_MODULE.currentLocation_code'].apply(map_institution_to_group)
    bm_at_site = bm_candidates.apply(
        lambda r: r['BARE_MODULE.currentLocation_code'] in CLUSTER_BM_ASSEMBLY_SITES.get(r['unasm_cluster'] or '', {r['BARE_MODULE.currentLocation_code']}),
        axis=1,
    )
    bm_unasm = (
        bm_candidates[bm_at_site]
        .groupby('unasm_cluster')
        .size()
        .reindex(tally.index, fill_value=0)
    )

    # unassembled flexes
    # _flex_current_stage: last non-UNHAPPY stage (used for COMPLETE — UNHAPPY+COMPLETE counts as done)
    # _flex_last_stage: true last stage (used for PCB_READY_FOR_MODULE — UNHAPPY flexes are NOT ready)
    def _flex_current_stage(stages):
        for s in reversed(stages):
            code = s.get('code') or s.get('stage', '')
            if code and code != 'UNHAPPY':
                return code
        return None

    def _flex_last_stage(stages):
        for s in reversed(stages):
            code = s.get('code') or s.get('stage', '')
            if code:
                return code
        return None

    def _flex_has(stages, code):
        return any(s.get('code') == code or s.get('stage') == code for s in stages)

    flex_current   = flex_df['PCB.stages'].apply(_flex_current_stage)
    flex_last      = flex_df['PCB.stages'].apply(_flex_last_stage)
    flex_no_parent = flex_df['PCB.hasParent_MODULE'].isna()
    flex_not_grav  = ~flex_df['PCB.stages'].apply(_flex_has, code='PCB/GRAVEYARD')

    # case 1: COMPLETE or PCB_RECEPTION_MODULE_SITE, no parent
    # UNHAPPY flexes whose last non-UNHAPPY stage is COMPLETE are counted (they're finished products)
    flex_ready_base = (
        flex_current.isin({'PCB_RECEPTION_MODULE_SITE', 'COMPLETE'}) &
        flex_no_parent & flex_not_grav
    )

    # case 2: PCB_READY_FOR_MODULE — at assembly site, not UNHAPPY, no parent
    # Uses true last stage: UNHAPPY flexes that once passed PCB_READY_FOR_MODULE are NOT available.
    # Edge case (UK): also include if parent module is past MODULE/WIREBONDING or in GRAVEYARD.
    def _past_wb(stages):
        c = _current_stage(stages)
        return c is not None and STAGE_RANK.get(c, -1) > STAGE_RANK['MODULE/WIREBONDING']

    modules_past_wb = set(
        module_df.loc[
            module_df['MODULE.stages'].apply(_past_wb),
            'MODULE.serialNumber',
        ]
    )
    flex_at_site = flex_df.apply(
        lambda r: r['PCB.currentLocation_code'] in CLUSTER_FLEX_ASSEMBLY_SITES.get(
            r['PCB.cluster_code'] or '', {r['PCB.currentLocation_code']}
        ),
        axis=1,
    )
    flex_pfm_parent_ok = flex_no_parent | flex_df['PCB.hasParent_MODULE'].isin(modules_past_wb)
    flex_ready_pfm = (
        (flex_last == 'PCB_READY_FOR_MODULE') &
        flex_at_site & flex_pfm_parent_ok & flex_not_grav
    )

    flex_unasm = (
        flex_df[flex_ready_base | flex_ready_pfm]
        .groupby('PCB.cluster_code')
        .size()
        .reindex(tally.index, fill_value=0)
    )

    tally['Total'] = tally.sum(axis=1)  # module-only total, before prepending BM/Flex

    tally.insert(0, 'BM_UNASM',   bm_unasm)
    tally.insert(0, 'FLEX_UNASM', flex_unasm)

    tally.index   = [CLUSTER_LABELS[c] for c in tally.index]
    tally.columns = [STAGE_SHORT.get(c, c) for c in tally.columns]

    return tally


BATCH_DISPLAY = ['BM_UNASM'] + list(STAGE_DISPLAY)


def make_batch_tallies(module_df, bm_df):
    """Returns dict of cluster_code → batch tally DataFrame (rows=batches, cols=stages)."""
    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)].copy()
    quad_df['current_stage'] = quad_df['MODULE.stages'].apply(_current_stage).replace(STAGE_MERGE)

    bm_to_module = {}
    for _, row in quad_df.iterrows():
        stage   = row['current_stage']
        cluster = row['MODULE.cluster_code']
        for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
            sn = row[col]
            if pd.notna(sn):
                bm_to_module[sn] = (cluster, stage)

    def _bm_last_code(stages):
        return next((s.get('code') for s in reversed(stages) if s.get('code')), None)

    bm_last      = bm_df['BARE_MODULE.stages'].apply(_bm_last_code)
    excluded_sns = {e['serialNumber'] for e in load_json('metadata/excluded_bms.json')}

    bm_candidates = bm_df[
        bm_df['BARE_MODULE.hasParent_MODULE'].isna() &
        bm_last.isin(UNASM_BM_STAGES) &
        bm_df['BARE_MODULE.batch_number'].notna() &
        ~bm_df['BARE_MODULE.serialNumber'].isin(excluded_sns)
    ].copy()
    bm_candidates['unasm_cluster'] = bm_candidates['BARE_MODULE.currentLocation_code'].apply(map_institution_to_group)
    bm_at_site = bm_candidates.apply(
        lambda r: r['BARE_MODULE.currentLocation_code'] in CLUSTER_BM_ASSEMBLY_SITES.get(r['unasm_cluster'] or '', {r['BARE_MODULE.currentLocation_code']}),
        axis=1,
    )
    unasm_sns = set(bm_candidates.loc[bm_at_site, 'BARE_MODULE.serialNumber'])
    unasm_cluster_map = bm_candidates.loc[bm_at_site].set_index('BARE_MODULE.serialNumber')['unasm_cluster'].to_dict()

    records = []
    for _, row in bm_df.iterrows():
        sn    = row['BARE_MODULE.serialNumber']
        batch = row['BARE_MODULE.batch_number']
        if pd.isna(batch):
            continue
        if sn in unasm_sns:
            cluster = unasm_cluster_map.get(sn)
            stage   = 'BM_UNASM'
        elif sn in bm_to_module:
            cluster, stage = bm_to_module[sn]
            if stage is None:
                continue
        else:
            continue
        if cluster not in CLUSTERS:
            continue
        records.append({'cluster': cluster, 'batch': batch, 'stage': stage})

    df = pd.DataFrame(records)
    all_stages = set(df['stage']) if len(df) else set()

    # Compute min cts per batch for date-ordering
    batch_min_cts = (
        bm_df[bm_df['BARE_MODULE.batch_number'].notna()]
        .groupby('BARE_MODULE.batch_number')['BARE_MODULE.cts']
        .min()
    )

    result = {}
    for cluster in CLUSTERS:
        sub = df[df['cluster'] == cluster]
        if sub.empty:
            continue
        cols = [c for c in BATCH_DISPLAY if c in all_stages]
        tally = (
            sub.groupby(['batch', 'stage']).size()
            .unstack(fill_value=0)
            .reindex(columns=cols, fill_value=0)
        )
        tally['Total'] = tally.sum(axis=1)
        # Sort by earliest BM creation timestamp in each batch
        sort_key = pd.Series({b: batch_min_cts.get(b, pd.NaT) for b in tally.index})
        tally = tally.iloc[sort_key.argsort().values]
        tally.columns  = [STAGE_SHORT.get(c, c) for c in tally.columns]
        tally.index    = [_shorten_batch_name(b) for b in tally.index]
        tally.index.name = 'Batch'
        result[cluster] = tally

    return result


def make_all_batches_tally(module_df, bm_df):
    """Batch tally across all clusters — rows=batches, cols=stages."""
    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)].copy()
    quad_df['current_stage'] = quad_df['MODULE.stages'].apply(_current_stage).replace(STAGE_MERGE)

    bm_to_stage = {}
    for _, row in quad_df.iterrows():
        stage = row['current_stage']
        for col in ['BARE_MODULE_0.serialNumber', 'BARE_MODULE_1.serialNumber', 'BARE_MODULE_2.serialNumber']:
            sn = row[col]
            if pd.notna(sn):
                bm_to_stage[sn] = stage

    def _bm_last_code(stages):
        return next((s.get('code') for s in reversed(stages) if s.get('code')), None)

    bm_last      = bm_df['BARE_MODULE.stages'].apply(_bm_last_code)
    excluded_sns = {e['serialNumber'] for e in load_json('metadata/excluded_bms.json')}

    unasm_sns = set(bm_df.loc[
        bm_df['BARE_MODULE.hasParent_MODULE'].isna() &
        bm_last.isin(UNASM_BM_STAGES) &
        bm_df['BARE_MODULE.batch_number'].notna() &
        ~bm_df['BARE_MODULE.serialNumber'].isin(excluded_sns),
        'BARE_MODULE.serialNumber',
    ])

    records = []
    for _, row in bm_df.iterrows():
        sn    = row['BARE_MODULE.serialNumber']
        batch = row['BARE_MODULE.batch_number']
        if pd.isna(batch):
            continue
        if sn in unasm_sns:
            stage = 'BM_UNASM'
        elif sn in bm_to_stage:
            stage = bm_to_stage[sn]
            if stage is None:
                continue
        else:
            continue
        records.append({'batch': batch, 'stage': stage})

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    all_stages = set(df['stage'])
    cols = [c for c in BATCH_DISPLAY if c in all_stages]

    batch_min_cts = (
        bm_df[bm_df['BARE_MODULE.batch_number'].notna()]
        .groupby('BARE_MODULE.batch_number')['BARE_MODULE.cts']
        .min()
    )

    tally = (
        df.groupby(['batch', 'stage']).size()
        .unstack(fill_value=0)
        .reindex(columns=cols, fill_value=0)
    )
    tally['Total'] = tally.sum(axis=1)
    sort_key = pd.Series({b: batch_min_cts.get(b, pd.NaT) for b in tally.index})
    tally = tally.iloc[sort_key.argsort().values]
    tally.columns    = [STAGE_SHORT.get(c, c) for c in tally.columns]
    tally.index      = [_shorten_batch_name(b) for b in tally.index]
    tally.index.name = 'Batch'
    return tally



def make_location_tallies(module_df, bm_df, flex_df):
    """Returns dict of cluster_code → DataFrame with rows = institution/location codes."""
    df = module_df.copy()
    df['current_stage'] = df['MODULE.stages'].apply(_current_stage).replace(STAGE_MERGE)
    df = df[df['current_stage'].notna() & df['MODULE.cluster_code'].notna()]

    # Unassembled BMs by location
    def _bm_last_code(stages):
        return next((s.get('code') for s in reversed(stages) if s.get('code')), None)

    bm_last         = bm_df['BARE_MODULE.stages'].apply(_bm_last_code)
    bm_no_parent    = bm_df['BARE_MODULE.hasParent_MODULE'].isna()
    bm_is_unasm     = bm_last.isin(UNASM_BM_STAGES)
    excluded_sns    = {e['serialNumber'] for e in load_json('metadata/excluded_bms.json')}
    bm_not_excluded = ~bm_df['BARE_MODULE.serialNumber'].isin(excluded_sns)
    bm_candidates   = bm_df[bm_no_parent & bm_is_unasm & bm_not_excluded].copy()
    bm_candidates['unasm_cluster'] = bm_candidates['BARE_MODULE.currentLocation_code'].apply(map_institution_to_group)
    bm_at_site = bm_candidates.apply(
        lambda r: r['BARE_MODULE.currentLocation_code'] in CLUSTER_BM_ASSEMBLY_SITES.get(
            r['unasm_cluster'] or '', {r['BARE_MODULE.currentLocation_code']}),
        axis=1,
    )
    bm_ready = bm_candidates[bm_at_site]

    # Unassembled flexes by location
    def _flex_current_stage(stages):
        for s in reversed(stages):
            code = s.get('code') or s.get('stage', '')
            if code and code != 'UNHAPPY':
                return code
        return None

    def _flex_last_stage(stages):
        for s in reversed(stages):
            code = s.get('code') or s.get('stage', '')
            if code:
                return code
        return None

    def _flex_has(stages, code):
        return any(s.get('code') == code or s.get('stage') == code for s in stages)

    flex_current   = flex_df['PCB.stages'].apply(_flex_current_stage)
    flex_last      = flex_df['PCB.stages'].apply(_flex_last_stage)
    flex_no_parent = flex_df['PCB.hasParent_MODULE'].isna()
    flex_not_grav  = ~flex_df['PCB.stages'].apply(_flex_has, code='PCB/GRAVEYARD')
    flex_ready_base = (
        flex_current.isin({'PCB_RECEPTION_MODULE_SITE', 'COMPLETE'}) &
        flex_no_parent & flex_not_grav
    )

    def _past_wb(stages):
        c = _current_stage(stages)
        return c is not None and STAGE_RANK.get(c, -1) > STAGE_RANK['MODULE/WIREBONDING']

    modules_past_wb = set(
        module_df.loc[module_df['MODULE.stages'].apply(_past_wb), 'MODULE.serialNumber']
    )
    flex_at_site = flex_df.apply(
        lambda r: r['PCB.currentLocation_code'] in CLUSTER_FLEX_ASSEMBLY_SITES.get(
            r['PCB.cluster_code'] or '', {r['PCB.currentLocation_code']}),
        axis=1,
    )
    flex_pfm_parent_ok = flex_no_parent | flex_df['PCB.hasParent_MODULE'].isin(modules_past_wb)
    flex_ready_pfm = (
        (flex_last == 'PCB_READY_FOR_MODULE') &
        flex_at_site & flex_pfm_parent_ok & flex_not_grav
    )
    flex_ready = flex_df[flex_ready_base | flex_ready_pfm]

    # Compute current institution from module location history
    def _get_current_inst(locs):
        # Handle numpy array, list, or None
        if locs is None or (hasattr(locs, '__len__') and len(locs) == 0):
            return None
        last = locs[-1]
        if isinstance(last, dict):
            return last.get('institution', None)
        return None

    df['current_inst'] = df['MODULE.locations'].apply(_get_current_inst)
    # Fallback to institution_code if no location history
    df['current_inst'] = df['current_inst'].fillna(df['MODULE.institution_code'])

    result = {}
    for cluster in CLUSTERS:
        df_c = df[df['MODULE.cluster_code'] == cluster]

        if df_c.empty:
            stage_pivot = pd.DataFrame(columns=STAGE_DISPLAY)
        else:
            stage_pivot = (
                df_c.groupby(['current_inst', 'current_stage'])
                .size()
                .unstack(fill_value=0)
            )
            for col in STAGE_DISPLAY:
                if col not in stage_pivot.columns:
                    stage_pivot[col] = 0
            stage_pivot = stage_pivot[[c for c in STAGE_DISPLAY if c in stage_pivot.columns]]

        bm_c       = bm_ready[bm_ready['unasm_cluster'] == cluster]
        bm_by_loc  = bm_c.groupby('BARE_MODULE.currentLocation_code').size()
        flex_c     = flex_ready[flex_ready['PCB.cluster_code'] == cluster]
        flex_by_loc = flex_c.groupby('PCB.currentLocation_code').size()

        all_locs = sorted(set(stage_pivot.index) | set(bm_by_loc.index) | set(flex_by_loc.index))
        if not all_locs:
            continue

        stage_pivot = stage_pivot.reindex(all_locs, fill_value=0)
        stage_pivot['Total'] = stage_pivot[[c for c in STAGE_DISPLAY if c in stage_pivot.columns]].sum(axis=1)
        stage_pivot.insert(0, 'BM_UNASM',   bm_by_loc.reindex(stage_pivot.index, fill_value=0))
        stage_pivot.insert(0, 'FLEX_UNASM', flex_by_loc.reindex(stage_pivot.index, fill_value=0))
        stage_pivot.columns  = [STAGE_SHORT.get(c, c) for c in stage_pivot.columns]
        stage_pivot.index.name = 'Location'
        result[cluster] = stage_pivot

    return result


def _render_html_table(tally, row_header='Cluster'):
    cols = tally.columns.tolist()

    def _td(val, col, is_label=False):
        classes = []
        if col == 'Total':
            classes.append('col-total')
        if not is_label and val == 0:
            classes.append('zero')
        cls = f' class="{" ".join(classes)}"' if classes else ''
        return f'<td{cls}>{val}</td>'

    header = f'<tr><th>{row_header}</th>' + ''.join(f'<th>{c}</th>' for c in cols) + '</tr>'

    body_rows = []
    for label, row in tally.iterrows():
        cells = _td(label, '', is_label=True)
        cells += ''.join(_td(row[c], c) for c in cols)
        body_rows.append(f'<tr>{cells}</tr>')

    totals = tally.sum()
    foot_cells = f'<td>Total</td>' + ''.join(_td(int(totals[c]), c) for c in cols)
    footer = f'<tr>{foot_cells}</tr>'

    return (
        '<table class="inv-table">'
        f'<thead>{header}</thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        f'<tfoot>{footer}</tfoot>'
        '</table>'
    )


def _inject_into_html(html_path, table_html, batch_tallies, all_batches_html, location_tallies=None, data_date=None, recent_activity_html=None):
    html = Path(html_path).read_text(encoding='utf-8')

    if data_date is not None:
        ts_html = f'<p style="font-size:12px; color:#888; margin-bottom:10px;">Data as of {data_date}</p>'
        html = re.sub(
            r'<!-- INV-TIMESTAMP-START -->.*?<!-- INV-TIMESTAMP-END -->',
            f'<!-- INV-TIMESTAMP-START -->\n{ts_html}\n<!-- INV-TIMESTAMP-END -->',
            html, flags=re.DOTALL,
        )

    html = re.sub(
        r'<!-- INV-TABLE-START -->.*?<!-- INV-TABLE-END -->',
        f'<!-- INV-TABLE-START -->\n{table_html}\n<!-- INV-TABLE-END -->',
        html, flags=re.DOTALL,
    )

    html = re.sub(
        r'<!-- ALL-BATCH-TABLE-START -->.*?<!-- ALL-BATCH-TABLE-END -->',
        f'<!-- ALL-BATCH-TABLE-START -->\n{all_batches_html}\n<!-- ALL-BATCH-TABLE-END -->',
        html, flags=re.DOTALL,
    )

    for cluster, tally in batch_tallies.items():
        batch_html = _render_html_table(tally, row_header='Batch')
        html = re.sub(
            rf'<!-- BATCH-TABLE-{cluster}-START -->.*?<!-- BATCH-TABLE-{cluster}-END -->',
            f'<!-- BATCH-TABLE-{cluster}-START -->\n{batch_html}\n<!-- BATCH-TABLE-{cluster}-END -->',
            html, flags=re.DOTALL,
        )

    if location_tallies:
        for cluster, tally in location_tallies.items():
            loc_html = _render_html_table(tally, row_header='Location')
            html = re.sub(
                rf'<!-- LOC-TABLE-{cluster}-START -->.*?<!-- LOC-TABLE-{cluster}-END -->',
                f'<!-- LOC-TABLE-{cluster}-START -->\n{loc_html}\n<!-- LOC-TABLE-{cluster}-END -->',
                html, flags=re.DOTALL,
            )

    if recent_activity_html is not None:
        if data_date is not None:
            ts_html = f'<p style="font-size:12px; color:#888; margin-bottom:10px;">Data as of {data_date}</p>'
            html = re.sub(
                r'<!-- RECENT-TIMESTAMP-START -->.*?<!-- RECENT-TIMESTAMP-END -->',
                f'<!-- RECENT-TIMESTAMP-START -->\n{ts_html}\n<!-- RECENT-TIMESTAMP-END -->',
                html, flags=re.DOTALL,
            )
        html = re.sub(
            r'<!-- RECENT-TABLE-START -->.*?<!-- RECENT-TABLE-END -->',
            f'<!-- RECENT-TABLE-START -->\n{recent_activity_html}\n<!-- RECENT-TABLE-END -->',
            html, flags=re.DOTALL,
        )

    Path(html_path).write_text(html, encoding='utf-8')


def main():
    out_dir = Path("inventory")
    out_dir.mkdir(exist_ok=True)

    module_df, bm_df, flex_df, data_date = load_data()
    module_df, bm_df, flex_df = filter_components(module_df, bm_df, flex_df)
    module_df, bm_df          = apply_cern_japan_cluster(module_df, bm_df)

    quad_df = module_df[module_df['MODULE.type_code'].str.contains('QUAD', na=False)]
    tally   = make_stage_tally(quad_df, bm_df, flex_df)

    out_path = out_dir / "module_stage_tally.csv"
    tally.to_csv(out_path)
    print(tally.to_string())
    print(f"\nSaved → {out_path}")

    batch_tallies = make_batch_tallies(module_df, bm_df)
    for cluster, bt in batch_tallies.items():
        bt.to_csv(out_dir / f"batch_inventory_{cluster}.csv")

    all_batches = make_all_batches_tally(module_df, bm_df)
    all_batches.to_csv(out_dir / "batch_inventory_ALL.csv")

    location_tallies = make_location_tallies(module_df, bm_df, flex_df)
    for cluster, lt in location_tallies.items():
        lt.to_csv(out_dir / f"location_inventory_{cluster}.csv")

    recent_tally = modules_last_month(quad_df, CLUSTERS)
    recent_tally.index = [CLUSTER_LABELS.get(c, c) for c in recent_tally.index]
    recent_tally.to_csv(out_dir / "recent_activity.csv")

    _inject_into_html(
        "index.html",
        _render_html_table(tally),
        batch_tallies,
        _render_html_table(all_batches, row_header='Batch'),
        location_tallies=location_tallies,
        data_date=data_date,
        recent_activity_html=_render_html_table(recent_tally),
    )
    print("Injected tables into index.html")


if __name__ == "__main__":
    main()
