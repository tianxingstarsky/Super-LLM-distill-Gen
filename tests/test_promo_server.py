"""The standalone promo preview must never expose the repository or accept remote writes."""

import http.client
import json
import socket
import threading

import pytest

from scripts.promo import serve


WEBM = b"\x1a\x45\xdf\xa3" + b"example browser recording"


@pytest.fixture
def start_server(tmp_path):
    servers = []
    threads = []

    def start(*, record=False):
        root = tmp_path / f"promo-{len(servers)}"
        root.mkdir()
        (root / "index.html").write_text("<h1>Local film</h1>", encoding="utf-8")
        (root / "film.mjs").write_text("export const duration = 60;", encoding="utf-8")
        (root / "master.mp3").write_bytes(b"0123456789")
        (root / "shujian-cube-promo.mp4").write_bytes(b"mp4-video-fixture")
        (root / "watch.html").write_text('<video controls src="shujian-cube-promo.mp4"></video>', encoding="utf-8")
        (root / "fonts").mkdir()
        (root / "fonts" / "promo.woff2").write_bytes(b"wOF2")
        (root / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nFilm\n", encoding="utf-8")
        server = serve.PromoServer(0, root, record)
        servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
        return server, root

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=2)


def request(server, method="GET", path="/", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def upload_headers(server, **overrides):
    return {"Origin": server.expected_origin, "Content-Type": "video/webm", **overrides}


def test_only_promo_assets_are_served_without_directory_listing(start_server, tmp_path):
    server, root = start_server()
    (tmp_path / "secret.txt").write_text("outside asset root")
    (root / ".private.txt").write_text("hidden")
    (root / "private.key").write_text("not a promo type")
    (root / "subdirectory").mkdir()
    status, headers, body = request(server)
    assert status == 200
    assert body == b"<h1>Local film</h1>"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert request(server, path="/film.mjs")[1]["Content-Type"] == "text/javascript; charset=utf-8"
    assert request(server, path="/fonts/promo.woff2")[1]["Content-Type"] == "font/woff2"
    assert request(server, path="/captions.srt")[1]["Content-Type"] == "text/plain; charset=utf-8"
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/%2eprivate.txt", "/private.key",
                 "/subdirectory/", "/%5c../secret.txt", "/index.html:secret", "/missing.html"):
        assert request(server, path=path)[0] == 404


def test_symlink_cannot_expose_a_file_outside_promo(start_server, tmp_path):
    server, root = start_server()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    try:
        (root / "linked.txt").symlink_to(secret)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    assert request(server, path="/linked.txt")[0] == 404


def test_get_and_head_media_ranges(start_server):
    server, _ = start_server()
    status, headers, body = request(server, path="/master.mp3", headers={"Range": "bytes=2-5"})
    assert (status, body) == (206, b"2345")
    assert headers["Content-Range"] == "bytes 2-5/10"
    assert headers["Content-Length"] == "4"
    status, headers, body = request(server, method="HEAD", path="/master.mp3", headers={"Range": "bytes=-3"})
    assert (status, body) == (206, b"")
    assert headers["Content-Range"] == "bytes 7-9/10"
    assert headers["Content-Length"] == "3"
    assert request(server, path="/master.mp3", headers={"Range": "bytes=8-"})[2] == b"89"


@pytest.mark.parametrize("value", ["bytes=10-", "bytes=4-2", "bytes=-0", "bytes=0-1,3-4", "invalid"])
def test_unavailable_ranges_are_rejected(start_server, value):
    server, _ = start_server()
    status, headers, _ = request(server, path="/master.mp3", headers={"Range": value})
    assert status == 416
    assert headers["Content-Range"] == "bytes */10"


@pytest.mark.parametrize("host", ["evil.example", "localhost", "127.0.0.1", "127.0.0.1:1", "localhost:1"])
def test_host_must_match_the_loopback_port(start_server, host):
    server, _ = start_server()
    assert request(server, headers={"Host": host})[0] == 403


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
def test_loopback_aliases_serve_player_and_native_film(start_server, hostname):
    server, _ = start_server()
    headers = {"Host": f"{hostname}:{server.server_port}"}
    assert request(server, headers=headers)[0] == 200
    assert request(server, path="/watch.html", headers=headers)[0] == 200
    assert request(server, path="/film.mjs", headers=headers)[1]["Content-Type"] == "text/javascript; charset=utf-8"
    status, response_headers, body = request(server, path="/shujian-cube-promo.mp4",
                                            headers={**headers, "Range": "bytes=4-8"})
    assert (status, body) == (206, b"video")
    assert response_headers["Content-Type"] == "video/mp4"
    assert response_headers["Content-Range"] == "bytes 4-8/17"
    assert request(server, "HEAD", "/shujian-cube-promo.mp4", headers=headers)[2] == b""


