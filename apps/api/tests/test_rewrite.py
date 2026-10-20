from app.services.rewrite import extract_slots, merge_slots, rewrite_query


def test_skips_llm_without_context():
    def boom(_: str) -> str:
        raise AssertionError("llm should not run")

    assert rewrite_query("退货运费谁承担？", [], {}, boom) == "退货运费谁承担？"


def test_rewrites_pronoun_with_history():
    def fake(_: str) -> str:
        return "K87 退货的运费由谁承担？"

    history = [{"role": "user", "content": "K87 支持退货吗？"}]
    assert rewrite_query("这个运费谁承担？", history, {}, fake).startswith("K87")


def test_extract_and_merge_slots():
    found = extract_slots("订单 ORD12345678 里的 P001")
    assert found["last_order_id"] == "ORD12345678"
    assert found["last_product_id"] == "P001"
    identity = extract_slots("我叫张三，手机13800001111")
    assert identity["last_name"] == "张三"
    assert identity["last_phone"] == "13800001111"
    assert merge_slots({"last_product_id": "P002"}, {"last_order_id": "ORD12345678"}) == {
        "last_product_id": "P002",
        "last_order_id": "ORD12345678",
    }


def test_phone_or_name_triggers_rewrite():
    def fake(_: str) -> str:
        return "张三的订单到哪了"

    assert rewrite_query("到哪了", [], {"last_name": "张三"}, fake) == "张三的订单到哪了"
