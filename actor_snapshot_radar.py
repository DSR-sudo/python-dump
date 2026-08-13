"""Convert PMU Type=6 actor snapshots into the WebRadar contract."""

from __future__ import annotations

from typing import Any


POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
HEALTH_FIELD = 1 << 5
MAX_HEALTH_FIELD = 1 << 6
ITEM_KINDS = frozenset(("Item", "Container", "DeadBox", "Box"))
AI_KINDS = frozenset(("AI", "Minion", "Boss"))


def build_radar_snapshot(actor_snapshot: dict[str, Any], local_player: dict[str, Any] | None) -> dict[str, Any]:
    entities, items = _map_records(actor_snapshot.get("actors") or [])
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
        },
        "local_player": local_player,
        "entities": entities,
        "items": items,
        "teammates": [],
    }


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


def _map_record(record: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    valid_fields = int(record.get("valid_fields", 0) or 0)
    has_valid_fields = "valid_fields" in record
    if has_valid_fields and not valid_fields & POSITION_FIELD:
        return None, None
    kind = str(record.get("kind_name") or "Unknown")
    position = _position(record.get("pos"))
    entity_id = f"0x{int(record.get('actor_address', 0) or 0):X}"
    class_name = str(record.get("class_name") or kind)
    if kind in ITEM_KINDS:
        return None, _item(entity_id, kind, class_name, position)
    return _entity(entity_id, kind, class_name, position, record, valid_fields, has_valid_fields), None


def _position(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {axis: int(float(source.get(axis, 0) or 0)) for axis in ("x", "y", "z")}


def _item(entity_id: str, kind: str, class_name: str, position: dict[str, int]) -> dict[str, Any]:
    return {
        "id": entity_id,
        "type": "deadbody" if kind == "DeadBox" else "item",
        "item_name": class_name,
        "dead_box_name": class_name if kind == "DeadBox" else "",
        "position": position,
        "source_kind": kind,
    }


def _entity(
    entity_id: str,
    kind: str,
    class_name: str,
    position: dict[str, int],
    record: dict[str, Any],
    valid_fields: int,
    has_valid_fields: bool,
) -> dict[str, Any]:
    has_team = not has_valid_fields or bool(valid_fields & TEAM_FIELD)
    has_health = not has_valid_fields or bool(valid_fields & HEALTH_FIELD)
    has_max_health = not has_valid_fields or bool(valid_fields & MAX_HEALTH_FIELD)
    return {
        "id": entity_id,
        "name": class_name,
        "type": "ai" if kind in AI_KINDS else "player",
        "class_name": class_name,
        "team_id": int(record.get("team_id", 0) or 0) if has_team else 0,
        "has_team": has_team,
        "position": position,
        "orientation": None,
        "has_orientation": False,
        "health": float(record.get("health", 0.0) or 0.0) if has_health else None,
        "max_health": float(record.get("max_health", 0.0) or 0.0) if has_max_health else None,
        "has_health": has_health,
        "has_max_health": has_max_health,
        "source_kind": kind,
    }
