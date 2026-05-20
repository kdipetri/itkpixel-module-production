#!/usr/bin/env python3
"""
Fetches all PCB data from the ITk Production Database, then saves a
preprocessed JSON cache to cache/pcb_data.json plus text summaries to
output/.  Run this once (or when data needs refreshing); then run
analysis_pcb.py to regenerate plots quickly without hitting the API.
"""

import io
import json
import os
import datetime
from collections import defaultdict
from datetime import datetime as dt

import itkdb

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR  = os.path.join(BASE_DIR, 'cache')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
CACHE_FILE = os.path.join(CACHE_DIR, 'pcb_data.json')
os.makedirs(CACHE_DIR,  exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

def out(name):
    return os.path.join(OUTPUT_DIR, name)

# ── authenticate ──────────────────────────────────────────────────────────────

print("Authenticating...")
client = itkdb.Client()
client.user.authenticate()
timestamp = datetime.datetime.now().strftime("%Y-%m-%d")

# ── site list + exclusions ────────────────────────────────────────────────────

flex_qc_site_list = ["INFN_LECCE", "SIEGEN", "OKLAHOMA", "EDI", "GL", "PUC"]

excluded_shipment_ids = {
    "INFN_LECCE": set(),
    "SIEGEN":     set(),
    "OKLAHOMA":   set(),
    "EDI":        set(),
    "GL":         {"6894b7e4b16897606cd863b3", "68405a72d71df4c27766e77a", "6882024fab2d5c9bc1d08fba"},
    "PUC":        set(),
}

# ── fetch shipments ───────────────────────────────────────────────────────────

def get_all_shipments(client, flex_qc_site, page_size=200):
    all_shipments = []
    page_index = 0
    while True:
        shipments = client.get("listShipmentsByInstitution", json={
            "outputType": "full",
            "filterMap": {"recipient": [flex_qc_site], "state": ["ready"], "status": ["delivered"]},
            "pageInfo": {"pageIndex": page_index, "pageSize": page_size},
        })
        if not shipments or not shipments.data:
            break
        page_results = shipments.data
        all_shipments.extend(page_results)
        if len(page_results) < page_size:
            break
        page_index += 1
    return all_shipments

print("Fetching shipments...")
raw_shipments = {}
for flex_qc_site in flex_qc_site_list:
    shipments = get_all_shipments(client, flex_qc_site)
    excluded = excluded_shipment_ids.get(flex_qc_site, set())
    raw_shipments[flex_qc_site] = [s for s in shipments if s["id"] not in excluded]

raw_shipments['UK'] = raw_shipments['EDI'] + raw_shipments['GL']
del raw_shipments['EDI']
del raw_shipments['GL']

# ── fetch shipment items (heavy ~1 min) ───────────────────────────────────────

def get_all_shipment_items(client, shipment_id, page_size=200):
    all_items = []
    page_index = 0
    while True:
        shipment_items = client.get("listShipmentItems", json={
            "shipment": shipment_id,
            "pageInfo": {"pageIndex": page_index, "pageSize": page_size},
        })
        if not shipment_items or not shipment_items.data:
            break
        all_items.extend(shipment_items.data)
        if len(shipment_items.data) < page_size:
            break
        page_index += 1
    return all_items

print("Fetching shipment items (heavy, ~1 min)...")
filtered_shipments = {}
for flex_qc_site, shipments in raw_shipments.items():
    site_filtered = []
    for shipment in shipments:
        all_items = get_all_shipment_items(client, shipment["id"])
        pcb_items = [item for item in all_items
                     if item.get('component', {}).get('componentType', {}).get('code') == 'PCB']
        if pcb_items:
            shipment['pcb_count'] = len(pcb_items)
            shipment['pcb_ids']   = [item['component']['id'] for item in pcb_items]
            site_filtered.append(shipment)
    filtered_shipments[flex_qc_site] = site_filtered

# save shipment summary before production filter (matches notebook cell 5 output)
buf = io.StringIO()
print(f"Data extracted on: {timestamp}\n", file=buf)
for site, shipments in filtered_shipments.items():
    total_pcb = sum(s['pcb_count'] for s in shipments)
    print(f"\n{site}: {len(shipments)} shipments, {total_pcb} PCBs total", file=buf)
    for s in shipments:
        print(f"  {s['id']}  {s['receivedTs']}  {s['pcb_count']} PCBs", file=buf)
        for pcb_id in s['pcb_ids']:
            print(f"    {pcb_id}", file=buf)
with open(out('01_shipment_summary.txt'), 'w') as f:
    f.write(buf.getvalue())
print("  saved 01_shipment_summary.txt")

# ── fetch PCB component info (heavy ~1 min) ───────────────────────────────────

PRODUCTION_DESIGN_VERSIONS = {'4', '5'}
CHUNK_SIZE = 500

def is_production_pcb(component):
    if not any(b.get('batchType', {}).get('code') == 'PIXEL_POPULATED_PCB_BATCH'
               for b in component.get('batches', [])):
        return False
    design_version = next(
        (p['value'] for p in component.get('properties', [])
         if p['code'] == 'PCB_DESIGN_VERSION'), None
    )
    return design_version in PRODUCTION_DESIGN_VERSIONS

print("Fetching PCB component data (heavy, ~1 min)...")
pcb_components = {}
for flex_qc_site, shipments in filtered_shipments.items():
    site_pcb_ids = list(set(pid for s in shipments for pid in s['pcb_ids']))
    print(f"  {flex_qc_site}: fetching {len(site_pcb_ids)} PCBs...")
    site_components = {}
    for i in range(0, len(site_pcb_ids), CHUNK_SIZE):
        chunk = site_pcb_ids[i:i + CHUNK_SIZE]
        results = client.get("getComponentBulk", json={
            "component": chunk, "identifierType": "id", "outputType": "full",
        })
        if results:
            for component in results:
                site_components[component['id']] = component
    before = len(site_components)
    site_components = {pid: c for pid, c in site_components.items() if is_production_pcb(c)}
    after = len(site_components)
    print(f"    -> {before} fetched, {after} production populated PCBs ({before - after} removed)")
    pcb_components[flex_qc_site] = site_components

# ── reconcile filtered_shipments to production PCBs only ─────────────────────

production_ids_per_site = {site: set(components.keys())
                           for site, components in pcb_components.items()}
for flex_qc_site, shipments in filtered_shipments.items():
    production_ids = production_ids_per_site.get(flex_qc_site, set())
    for shipment in shipments:
        new_ids = [pid for pid in shipment['pcb_ids'] if pid in production_ids]
        shipment['pcb_ids']   = new_ids
        shipment['pcb_count'] = len(new_ids)
    filtered_shipments[flex_qc_site] = [s for s in shipments if s['pcb_count'] > 0]

# ── group by month + deduplicate across all sites ─────────────────────────────

all_shipments_flat = [s for shipments in filtered_shipments.values() for s in shipments]
sorted_all = sorted(all_shipments_flat, key=lambda s: s['receivedTs'])

global_last_occurrence = {}
seen_pcb_ids = set()
for shipment in reversed(sorted_all):
    for pid in shipment['pcb_ids']:
        if pid not in seen_pcb_ids:
            seen_pcb_ids.add(pid)
            global_last_occurrence[pid] = shipment['id']

grouped_shipments = {}
for flex_qc_site, shipments in filtered_shipments.items():
    sorted_shipments = sorted(shipments, key=lambda s: s['receivedTs'])
    groups = defaultdict(list)
    for shipment in sorted_shipments:
        d = dt.fromisoformat(shipment['receivedTs'].replace('Z', '+00:00'))
        month_year = d.strftime('%m-%Y')
        new_pcb_ids = [pid for pid in shipment['pcb_ids']
                       if global_last_occurrence.get(pid) == shipment['id']]
        if new_pcb_ids:
            groups[month_year].append({
                'pcb_count': len(new_pcb_ids),
                'pcb_ids':   new_pcb_ids,
            })
    grouped_shipments[flex_qc_site] = dict(groups)

# ── extract per-PCB QC stage info ────────────────────────────────────────────

BOM_VERSION_MAP = {
    '10': 'V1.1 L0', '11': 'V1.1 L1', '12': 'V1.1 L2',
    '20': 'V2 L0',   '21': 'V2 L1',   '22': 'V2 L2',
}

def get_stage_info(component, stage_code):
    pcb_stage = next(
        (s for s in component.get('stages', []) if s['code'] == stage_code), None
    )
    if pcb_stage is None:
        return False, None
    ts = dt.fromisoformat(pcb_stage['dateTime'].replace('Z', '+00:00'))
    return True, ts.strftime('%m-%Y')

pcb_ready_info = {}
for flex_qc_site, components in pcb_components.items():
    site_info = {}
    for pcb_id, component in components.items():
        qc_done,       qc_month       = get_stage_info(component, "PCB_READY_FOR_MODULE")
        unhappy_done,  unhappy_month  = get_stage_info(component, "UNHAPPY")
        graveyard_done, graveyard_month = get_stage_info(component, "PCB/GRAVEYARD")

        populated_batches = [b for b in component.get('batches', [])
                             if b.get('batchType', {}).get('code') == 'PIXEL_POPULATED_PCB_BATCH']
        populated_batch_number = (
            max(populated_batches, key=lambda b: b['stateTs'])['number']
            if populated_batches else None
        )

        design_version = next(
            (p['value'] for p in component.get('properties', [])
             if p['code'] == 'PCB_DESIGN_VERSION'), None
        )
        pcb_type = {'4': 'OS', '5': 'IS'}.get(design_version, 'UNKNOWN')

        bom_version = next(
            (p['value'] for p in component.get('properties', [])
             if p['code'] == 'PCB_BOM_VERSION'), None
        )

        current_location = component.get('currentLocation') or {}
        current_stage    = component.get('currentStage')    or {}

        site_info[pcb_id] = {
            'serial_number':        component.get('serialNumber'),
            'qc_done':              qc_done,       'qc_month':       qc_month,
            'unhappy_done':         unhappy_done,  'unhappy_month':  unhappy_month,
            'graveyard_done':       graveyard_done,'graveyard_month':graveyard_month,
            'populated_batch':      populated_batch_number,
            'design_version':       design_version,
            'pcb_type':             pcb_type,
            'bom_version':          bom_version,
            'bom_version_label':    BOM_VERSION_MAP.get(bom_version, bom_version),
            'current_location_code':current_location.get('code'),
            'current_location_name':current_location.get('name'),
            'current_stage_code':   current_stage.get('code'),
        }
    pcb_ready_info[flex_qc_site] = site_info

# deduplicate: keep each PCB only at its last-received site
pcb_to_site = {
    pcb_id: flex_qc_site
    for flex_qc_site, months in grouped_shipments.items()
    for month, shipments in months.items()
    for shipment in shipments
    for pcb_id in shipment['pcb_ids']
}

final_pcb_ready_info = {site: {} for site in pcb_ready_info}
for flex_qc_site, info in pcb_ready_info.items():
    for pcb_id, data in info.items():
        if pcb_to_site.get(pcb_id) == flex_qc_site:
            final_pcb_ready_info[flex_qc_site][pcb_id] = data
pcb_ready_info = final_pcb_ready_info

# ── save PCB ready summary (txt) ──────────────────────────────────────────────

buf = io.StringIO()
print(f"Data extracted on: {timestamp}\n", file=buf)
print("=== PCB Ready Summary ===", file=buf)
for flex_qc_site, info in pcb_ready_info.items():
    n_qc_done = sum(1 for v in info.values() if v['qc_done'])
    print(f"\n--- {flex_qc_site} ---", file=buf)
    print(f"Total PCBs: {len(info)}, Signed off: {n_qc_done}", file=buf)
    print("-" * 94, file=buf)
    print(f"{'Serial':<14} {'Batch':<40} {'PCB_READY':<12} {'UNHAPPY':<12} {'GRAVEYARD':<12}", file=buf)
    print("-" * 94, file=buf)
    for pcb_id, v in info.items():
        unhappy_str   = v['unhappy_month']   if v['unhappy_done']   else 'NA'
        graveyard_str = v['graveyard_month'] if v['graveyard_done'] else 'NA'
        qc_str        = (v['qc_month'] if v['qc_done']
                         else 'NA' if graveyard_str != 'NA'
                         else 'not yet')
        print(f"{str(v['serial_number']):<14} {str(v['populated_batch']):<40} "
              f"{qc_str:<12} {unhappy_str:<12} {graveyard_str:<12}", file=buf)
with open(out('02_pcb_ready_summary.txt'), 'w') as f:
    f.write(buf.getvalue())
print("  saved 02_pcb_ready_summary.txt")

# ── save JSON cache ───────────────────────────────────────────────────────────

cache = {
    "timestamp":        timestamp,
    "grouped_shipments": grouped_shipments,
    "pcb_ready_info":   pcb_ready_info,
}
with open(CACHE_FILE, 'w') as f:
    json.dump(cache, f)

print(f"\nCache saved to: {CACHE_FILE}")
print("Run analysis_pcb.py to generate plots from this cache.")
