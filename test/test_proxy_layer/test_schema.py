"""schema 单元测试：只锁本模块自己的约定，不测调度、注册和错误码映射。"""

import pytest
from pydantic import ValidationError

from proxy_layer.schema import (
    CommandProtocol,
    ErrorCode,
    EventProtocol,
    MessageType,
    ReceiptProtocol,
    build_error_message,
    from_line,
    make_command,
    make_event,
    make_receipt_error,
    make_receipt_success,
    to_line,
)


def test_make_command_defaults_empty_parameters():
    """不传 parameters 时，约定是空 dict，不是 None。"""
    cmd = make_command("req-1", "c-1", "chat.send")

    assert cmd.message_type == MessageType.COMMAND
    assert cmd.request_id == "req-1"
    assert cmd.conversation_id == "c-1"
    assert cmd.operation == "chat.send"
    assert cmd.parameters == {}
    assert cmd.protocol_version == "version-1"


def test_make_event_defaults_empty_data():
    event = make_event("c-1", "token")

    assert event.message_type == MessageType.EVENT
    assert event.event == "token"
    assert event.data == {}


def test_make_receipt_success_carries_result_without_error():
    receipt = make_receipt_success("req-1", "c-1", result={"ok": True})

    assert receipt.accepted is True
    assert receipt.result == {"ok": True}
    assert receipt.error_code is None
    assert receipt.error_message is None


def test_make_receipt_error_allows_missing_request_id():
    """request_id 为 None 只用于整行都解析不了的失败回执。"""
    receipt = make_receipt_error(
        None,
        "c-1",
        ErrorCode.INVALID_MESSAGE,
        error_message="不是合法 JSON",
    )

    assert receipt.accepted is False
    assert receipt.request_id is None
    assert receipt.error_code == ErrorCode.INVALID_MESSAGE
    assert receipt.result is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"accepted": True, "error_code": ErrorCode.BUSY},
        {"accepted": True, "error_message": "不应出现"},
        {"accepted": False},
        {"accepted": False, "error_code": ErrorCode.BUSY, "result": {"x": 1}},
    ],
)
def test_receipt_rejects_illegal_shape(overrides):
    """成功不能带错误，失败必须有错误码且不能带 result。"""
    with pytest.raises(ValidationError):
        ReceiptProtocol(
            conversation_id="c-1",
            request_id="req-1",
            **overrides,
        )


def test_from_line_parses_command():
    line = to_line(make_command("req-1", "c-1", "chat.send", {"text": "hi"}))

    msg = from_line(line)

    assert isinstance(msg, CommandProtocol)
    assert msg.operation == "chat.send"
    assert msg.parameters == {"text": "hi"}


def test_command_survives_to_line_and_from_line():
    original = make_command("req-1", "c-1", "chat.send", {"text": "hi"})

    restored = from_line(to_line(original))

    assert restored.model_dump() == original.model_dump()


def test_make_event_keeps_given_data():
    event = make_event("c-1", "token", {"text": "hi"})

    assert event.data == {"text": "hi"}


def test_make_receipt_success_defaults_result_to_none():
    receipt = make_receipt_success("req-1", "c-1")

    assert receipt.accepted is True
    assert receipt.result is None


def test_make_receipt_error_keeps_request_id_without_message():
    receipt = make_receipt_error("req-1", "c-1", ErrorCode.UNKNOWN_OPERATION)

    assert receipt.request_id == "req-1"
    assert receipt.accepted is False
    assert receipt.error_code == ErrorCode.UNKNOWN_OPERATION
    assert receipt.error_message is None
    assert receipt.result is None


@pytest.mark.parametrize(
    ("original", "expected_type"),
    [
        (make_event("c-1", "token", {"text": "你好"}), EventProtocol),
        (make_receipt_success("req-1", "c-1", result={"ok": True}), ReceiptProtocol),
        (
            make_receipt_error("req-1", "c-1", ErrorCode.CANCELLED, "已被取消"),
            ReceiptProtocol,
        ),
    ],
)
def test_event_and_receipt_survive_to_line_and_from_line(original, expected_type):
    line = to_line(original)

    restored = from_line(line)

    assert "\n" not in line
    assert isinstance(restored, expected_type)
    assert restored.model_dump() == original.model_dump()


def test_to_line_keeps_non_ascii_characters():
    line = to_line(make_command("req-1", "c-1", "chat.send", {"text": "你好"}))

    assert '"text":"你好"' in line


def test_from_line_rejects_invalid_json():
    with pytest.raises(ValidationError):
        from_line("{not json")


def test_from_line_rejects_unknown_message_type():
    with pytest.raises(ValidationError):
        from_line('{"message_type":"nope"}')


@pytest.mark.parametrize(
    "line",
    [
        '{"message_type":"command","conversation_id":"c-1","request_id":"req-1","parameters":{}}',
        '{"message_type":"event","conversation_id":"c-1","data":{}}',
        '{"message_type":"receipt","conversation_id":"c-1","request_id":"req-1"}',
    ],
)
def test_from_line_rejects_missing_required_field(line):
    with pytest.raises(ValidationError):
        from_line(line)


def test_build_error_message_names_each_missing_field():
    with pytest.raises(ValidationError) as caught:
        from_line(
            '{"message_type":"command","conversation_id":"c-1","parameters":{}}'
        )

    text = build_error_message(caught.value)

    assert "command.request_id: Field required" in text
    assert "command.operation: Field required" in text
    assert "；" in text


def test_build_error_message_labels_invalid_json_as_root():
    with pytest.raises(ValidationError) as caught:
        from_line("{not json")

    assert build_error_message(caught.value).startswith("<根级>: ")
