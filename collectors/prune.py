"""Docker CLI reclaimable accounting, preserved from docker-prune/parse.star."""

def reclaimable(data):
    if "ImageUsage" in data and "ContainerUsage" in data and "VolumeUsage" in data:
        # Byte-exact summaries returned by modern Docker, also used by its CLI.
        without = int(data["ImageUsage"].get("Reclaimable", 0))
        without += int(data["ContainerUsage"].get("Reclaimable", 0))
        without += int(data.get("BuildCacheUsage", {}).get("Reclaimable", 0))
        volumes = int(data["VolumeUsage"].get("Reclaimable", 0))
        return [max(0, without), max(0, volumes)]
    # Compatibility with pre-summary Engine API / Docker CLI calculations.
    if "LayersSize" not in data or "Images" not in data or "Containers" not in data or "Volumes" not in data:
        raise ValueError("Incomplete Docker disk-usage response")
    # Match Docker CLI's legacy (<1.52) calculation. Only the unique portion of
    # images with no associated container is reclaimable. Do not subtract all
    # active image layers from LayersSize: shared layers make that overestimate.
    without = 0
    for image in data.get("Images") or []:
        size = int(image.get("Size", -1))
        shared = int(image.get("SharedSize", -1))
        if image.get("Containers", -1) == 0 and size >= 0 and shared >= 0:
            without += max(0, size - shared)
    for container in data.get("Containers") or []:
        if container.get("State", "") not in ["running", "paused", "restarting"]:
            without += max(0, int(container.get("SizeRw", 0)))
    build_total = 0
    build_used = 0
    for record in data.get("BuildCache") or []:
        if not record.get("Shared", False):
            size = max(0, int(record.get("Size", 0)))
            build_total += size
            if record.get("InUse", False):
                build_used += size
    without += max(0, build_total - build_used)
    volume_total = 0
    volume_used = 0
    for volume in data.get("Volumes") or []:
        usage = volume.get("UsageData") or {}
        size = int(usage.get("Size", 0))
        if size > 0:
            volume_total += size
        if usage.get("RefCount", -1) > 0:
            volume_used += max(0, size)
    return [without, max(0, volume_total - volume_used)]

def fields(result):
    success = int(result.get("collection_success", 0))
    sizes = reclaimable(result["disk_usage"]) if success == 1 else [0, 0]
    return {
        "collection_success": success,
        "last_success_timestamp_seconds": int(result.get("last_success_timestamp_seconds", 0)),
        "refresh_interval_seconds": int(result.get("refresh_interval_seconds", 10800)),
        "reclaimable_without_volumes_bytes": sizes[0],
        "reclaimable_volumes_bytes": sizes[1],
        "reclaimable_total_bytes": sizes[0] + sizes[1],
    }
