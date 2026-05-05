import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pyarrow.parquet as pq


@lru_cache(maxsize=None)
def load_json(json_filename):
    with Path(json_filename).open("r") as f:
        return json.load(f)


def parquet_to_df(pq_filename, columns=None):
    table = pq.read_table(Path(pq_filename), columns=columns)
    df_date = datetime.fromisoformat(
        table.schema.metadata[b'timestamp'].decode('utf-8')
    ).date()
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


def _fmt_stage(name):
    return name.replace('MODULE/', '').replace('_', ' ')


def _shorten_batch_name(name):
    date_match = re.search(r'\(([^)]+)\)', name)
    date = date_match.group(1) if date_match else ''
    for full, abbr in [
        ('January', 'Jan'), ('February', 'Feb'), ('March', 'Mar'), ('April', 'Apr'),
        ('May', 'May'), ('June', 'Jun'), ('July', 'Jul'), ('August', 'Aug'),
        ('September', 'Sep'), ('October', 'Oct'), ('November', 'Nov'), ('December', 'Dec'),
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
