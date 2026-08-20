"""Legacy RWVG Type=3 item display conversion."""

from __future__ import annotations

from typing import Any


QUALITY_DISPLAY = {
    0: ("None", "#9e9e9e"),
    1: ("White", "#f7f7f7"),
    2: ("Green", "#14d914"),
    3: ("Blue", "#4876ff"),
    4: ("Purple", "#e066ff"),
    5: ("Gold", "#ffff00"),
    6: ("Red", "#ff3030"),
}


def describe_item(raw_item: dict[str, Any] | None) -> dict[str, Any]:
    item = dict(raw_item or {})
    item_type = int(item.get("item_type", item.get("type", 0)) or 0)
    quality = int(item.get("item_quality", 0) or 0)
    quality_label, quality_color = QUALITY_DISPLAY.get(
        quality, (f"Quality_{quality}", "#9e9e9e"),
    )
    dead_box_type = int(item.get("dead_box_type", 0) or 0)

    item["item_quality_label"] = quality_label
    item["item_quality_color"] = quality_color
    if item_type == 3:
        item["type"] = "deadbody"
        item["dead_box_name"] = f"DeadBox_{dead_box_type}"
        item["item_name"] = item.get("item_name") or item["dead_box_name"]
        return item

    item["type"] = "item"
    item["dead_box_name"] = ""
    item["item_name"] = item.get("item_name") or f"UnknownItem_{item_type}"
    return item
