QUALITY_DISPLAY = {
    0: ("None", "#9e9e9e"),
    1: ("White", "#f7f7f7"),
    2: ("Green", "#14d914"),
    3: ("Blue", "#4876ff"),
    4: ("Purple", "#e066ff"),
    5: ("Gold", "#ffff00"),
    6: ("Red", "#ff3030"),
}


def _quality_display(quality):
    key = int(quality or 0)
    label, color = QUALITY_DISPLAY.get(key, (f"Quality_{key}", "#9e9e9e"))
    return label, color


def describe_item(raw_item):
    item = dict(raw_item or {})
    item_type = int(item.get("item_type", item.get("type", 0)) or 0)
    quality = int(item.get("item_quality", 0) or 0)
    quality_label, quality_color = _quality_display(quality)
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