def test_recording_save_is_opt_in(start_server):
    server, root = start_server()
    assert request(server, "POST", "/__recording", WEBM, upload_headers(server))[0] == 403
    assert not (root / serve.RECORDING_NAME).exists()


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "http://localhost:8766"])
def test_recording_requires_exact_preview_origin(start_server, origin):
    server, root = start_server(record=True)
    headers = {"Content-Type": "video/webm"}
    if origin is not None:
        headers["Origin"] = origin
    assert request(server, "POST", "/__recording", WEBM, headers)[0] == 403
    assert not (root / serve.RECORDING_NAME).exists()


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
def test_recording_accepts_each_loopback_origin_when_it_matches_the_page_host(start_server, hostname):
    server, root = start_server(record=True)
    host = f"{hostname}:{server.server_port}"
    status, _, body = request(server, "POST", "/__recording", WEBM,
                              {"Host": host, "Origin": f"http://{host}", "Content-Type": "video/webm"})
    assert status == 200
    assert json.loads(body)["saved"] is True
    assert (root / serve.RECORDING_NAME).read_bytes() == WEBM


def test_recording_rejects_cross_alias_origin_on_the_same_port(start_server):
    server, root = start_server(record=True)
    for host, origin_host in (("localhost", "127.0.0.1"), ("127.0.0.1", "localhost")):
        assert request(server, "POST", "/__recording", WEBM,
                       {"Host": f"{host}:{server.server_port}",
                        "Origin": f"http://{origin_host}:{server.server_port}",
                        "Content-Type": "video/webm"})[0] == 403
    assert not (root / serve.RECORDING_NAME).exists()


def test_recording_atomically_replaces_one_fixed_file(start_server):
    server, root = start_server(record=True)
    destination = root / serve.RECORDING_NAME
    destination.write_bytes(b"previous recording")
    status, _, body = request(server, "POST", "/__recording", WEBM,
                              upload_headers(server, **{"Content-Type": "video/webm; codecs=vp9,opus"}))
    assert status == 200
    assert json.loads(body) == {"saved": True, "url": serve.RECORDING_NAME}
    assert destination.read_bytes() == WEBM
    assert not list(root.glob(".recording-*"))
    assert request(server, path=f"/{serve.RECORDING_NAME}")[2] == WEBM


@pytest.mark.parametrize("path", ["/__recording?filename=elsewhere.webm", "/../elsewhere.webm", "/elsewhere.webm"])
def test_upload_cannot_select_its_destination(start_server, path):
    server, root = start_server(record=True)
    assert request(server, "POST", path, WEBM, upload_headers(server))[0] == 404
    assert not (root / serve.RECORDING_NAME).exists()


def test_invalid_container_does_not_clobber_previous_recording(start_server):
    server, root = start_server(record=True)
    destination = root / serve.RECORDING_NAME
    destination.write_bytes(WEBM)
    assert request(server, "POST", "/__recording", b"not a WebM", upload_headers(server))[0] == 400
    assert destination.read_bytes() == WEBM
    assert not list(root.glob(".recording-*"))


def test_invalid_upload_metadata_rejected_before_reading_body(start_server):
    server, root = start_server(record=True)
    for extra, expected in [
        ({"Content-Type": "text/html"}, 415),
        ({"Content-Length": str(serve.MAX_RECORDING_BYTES + 1)}, 413),
        ({"Content-Length": "nonsense"}, 411),
        ({"Content-Length": "0"}, 400),
        ({"Transfer-Encoding": "chunked"}, 400),
    ]:
        assert request(server, "POST", "/__recording", b"", upload_headers(server, **extra))[0] == expected
    assert not (root / serve.RECORDING_NAME).exists()


def test_duplicate_origin_and_length_are_not_accepted(start_server):
    server, _ = start_server(record=True)
    for duplicate in ("Origin", "Content-Length"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.putrequest("POST", "/__recording")
        connection.putheader("Origin", server.expected_origin)
        connection.putheader("Content-Type", "video/webm")
        connection.putheader("Content-Length", str(len(WEBM)))
        connection.putheader(duplicate, server.expected_origin if duplicate == "Origin" else str(len(WEBM)))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == (403 if duplicate == "Origin" else 411)
        response.read()
        connection.close()


def test_incomplete_upload_keeps_previous_file_and_cleans_temporary(start_server):
    server, root = start_server(record=True)
    destination = root / serve.RECORDING_NAME
    destination.write_bytes(WEBM)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.putrequest("POST", "/__recording")
    for name, value in upload_headers(server, **{"Content-Length": "100"}).items():
        connection.putheader(name, value)
    connection.endheaders()
    connection.send(WEBM)
    connection.sock.shutdown(socket.SHUT_WR)
    response = connection.getresponse()
    assert response.status == 400
    response.read()
    connection.close()
    assert destination.read_bytes() == WEBM
    assert not list(root.glob(".recording-*"))


def test_concurrent_upload_does_not_start_a_second_write(start_server):
    server, root = start_server(record=True)
    with server.record_lock:
        assert request(server, "POST", "/__recording", WEBM, upload_headers(server))[0] == 409
    assert not (root / serve.RECORDING_NAME).exists()
    assert not list(root.glob(".recording-*"))
