"""Gerador reprodutível de datasets, sem dependências externas (Python 3.10+)."""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sys
import unicodedata
from collections import Counter

VERSION = "1.0.1"
CATEGORIES = ("distraction", "harmful", "hate", "jailbreak", "prompt_stealing", "social_bias")
KINDS = {
    "delete": "Deleção", "insercao_pontuacao": "Inserção de pontuação",
    "insercao_ruido": "Inserção de ruído", "substituicao": "Substituição",
    "transposicao": "Transposição", "homoglifo": "Homóglifos Unicode", "hibrido": "Híbrido",
}
SCOPES = ("1", "2", "todos")
BASE = "versificacao_prosa_poetica"
BASE_SOURCE = "IMPERATIVO_BASE_COMPLETO_ORIGINAL"
BASE_CLEAN = "IMPERATIVO_BASE_LIMPO"
MERGED = "IMPERATIVO_INSERCAO_PONTUACAO_SUBSTITUICAO"
PROMPT_ALIASES = ("prompt_imperativo", "text", "input", "instruction", "question", "content",
                  "prompt_modificado", "modified_prompt", "mutated_prompt", "attack_prompt")
WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
HOMO = dict(zip("aceiopsxyABCEHIKMOPTX", "асеіорѕхуАВСЕНІКМОРТХ"))
LEET = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7", "b": "8", "g": "9"}


class ValidationError(ValueError):
    pass


def without_nulls(value):
    """Omite campos e elementos null sem remover False, zero ou texto vazio."""
    if isinstance(value, dict):
        return {key: without_nulls(child) for key, child in value.items() if child is not None}
    if isinstance(value, list):
        return [without_nulls(child) for child in value if child is not None]
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Não foi possível ler o JSON: {exc}") from exc


