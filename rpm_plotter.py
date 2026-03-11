#!/usr/bin/env python3
"""
Reads a .bin file, computes peak and mean RPM, and saves a PNG graph.

to use: get in terminal, and slam out one of these.

    python rpm_plot.py RPM_A_0000.bin #prim
    python rpm_plot.py RPM_B_0003.bin #sec


    #if you want prim vs sec, you need to call with both .bins:

    python rpm_plot.py RPM_A_0000.bin RPM_B_0003.bin

    #make sure your .bin files are in the same directory as this one or just paste in the whole path. lol
    #the png graph saves with the .bin file wherever that is.

    python rpm_plot.py --folder ./logs      # plots every .bin in the folder

dont forget to install numpy lol.
    pip install matplotlib numpy
"""

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# BINARY FORMAT (must match firmware)

MAGIC_NUMBER = 0x52504D31 #it spells out RPM1 in ascii. just so u dont put the wrong file in.
HEADER_FMT   = "<IIII"
HEADER_SIZE  = struct.calcsize(HEADER_FMT)
RECORD_FMT   = "<IfI"
RECORD_SIZE  = struct.calcsize(RECORD_FMT)


# parse em

def parse_bin(path: Path):
    with open(path, "rb") as f:
        raw_hdr = f.read(HEADER_SIZE)
        if len(raw_hdr) < HEADER_SIZE:
            raise ValueError(f"{path.name}: file too short to contain a valid header.")

        magic, teeth, interval_us, _ = struct.unpack(HEADER_FMT, raw_hdr)
        if magic != MAGIC_NUMBER:
            raise ValueError(f"{path.name}: bad magic 0x{magic:08X} — is this a valid RPM log?")

        timestamps, rpms = [], []
        while True:
            raw = f.read(RECORD_SIZE)
            if len(raw) < RECORD_SIZE:
                break
            ts, rpm, _ = struct.unpack(RECORD_FMT, raw)
            timestamps.append(ts)
            rpms.append(rpm)

    if not timestamps:
        raise ValueError(f"{path.name}: no data records found.")

    ts = np.array(timestamps, dtype=np.float64)
    ts = (ts - ts[0]) / 1e6   # µs → seconds from t=0
    rpm = np.array(rpms, dtype=np.float32)

    return {"teeth": teeth, "interval_us": interval_us}, ts, rpm


# plot em

