"""Legacy RWVG Type=3 item display conversion."""

from __future__ import annotations

from typing import Any


QUALITY_DISPLAY = {
    0: ("None", "#c8c8c8"),
    1: ("White", "#f7f7f7"),
    2: ("Green", "#14d914"),
    3: ("Blue", "#4876ff"),
    4: ("Purple", "#e066ff"),
    5: ("Gold", "#ffff00"),
    6: ("Red", "#ff3030"),
}

FORCED_RED_ITEM_IDS = frozenset({
    18010000016, 18010000037, 18010000038, 18010000040,
    18020000010, 18050000005, 18050000007, 18060000009, 18060000011,
})
GRADE_DERIVED_BAG_IDS = frozenset({
    11080003004, 11080003005, 11080004003, 11080004004,
    11080005003, 11080005004, 11080006002, 11080006003, 11080006004,
})


def effective_item_quality(item_id: int, item_quality: int = 0) -> int:
    """Apply the item quality rules used by the desktop radar renderer."""
    value = int(item_id or 0)
    quality = int(item_quality or 0)
    if value in FORCED_RED_ITEM_IDS:
        return 6
    if quality > 0:
        return quality
    if value in GRADE_DERIVED_BAG_IDS:
        return (value % 10000) // 1000
    return quality


def quality_display(quality: int) -> tuple[str, str]:
    value = int(quality or 0)
    if value >= 6:
        return QUALITY_DISPLAY[6]
    return QUALITY_DISPLAY.get(value, (f"Quality_{value}", "#c8c8c8"))


def describe_item(raw_item: dict[str, Any] | None) -> dict[str, Any]:
    item = dict(raw_item or {})
    item_type = int(item.get("item_type", item.get("type", 0)) or 0)
    quality = int(item.get("item_quality", 0) or 0)
    quality = effective_item_quality(int(item.get("item_id", 0) or 0), quality)
    quality_label, quality_color = quality_display(quality)
    dead_box_type = int(item.get("dead_box_type", 0) or 0)

    item["item_quality"] = quality
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
