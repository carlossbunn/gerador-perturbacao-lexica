"""Integração opcional: executa apenas funções de preparação do pipeline público."""
import ast
import hashlib
import logging
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
import typing
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gerador import BASE, BASE_CLEAN, BASE_SOURCE, KINDS, generate, read_json

URL = "https://raw.githubusercontent.com/carlossbunn/TCC-2-Avalia-o-de-Robustez/main/pipeline.py"


def main():
    source = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8")
    names = {"sha256_text", "norm_text", "normalize_category", "normalize_conditions", "get_prompt_text",
             "walk_items", "build_clean_baseline_dataset", "extract_target_text", "canonical_key",
             "build_canonical_prompt_index", "effective_perturbation_group", "central_noise_level",
             "intensity_label", "stable_attack_hash", "resolve_attack_id", "intent_reference",
             "prompt_noise_features", "flatten_prompts"}
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in functions} != names:
        raise RuntimeError("O contrato do pipeline mudou: funções esperadas ausentes.")
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + functions, type_ignores=[])
    result = generate(read_json(Path(__file__).resolve().parents[1] / "exemplos/modelo_entrada.json"))
    namespace = {**vars(typing), "re": re, "hashlib": hashlib, "log": logging.getLogger("integration"),
                 "BASELINE_DATASET_ID": BASE_CLEAN, "BASELINE_SOURCE_DATASET_ID": BASE_SOURCE,
                 "BASELINE_CLEAN_SOURCE_DATASET": BASE, "BASELINE_CLEAN_PERTURBATION_TYPE": BASE,
                 "load_payload": lambda: result, "_CANONICAL_INDEX": None,
                 "_CANONICAL_INDEX_LOCK": threading.Lock()}
    exec(compile(ast.fix_missing_locations(module), "pipeline_preparation_only", "exec"), namespace)
    baseline = namespace["build_clean_baseline_dataset"](result)
    assert len(baseline) == 6
    records = []
    for key, data in [(BASE_CLEAN, baseline)] + [(key, entry["data"]) for key, entry in result["datasets"].items() if key != BASE_SOURCE]:
        config = SimpleNamespace(id=key, name=key, filename="integration.json", slug=key.lower())
        flattened = namespace["flatten_prompts"](data, config, None)
        assert len(flattened) == len(data)
        for raw, prepared in zip(data, flattened):
            assert prepared["prompt"] == raw["prompt"]
            assert prepared["canonical_attack_id"] == raw["original_id"]
            assert raw["intent"] in prepared["intent_reference"]
            assert prepared["pass_conditions"] == raw["pass_conditions"]
            expected = "base" if key == BASE_CLEAN else raw["metadata"]["effective_perturbation_group"]
            assert prepared["effective_perturbation_group"] == expected, (raw["id"], prepared)
            assert prepared["central_noise_level"] == raw["central_noise_level"]
        records.extend(flattened)
    assert len(records) == 132
    assert {r["effective_perturbation_group"] for r in records} == {"base", *KINDS}
    print("Integração OK: 132 registros; baseline, 7 operações, 3 escopos, IDs, intenção e critérios reconhecidos.")
    print("SHA-256 do pipeline consultado:", hashlib.sha256(source.encode()).hexdigest())


if __name__ == "__main__":
    main()
