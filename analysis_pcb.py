#!/usr/bin/env python3
"""
Generates all plots, tables, and printouts from the preprocessed cache
produced by preprocess_pcb.py.  Outputs are written to ./output/.

Run preprocess_pcb.py first to populate cache/pcb_data.json.
"""

import io
import json
import os
import re
from collections import defaultdict
from datetime import datetime as dt

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({
    "font.size":        14,
    "axes.titlesize":   16,
    "axes.labelsize":   16,
    "legend.fontsize":  14,
    "xtick.labelsize":  14,
    "ytick.labelsize":  14,
})
plt.style.use("seaborn-v0_8-whitegrid")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, 'cache', 'pcb_data.json')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

def out(name):
    return os.path.join(OUTPUT_DIR, name)

# ── load cache ────────────────────────────────────────────────────────────────

if not os.path.exists(CACHE_FILE):
    raise SystemExit(
        f"Cache not found: {CACHE_FILE}\n"
        "Run preprocess_pcb.py first to fetch data from the ITk PDB."
    )

print(f"Loading cache from {CACHE_FILE}...")
with open(CACHE_FILE) as f:
    cache = json.load(f)

timestamp        = cache["timestamp"]
grouped_shipments = cache["grouped_shipments"]
pcb_ready_info   = cache["pcb_ready_info"]
print(f"  data extracted on: {timestamp}")

# ── rebuild derived lookups ───────────────────────────────────────────────────

pcb_to_site = {
    pcb_id: site
    for site, months in grouped_shipments.items()
    for shipments_list in months.values()
    for shipment in shipments_list
    for pcb_id in shipment['pcb_ids']
}

# ── shared helpers ────────────────────────────────────────────────────────────

def simplify_batch_name(batch_name):
    if batch_name is None:
        return 'UNKNOWN'
    match = re.search(r'(?:^|[_\s])(GO\d+|N\d+)(?:[_\s]|$)', batch_name)
    return match.group(1) if match else batch_name

sites = list(grouped_shipments.keys())
start_month = dt(2025, 1, 1)

all_months = sorted(set(
    dt.strptime(m, '%m-%Y')
    for groups in grouped_shipments.values()
    for m in groups.keys()
))
active_months = [m for m in all_months if m >= start_month]
month_labels  = [m.strftime('%m-%Y') for m in active_months]


def plot_stacked_bar(ax, bar_sites, data, labels, title, ylabel="Number of PCBs received"):
    x = np.arange(len(labels))
    bottoms = np.zeros(len(labels))
    for site in bar_sites:
        counts = np.array(data[site])
        ax.bar(x, counts, bottom=bottoms, label=site)
        for i, (count, bottom) in enumerate(zip(counts, bottoms)):
            if count > 0:
                ax.text(x[i], bottom + count / 2, str(count),
                        ha='center', va='center', fontsize=10, color='white', fontweight='bold')
        bottoms += counts
    totals = np.array([sum(data[s][i] for s in bar_sites) for i in range(len(labels))])
    for i, total in enumerate(totals):
        if total > 0:
            ax.text(x[i], total + 0.5, str(total),
                    ha='center', va='bottom', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_xlabel("Month")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.text(0.02, 0.95, f"Data extracted on: {timestamp}",
            transform=ax.transAxes, fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.5))
    ax.legend()


def plot_cumulative_area(ax, area_sites, data, labels, title, ylabel="Cumulative PCBs received"):
    x = np.arange(len(labels))
    cumulative = {site: np.cumsum(data[site]) for site in area_sites}
    bottoms = np.zeros(len(labels))
    for site in area_sites:
        values = cumulative[site]
        tops = bottoms + values
        ax.fill_between(x, bottoms, tops, alpha=0.7, label=site)
        for i in range(len(labels)):
            count = int(values[i])
            if count > 0 and (tops[i] - bottoms[i]) > 2:
                ax.text(x[i], bottoms[i] + 1, str(count),
                        ha='center', va='bottom', fontsize=9, color='black')
        bottoms = tops
    totals = sum(cumulative[site] for site in area_sites)
    for i, total in enumerate(totals):
        if total > 0:
            ax.text(x[i], total + 0.5, str(int(total)),
                    ha='center', va='bottom', fontsize=10, fontweight='bold', color='black')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_xlabel("Month")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.text(0.02, 0.95, f"Data extracted on: {timestamp}",
            transform=ax.transAxes, fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.5))
    ax.legend()

# ── 01 delivery plots (cell 11) ───────────────────────────────────────────────