def normalize(payload, strict=False):
    """Aceita o contrato documentado ou um baseline do pipeline publicado."""
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict) and "datasets" in payload:
        datasets = payload["datasets"]
        if not isinstance(datasets, dict):
            raise ValidationError("datasets deve ser um objeto.")
        entry = datasets.get(BASE_CLEAN) or datasets.get(BASE_SOURCE)
        if entry is None:
            raise ValidationError("O arquivo não contém um conjunto Base limpo.")
        raw = entry.get("data") if isinstance(entry, dict) else entry
        if not isinstance(raw, list):
            raise ValidationError("O baseline deve conter uma lista data.")
        raw = [r for r in raw if isinstance(r, dict) and not r.get("variant_of")
               and (r.get("perturbation_type") or BASE) == BASE]
    elif isinstance(payload, dict):
        raw = payload.get("items", payload.get("data", payload.get("prompts")))
    else:
        raw = None
    if not isinstance(raw, list) or not raw:
        raise ValidationError("Informe uma lista não vazia em items (ou data/prompts).")
    result, ids, warnings = [], set(), []
    for number, original in enumerate(raw, 1):
        if original is None:
            warnings.append(f"Item {number}: registro null ignorado.")
            continue
        if not isinstance(original, dict):
            raise ValidationError(f"Item {number}: esperado um objeto.")
        if "prompt" in original and original["prompt"] is None:
            warnings.append(f"Item {number}: prompt null; registro ignorado por falta de texto.")
            continue
        item = without_nulls(copy.deepcopy(original))
        def text(key):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"Item {number}: {key} deve ser texto não vazio.")
            return value
        prompt = text("prompt")
        oid = ("AUTO-" + hashlib.sha256(prompt.encode()).hexdigest()[:20]
               if "original_id" in original and original["original_id"] is None else text("original_id").strip())
        if original.get("original_id") is None:
            warnings.append(f"Item {number}: original_id null; identificador de rastreio gerado: {oid}.")
        if oid in ids:
            raise ValidationError(f"original_id repetido: {oid}")
        ids.add(oid)
        if "category" in original and original["category"] is None:
            warnings.append(f"Item {oid}: category null; categoria omitida da saída.")
        else:
            category = text("category").strip().lower().replace(" ", "_")
            if category not in CATEGORIES:
                raise ValidationError(f"Item {oid}: categoria inválida: {category}")
            item["category"] = category
        if not ("intent" in original and original["intent"] is None):
            item["intent"] = text("intent")
        for key in ("pass_conditions", "fail_conditions"):
            if key in original and original[key] is None:
                continue
            values = item.get(key)
            if values == [] and isinstance(original.get(key), list) and None in original[key]:
                item.pop(key, None)
                continue
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValidationError(f"Item {oid}: {key} deve ser uma lista não vazia de textos.")
        if not isinstance(item.get("metadata", {}), dict):
            raise ValidationError(f"Item {oid}: metadata deve ser um objeto.")
        target = item.get("target_text")
        if target is None:
            matches = re.findall(r"«([^»]+)»", prompt, re.S)
            target = matches[-1] if matches else prompt
        if not isinstance(target, str) or not target.strip() or prompt.count(target) != 1:
            raise ValidationError(f"Item {oid}: target_text precisa ocorrer exatamente uma vez no prompt.")
        if unicodedata.normalize("NFC", target) != target:
            raise ValidationError(f"Item {oid}: normalize target_text e prompt para Unicode NFC antes de gerar.")
        words = list(WORDS.finditer(target))
        if not words:
            raise ValidationError(f"Item {oid}: trecho-alvo sem palavras.")
        central = item.get("central_targets")
        if central == [] and isinstance(original.get("central_targets"), list) and None in original["central_targets"]:
            item.pop("central_targets", None)
            central = None
        if central is not None:
            if (not isinstance(central, list) or not 1 <= len(central) <= 2
                    or any(type(i) is not int or i < 0 or i >= len(words) for i in central)
                    or len(set(central)) != len(central)):
                raise ValidationError(f"Item {oid}: central_targets deve conter 1 ou 2 índices distintos de palavras (a partir de zero).")
        else:
            warnings.append(f"{oid}: termos centrais selecionados por proximidade textual, sem análise semântica.")
        item.update(original_id=oid, prompt=prompt, target_text=target)
        result.append(item)
    if not result:
        raise ValidationError("Nenhum caso com texto disponível após ignorar os registros null.")
    counts = Counter(i.get("category", "unknown") for i in result)
    balanced = len(result) == 30 and all(counts[c] == 5 for c in CATEGORIES)
    if strict and not balanced:
        raise ValidationError(f"Perfil TCC exige 30 itens, 5 em cada categoria. Recebido: {dict(counts)}")
    if not balanced:
        warnings.append("Entrada fora do perfil de 30 casos / 5 por categoria do TCC.")
    return result, warnings


def word_order(text, explicit=None):
    words = list(WORDS.finditer(text))
    # O meio do trecho não é necessariamente o termo semanticamente central.
    order = sorted(range(len(words)), key=lambda i: (len(words[i].group()) < 3,
                   abs((words[i].start() + words[i].end()) / 2 - len(text) / 2), i))
    if explicit:
        order = list(explicit) + [i for i in order if i not in explicit]
    return words, order


def replace_word(word, kind, rng, rate):
    chars = list(word)
    count = max(1, math.ceil(len(chars) * rate))
    if kind == "delete":
        positions = rng.sample(range(len(chars)), min(count, len(chars) - 1))
        return "".join(c for i, c in enumerate(chars) if i not in positions)
    if kind == "transposicao":
        pairs = [i for i in range(len(chars) - 1) if chars[i] != chars[i + 1]]
        rng.shuffle(pairs)
        occupied = set()
        for i in pairs[:]:
            if i in occupied or i + 1 in occupied:
                continue
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
            occupied.update((i, i + 1))
            if len(occupied) // 2 >= count:
                break
        return "".join(chars)
    if kind == "homoglifo":
        candidates = [i for i, c in enumerate(chars) if c in HOMO]
        for i in rng.sample(candidates, min(count, len(candidates))):
            chars[i] = HOMO[chars[i]]
        return "".join(chars)
    if kind in ("substituicao", "hibrido"):
        for i in rng.sample(range(len(chars)), min(count, len(chars))):
            c = chars[i]
            chars[i] = LEET.get(c.lower()) or rng.choice([x for x in "abcdefghijklmnopqrstuvwxyz" if x != c.lower()])
        changed = "".join(chars)
        return replace_word(changed, "insercao_pontuacao", rng, rate) if kind == "hibrido" else changed
    alphabet = ".,-;:!?" if kind == "insercao_pontuacao" else "xk#@%&0123456789"
    for _ in range(count):
        pos = rng.randint(1, max(1, len(chars) - 1))
        chars.insert(pos, rng.choice(alphabet))
    return "".join(chars)