def plot_bin(path: Path) -> None:
    path = Path(path)
    header, ts, rpm = parse_bin(path)

    peak_rpm = float(np.max(rpm))
    mean_rpm = float(np.mean(rpm[rpm > 0])) if np.any(rpm > 0) else 0.0

    # figure
    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#8b949e", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#30363d")

    # actual rpm curve line
    ax.plot(ts, rpm, color="#58a6ff", linewidth=1.4, label="RPM", zorder=3)
    ax.fill_between(ts, rpm, alpha=0.12, color="#58a6ff", zorder=2)

    # mean rpm line
    ax.axhline(mean_rpm, color="#f0e68c", linewidth=1.0, linestyle="--",
               label=f"Mean: {mean_rpm:,.0f} RPM", zorder=4)

    # peak rpm point
    ax.axhline(peak_rpm, color="#ff7b72", linewidth=1.0, linestyle=":",
               label=f"Peak: {peak_rpm:,.0f} RPM", zorder=4)

    # peak rpm label
    peak_idx = int(np.argmax(rpm))
    ax.annotate(
        f"{peak_rpm:,.0f} RPM",
        xy=(ts[peak_idx], peak_rpm),
        xytext=(ts[peak_idx], peak_rpm * 1.05),
        color="#ff7b72", fontsize=8.5,
        ha="center",
        arrowprops=dict(arrowstyle="->", color="#ff7b72", lw=0.8)
    )

    # other labels
    ax.set_xlabel("Time (s)", color="#cdd9e5", fontsize=11)
    ax.set_ylabel("RPM", color="#cdd9e5", fontsize=11)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(True, color="#21262d", linewidth=0.6, zorder=1)
    ax.legend(loc="upper right", framealpha=0.35, fontsize=10,
              facecolor="#161b22", edgecolor="#30363d", labelcolor="#cdd9e5")

    # stats?
    stats = (
        f"File:      {path.name}\n"
        f"Teeth:     {header['teeth']}\n"
        f"Duration:  {ts[-1]:.2f} s\n"
        f"Samples:   {len(ts)}\n"
        f"Peak RPM:  {peak_rpm:,.0f}\n"
        f"Mean RPM:  {mean_rpm:,.0f}"
    )
    ax.text(
        0.01, 0.97, stats,
        transform=ax.transAxes, va="top", ha="left",
        fontsize=8.5, family="monospace", color="#8b949e",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#161b22",
                  edgecolor="#30363d", alpha=0.85)
    )

    fig.suptitle(f"RPM Log — {path.name}", color="#cdd9e5",
                 fontsize=12, fontweight="bold", y=0.99)

    plt.tight_layout()

    png_path = path.with_suffix(".png")
    fig.savefig(png_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[✓] Saved → {png_path}")
    plt.close(fig)



def plot_primary_vs_secondary(path_a: Path, path_b: Path) -> None:
    _, ts_a, rpm_a = parse_bin(path_a)
    _, ts_b, rpm_b = parse_bin(path_b)


    #make sure a and b have a common time axis as they might have slightly varied sample counts
    rpm_b_interp = np.interp(ts_a, ts_b, rpm_b)

    
    fig, ax = plt.subplots(figsize=(10, 9))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#8b949e", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#30363d")


    #scatter plotted: color points by time so you can see progression
    scatter = ax.scatter(
        rpm_b_interp, rpm_a,
        c=ts_a,                    
        cmap="plasma",
        s=4, alpha=0.7, zorder=3
    )

    cbar = fig.colorbar(scatter, ax=ax, pad=0.02)
    cbar.set_label("Time (s)", color="#cdd9e5", fontsize=10)
    cbar.ax.yaxis.set_tick_params(color="#8b949e", labelcolor="#8b949e")

    #for peak rpm and mean:
    peak_a = float(np.max(rpm_a))
    mean_a = float(np.mean(rpm_a[rpm_a > 0])) if np.any(rpm_a > 0) else 0.0
    peak_b = float(np.max(rpm_b_interp))
    mean_b = float(np.mean(rpm_b_interp[rpm_b_interp > 0])) if np.any(rpm_b_interp > 0) else 0.0


    ax.axhline(mean_a, color="#f0e68c", linewidth=0.8, linestyle="--",
               label=f"Primary mean: {mean_a:,.0f} RPM")
    ax.axhline(peak_a, color="#ff7b72", linewidth=0.8, linestyle=":",
               label=f"Primary peak: {peak_a:,.0f} RPM")
    ax.axvline(mean_b, color="#f0e68c", linewidth=0.8, linestyle="--",
               label=f"Secondary mean: {mean_b:,.0f} RPM")
    ax.axvline(peak_b, color="#ff7b72", linewidth=0.8, linestyle=":",
               label=f"Secondary peak: {peak_b:,.0f} RPM")

    ax.set_xlabel("Secondary RPM", color="#cdd9e5", fontsize=11)
    ax.set_ylabel("Primary RPM", color="#cdd9e5", fontsize=11)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(True, color="#21262d", linewidth=0.6, zorder=1)
    ax.legend(loc="upper left", framealpha=0.35, fontsize=9,
              facecolor="#161b22", edgecolor="#30363d", labelcolor="#cdd9e5")

    fig.suptitle(
        f"Primary vs Secondary RPM\n{path_a.name}  /  {path_b.name}",
        color="#cdd9e5", fontsize=12, fontweight="bold", y=0.99
    )

    plt.tight_layout()

    #save primvsec graph 
    run_id = path_a.stem.replace("RPM_A_", "")
    png_path = path_a.parent / f"RPM_AB_{run_id}.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[✓] Saved → {png_path}")
    plt.close(fig)



# main

def main():
    parser = argparse.ArgumentParser(description="Plot RPM .bin log files")
    parser.add_argument("files", nargs="*", metavar="FILE", help=".bin file(s) to plot")
    parser.add_argument("--folder", metavar="DIR", help="Plot every .bin file in a folder")
    args = parser.parse_args()

    targets = []

    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            print(f"[ERROR] Folder not found: {folder}")
            sys.exit(1)
        targets = sorted(folder.glob("*.bin"))
        if not targets:
            print(f"[ERROR] No .bin files found in {folder}")
            sys.exit(1)
        print(f"[i] Found {len(targets)} .bin file(s) in {folder}")

    elif args.files:
        targets = [Path(f) for f in args.files]

    else:
        parser.print_help()
        sys.exit(1)

    for path in targets:
        try:
            print(f"[…] Plotting {path.name}")
            plot_bin(path)
        except Exception as e:
            print(f"[ERROR] {path.name}: {e}")

    if len(targets)==2:
        a = next((p for p in targets if "RPM_A" in p.name), None)
        b = next((p for p in targets if "RPM_B" in p.name), None)
        if a and b:
            print(f"plotting prim vs sec")
            plot_primary_vs_secondary(a,b)


if __name__ == "__main__":
    main()
