#!/usr/bin/env python3
"""Sample load-generator process-tree and local socket resource usage.

This helper is intentionally local/loadgen-only. It does not inspect payloads,
environment variables, or network peer addresses.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


CPU_COUNT = os.cpu_count() or 1


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    command: str
    cpu_ticks: int
    rss_kb: int
    fd_count: int
    socket_fd_count: int


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_stat(stat_text: str) -> tuple[int, str, int, int] | None:
    end = stat_text.rfind(")")
    if not stat_text.startswith("(") and " (" not in stat_text:
        return None
    start = stat_text.find("(")
    if start < 0 or end < start:
        return None
    prefix = stat_text[:start].strip()
    rest = stat_text[end + 2 :].split()
    try:
        pid = int(prefix)
        comm = stat_text[start + 1 : end]
        ppid = int(rest[1])
        utime = int(rest[11])
        stime = int(rest[12])
    except (IndexError, ValueError):
        return None
    return pid, comm, ppid, utime + stime


def _status_rss_kb(status_text: str) -> int:
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return 0
    return 0


def _fd_counts(pid: int) -> tuple[int, int]:
    fd_dir = Path("/proc") / str(pid) / "fd"
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return 0, 0
    socket_count = 0
    for entry in entries:
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith("socket:"):
            socket_count += 1
    return len(entries), socket_count


def _cmdline(pid: int, fallback: str) -> str:
    raw = _read_text(Path("/proc") / str(pid) / "cmdline")
    text = raw.replace("\x00", " ").strip()
    return text or fallback


def read_processes() -> dict[int, ProcInfo]:
    procs: dict[int, ProcInfo] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        stat = _parse_stat(_read_text(proc_dir / "stat"))
        if stat is None:
            continue
        pid, comm, ppid, cpu_ticks = stat
        rss_kb = _status_rss_kb(_read_text(proc_dir / "status"))
        fd_count, socket_fd_count = _fd_counts(pid)
        procs[pid] = ProcInfo(
            pid=pid,
            ppid=ppid,
            command=_cmdline(pid, comm),
            cpu_ticks=cpu_ticks,
            rss_kb=rss_kb,
            fd_count=fd_count,
            socket_fd_count=socket_fd_count,
        )
    return procs


def select_roots(procs: dict[int, ProcInfo], pid: int | None, match: str | None) -> set[int]:
    if pid is not None:
        return {pid} if pid in procs else set()
    if not match:
        return set()
    needle = match.lower()
    return {p.pid for p in procs.values() if needle in p.command.lower()}


def expand_tree(procs: dict[int, ProcInfo], roots: set[int]) -> set[int]:
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for proc in procs.values():
            if proc.ppid in selected and proc.pid not in selected:
                selected.add(proc.pid)
                changed = True
    return selected


def read_cpu_total_ticks() -> int:
    first = _read_text(Path("/proc/stat")).splitlines()[0]
    return sum(int(part) for part in first.split()[1:])


def read_file_nr() -> tuple[int, int, int]:
    parts = _read_text(Path("/proc/sys/fs/file-nr")).split()
    if len(parts) >= 3:
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            pass
    return 0, 0, 0


def read_ephemeral_range() -> tuple[int, int]:
    parts = _read_text(Path("/proc/sys/net/ipv4/ip_local_port_range")).split()
    if len(parts) >= 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 0, 0


def read_socket_state_counts() -> dict[str, int]:
    counts = {"ESTAB": 0, "TIME-WAIT": 0, "SYN-RECV": 0}
    try:
        result = subprocess.run(
            ["ss", "-tan"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return counts
    for line in result.stdout.splitlines()[1:]:
        state = line.split(maxsplit=1)[0] if line.split() else ""
        if state in counts:
            counts[state] += 1
    return counts


def summarize(procs: dict[int, ProcInfo], selected: set[int]) -> dict[str, int | str]:
    subset = [procs[pid] for pid in sorted(selected) if pid in procs]
    return {
        "pids": ",".join(str(proc.pid) for proc in subset),
        "process_count": len(subset),
        "cpu_ticks": sum(proc.cpu_ticks for proc in subset),
        "rss_kb": sum(proc.rss_kb for proc in subset),
        "fd_count": sum(proc.fd_count for proc in subset),
        "socket_fd_count": sum(proc.socket_fd_count for proc in subset),
    }


def sample_rows(args: argparse.Namespace):
    previous_total = read_cpu_total_ticks()
    previous_proc_ticks = 0
    first = True
    emitted = 0

    while args.samples is None or emitted < args.samples:
        time.sleep(args.interval)
        procs = read_processes()
        selected = expand_tree(procs, select_roots(procs, args.pid, args.match))
        summary = summarize(procs, selected)
        total_ticks = read_cpu_total_ticks()
        proc_ticks = int(summary["cpu_ticks"])
        if first:
            cpu_percent = 0.0
            first = False
        else:
            total_delta = max(1, total_ticks - previous_total)
            proc_delta = max(0, proc_ticks - previous_proc_ticks)
            cpu_percent = (proc_delta / total_delta) * CPU_COUNT * 100.0
        previous_total = total_ticks
        previous_proc_ticks = proc_ticks

        file_allocated, file_unused, file_max = read_file_nr()
        eph_low, eph_high = read_ephemeral_range()
        sockets = read_socket_state_counts()
        emitted += 1
        yield {
            "timestamp_unix": f"{time.time():.3f}",
            "timestamp_local": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "label": args.label,
            "match": args.match or "",
            "root_pid": args.pid or "",
            "pids": summary["pids"],
            "process_count": summary["process_count"],
            "cpu_percent": f"{cpu_percent:.2f}",
            "rss_kb": summary["rss_kb"],
            "fd_count": summary["fd_count"],
            "socket_fd_count": summary["socket_fd_count"],
            "system_established": sockets["ESTAB"],
            "system_time_wait": sockets["TIME-WAIT"],
            "system_syn_recv": sockets["SYN-RECV"],
            "file_allocated": file_allocated,
            "file_unused": file_unused,
            "file_max": file_max,
            "ephemeral_low": eph_low,
            "ephemeral_high": eph_high,
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample local load-generator resource usage as TSV.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pid", type=int, help="Root process PID to sample, including children.")
    group.add_argument("--match", help="Case-insensitive command-line substring for root processes.")
    parser.add_argument("--interval", type=float, default=2.0, help="Sampling interval seconds.")
    parser.add_argument("--samples", type=int, help="Number of samples to collect. Omit to run until interrupted.")
    parser.add_argument("--label", default="", help="Stage label written to each output row.")
    parser.add_argument("--out", required=True, help="Output TSV path.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp_unix",
        "timestamp_local",
        "label",
        "match",
        "root_pid",
        "pids",
        "process_count",
        "cpu_percent",
        "rss_kb",
        "fd_count",
        "socket_fd_count",
        "system_established",
        "system_time_wait",
        "system_syn_recv",
        "file_allocated",
        "file_unused",
        "file_max",
        "ephemeral_low",
        "ephemeral_high",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        try:
            for row in sample_rows(args):
                writer.writerow(row)
                handle.flush()
        except KeyboardInterrupt:
            return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