def transform(text, kind, scope, rng, rate, explicit=None, central_insertion=False):
    words, order = word_order(text, explicit)
    chosen = list(range(len(words))) if scope == "todos" else order[:int(scope)]
    edits, chunks, cursor = [], [], 0
    for i, match in enumerate(words):
        before = match.group()
        after = before
        if i in chosen:
            if central_insertion:
                pos = max(1, len(before) // 2)
                after = before[:pos] + ("x" if scope == "1" else "k") + before[pos:]
            else:
                after = replace_word(before, kind, rng, rate)
            edits.append({"word_index": i, "start": match.start(), "end": match.end(),
                          "before": before, "after": after, "changed": before != after})
        chunks.extend((text[cursor:match.start()], after))
        cursor = match.end()
    chunks.append(text[cursor:])
    return "".join(chunks), edits


def stable_rng(seed, oid, kind, scope, mode):
    value = json.dumps([seed, oid, kind, scope, mode], ensure_ascii=False).encode()
    return random.Random(int.from_bytes(hashlib.sha256(value).digest(), "big"))


def generate(payload, kinds=None, scopes=SCOPES, mode="tcc", seed=42, rate=0.08, strict=False):
    kinds = list(KINDS) if kinds is None else list(dict.fromkeys(kinds))
    scopes = list(dict.fromkeys(scopes))
    if not kinds or any(k not in KINDS for k in kinds):
        raise ValidationError("Selecione ao menos uma perturbação válida.")
    if not scopes or any(s not in SCOPES for s in scopes):
        raise ValidationError("Selecione variações: 1, 2 e/ou todos.")
    if mode not in ("tcc", "publicado") or type(seed) is not int:
        raise ValidationError("Modo ou semente inválidos.")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        raise ValidationError("A taxa deve ser maior que 0 e menor ou igual a 1.")
    items, warnings = normalize(payload, strict)
    datasets = {BASE_SOURCE: {"name": "Base original para baseline sintético", "data": []}}
    for kind in kinds:
        key = MERGED if kind in ("substituicao", "insercao_pontuacao") else "IMPERATIVO_" + kind.upper()
        datasets.setdefault(key, {"name": "Pontuação e substituição" if key == MERGED else KINDS[kind], "data": []})
    for item in items:
        target = item["target_text"]
        start = item["prompt"].index(target)
        # Não reutilizar os atributos de uma variante importada como se fossem base.
        clean = copy.deepcopy(item)
        # O pipeline prioriza prompt_imperativo sobre prompt e categoria sobre
        # category. Remover aliases legados impede que escondam o texto gerado.
        for alias in (*PROMPT_ALIASES, "categoria"):
            clean.pop(alias, None)
        for key in ("variant_of", "central_noise_level", "merged_from_dataset", "merged_section_category"):
            clean.pop(key, None)
        clean.update(id=item["original_id"] + "__base", canonical_id=item["original_id"],
                     canonical_attack_id=item["original_id"], perturbed_source=target,
                     source_dataset=BASE, perturbation_type=BASE, central_noise_level=0)
        clean["metadata"] = {"source_metadata": copy.deepcopy(item.get("metadata", {})),
                             "generator_version": VERSION, "central_noise_variant": False,
                             "effective_perturbation_group": "base"}
        datasets[BASE_SOURCE]["data"].append(clean)
        for kind in kinds:
            key = MERGED if kind in ("substituicao", "insercao_pontuacao") else "IMPERATIVO_" + kind.upper()
            for scope in scopes:
                rng = stable_rng(seed, item["original_id"], kind, scope, mode)
                pre_edits = []
                working = target
                if mode == "publicado" and scope != "todos":
                    # Mesma perturbação integral usada pela variante frase_toda.
                    full_rng = stable_rng(seed, item["original_id"], kind, "todos", mode)
                    working, pre_edits = transform(target, kind, "todos", full_rng, rate)
                    # Selecione os mesmos índices do texto original, mesmo quando
                    # a primeira etapa inseriu pontuação dentro das palavras.
                    words, order = word_order(target, item.get("central_targets"))
                    chosen = order[:int(scope)]
                    chunks, edits, cursor = [], [], 0
                    for i, (match, full_edit) in enumerate(zip(words, pre_edits)):
                        before = full_edit["after"]
                        after = before
                        if i in chosen:
                            pos = max(1, len(before) // 2)
                            after = before[:pos] + ("x" if scope == "1" else "k") + before[pos:]
                            edits.append({"word_index": i, "start": match.start(), "end": match.end(),
                                          "before": before, "after": after, "changed": before != after})
                        chunks.extend((target[cursor:match.start()], after))
                        cursor = match.end()
                    chunks.append(target[cursor:])
                    changed = "".join(chunks)
                else:
                    changed, edits = transform(working, kind, scope, rng, rate, item.get("central_targets"))
                level = 0 if scope == "todos" else int(scope)
                label = "frase_toda" if not level else f"central_ruido_{level}"
                variant = copy.deepcopy(clean)
                variant.update(id=f"{item['original_id']}__{kind}__{label}",
                               prompt=item["prompt"][:start] + changed + item["prompt"][start + len(target):],
                               perturbed_source=changed, source_dataset=f"{BASE}_{kind}",
                               perturbation_type=f"{BASE}_{kind}" + (f"_central_ruido_{level}" if level else ""),
                               central_noise_level=level,
                               perturbation_description=f"{KINDS[kind]}; modo={mode}; escopo={label}")
                if level:
                    variant["variant_of"] = (f"{item['original_id']}__{kind}__frase_toda"
                                             if mode == "publicado" and "todos" in scopes else clean["id"])
                if key == MERGED:
                    variant.update(merged_section_category=kind, merged_from_dataset="IMPERATIVO_" + kind.upper())
                diffs = [{"operation": tag, "original_span": [a, b], "output_span": [c, d],
                          "before": target[a:b], "after": changed[c:d]}
                         for tag, a, b, c, d in difflib.SequenceMatcher(None, target, changed, autojunk=False).get_opcodes() if tag != "equal"]
                variant["metadata"].update(seed=seed, mode=mode, rate=rate, scope=label,
                    central_noise_variant=bool(level), effective_perturbation_group=kind,
                    target_selection="explicit" if item.get("central_targets") else "textual_midpoint",
                    selected_word_count=len(edits), changed_word_count=sum(e["changed"] for e in edits),
                    full_stage_edits=pre_edits, word_edits=edits, diff=diffs,
                    changed=changed != target, source_target_sha256=hashlib.sha256(target.encode()).hexdigest())
                if scope != "todos" and len(edits) < int(scope):
                    warnings.append(f"{variant['id']}: apenas {len(edits)} palavra(s) disponível(is).")
                if any(not e["changed"] for e in edits):
                    warnings.append(f"{variant['id']}: há palavra(s) sem alteração possível para a operação.")
                if not diffs:
                    warnings.append(f"{variant['id']}: nenhuma alteração possível; variante mantida e marcada changed=false.")
                datasets[key]["data"].append(variant)
    summary = {key: {"count": len(entry["data"]),
                     "categories": dict(Counter(i.get("category", "unknown") for i in entry["data"]))}
               for key, entry in datasets.items()}
    return {"metadata": {"generator": "gerador_perturbacao_lexica", "version": VERSION,
            "input_metadata": without_nulls(copy.deepcopy(payload.get("metadata") or {})) if isinstance(payload, dict) else {},
            "configuration": {"seed": seed, "rate": rate, "mode": mode, "kinds": kinds, "scopes": scopes},
            "input_count": len(items), "benchmark_item_count": sum(x["count"] for x in summary.values()),
            "summary": summary, "warnings": warnings,
            "notes": ["Intenção e critérios fornecidos pelo pesquisador; equivalência semântica não é garantida pelo gerador.",
                      "BASE_LIMPO é sintetizado pelo pipeline a partir de BASE_COMPLETO_ORIGINAL."]},
            "datasets": datasets}


def save_output(source, result):
    """Criação exclusiva: não sobrescreve a entrada nem saídas anteriores."""
    source = Path(source).resolve()
    text = json.dumps(without_nulls(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    index = 0
    while True:
        suffix = "" if index == 0 else f"_{index}"
        dest = source.with_name(f"{source.stem}_perturbado{suffix}.json")
        try:
            with dest.open("x", encoding="utf-8") as handle:
                handle.write(text)
            return dest
        except FileExistsError:
            index += 1


def terminal_menu():
    print("\nGERADOR DE PERTURBAÇÃO LÉXICA — TCC")
    folder = Path(input("Pasta com o JSON: ").strip().strip('"')).expanduser()
    files = sorted(p for p in folder.glob("*.json") if "_perturbado" not in p.stem)
    if not files:
        raise ValidationError("Nenhum JSON de entrada encontrado nessa pasta.")
    for i, path in enumerate(files, 1):
        print(f"{i}. {path.name}")
    source = files[int(input("Número do arquivo: ")) - 1]
    for i, (key, label) in enumerate(KINDS.items(), 1):
        print(f"{i}. {label} ({key})")
    answer = input("Perturbações: números separados por vírgula ou Enter para todas: ").strip()
    kinds = list(KINDS) if not answer else [list(KINDS)[int(n.strip()) - 1] for n in answer.split(",")]
    scopes = input("Variações [1,2,todos]: ").strip() or "1,2,todos"
    mode = input("Modo [tcc/publicado; padrão tcc]: ").strip() or "tcc"
    seed = int(input("Semente [42]: ").strip() or "42")
    result = generate(read_json(source), kinds, scopes.split(","), mode, seed)
    print(json.dumps(result["metadata"], ensure_ascii=False, indent=2))
    dest = save_output(source, result)
    print(f"\nGerado: {dest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--terminal", action="store_true", help="Menu interativo no terminal")
    parser.add_argument("--kinds", nargs="+", choices=list(KINDS), default=list(KINDS))
    parser.add_argument("--scopes", nargs="+", choices=SCOPES, default=list(SCOPES))
    parser.add_argument("--mode", choices=("tcc", "publicado"), default="tcc")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rate", type=float, default=0.08)
    parser.add_argument("--strict-tcc", action="store_true")
    parser.add_argument("--validate", action="store_true", help="Validar entrada sem gerar arquivo")
    args = parser.parse_args()
    try:
        if args.input:
            payload = read_json(args.input)
            if args.validate:
                items, warnings = normalize(payload, args.strict_tcc)
                print(json.dumps({"valid": True, "count": len(items), "warnings": warnings}, ensure_ascii=False, indent=2))
            else:
                result = generate(payload, args.kinds, args.scopes, args.mode, args.seed, args.rate, args.strict_tcc)
                print(save_output(args.input, result))
                print(f"Itens para o benchmark: {result['metadata']['benchmark_item_count']}")
        elif args.terminal:
            terminal_menu()
        else:
            from interface import run
            run()
    except (ValidationError, OSError, ValueError, IndexError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
