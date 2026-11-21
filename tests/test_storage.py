from app.storage import classify

ROOT = "1 0 0:50 / / rw,relatime master:1 - overlay overlay rw,lowerdir=/x\n"
HASH = "a" * 64


def mount(root, point, fstype="zfs", source="tank/apps"):
    return f"20 1 0:60 {root} {point} rw,noatime - {fstype} {source} rw\n"


def test_nothing_mounted_is_temporary():
    assert classify(ROOT, "/data") == "temporary"


def test_unnamed_docker_volume_is_temporary():
    info = ROOT + mount(f"/docker/volumes/{HASH}/_data", "/data", "ext4", "/dev/sda1")
    assert classify(info, "/data") == "temporary"


def test_truenas_temporary_storage_is_temporary():
    assert classify(ROOT + mount("/", "/data", "tmpfs", "tmpfs"), "/data") == "temporary"


def test_host_path_is_permanent():
    assert classify(ROOT + mount("/apps/home-library", "/data"), "/data") == "permanent"


def test_named_volume_is_permanent():
    info = ROOT + mount("/docker/volumes/home-library_data/_data", "/data", "ext4", "/dev/sda1")
    assert classify(info, "/data") == "permanent"


def test_closest_mount_wins_and_escapes_are_read():
    info = ROOT + mount("/", "/srv", "tmpfs", "tmpfs") + mount("/lib", "/srv/my\\040books")
    assert classify(info, "/srv/my books") == "permanent"
    assert classify(info, "/srv/other") == "temporary"


def test_not_in_a_container():
    assert classify("1 0 8:1 / / rw - ext4 /dev/sda1 rw\n", "/home/me/data") == "permanent"
    assert classify("", "/data") == "unknown"
