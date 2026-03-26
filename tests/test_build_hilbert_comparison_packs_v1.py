import json

from scripts.build_hilbert_comparison_packs_v1 import HilbertDatasetRow
from scripts.build_hilbert_comparison_packs_v1 import PACK_SPECS
from scripts.build_hilbert_comparison_packs_v1 import canonicalize_task_input_text
from scripts.build_hilbert_comparison_packs_v1 import split_header_and_formal_statement


def test_split_header_and_formal_statement_removes_trailing_sorry():
    text = (
        "import Mathlib\n"
        "import Aesop\n\n"
        "theorem demo (n : Nat) : n = n := by\n"
        "  sorry\n"
    )
    header, formal = split_header_and_formal_statement(text)
    assert header == "import Mathlib\nimport Aesop\n\n"
    assert formal == "theorem demo (n : Nat) : n = n := by\n"


def test_split_header_and_formal_statement_handles_colon_equals_sorry():
    text = "import Mathlib\n\ntheorem demo : True := sorry"
    _, formal = split_header_and_formal_statement(text)
    assert formal == "theorem demo : True := by\n"


def test_hilbert_dataset_row_serializes_expected_fields():
    row = HilbertDatasetRow(
        name="demo",
        header="import Mathlib\n",
        formal_statement="theorem demo : True := by\n",
        split="test",
        informal_prefix="",
        benchmark="minif2f_v2",
        source_pack="pack_a",
        reference_tier="gold",
        reference_source="Seed-Prover MiniF2F.zip",
        reference_quality="published_clean",
    )
    payload = json.loads(json.dumps(row.__dict__))
    assert payload["name"] == "demo"
    assert payload["reference_tier"] == "gold"
    assert payload["reference_quality"] == "published_clean"


def test_split_header_and_formal_statement_rejects_sorry_in_header():
    text = (
        "import Mathlib\n\n"
        "abbrev helper : Nat := sorry\n\n"
        "theorem demo : True := by\n"
        "  trivial\n"
    )
    try:
        split_header_and_formal_statement(text)
    except ValueError as exc:
        assert "header contains sorry" in str(exc)
    else:
        raise AssertionError("expected ValueError for sorry in header")


def test_canonicalize_task_input_text_overrides_malformed_pack_task():
    malformed = (
        "import Mathlib\n\n"
        "theorem mathd_numbertheory_780\n"
        "  (m : ℕ)\n"
        "  (h₂ : ∃ (x:ZMod m), x = 6⁻¹)\n"
        "  (h₃ : x ≡ 6^2 [MOD m]) :\n"
        "  m = 43 := by\n"
        "  sorry\n"
    )
    fixed = canonicalize_task_input_text(task_id="mathd_numbertheory_780", input_text=malformed)
    assert "(m x : ℤ)" in fixed
    assert "(h₂ : (6 * x) % m = 1)" in fixed
    assert "(h₃ : (x - 6 ^ 2) % m = 0)" in fixed


def test_pack_specs_include_calibration_and_stress_tranches():
    assert "pack_a2_calibration_minif2f_v1" in PACK_SPECS
    assert "pack_s1_imo_stress_minif2f_v1" in PACK_SPECS
    calibration_ids = [entry["task_id"] for entry in PACK_SPECS["pack_a2_calibration_minif2f_v1"]["task_entries"]]
    assert "imo_1977_p6" not in calibration_ids
    assert "mathd_algebra_282" not in calibration_ids
    assert calibration_ids[:2] == ["mathd_numbertheory_780", "mathd_numbertheory_530"]


def test_pack_specs_exclude_malformed_mathd_algebra_282_from_active_hilbert_packs():
    active_packs = [
        "pack_a_seedproof_sanity_minif2f_v1",
        "pack_a2_calibration_minif2f_v1",
        "pack_b_hilbert_comparator_minif2f_v1",
    ]
    for pack_name in active_packs:
        task_ids = [entry["task_id"] for entry in PACK_SPECS[pack_name]["task_entries"]]
        assert "mathd_algebra_282" not in task_ids