def build_delivery_counts(pcb_type_filter=None):
    result = {}
    for site in sites:
        monthly = []
        for m in active_months:
            shipments = grouped_shipments[site].get(m.strftime('%m-%Y'), [])
            if pcb_type_filter is None:
                count = sum(s['pcb_count'] for s in shipments)
            else:
                count = sum(
                    sum(1 for pid in s['pcb_ids']
                        if pcb_ready_info.get(site, {}).get(pid, {}).get('pcb_type') == pcb_type_filter)
                    for s in shipments
                )
            monthly.append(count)
        result[site] = monthly
    return result

def build_multindex_delivery(data_os, data_is):
    df = pd.DataFrame(index=month_labels)
    df.index.name = "Month"
    for site in sites:
        df[(site, "OS")]    = data_os[site]
        df[(site, "IS")]    = data_is[site]
        df[(site, "Total")] = [data_os[site][i] + data_is[site][i] for i in range(len(active_months))]
    total_os = [sum(data_os[s][i] for s in sites) for i in range(len(active_months))]
    total_is = [sum(data_is[s][i] for s in sites) for i in range(len(active_months))]
    df[("Total", "OS")]    = total_os
    df[("Total", "IS")]    = total_is
    df[("Total", "Total")] = [total_os[i] + total_is[i] for i in range(len(active_months))]
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    df.loc["Total"] = df.sum()
    return df

def build_simple_delivery(data_single):
    df = pd.DataFrame(index=month_labels)
    df.index.name = "Month"
    for site in sites:
        df[(site, "")] = data_single[site]
    df[("Total", "")] = [sum(data_single[s][i] for s in sites) for i in range(len(active_months))]
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    df.loc["Total"] = df.sum()
    return df

print("Generating delivery plots...")
data    = build_delivery_counts()
data_os = build_delivery_counts('OS')
data_is = build_delivery_counts('IS')

fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, data, month_labels,
                 "Production PCBs delivered to flex QC sites (OS+IS)",
                 ylabel="PCBs/month")
