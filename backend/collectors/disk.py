import subprocess


_SKIP_FSTYPES = {"tmpfs", "devtmpfs", "overlay", "squashfs", "efivarfs"}


def collect() -> list:
    try:
        result = subprocess.run(
            ["nsenter", "--mount=/proc/1/ns/mnt", "--", "df", "-T", "--block-size=1"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().splitlines()
    except Exception:
        return []

    partitions = []
    # Header: Filesystem  Type  1B-blocks  Used  Available  Use%  Mounted on
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        source = parts[0]
        fstype = parts[1]
        try:
            size_b = int(parts[2])
            used_b = int(parts[3])
            avail_b = int(parts[4])
        except ValueError:
            continue
        pct_str = parts[5].rstrip("%")
        mount = parts[6]

        if fstype in _SKIP_FSTYPES:
            continue
        if size_b == 0:
            continue

        try:
            pct = int(pct_str)
        except ValueError:
            pct = 0

        partitions.append({
            "source": source,
            "mount": mount,
            "fstype": fstype,
            "size_gb": round(size_b / 1024**3, 2),
            "used_gb": round(used_b / 1024**3, 2),
            "avail_gb": round(avail_b / 1024**3, 2),
            "pct": pct,
        })

    return partitions
