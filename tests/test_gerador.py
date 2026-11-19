import copy
import json
from pathlib import Path
import tempfile
import unittest

from gerador import BASE_SOURCE, CATEGORIES, KINDS, MERGED, ValidationError, generate, normalize, read_json, save_output

ROOT = Path(__file__).resolve().parents[1]


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.payload = read_json(ROOT / "exemplos/modelo_entrada.json")

    def variants(self, result):
        return [item for key, entry in result["datasets"].items() if key != BASE_SOURCE for item in entry["data"]]

    def test_complete_benchmark_counts_identity_and_wrapper(self):
        source = copy.deepcopy(self.payload)
        result = generate(self.payload)
        self.assertEqual(self.payload, source)
        self.assertEqual(result["metadata"]["benchmark_item_count"], 132)
        originals = {i["original_id"]: i for i in source["items"]}
        all_items = [i for entry in result["datasets"].values() for i in entry["data"]]
        self.assertEqual(len({i["id"] for i in all_items}), 132)
        ids = {i["id"] for i in all_items}
        for item in self.variants(result):
            original = originals[item["original_id"]]
            target = original["target_text"]
            start = original["prompt"].index(target)
            self.assertEqual(item["prompt"], original["prompt"][:start] + item["perturbed_source"] + original["prompt"][start + len(target):])
            self.assertEqual(item["canonical_id"], original["original_id"])
            for key in ("intent", "pass_conditions", "fail_conditions"):
                self.assertEqual(item[key], original[key])
            if "variant_of" in item:
                self.assertIn(item["variant_of"], ids)
            if item["central_noise_level"]:
                self.assertEqual(len(item["metadata"]["word_edits"]), item["central_noise_level"])
        merged = result["datasets"][MERGED]["data"]
        self.assertEqual({i["merged_section_category"] for i in merged}, {"substituicao", "insercao_pontuacao"})

    def test_determinism_selection_order_and_seed(self):
        first = generate(self.payload)
        self.assertEqual(first, generate(self.payload))
        subset = generate(self.payload, ["delete"])
        self.assertEqual(first["datasets"]["IMPERATIVO_DELETE"], subset["datasets"]["IMPERATIVO_DELETE"])
        reversed_payload = copy.deepcopy(self.payload)
        reversed_payload["items"].reverse()
        reordered = generate(reversed_payload)
        a = {i["id"]: i for i in self.variants(first)}
        b = {i["id"]: i for i in self.variants(reordered)}
        self.assertEqual(a, b)
        self.assertNotEqual(first["datasets"], generate(self.payload, seed=43)["datasets"])

    def test_published_mode_is_full_stage_plus_exact_insertions(self):
        result = generate(self.payload, mode="publicado")
        variants = {i["id"]: i for i in self.variants(result)}
        originals = {i["original_id"]: i for i in self.payload["items"]}
        for item in variants.values():
            level = item["central_noise_level"]
            if not level:
                continue
            full = variants[item["variant_of"]]
            self.assertEqual(len(item["perturbed_source"]), len(full["perturbed_source"]) + level)
            edits = item["metadata"]["word_edits"]
            self.assertEqual(len(edits), level)
            char = "x" if level == 1 else "k"
            for edit in edits:
                before, after = edit["before"], edit["after"]
                pos = max(1, len(before) // 2)
                self.assertEqual(after, before[:pos] + char + before[pos:])
                self.assertIn(edit["word_index"], originals[item["original_id"]]["central_targets"])
        only_central = generate(self.payload, ["delete"], ["1"], mode="publicado")
        self.assertTrue(all(i["variant_of"].endswith("__base") for i in self.variants(only_central)))

    def test_diff_can_reconstruct_original_and_final(self):
        for mode in ("tcc", "publicado"):
            for item in self.variants(generate(self.payload, mode=mode)):
                original = item["target_text"]
                chunks, cursor = [], 0
                for diff in item["metadata"]["diff"]:
                    start, end = diff["original_span"]
                    self.assertEqual(original[start:end], diff["before"])
                    a, b = diff["output_span"]
                    self.assertEqual(item["perturbed_source"][a:b], diff["after"])
                    chunks.extend((original[cursor:start], diff["after"]))
                    cursor = end
                chunks.append(original[cursor:])
                self.assertEqual("".join(chunks), item["perturbed_source"])

    def test_validation_and_strict_profile(self):
        for field, value in [("category", "invalid"), ("intent", ""), ("pass_conditions", []),
                             ("central_targets", [999]), ("target_text", "não existe")]:
            bad = copy.deepcopy(self.payload)
            bad["items"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                generate(bad)
        bad = copy.deepcopy(self.payload)
        bad["items"].append(copy.deepcopy(bad["items"][0]))
        with self.assertRaises(ValidationError):
            normalize(bad)
        with self.assertRaises(ValidationError):
            normalize(self.payload, strict=True)
        full = {"items": []}
        for item in self.payload["items"]:
            for index in range(5):
                new = copy.deepcopy(item)
                new["original_id"] += f"-{index}"
                full["items"].append(new)
        result = generate(full, strict=True)
        self.assertEqual(result["metadata"]["benchmark_item_count"], 660)
        with self.assertRaises(ValidationError):
            generate(self.payload, rate=float("nan"))

    def test_no_change_is_explicit_and_files_not_overwritten(self):
        payload = copy.deepcopy(self.payload)
        payload["items"] = payload["items"][:1]
        item = payload["items"][0]
        item.update(prompt="Moldura «zzz» fim.", target_text="zzz", central_targets=[0])
        result = generate(payload, ["homoglifo"], ["2"])
        variant = self.variants(result)[0]
        self.assertFalse(variant["metadata"]["changed"])
        self.assertTrue(result["metadata"]["warnings"])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "entrada.json"
            source.write_text("original", encoding="utf-8")
            first, second = save_output(source, result), save_output(source, result)
            self.assertNotEqual(first, second)
            self.assertEqual(source.read_text(), "original")
            self.assertEqual(json.loads(first.read_text()), result)

    def test_export_can_be_reimported_as_clean_baseline(self):
        result = generate(self.payload)
        items, _ = normalize(result)
        self.assertEqual(len(items), len(self.payload["items"]))
        self.assertEqual([i["prompt"] for i in items], [i["prompt"] for i in self.payload["items"]])

    def test_legacy_aliases_do_not_shadow_generated_prompt_or_category(self):
        self.payload["items"][0].update(prompt_imperativo="Texto legado", categoria="unknown")
        result = generate(self.payload, ["delete"])
        for item in self.variants(result):
            self.assertNotIn("prompt_imperativo", item)
            self.assertNotIn("categoria", item)

    def test_null_fields_are_accepted_and_omitted_recursively(self):
        payload = copy.deepcopy(self.payload)
        payload["metadata"] = {"absent": None, "values": [None, 0, False, {"x": None, "y": "ok"}]}
        item = payload["items"][0]
        item.update(intent=None, pass_conditions=None, fail_conditions=[None, "Critério mantido", None],
                    central_targets=[None, 3, None, 5], extra=None,
                    metadata={"nested": {"gone": None, "keep": False}})
        payload["items"][1]["metadata"] = None
        payload["items"][2]["fail_conditions"] = [None, None]
        result = generate(payload)
        record = result["datasets"][BASE_SOURCE]["data"][0]
        self.assertNotIn("intent", record)
        self.assertNotIn("pass_conditions", record)
        self.assertNotIn("extra", record)
        self.assertEqual(record["fail_conditions"], ["Critério mantido"])
        self.assertEqual(record["central_targets"], [3, 5])
        self.assertEqual(record["metadata"]["source_metadata"], {"nested": {"keep": False}})
        self.assertEqual(result["metadata"]["input_metadata"], {"values": [0, False, {"y": "ok"}]})

        def assert_no_null(value):
            self.assertIsNotNone(value)
            if isinstance(value, dict):
                for child in value.values():
                    assert_no_null(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_null(child)
        assert_no_null(result)
        self.assertIsNone(payload["items"][0]["intent"])
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_output(Path(tmp) / "source.json", result)
            self.assertEqual(json.loads(saved.read_text()), result)

    def test_null_records_and_prompts_are_skipped_ids_derived_category_omitted(self):
        payload = copy.deepcopy(self.payload)
        payload["items"].extend([None, {"prompt": None}])
        payload["items"][0].update(original_id=None, category=None, target_text=None, central_targets=None)
        result = generate(payload)
        self.assertEqual(result["metadata"]["input_count"], 6)
        record = result["datasets"][BASE_SOURCE]["data"][0]
        self.assertTrue(record["original_id"].startswith("AUTO-"))
        self.assertNotIn("category", record)
        self.assertNotIn("central_targets", record)
        self.assertEqual(record["target_text"], self.payload["items"][0]["target_text"])
        self.assertTrue(any("registro null ignorado" in w for w in result["metadata"]["warnings"]))
        with self.assertRaises(ValidationError):
            generate({"items": [None, {"prompt": None}]})


if __name__ == "__main__":
    unittest.main()
