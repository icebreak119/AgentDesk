from channels.douyin_reverse_ipc.protocol import (
    parse_request,
    encode_ok,
    encode_err,
)
from channels.douyin_reverse_ipc.errors import RpcError
import json
import pytest


def test_parse_request_ok():
    req = parse_request('{"id":"1","method":"ping","params":{}}\n')
    assert req["id"] == "1"
    assert req["method"] == "ping"
    assert req["params"] == {}


def test_parse_request_defaults_params():
    req = parse_request('{"id":"1","method":"ping"}')
    assert req["params"] == {}


def test_parse_request_invalid():
    with pytest.raises(RpcError) as ei:
        parse_request("not-json\n")
    assert ei.value.code == "invalid_request"


def test_encode_ok_roundtrip():
    line = encode_ok("1", {"pong": True})
    obj = json.loads(line)
    assert obj == {"id": "1", "ok": True, "data": {"pong": True}}
    assert line.endswith("\n")


def test_encode_err_shape():
    obj = json.loads(encode_err("1", "account_required", "missing"))
    assert obj["ok"] is False
    assert obj["error"]["code"] == "account_required"
    assert obj["error"]["message"] == "missing"
