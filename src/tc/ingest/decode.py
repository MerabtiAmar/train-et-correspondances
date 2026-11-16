"""Protobuf decoding shared by the Trip Updates and Alerts parsers."""

from __future__ import annotations

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

FeedMessage = gtfs_realtime_pb2.FeedMessage


class FeedParseError(Exception):
    pass


def decode_feed(content: bytes) -> FeedMessage:
    """Decode a GTFS-RT snapshot. An empty body decodes to an empty feed (a normal state)."""
    msg = FeedMessage()
    try:
        msg.ParseFromString(content)
    except DecodeError as exc:
        raise FeedParseError(f"protobuf decode failed: {exc}") from exc
    return msg


def header_timestamp(msg: FeedMessage) -> int:
    """Feed header timestamp (epoch seconds), 0 if the feed does not carry one."""
    return int(msg.header.timestamp) if msg.header.HasField("timestamp") else 0


def enum_name(enum_wrapper, value: int) -> str:
    """Enum value to its name; unknown values (newer feed versions) become their number."""
    try:
        return enum_wrapper.Name(value)
    except ValueError:
        return str(value)
