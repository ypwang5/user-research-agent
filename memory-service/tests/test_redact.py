import pytest

from memory_service.redact import redact


@pytest.mark.parametrize("raw,label", [
    ("我手机 13812345678 随时打", "[手机号]"),
    ("电话 +86 138-1234-5678", "[手机号]"),
    ("邮箱是 zhang.san@example.com", "[邮箱]"),
    ("身份证 11010119900307123X", "[身份证]"),
    ("卡号 6222021234567890123", "[银行卡]"),
    ("加我微信：zhangsan_88", "[微信]"),
    ("QQ号 123456789", "[QQ]"),
])
def test_rules_hit(raw, label):
    text, counts = redact(raw)
    assert label in text
    assert counts.get(label) == 1


def test_idcard_not_split_into_phone():
    text, counts = redact("证件 110101199003071234")
    assert text == "证件 [身份证]"
    assert "[手机号]" not in counts


@pytest.mark.parametrize("raw", [
    "每个月大概花 3000 元在这上面",
    "2026年10月开始用的",
    "一天要处理 150 张单据，耗时 3 小时",
    "订单号 20261028",
])
def test_no_false_positive(raw):
    text, counts = redact(raw)
    assert text == raw
    assert counts == {}


def test_names_replaced():
    text, counts = redact("张三说他老板李四很难搞，张三很头疼", names=["张三", "李四"])
    assert "张三" not in text and "李四" not in text
    assert counts["人名"] == 3


def test_single_name_token():
    text, _ = redact("王小明每天加班", names=["王小明"])
    assert text == "[受访者]每天加班"
