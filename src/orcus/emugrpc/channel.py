"""gRPC channel to the emulator (ARCHITECTURE.md §5.4).

Target `localhost:<port>` (the token server may bind ::1 or 127.0.0.1), HTTP proxy off for this
channel only (the emulator process keeps http_proxy for the guest), Bearer token on every call.
"""

import collections

import grpc

from .generated import emulator_controller_pb2_grpc, snapshot_service_pb2_grpc

MAX_MESSAGE = 16 << 20   # a 720x1280 RGB888 frame is 2.7 MB


class _CallDetails(collections.namedtuple("_CallDetails", ("method", "timeout", "metadata", "credentials",
                                                           "wait_for_ready", "compression")),
                   grpc.ClientCallDetails):
    pass


class BearerAuth(grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
                 grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):
    """Adds `authorization: Bearer <token>`; the emulator was started with -grpc-use-token."""

    def __init__(self, token: str) -> None:
        self._header = ("authorization", f"Bearer {token}")

    def _with_token(self, details: grpc.ClientCallDetails) -> grpc.ClientCallDetails:
        metadata = [*(details.metadata or ()), self._header]
        return _CallDetails(details.method, details.timeout, metadata, details.credentials,
                            getattr(details, "wait_for_ready", None), getattr(details, "compression", None))

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return continuation(self._with_token(client_call_details), request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        return continuation(self._with_token(client_call_details), request)

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        return continuation(self._with_token(client_call_details), request_iterator)

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        return continuation(self._with_token(client_call_details), request_iterator)


def make_channel(port: int, token: str, host: str = "localhost") -> grpc.Channel:
    raw = grpc.insecure_channel(f"{host}:{port}", options=[
        ("grpc.enable_http_proxy", 0),
        ("grpc.max_receive_message_length", MAX_MESSAGE),
        ("grpc.max_send_message_length", MAX_MESSAGE),
    ])
    return grpc.intercept_channel(raw, BearerAuth(token))


def controller(channel: grpc.Channel) -> emulator_controller_pb2_grpc.EmulatorControllerStub:
    return emulator_controller_pb2_grpc.EmulatorControllerStub(channel)


def snapshots(channel: grpc.Channel) -> snapshot_service_pb2_grpc.SnapshotServiceStub:
    return snapshot_service_pb2_grpc.SnapshotServiceStub(channel)
