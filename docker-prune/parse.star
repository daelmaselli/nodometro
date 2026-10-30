load("json.star", "json")

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
        fail("Incomplete Docker disk-usage response")
    used = 0
    for image in data.get("Images") or []:
        size = int(image.get("Size", -1))
        shared = int(image.get("SharedSize", -1))
        if image.get("Containers", 0) != 0 and size >= 0 and shared >= 0:
            used += size - shared
    without = max(0, int(data["LayersSize"]) - used)
    for container in data.get("Containers") or []:
        if container.get("State", "") not in ["running", "paused", "restarting"]:
            without += max(0, int(container.get("SizeRw", 0)))
    for record in data.get("BuildCache") or []:
        if not record.get("Shared", False) and not record.get("InUse", False):
            without += max(0, int(record.get("Size", 0)))
    volumes = 0
    for volume in data.get("Volumes") or []:
        usage = volume.get("UsageData") or {}
        if usage.get("RefCount", -1) == 0:
            volumes += max(0, int(usage.get("Size", 0)))
    return [without, volumes]

def apply(metric):
    result = json.decode(metric.fields.pop("value"))
    success = int(result.get("collection_success", 0))
    sizes = reclaimable(result["disk_usage"]) if success == 1 else [0, 0]
    metric.name = "docker_prune"
    metric.fields["collection_success"] = success
    metric.fields["last_success_timestamp_seconds"] = int(result.get("last_success_timestamp_seconds", 0))
    metric.fields["refresh_interval_seconds"] = int(result.get("refresh_interval_seconds", 10800))
    metric.fields["reclaimable_without_volumes_bytes"] = sizes[0]
    metric.fields["reclaimable_volumes_bytes"] = sizes[1]
    metric.fields["reclaimable_total_bytes"] = sizes[0] + sizes[1]
    return metric
