"""Round-trip de los mensajes del protocolo y del framing por longitud."""

import pytest

from wom.net import protocol
from wom.net.protocol import (
    Bye,
    Chat,
    FrameDecoder,
    GameSetup,
    Hash,
    Hello,
    Orders,
    Ping,
    Pong,
    Ready,
    Start,
    StateSync,
    Welcome,
    decode,
    encode,
    encode_frame,
    from_dict,
    to_dict,
)

MESSAGES = [
    Hello(wom_version="0.4.0", protocol_version=1, config_hash="abc", name="Ana"),
    Welcome(accepted=True, reason="", name="Beto"),
    GameSetup(state={"turn": 0, "seed": 42}, rules={"turn_seconds": 60}, names=["Ana", "Beto"]),
    Ready(ready=True),
    Start(),
    Orders(turn=3, orders=[{"kind": "move", "army_id": 1, "path": [[0, 0]]}]),
    Hash(turn=3, digest="deadbeef"),
    StateSync(turn=3, state={"turn": 3}),
    Chat(name="Ana", text="hola", ts=123.0),
    Ping(t=1.0),
    Pong(t=1.0),
    Bye(reason="host canceló"),
]


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: type(m).__name__)
def test_message_round_trips_via_dict(message):
    assert from_dict(to_dict(message)) == message


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: type(m).__name__)
def test_message_round_trips_via_bytes(message):
    assert decode(encode(message)) == message


def test_to_dict_carries_type_discriminator():
    data = to_dict(Ready(ready=False))
    assert data["type"] == "Ready"
    assert data["ready"] is False


def test_from_dict_rejects_unknown_type():
    with pytest.raises(ValueError):
        from_dict({"type": "Inexistente"})


def test_frame_decoder_single_message():
    decoder = FrameDecoder()
    msg = Chat(name="Ana", text="hola", ts=1.0)
    assert decoder.feed(encode_frame(msg)) == [msg]


def test_frame_decoder_handles_concatenated_frames():
    decoder = FrameDecoder()
    stream = encode_frame(MESSAGES[0]) + encode_frame(MESSAGES[1]) + encode_frame(MESSAGES[2])
    assert decoder.feed(stream) == MESSAGES[:3]


def test_frame_decoder_handles_split_frame():
    decoder = FrameDecoder()
    frame = encode_frame(Hash(turn=7, digest="x" * 40))
    # Entregamos el frame byte a byte: solo se reconstruye al llegar el último.
    out = []
    for i in range(len(frame)):
        out.extend(decoder.feed(frame[i : i + 1]))
    assert out == [Hash(turn=7, digest="x" * 40)]


def test_frame_decoder_waits_for_incomplete_length_prefix():
    decoder = FrameDecoder()
    frame = encode_frame(Start())
    assert decoder.feed(frame[:2]) == []  # prefijo de longitud incompleto
    assert decoder.feed(frame[2:]) == [Start()]


def test_frame_decoder_rejects_oversized_frame():
    decoder = FrameDecoder()
    import struct

    bogus = struct.pack(">I", protocol.MAX_FRAME_SIZE + 1)
    with pytest.raises(ValueError):
        decoder.feed(bogus)


def test_oversized_message_is_rejected_on_encode():
    big = GameSetup(state={"blob": "x" * (protocol.MAX_FRAME_SIZE + 1)}, rules={}, names=[])
    with pytest.raises(ValueError):
        encode_frame(big)
