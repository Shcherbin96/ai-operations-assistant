"""Inter-step data-flow: resolve {{step.field}} references from earlier outputs."""

from ops_assistant.dataflow import referenced_steps, resolve_references


def test_full_reference_is_replaced_with_the_value() -> None:
    outputs = {"s1": {"from": "anna@example.com", "subject": "Hi"}}
    assert resolve_references({"to": "{{s1.from}}"}, outputs) == {"to": "anna@example.com"}


def test_reference_into_a_list_uses_the_first_item() -> None:
    outputs = {"s1": [{"from": "a@b.c"}, {"from": "x@y.z"}]}
    assert resolve_references({"to": "{{s1.from}}"}, outputs) == {"to": "a@b.c"}


def test_model_style_wrapper_and_index_reference_resolves() -> None:
    # LLMs often guess a shape like results[0].from; resolve it against the leaf.
    outputs = {"s1": [{"from": "anna@x.com", "subject": "Hi"}]}
    out = resolve_references(
        {"to": "{{s1.results[0].from}}", "subject": "Re: {{s1[0].subject}}"}, outputs
    )
    assert out == {"to": "anna@x.com", "subject": "Re: Hi"}


def test_embedded_reference_is_substituted_as_text() -> None:
    outputs = {"s1": {"subject": "Invoice"}}
    out = resolve_references({"body": "Re: {{s1.subject}} — thanks"}, outputs)
    assert out == {"body": "Re: Invoice — thanks"}


def test_nested_path_and_whole_output_reference() -> None:
    outputs = {"s1": {"user": {"email": "deep@x.com"}}, "s2": {"id": 7}}
    assert resolve_references({"to": "{{s1.user.email}}"}, outputs)["to"] == "deep@x.com"
    assert resolve_references({"blob": "{{s2}}"}, outputs)["blob"] == {"id": 7}


def test_unresolvable_reference_is_left_literal() -> None:
    assert resolve_references({"to": "{{missing.x}}"}, {}) == {"to": "{{missing.x}}"}
    # empty list output -> unresolvable, left literal
    assert resolve_references({"to": "{{s1.f}}"}, {"s1": []}) == {"to": "{{s1.f}}"}
    # path descends into a scalar -> unresolvable, left literal
    assert resolve_references({"to": "{{s1.a.b}}"}, {"s1": {"a": 5}}) == {"to": "{{s1.a.b}}"}
    # malformed reference with no step id -> left literal
    assert resolve_references({"to": "{{.x}}"}, {"s1": {}}) == {"to": "{{.x}}"}


def test_non_reference_and_nested_values_are_preserved() -> None:
    outputs = {"s1": {"from": "a@b.c"}}
    args = {"plain": "hello", "n": 5, "nested": {"to": "{{s1.from}}"}, "list": ["{{s1.from}}", 1]}
    out = resolve_references(args, outputs)
    assert out["plain"] == "hello"
    assert out["n"] == 5
    assert out["nested"] == {"to": "a@b.c"}
    assert out["list"] == ["a@b.c", 1]


def test_referenced_steps_finds_every_id_across_nested_structures() -> None:
    args = {
        "to": "{{s1.from}}",
        "cc": ["{{s2.x}}", "static@example.com"],
        "meta": {"note": "see {{s1.subject}} and {{s3}}"},
    }
    assert referenced_steps(args) == {"s1", "s2", "s3"}


def test_referenced_steps_is_empty_without_references() -> None:
    assert referenced_steps({"to": "anna@example.com", "n": 5, "list": ["a", 1]}) == set()