fig.tight_layout()
fig.savefig(out('01_delivery_os_plus_is_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, data, month_labels,
                     "Cumulative PCBs delivered to flex QC sites (OS+IS)",
                     ylabel="PCBs")
fig.tight_layout()
fig.savefig(out('01_delivery_os_plus_is_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_multindex_delivery(data_os, data_is).to_csv(out('01_delivery_table_combined.csv'))

fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, data_os, month_labels,
                 "Production OS PCBs delivered to flex QC sites",
                 ylabel="PCBs/month")
fig.tight_layout()
fig.savefig(out('01_delivery_os_only_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, data_os, month_labels,
                     "Cumulative OS PCBs delivered to flex QC sites",
                     ylabel="PCBs")
fig.tight_layout()
fig.savefig(out('01_delivery_os_only_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_simple_delivery(data_os).to_csv(out('01_delivery_table_os.csv'))

fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, data_is, month_labels,
                 "Production IS PCBs delivered to flex QC sites",
                 ylabel="PCBs/month")
fig.tight_layout()
fig.savefig(out('01_delivery_is_only_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, data_is, month_labels,
                     "Cumulative IS PCBs delivered to flex QC sites",
                     ylabel="PCBs")
fig.tight_layout()
fig.savefig(out('01_delivery_is_only_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_simple_delivery(data_is).to_csv(out('01_delivery_table_is.csv'))
print("  saved 01_delivery_*.png + 01_delivery_table_*.csv")

# ── 02 QC ready plots + batch tables (cell 12) ───────────────────────────────

def build_ready_counts(pcb_type_filter=None):
    result = {}
    for flex_qc_site, info in pcb_ready_info.items():
        counts = defaultdict(int)
        for v in info.values():
            if pcb_type_filter and v['pcb_type'] != pcb_type_filter:
                continue
            if v['qc_done'] and v['qc_month']:
                counts[v['qc_month']] += 1
        result[flex_qc_site] = dict(counts)
    return result

qc_counts    = build_ready_counts()
qc_counts_os = build_ready_counts('OS')
qc_counts_is = build_ready_counts('IS')

all_qc_months   = sorted(set(m for c in qc_counts.values() for m in c.keys()),
                          key=lambda m: dt.strptime(m, '%m-%Y'))
active_qc_months = [m for m in all_qc_months if dt.strptime(m, '%m-%Y') >= dt(2025, 1, 1)]
qc_month_labels  = active_qc_months
qc_sites         = list(qc_counts.keys())

def build_qc_data(counts_dict):
    return {site: [counts_dict[site].get(m, 0) for m in active_qc_months] for site in qc_sites}

qc_data    = build_qc_data(qc_counts)
qc_data_os = build_qc_data(qc_counts_os)
qc_data_is = build_qc_data(qc_counts_is)

def build_multindex_qc(d_os, d_is):
    df = pd.DataFrame(index=qc_month_labels)
    df.index.name = "Month"
    for site in qc_sites:
        df[(site, "OS")]    = d_os[site]
        df[(site, "IS")]    = d_is[site]
        df[(site, "Total")] = [d_os[site][i] + d_is[site][i] for i in range(len(active_qc_months))]
    total_os = [sum(d_os[s][i] for s in qc_sites) for i in range(len(active_qc_months))]
    total_is = [sum(d_is[s][i] for s in qc_sites) for i in range(len(active_qc_months))]
    df[("Total", "OS")]    = total_os
    df[("Total", "IS")]    = total_is
    df[("Total", "Total")] = [total_os[i] + total_is[i] for i in range(len(active_qc_months))]
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    df.loc["Total"] = df.sum()
    return df

def build_simple_qc(d_single):
    df = pd.DataFrame(index=qc_month_labels)
    df.index.name = "Month"
    for site in qc_sites:
        df[(site, "")] = d_single[site]
    df[("Total", "")] = [sum(d_single[s][i] for s in qc_sites) for i in range(len(active_qc_months))]
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    df.loc["Total"] = df.sum()
    return df

def build_batch_table(pcb_type_filter=None):
    batch_summary = defaultdict(lambda: defaultdict(lambda: {
        'total': 0, 'total_os': 0, 'total_is': 0,
        'ready': 0, 'ready_os': 0, 'ready_is': 0,
        'not_ready_unhappy': 0, 'not_ready_graveyard': 0, 'not_ready_yet': 0,
    }))
    for flex_qc_site, info in pcb_ready_info.items():
        for pcb_id, v in info.items():
            if pcb_type_filter and v['pcb_type'] != pcb_type_filter:
                continue
            batch = simplify_batch_name(v['populated_batch'])
            s = batch_summary[batch][flex_qc_site]
            s['total'] += 1
            s[f"total_{v['pcb_type'].lower()}"] += 1
            if v['qc_done']:
                s['ready'] += 1
                s[f"ready_{v['pcb_type'].lower()}"] += 1
            elif v['unhappy_done']:
                s['not_ready_unhappy'] += 1
            elif v['graveyard_done']:
                s['not_ready_graveyard'] += 1
            else:
                s['not_ready_yet'] += 1

    rows = []
    for batch, batch_sites in sorted(batch_summary.items()):
        bt = {k: 0 for k in ('total', 'total_os', 'total_is',
                              'ready', 'ready_os', 'ready_is',
                              'not_ready_unhappy', 'not_ready_graveyard', 'not_ready_yet')}
        for site, s in sorted(batch_sites.items()):
            total = s['total']
            row = {
                'Batch': batch, 'Site': site, 'Total': total,
                'Ready':                 s['ready'],
                'Not ready (unhappy)':   s['not_ready_unhappy'],
                'Not ready (graveyard)': s['not_ready_graveyard'],
                'Not ready (yet)':       s['not_ready_yet'],
                '% ready':                f"{100 * s['ready'] / total:.0f}%" if total > 0 else "N/A",
                '% not ready (unhappy)':  f"{100 * s['not_ready_unhappy'] / total:.0f}%" if total > 0 else "N/A",
                '% not ready (graveyard)':f"{100 * s['not_ready_graveyard'] / total:.0f}%" if total > 0 else "N/A",
                '% not ready (yet)':      f"{100 * s['not_ready_yet'] / total:.0f}%" if total > 0 else "N/A",
            }
            if pcb_type_filter is None:
                row['OS'] = s['total_os'];  row['IS'] = s['total_is']
                row['Ready OS'] = s['ready_os']; row['Ready IS'] = s['ready_is']
            rows.append(row)
            for k in bt:
                bt[k] += s[k]

        total = bt['total']
        total_row = {
            'Batch': batch, 'Site': 'Total', 'Total': total,
            'Ready':                 bt['ready'],
            'Not ready (unhappy)':   bt['not_ready_unhappy'],
            'Not ready (graveyard)': bt['not_ready_graveyard'],
            'Not ready (yet)':       bt['not_ready_yet'],
            '% ready':                f"{100 * bt['ready'] / total:.0f}%" if total > 0 else "N/A",
            '% not ready (unhappy)':  f"{100 * bt['not_ready_unhappy'] / total:.0f}%" if total > 0 else "N/A",
            '% not ready (graveyard)':f"{100 * bt['not_ready_graveyard'] / total:.0f}%" if total > 0 else "N/A",
            '% not ready (yet)':      f"{100 * bt['not_ready_yet'] / total:.0f}%" if total > 0 else "N/A",
        }
        if pcb_type_filter is None:
            total_row['OS'] = bt['total_os']; total_row['IS'] = bt['total_is']
            total_row['Ready OS'] = bt['ready_os']; total_row['Ready IS'] = bt['ready_is']
        rows.append(total_row)

    cols = ['Batch', 'Site', 'Total']
    if pcb_type_filter is None:
        cols += ['OS', 'IS']
    cols += ['Ready']
    if pcb_type_filter is None:
        cols += ['Ready OS', 'Ready IS']
    cols += ['Not ready (unhappy)', 'Not ready (graveyard)', 'Not ready (yet)',
             '% ready', '% not ready (unhappy)', '% not ready (graveyard)', '% not ready (yet)']
    return pd.DataFrame(rows, columns=cols).set_index(['Batch', 'Site'])

print("Generating QC ready plots...")
fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, qc_data, qc_month_labels,
                 "PCBs reaching 'ready for module' per month per site (OS+IS)",
                 ylabel="Number of PCBs")
fig.tight_layout()
fig.savefig(out('02_qc_ready_os_plus_is_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, qc_data, qc_month_labels,
                     "Cumulative PCBs ready for module per site (OS+IS)",
                     ylabel="Cumulative PCBs ready for module")
fig.tight_layout()
fig.savefig(out('02_qc_ready_os_plus_is_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_multindex_qc(qc_data_os, qc_data_is).to_csv(out('02_qc_table_combined.csv'))

fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, qc_data_os, qc_month_labels,
                 "OS PCBs reaching 'ready for module' per month per site",
                 ylabel="Number of PCBs")
fig.tight_layout()
fig.savefig(out('02_qc_ready_os_only_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, qc_data_os, qc_month_labels,
                     "Cumulative OS PCBs ready for module per site",
                     ylabel="Cumulative PCBs ready for module")
fig.tight_layout()
fig.savefig(out('02_qc_ready_os_only_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_simple_qc(qc_data_os).to_csv(out('02_qc_table_os.csv'))

fig, ax = plt.subplots(figsize=(10, 6))
plot_stacked_bar(ax, sites, qc_data_is, qc_month_labels,
                 "IS PCBs reaching 'ready for module' per month per site",
                 ylabel="Number of PCBs")
fig.tight_layout()
fig.savefig(out('02_qc_ready_is_only_bar.png'), dpi=150, bbox_inches='tight')
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 6))
plot_cumulative_area(ax, sites, qc_data_is, qc_month_labels,
                     "Cumulative IS PCBs ready for module per site",
                     ylabel="Cumulative PCBs ready for module")
fig.tight_layout()
fig.savefig(out('02_qc_ready_is_only_cumul.png'), dpi=150, bbox_inches='tight')
plt.close(fig)
build_simple_qc(qc_data_is).to_csv(out('02_qc_table_is.csv'))

print("Generating batch summary tables...")
build_batch_table().to_csv(out('02_batch_summary_all.csv'))
build_batch_table('OS').to_csv(out('02_batch_summary_os.csv'))
build_batch_table('IS').to_csv(out('02_batch_summary_is.csv'))
print("  saved 02_qc_ready_*.png, 02_qc_table_*.csv, 02_batch_summary_*.csv")

# ── 03 monthly + cumulative QC outcome per site ───────────────────────────────

def make_monthly_qc_plot(title_suffix="", pcb_type_filter=None, batch_filter=None):
    all_months_set = set()
    for site_info in pcb_ready_info.values():
        for info in site_info.values():
            if pcb_type_filter and info['pcb_type'] != pcb_type_filter:
                continue
            if batch_filter and not simplify_batch_name(info['populated_batch']).startswith(batch_filter):
                continue
            for key in ('qc_month', 'unhappy_month', 'graveyard_month'):
                if info.get(key):
                    all_months_set.add(info[key])
    all_months_sorted = [m for m in sorted(all_months_set, key=lambda m: dt.strptime(m, '%m-%Y'))
                         if dt.strptime(m, '%m-%Y') >= dt(2025, 1, 1)]

    figures = {}
    for site in sites:
        ready     = defaultdict(int)
        unhappy   = defaultdict(int)
        graveyard = defaultdict(int)
        for pid, info in pcb_ready_info.get(site, {}).items():
            if pcb_type_filter and info['pcb_type'] != pcb_type_filter:
                continue
            if batch_filter and not simplify_batch_name(info['populated_batch']).startswith(batch_filter):
                continue
            if info['graveyard_done'] and info['graveyard_month']:
                graveyard[info['graveyard_month']] += 1
            elif info['unhappy_done'] and info['unhappy_month']:
                unhappy[info['unhappy_month']] += 1
            elif info['qc_done'] and info['qc_month']:
                ready[info['qc_month']] += 1

        x              = np.arange(len(all_months_sorted))
        ready_vals     = np.array([ready.get(m, 0)     for m in all_months_sorted])
        unhappy_vals   = np.array([unhappy.get(m, 0)   for m in all_months_sorted])
        graveyard_vals = np.array([graveyard.get(m, 0) for m in all_months_sorted])

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(x, ready_vals,     label='Ready',    color='green',  alpha=0.8)
        ax.bar(x, unhappy_vals,   label='Unhappy',  color='orange', alpha=0.8, bottom=ready_vals)
        ax.bar(x, graveyard_vals, label='Graveyard',color='red',    alpha=0.8, bottom=ready_vals + unhappy_vals)
        ax.set_xticks(x)
        ax.set_xticklabels(all_months_sorted, rotation=45, ha='right')
        ax.set_xlabel("Month")
        ax.set_ylabel("PCBs/month")
        ax.set_title(f"{site} — QC outcomes per month {title_suffix}")
        ax.text(0.02, 0.97, f"Data extracted on: {timestamp}",
                transform=ax.transAxes, fontsize=9, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))
        ax.legend()
        fig.tight_layout()
        figures[site] = fig
    return figures


def make_cumulative_qc_plot(title_suffix="", pcb_type_filter=None, batch_filter=None):
    all_months_set = set()
    for groups in grouped_shipments.values():
        all_months_set.update(groups.keys())
    for site_info in pcb_ready_info.values():
        for info in site_info.values():
            if pcb_type_filter and info['pcb_type'] != pcb_type_filter:
                continue
            if batch_filter and not simplify_batch_name(info['populated_batch']).startswith(batch_filter):
                continue
            for key in ('qc_month', 'unhappy_month', 'graveyard_month'):
                if info.get(key):
                    all_months_set.add(info[key])
    all_months_sorted = sorted(all_months_set, key=lambda m: dt.strptime(m, '%m-%Y'))

    delivered_counts = {}
    decision_counts  = {}
    for site in sites:
        delivered = defaultdict(int)
        ready     = defaultdict(int)
        unhappy   = defaultdict(int)
        graveyard = defaultdict(int)

        for month, shipments in grouped_shipments.get(site, {}).items():
            for shipment in shipments:
                for pid in shipment['pcb_ids']:
                    info = pcb_ready_info.get(site, {}).get(pid)
                    if info is None:
                        continue
                    if pcb_type_filter and info['pcb_type'] != pcb_type_filter:
                        continue
                    if batch_filter and not simplify_batch_name(info['populated_batch']).startswith(batch_filter):
                        continue
                    delivered[month] += 1

        for pid, info in pcb_ready_info.get(site, {}).items():
            if pcb_type_filter and info['pcb_type'] != pcb_type_filter:
                continue
            if batch_filter and not simplify_batch_name(info['populated_batch']).startswith(batch_filter):
                continue
            if info['graveyard_done'] and info['graveyard_month']:
                graveyard[info['graveyard_month']] += 1
            elif info['unhappy_done'] and info['unhappy_month']:
                unhappy[info['unhappy_month']] += 1
            elif info['qc_done'] and info['qc_month']:
                ready[info['qc_month']] += 1

        delivered_counts[site] = delivered
        decision_counts[site]  = {'ready': ready, 'unhappy': unhappy, 'graveyard': graveyard}

    x = np.arange(len(all_months_sorted))
    figures = {}
    for site in sites:
        fig, ax = plt.subplots(figsize=(10, 5))

        delivered_monthly = [delivered_counts[site].get(m, 0) for m in all_months_sorted]
        ready_monthly     = [decision_counts[site]['ready'].get(m, 0) for m in all_months_sorted]
        unhappy_monthly   = [decision_counts[site]['unhappy'].get(m, 0) for m in all_months_sorted]
        graveyard_monthly = [decision_counts[site]['graveyard'].get(m, 0) for m in all_months_sorted]

        delivered_cum = np.cumsum(delivered_monthly)
        ready_cum     = np.cumsum(ready_monthly)
        unhappy_cum   = np.cumsum(unhappy_monthly)
        graveyard_cum = np.cumsum(graveyard_monthly)
        decided_total = ready_cum + unhappy_cum + graveyard_cum

        ax.fill_between(x, 0, ready_cum, color='green', alpha=0.7, label='Ready')
        ax.fill_between(x, ready_cum, ready_cum + unhappy_cum, color='orange', alpha=0.7, label='Unhappy')
        ax.fill_between(x, ready_cum + unhappy_cum, decided_total, color='red', alpha=0.7, label='Graveyard')
        ax.plot(x, delivered_cum, color='black', marker='o', linewidth=2.5, label='Delivered')
        ax.fill_between(x, decided_total, delivered_cum, color='gray', alpha=0.25, label='Backlog')
        ax.set_title(f"{site} — Cumulative QC outcomes {title_suffix}")
        ax.grid(alpha=0.3)
        ax.text(0.02, 0.97, f"Data extracted on: {timestamp}",
                transform=ax.transAxes, fontsize=9, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))
        ax.legend()
        ax.set_xticks(x)
        ax.set_xticklabels(all_months_sorted, rotation=45, ha='right')
        ax.set_xlabel("Month")
        ax.set_ylabel("Cumulative PCBs")
        fig.tight_layout()
        figures[site] = fig
    return figures

print("Generating cumulative QC plots per site...")
for label, kwargs, suffix in [
    ('all',    {},                         '(OS + IS)'),
    ('os',     {'pcb_type_filter': 'OS'}, '(OS only)'),
    ('is',     {'pcb_type_filter': 'IS'}, '(IS only)'),
    ('norbit', {'batch_filter': 'N'},     '(Norbit batches)'),
    ('go',     {'batch_filter': 'GO'},    '(GO batches)'),
]:
    monthly_figs = make_monthly_qc_plot(title_suffix=suffix, **kwargs)
    for site, fig in monthly_figs.items():
        safe_site = re.sub(r'[^A-Za-z0-9_-]', '_', site)
        fig.savefig(out(f'03_monthly_qc_{label}_{safe_site}.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)
    cumul_figs = make_cumulative_qc_plot(title_suffix=suffix, **kwargs)
    for site, fig in cumul_figs.items():
        safe_site = re.sub(r'[^A-Za-z0-9_-]', '_', site)
        fig.savefig(out(f'03_cumulative_qc_{label}_{safe_site}.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)
print("  saved 03_monthly_qc_*_<site>.png + 03_cumulative_qc_*_<site>.png")

# ── 04 backlog plots (cell 14) ────────────────────────────────────────────────

all_months_set_bl = (
    set(m for groups in grouped_shipments.values() for m in groups.keys())
    | set(v['qc_month']       for info in pcb_ready_info.values()
          for v in info.values() if v['qc_done']       and v['qc_month'])
    | set(v['unhappy_month']  for info in pcb_ready_info.values()
          for v in info.values() if v['unhappy_done']  and v['unhappy_month'])
    | set(v['graveyard_month']for info in pcb_ready_info.values()
          for v in info.values() if v['graveyard_done'] and v['graveyard_month'])
)
active_months_bl = [m for m in sorted(all_months_set_bl, key=lambda m: dt.strptime(m, '%m-%Y'))
                    if dt.strptime(m, '%m-%Y') >= dt(2025, 1, 1)]

def compute_backlog(pcb_type_filter=None, batch_filter=None):
    backlog = {}
    for site in sites:
        received_by_month = defaultdict(int)
        decided_by_month  = defaultdict(int)
        for month, shipments in grouped_shipments.get(site, {}).items():
            for s in shipments:
                for pid in s['pcb_ids']:
                    pcb_info = pcb_ready_info.get(site, {}).get(pid, {})
                    if batch_filter and not simplify_batch_name(pcb_info.get('populated_batch')).startswith(batch_filter):
                        continue
                    if pcb_type_filter and pcb_info.get('pcb_type') != pcb_type_filter:
                        continue
                    received_by_month[month] += 1
        for v in pcb_ready_info.get(site, {}).values():
            if batch_filter and not simplify_batch_name(v.get('populated_batch')).startswith(batch_filter):
                continue
            if pcb_type_filter and v['pcb_type'] != pcb_type_filter:
                continue
            if v['graveyard_done'] and v['graveyard_month']:
                decided_by_month[v['graveyard_month']] += 1
            elif v['unhappy_done'] and v['unhappy_month']:
                decided_by_month[v['unhappy_month']] += 1
            elif v['qc_done'] and v['qc_month']:
                decided_by_month[v['qc_month']] += 1
        cum_recv = 0
        cum_dec  = 0
        monthly_backlog = []
        for month in active_months_bl:
            cum_recv += received_by_month.get(month, 0)
            cum_dec  += decided_by_month.get(month, 0)
            monthly_backlog.append(max(0, cum_recv - cum_dec))
        backlog[site] = monthly_backlog
    return backlog

def plot_backlog(ax, sites_plot, data, labels, title):
    x = np.arange(len(labels))
    for site in sites_plot:
        ax.plot(x, data[site], marker='o', label=site)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_xlabel("Month")
    ax.set_ylabel("PCBs awaiting QC decision")
    ax.set_title(title)
    ax.text(0.02, 0.95, f"Data extracted on: {timestamp}",
            transform=ax.transAxes, fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.5))
    ax.legend()
    ax.grid(axis='y', linestyle='--', alpha=0.5)
    for site in sites_plot:
        for i, val in enumerate(data[site]):
            if val > 0:
                ax.text(x[i], data[site][i] + 0.3, str(val),
                        ha='center', va='bottom', fontsize=9)

print("Generating backlog plots...")
backlog_variants = [
    ('os_plus_is', compute_backlog(),                     '{site} PCBs awaiting QC decision (OS+IS)'),
    ('os_only',    compute_backlog(pcb_type_filter='OS'), '{site} OS PCBs awaiting QC decision'),
    ('is_only',    compute_backlog(pcb_type_filter='IS'), '{site} IS PCBs awaiting QC decision'),
    ('norbit',     compute_backlog(batch_filter='N'),     '{site} Norbit (N*) PCBs awaiting QC decision'),
    ('go',         compute_backlog(batch_filter='GO'),    '{site} GO (GO*) PCBs awaiting QC decision'),
]
for label, bl_data, title_tpl in backlog_variants:
    for site in sites:
        safe_site = re.sub(r'[^A-Za-z0-9_-]', '_', site)
        fig, ax = plt.subplots(figsize=(10, 4))
        plot_backlog(ax, [site], bl_data, active_months_bl, title_tpl.format(site=site))
        fig.tight_layout()
        fig.savefig(out(f'04_backlog_{label}_{safe_site}.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)
print("  saved 04_backlog_*_<site>.png")

# ── 05 batch status plots + tables (cell 15) ─────────────────────────────────

def plot_batch_status(df_batch, batch, filepath):
    df_plot = df_batch[df_batch.index != 'Total'].copy()
    sites_plot = list(df_plot.index)
    ready     = df_plot['Ready'].values     if 'Ready'     in df_plot.columns else df_plot['Ready OS'].values
    not_yet   = df_plot['Not ready (yet)'].values
    unhappy   = df_plot['Not ready (unhappy)'].values
    graveyard = df_plot['Not ready (graveyard)'].values
    totals    = df_plot['Total'].values     if 'Total'     in df_plot.columns else df_plot['Total (OS)'].values

    fig, ax = plt.subplots(figsize=(10, max(2, len(sites_plot) * 0.8)))
    y = np.arange(len(sites_plot))
    height    = 0.5
    total_max = max(totals) if len(totals) > 0 and max(totals) > 0 else 1

    ax.barh(y, ready,     height=height, color='green',   label='Ready')
    ax.barh(y, not_yet,   height=height, color='orange',  label='Not ready (yet)',       left=ready)
    ax.barh(y, unhappy,   height=height, color='red',     label='Not ready (unhappy)',   left=ready + not_yet)
    ax.barh(y, graveyard, height=height, color='darkred', label='Not ready (graveyard)', left=ready + not_yet + unhappy)

    for i, (r, ny, u, g, t) in enumerate(zip(ready, not_yet, unhappy, graveyard, totals)):
        if r  > 0: ax.text(r / 2,              i, str(int(r)),  ha='center', va='center', fontsize=8, color='white', fontweight='bold')
        if ny > 0: ax.text(r + ny / 2,         i, str(int(ny)), ha='center', va='center', fontsize=8, color='white', fontweight='bold')
        if u  > 0: ax.text(r + ny + u / 2,     i, str(int(u)),  ha='center', va='center', fontsize=8, color='white', fontweight='bold')
        if g  > 0: ax.text(r + ny + u + g / 2, i, str(int(g)),  ha='center', va='center', fontsize=8, color='white', fontweight='bold')
        ax.text(t + total_max * 0.01, i, str(int(t)), ha='left', va='center', fontsize=8, color='black', fontweight='bold')
        pct = 100 * (t - ny) / t if t > 0 else 0
        yld = 100 * r / (t - ny) if (t - ny) > 0 else 0
        ax.text(total_max * 1.08, i, f"{pct:.0f}% ready (yield: {yld:.0f}%)",
                ha='left', va='center', fontsize=9, color='gray', fontweight='bold')

    ax.set_yticks(y)
    ax.set_yticklabels(sites_plot)
    ax.set_xlabel("Number of PCBs")
    ax.set_title(f"Batch {batch} — PCB status per site")
    ax.text(0.02, 0.99, f"Data extracted on: {timestamp}",
            transform=ax.transAxes, fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.5))
    ax.set_xlim(0, total_max * 1.5)
    ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1), borderaxespad=0)
    fig.tight_layout()
    fig.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close(fig)

print("Generating batch status plots and tables...")
df_all_batches = build_batch_table()
all_batches = sorted(set(
    simplify_batch_name(v['populated_batch'])
    for site_info in pcb_ready_info.values()
    for v in site_info.values()
))
is_related_cols = {'IS', 'Ready IS', '% not ready IS'}

for batch in all_batches:
    df_batch = df_all_batches.loc[batch].copy()

    numeric_cols = df_batch.select_dtypes(include='number').columns
    zero_cols = [c for c in numeric_cols if (df_batch[c] == 0).all() and c in is_related_cols]
    df_batch = df_batch.drop(columns=zero_cols)

    for base_col, pct_col in (('Ready OS', '% ready OS'), ('Ready IS', '% ready IS')):
        if base_col in zero_cols and pct_col in df_batch.columns:
            df_batch = df_batch.drop(columns=[pct_col])

    is_related_present = {'IS', 'Ready IS'}
    all_is_zero = all(c in zero_cols for c in is_related_present
                      if c in df_all_batches.loc[batch].columns)
    if all_is_zero:
        for col in ('Ready', 'OS'):
            if col in df_batch.columns:
                df_batch = df_batch.drop(columns=[col])
        if 'Total' in df_batch.columns:
            df_batch = df_batch.rename(columns={'Total': 'Total (OS)'})

    safe = re.sub(r'[^A-Za-z0-9_-]', '_', batch)
    plot_batch_status(df_batch, batch, out(f'05_batch_{safe}_status.png'))
    df_batch.to_csv(out(f'05_batch_{safe}_table.csv'))
print("  saved 05_batch_*_status.png + 05_batch_*_table.csv")

# ── 06 V1.1 BOM check (cell 17) ──────────────────────────────────────────────

print("Generating V1.1 BOM check...")
v11_bom_versions = {'10', '11', '12'}
buf = io.StringIO()
print(f"Data extracted on: {timestamp}\n", file=buf)
print("PCBs with V1.1 BOM version:", file=buf)
print("-" * 140, file=buf)

found_any = False
for flex_qc_site, info in pcb_ready_info.items():
    site_v11 = [
        (v['serial_number'], v['bom_version_label'],
         simplify_batch_name(v['populated_batch']),
         v['qc_done'], v['qc_month'],
         v['current_location_code'], v['current_stage_code'])
        for v in info.values()
        if v.get('bom_version') in v11_bom_versions
    ]
    if site_v11:
        found_any = True
        print(f"\n{flex_qc_site}: {len(site_v11)} V1.1 BOM PCBs", file=buf)
        print(f"  {'Serial':<16} {'BOM':<10} {'Batch':<10} {'QC done':<10} {'QC month':<10} {'Location':<20} {'Stage'}", file=buf)
        print(f"  {'-'*16} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*20} {'-'*20}", file=buf)
        for serial, bom, batch, qc_done, qc_month, loc, stage in sorted(site_v11):
            qc_str = qc_month if qc_done else 'not yet'
            print(f"  {str(serial):<16} {str(bom):<10} {batch:<10} {str(qc_done):<10} "
                  f"{qc_str:<10} {loc or 'N/A':<20} {stage or 'N/A'}", file=buf)

if not found_any:
    print("None found — all PCBs in the final selection have V2 BOM.", file=buf)

with open(out('06_v11_bom_check.txt'), 'w') as f:
    f.write(buf.getvalue())
print("  saved 06_v11_bom_check.txt")

print(f"\nDone. All outputs written to: {OUTPUT_DIR}")
