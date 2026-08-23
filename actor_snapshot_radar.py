"""Convert PMU Type=6 actor snapshots into the WebRadar contract."""

from __future__ import annotations

from typing import Any

from item_descriptor import effective_item_quality, quality_display
from item_catalog import item_name


POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
HEALTH_FIELD = 1 << 5
MAX_HEALTH_FIELD = 1 << 6
WEAPON_FIELD = 1 << 7
HERO_FIELD = 1 << 8
ITEM_ID_FIELD = 1 << 9
VIEW_LOCAL_PAWN_FIELD = 1 << 0
VIEW_CONTROL_ROTATION_YAW_FIELD = 1 << 1
ITEM_KINDS = frozenset(("Item", "Container", "DeadBox", "Box"))
AI_KINDS = frozenset(("AI", "Minion", "Boss"))


def build_radar_snapshot(
    actor_snapshot: dict[str, Any],
    local_player: dict[str, Any] | None,
    fallback_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entities, snapshot_items = _map_records(actor_snapshot.get("actors") or [])
    items = _merge_items(snapshot_items, fallback_items or [])
    snapshot_local_player = _local_player_from_snapshot(entities, actor_snapshot.get("local_view"))
    resolved_local_player = snapshot_local_player or local_player
    return {
        "meta": {
            "source": f"actor_snapshot_v{int(actor_snapshot.get('version', 0) or 0)}",
            "entity_count": len(entities),
            "item_count": len(items),
            "snapshot_id": actor_snapshot.get("snapshot_id"),
            "snapshot_status": actor_snapshot.get("status"),
            "snapshot_complete": bool(actor_snapshot.get("complete")),
            "utils_present": local_player is not None,
            "utils_age_ms": -1,
            "type6_view_present": snapshot_local_player is not None,
        },
        "local_player": resolved_local_player,
        "entities": entities,
        "items": items,
        "teammates": [],
    }


def _local_player_from_snapshot(
    entities: list[dict[str, Any]], local_view: Any,
) -> dict[str, Any] | None:
    view = local_view if isinstance(local_view, dict) else {}
    view_fields = int(view.get("valid_fields", 0) or 0)
    required_fields = VIEW_LOCAL_PAWN_FIELD | VIEW_CONTROL_ROTATION_YAW_FIELD
    if view_fields & required_fields != required_fields or not bool(view.get("has_yaw")):
        return None
    local_pawn = int(view.get("local_pawn", 0) or 0)
    local_id = f"0x{local_pawn:X}"
    for index, entity in enumerate(entities):
        if entity.get("id") != local_id:
            continue
        yaw = float(view["yaw"])
        entities[index] = {**entity, "orientation": yaw, "has_orientation": True}
        return {
            "id": local_id, "team_id": int(entity.get("team_id", 0) or 0),
            "camp_id": 0, "yaw": yaw, "position": dict(entity["position"]),
            "neck_position": dict(entity["position"]),
        }
    return None


def _map_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for record in records:
        entity, item = _map_record(record)
        if entity is not None:
            entities.append(entity)
        if item is not None:
            items.append(item)
    return entities, items


def _merge_items(
    snapshot_items: list[dict[str, Any]], fallback_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in [*snapshot_items, *fallback_items]:
        entity_id = str(item.get("id") or "")
        if entity_id and entity_id in seen_ids:
            continue
        if entity_id:
            seen_ids.add(entity_id)
        merged.append(dict(item))
    return merged


def _map_record(record: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    valid_fields = int(record.get("valid_fields", 0) or 0)
    has_valid_fields = "valid_fields" in record
    if has_valid_fields and not valid_fields & POSITION_FIELD:
        return None, None
    kind = str(record.get("kind_name") or "Unknown")
    position = _position(record.get("pos"))
    record_id = int(record.get("record_id", 0) or 0)
    entity_id = f"record:{record_id:X}"
    if kind in ITEM_KINDS:
        return None, _item(entity_id, kind, position, record, valid_fields, has_valid_fields)
    return _entity(entity_id, kind, position, record, valid_fields, has_valid_fields), None


def _position(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {axis: int(float(source.get(axis, 0) or 0)) for axis in ("x", "y", "z")}


def _item(
    entity_id: str,
    kind: str,
    position: dict[str, int],
    record: dict[str, Any],
    valid_fields: int,
    has_valid_fields: bool,
) -> dict[str, Any]:
    has_item_id = not has_valid_fields or bool(valid_fields & ITEM_ID_FIELD)
    item_id = int(record.get("item_id", 0) or 0) if has_item_id else 0
    resolved_name = str(record.get("item_name") or "") if has_item_id else ""
    resolved_name = resolved_name or (item_name(item_id) if has_item_id else "")
    item_quality = effective_item_quality(item_id, record.get("item_quality", 0))
    quality_label, quality_color = quality_display(item_quality)
    return {
        "id": entity_id,
        "type": "deadbody" if kind == "DeadBox" else "item",
        "item_name": resolved_name or kind,
        "item_id": item_id,
        "item_id_hex": f"0x{item_id:X}" if has_item_id else "",
        "item_quality": item_quality,
        "item_quality_label": quality_label,
        "item_quality_color": quality_color,
        "dead_box_name": kind if kind == "DeadBox" else "",
        "position": position,
        "source_kind": kind,
    }


def _entity(
    entity_id: str,
    kind: str,
    position: dict[str, int],
    record: dict[str, Any],
    valid_fields: int,
    has_valid_fields: bool,
) -> dict[str, Any]:
    has_team = not has_valid_fields or bool(valid_fields & TEAM_FIELD)
    has_health = not has_valid_fields or bool(valid_fields & HEALTH_FIELD)
    has_max_health = not has_valid_fields or bool(valid_fields & MAX_HEALTH_FIELD)
    has_weapon = not has_valid_fields or bool(valid_fields & WEAPON_FIELD)
    has_hero = not has_valid_fields or bool(valid_fields & HERO_FIELD)
    return {
        "id": entity_id,
        "name": kind,
        "type": "ai" if kind in AI_KINDS else "player",
        "team_id": int(record.get("team_id", 0) or 0) if has_team else 0,
        "has_team": has_team,
        "position": position,
        "orientation": None,
        "has_orientation": False,
        "health": float(record.get("health", 0.0) or 0.0) if has_health else None,
        "max_health": float(record.get("max_health", 0.0) or 0.0) if has_max_health else None,
        "has_health": has_health,
        "has_max_health": has_max_health,
        "weapon_id": int(record.get("weapon_id", 0) or 0) if has_weapon else None,
        "weapon_id_hex": str(record.get("weapon_id_hex") or "0x0") if has_weapon else None,
        "weapon_name": str(record.get("weapon_name") or "") if has_weapon else None,
        "hero_id": int(record.get("hero_id", 0) or 0) if has_hero else None,
        "hero_id_hex": str(record.get("hero_id_hex") or "0x0") if has_hero else None,
        "hero_name": str(record.get("hero_name") or "") if has_hero else None,
        "has_weapon": has_weapon,
        "has_hero": has_hero,
        "source_kind": kind,
    }
