from pathlib import Path


PMU = Path(r"D:\RWbase\RWbase\PMU")


def test_export_happens_before_the_new_uworld_scan_and_uses_safe_copy():
    traversal = (PMU / "src" / "Core" / "ActorScan" / "ActorTraversal.cpp").read_text(encoding="utf-8")
    export = (PMU / "src" / "Business" / "SnapshotUdpExport.cpp").read_text(encoding="utf-8")
    scan_body = traversal[traversal.index("NTSTATUS ScanCurrentUWorld() "):]
    assert scan_body.index("ExportLatestActorSnapshotUdp") < scan_body.index("PrepareTraversalContext")
    assert "CopyLatestActorSnapshots" in export
    assert "gLatest" not in export


def test_export_encodes_all_snapshot_fields_with_a_511_byte_data_limit():
    export = (PMU / "src" / "Business" / "SnapshotUdpExport.cpp").read_text(encoding="utf-8")
    for field in (
        "ActorAddress", "ObjectId", "Kind", "ClassName", "Mesh", "RootComponent",
        "PlayerState", "Position.X", "Position.Y", "Position.Z", "PositionSource",
        "LastDbPositionTsc", "TeamId", "Health", "MaxHealth", "WeaponId",
        "ValidFields", "Diagnostics.Attempts", "Diagnostics.Failures", "Diagnostics.FirstFailure",
    ):
        assert field in export
    assert "kMaximumDataPayloadBytes = 511" in export
    assert "QueueBinaryData" in export and "PumpLogTransport" in export


def test_binary_transport_reports_not_ready_and_preserves_log_packet_type():
    transport = (PMU / "src" / "Core" / "Trace" / "KdPrintCompat.cpp").read_text(encoding="utf-8")
    internal = (PMU / "src" / "Core" / "Trace" / "LogTransportInternal.hpp").read_text(encoding="utf-8")
    assert "kPacketTypeData = 0x02" in internal
    assert "return STATUS_DEVICE_NOT_READY" in transport
    assert "kPacketTypeLog" in transport
