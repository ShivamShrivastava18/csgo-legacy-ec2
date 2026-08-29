import struct
import a2s


def build_info_packet(map_name=b"de_dust2", players=3, max_players=16):
    return (
        b"\xff\xff\xff\xffI\x11"
        + b"LinuxGSM\x00"
        + map_name + b"\x00"
        + b"csgo\x00"
        + b"Counter-Strike: Global Offensive\x00"
        + struct.pack("<H", 730)
        + bytes([players, max_players])
        + b"\x00" * 5
    )


def test_parse_info_happy_path():
    info = a2s.parse_info(build_info_packet())
    assert info == {"name": "LinuxGSM", "map": "de_dust2", "players": 3, "max_players": 16}


def test_parse_info_rejects_non_info_packet():
    assert a2s.parse_info(b"\xff\xff\xff\xffA\x01\x02\x03\x04") is None


def test_parse_info_rejects_short_packet():
    assert a2s.parse_info(b"\xff\xff") is None


class FakeSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        self.sent.append(data)

    def recvfrom(self, n):
        return self.replies.pop(0), ("1.2.3.4", 27015)

    def close(self):
        pass


def test_query_handles_challenge(monkeypatch):
    challenge = b"\xff\xff\xff\xffA\xaa\xbb\xcc\xdd"
    fake = FakeSocket([challenge, build_info_packet()])
    monkeypatch.setattr(a2s.socket, "socket", lambda *a, **k: fake)
    info = a2s.query("1.2.3.4")
    assert info["map"] == "de_dust2"
    assert fake.sent[1] == a2s.A2S_INFO + b"\xaa\xbb\xcc\xdd"


def test_query_returns_none_on_timeout(monkeypatch):
    class TimeoutSocket(FakeSocket):
        def recvfrom(self, n):
            raise a2s.socket.timeout()

    monkeypatch.setattr(a2s.socket, "socket", lambda *a, **k: TimeoutSocket([]))
    assert a2s.query("1.2.3.4") is None
