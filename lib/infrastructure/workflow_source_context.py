"""Durable, bounded teacher contexts made exclusively from accepted clean text.

Rows carry small references, rather than repeating a document in every sample.
The referenced material order is fixed for a whole window, which lets providers
reuse the same prompt prefix across questions and generation variants.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import closing
from copy import deepcopy
import hashlib
import heapq
import json
from pathlib import Path
import re
import sqlite3
import threading

from lib.domain.workflow_quality import canonical
from lib.domain.workflow_scale import DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from lib.domain.workflow_targets import PREFERENCE_TARGETS
from lib.domain.source_line_ranges import (
    SOURCE_LINE_PROTOCOL_VERSION, number_source_material, teacher_material, validate_numbered_material,
)
from lib.infrastructure.workflow_rows import WorkflowRows

VERSION = 1
MAX_MATERIALS = 2048
MAX_CACHE_BYTES = 8 * 1024 * 1024
_PACK_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _bytes(value):
    return len(canonical(value).encode("utf-8"))


def _material(unit, *, numbered=False):
    receipt = unit.get("source_cleaning") or {}
    text = unit.get("text")
    if (unit.get("status") not in {"ready", "eligible"} or unit.get("kind") != "document"
            or receipt.get("status") != "accepted" or not isinstance(text, str) or not text.strip()
            or receipt.get("cleaned_text_sha256") != _digest(text)):
        raise ValueError("source_context_requires_accepted_clean_text")
    material = {"source_id": unit["source_id"], "source_name": unit.get("source_name", ""),
            "unit_id": unit["id"], "source_location": unit.get("source_location", {}), "text": text,
            "cleaning_status": "accepted", "cleaned_text_sha256": receipt["cleaned_text_sha256"]}
    return number_source_material(material) if numbered else material


def _terms(text, *, limit=24):
    # Chinese bigrams avoid treating a whole unspaced paragraph as one FTS term.
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u3400-\u9fff]+", text.lower())
    counts = Counter()
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            counts.update("c" + hashlib.sha256(word[i:i + 2].encode()).hexdigest()[:16]
                          for i in range(len(word) - 1))
        else:
            counts["w" + word] += 1
    return [term for term, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


def context_capacity(recipe):
    """Use the same UTF-8 upper bound as the request preflight, not chars/4.

    Output capacity, the pinned prompt and a task/contract reserve all remain
    available. The actual final request is still checked before transmission.
    """
    capacities = []
    director = recipe.get("qa_director", {})
    # A changed pack can change a director plan. Pin capacity policy in the
    # recipe so resumed tasks retain the evidence tied to their checkpoints.
    revised_capacity = recipe.get("source_context_capacity_version") == 2
    extra_rules = len((director.get("question_rules", "") + "\n" + director.get("answer_rules", "")
                       + "\n" + recipe.get("brief", "")).encode("utf-8"))
    styles = recipe.get("node_generation_prompts", {})
    style_bytes = max((len(text.encode("utf-8")) for prompts in styles.values()
                       for text in prompts.values()), default=0)
    targets = set(recipe.get("targets", ("sft",)))
    stages = ({"sft"} if targets.intersection(PREFERENCE_TARGETS | {"sft", "cot"}) else set())
    stages.update(targets.intersection({"multiturn", "cot"}))
    if targets.intersection(PREFERENCE_TARGETS):
        stages.add("preference")
    if (recipe.get("reasoning_trim") or {}).get("enabled"):
        stages.add("trim")
    if (recipe.get("review_repair") or {}).get("mode") == "auto":
        stages.add("review")
    if recipe.get("qa_director", {}).get("enabled"):
        stages.add("director")
    templates = recipe.get("node_prompt_templates", {})
    for stage in stages:
        prompts = templates.get(stage, {})
        if revised_capacity and stage == "director" and director.get("question_separation") is True:
            prompts = {key: value for key, value in prompts.items() if key != "workflow.question_user"}
        if not prompts:
            continue
        overhead = len(recipe.get("node_prompt_system", "").encode("utf-8"))
        overhead += max(len(text.encode("utf-8")) for text in prompts.values())
        bindings = recipe.get("node_models", {}).get(stage, {}) or {"generation": {}}
        for role, binding in bindings.items():
            # The separate questioner receives only a public brief/history.
            # Its smaller context must not truncate evidence for the director
            # and answerer; its actual request is preflighted independently.
            if (revised_capacity and stage == "director" and role == "question"
                    and director.get("question_separation") is True):
                continue
            context = binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
            output = binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
            capacities.append(context - output - overhead - extra_rules - style_bytes - 8192 - 1024)
    capacity = min(capacities, default=DEFAULT_CONTEXT_WINDOW_TOKENS - DEFAULT_MAX_OUTPUT_TOKENS - 12288)
    if capacity < 1024:
        raise ValueError("source_context_model_capacity_too_small")
    return capacity


def _pack_payload(materials):
    content = {"version": VERSION, "materials": materials}
    prefix_hash = _digest(content)
    return {"version": VERSION, "id": prefix_hash, "prefix_sha256": prefix_hash,
            "materials": materials}


def teacher_context_pack(pack):
    """Original bodies remain on disk; the teacher receives their numbered view."""
    return {**pack, "materials": [teacher_material(item) for item in pack["materials"]]}


def _save_pack(directory, materials):
    pack = _pack_payload(materials)
    path = directory / "packs" / (pack["id"] + ".json")
    if not path.exists():
        temporary = path.with_suffix(".pending")
        temporary.write_text(canonical(pack), encoding="utf-8")
        temporary.replace(path)
    numbered = all(item.get("source_line_protocol_version") == SOURCE_LINE_PROTOCOL_VERSION for item in materials)
    return {"version": VERSION, "id": pack["id"], "prefix_sha256": pack["prefix_sha256"],
            **({"source_line_protocol_version": SOURCE_LINE_PROTOCOL_VERSION} if numbered else {}),
            "material_count": len(materials), "input_bytes": _bytes(teacher_context_pack(pack)),
            "relative_path": "source-context/packs/" + pack["id"] + ".json"}


def load_context_pack(run_path, reference):
    """Validate a pinned reference and return a caller-owned copy of its text."""
    if not isinstance(reference, dict) or reference.get("version") != VERSION:
        raise ValueError("source_context_pack_invalid")
    if "materials" in reference:
        pack = reference
    else:
        identity = reference.get("id")
        if (not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity)
                or reference.get("relative_path") != "source-context/packs/" + identity + ".json"):
            raise ValueError("source_context_pack_invalid")
        path = Path(run_path) / reference["relative_path"]
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise ValueError("source_context_pack_invalid")
        try:
            stat = path.stat()
        except FileNotFoundError:
            raise ValueError("source_context_pack_missing") from None
        except OSError:
            raise ValueError("source_context_pack_invalid") from None
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
        with _CACHE_LOCK:
            cached = _PACK_CACHE.get(key)
            if cached is not None:
                _PACK_CACHE.move_to_end(key)
                pack = cached[0]
            else:
                try:
                    pack = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    raise ValueError("source_context_pack_missing") from None
                except (OSError, UnicodeError, json.JSONDecodeError):
                    raise ValueError("source_context_pack_invalid") from None
                if stat.st_size <= MAX_CACHE_BYTES:
                    _PACK_CACHE[key] = (pack, stat.st_size)
                    while sum(item[1] for item in _PACK_CACHE.values()) > MAX_CACHE_BYTES:
                        _PACK_CACHE.popitem(last=False)
    materials = pack.get("materials")
    if (not isinstance(materials, list) or not materials or len(materials) > MAX_MATERIALS
            or any(not isinstance(item, dict) or not isinstance(item.get("text"), str)
                   or not item["text"].strip() or item.get("cleaning_status") != "accepted"
                   or item.get("cleaned_text_sha256") != _digest(item["text"]) for item in materials)):
        raise ValueError("source_context_pack_invalid")
    if any("source_line_protocol_version" in item for item in materials):
        try:
            for item in materials:
                validate_numbered_material(item)
            if len({item["material_id"] for item in materials}) != len(materials):
                raise ValueError("invalid_source_line_material")
        except ValueError:
            raise ValueError("source_context_pack_invalid") from None
        if reference.get("source_line_protocol_version", SOURCE_LINE_PROTOCOL_VERSION) != SOURCE_LINE_PROTOCOL_VERSION:
            raise ValueError("source_context_pack_invalid")
    expected = _digest({"version": VERSION, "materials": materials})
    if (pack.get("id") != expected or pack.get("prefix_sha256") != expected
            or reference.get("id") != expected or reference.get("prefix_sha256") != expected):
        raise ValueError("source_context_pack_changed")
    return deepcopy(pack)


def pack_evidence_text(unit, run_path):
    reference = unit.get("source_context_pack") if isinstance(unit, dict) else None
    if reference is None:
        return unit.get("text", "") if isinstance(unit, dict) else ""
    return "\n\n".join(item["text"] for item in load_context_pack(run_path, reference)["materials"])


class ContextPackRows(WorkflowRows):
    """Replay rows with a disk lookup per unit and bounded per-iterator memory."""
    def __init__(self, rows, index_path):
        super().__init__(rows.path, len(rows), predicate=rows.predicate,
                         transform=rows.transform, limit=rows.limit)
        self.index_path = Path(index_path)

    def __iter__(self):
        with closing(sqlite3.connect(self.index_path)) as database:
            database.execute("PRAGMA cache_size=-2048")
            for unit in super().__iter__():
                if unit.get("kind") != "document":
                    yield unit
                    continue
                found = database.execute("SELECT reference FROM assignments WHERE unit_id=?", (unit["id"],)).fetchone()
                if found is None:
                    raise ValueError("source_context_pack_missing")
                reference = json.loads(found[0])
                yield {**unit, "source_context_pack": reference,
                       **({"source_line_material_id": unit["id"]}
                          if reference.get("source_line_protocol_version") == SOURCE_LINE_PROTOCOL_VERSION else {})}


def prepare_source_context_packs(workflow, rows):
    """Build or reuse stable clean document windows without loading the corpus."""
    if workflow.recipe.get("source_context_pack_version") != VERSION:
        return rows
    directory = workflow.path / "source-context"
    (directory / "packs").mkdir(parents=True, exist_ok=True)
    index_path = directory / "index.sqlite3"
    pending = directory / "index.pending.sqlite3"
    pending.unlink(missing_ok=True)
    capacity = context_capacity(workflow.recipe)
    numbered = workflow.recipe.get("source_line_protocol_version") == SOURCE_LINE_PROTOCOL_VERSION
    source_hash = hashlib.sha256()
    source_hash.update(canonical({"version": VERSION, "capacity": capacity,
                                 **({"source_line_protocol_version": SOURCE_LINE_PROTOCOL_VERSION} if numbered else {})}).encode())
    with closing(sqlite3.connect(pending)) as database:
        database.execute("PRAGMA cache_size=-2048")
        database.executescript("""
            CREATE TABLE units(seq INTEGER PRIMARY KEY, unit_id TEXT UNIQUE, source TEXT,
                material TEXT, size INTEGER);
            CREATE INDEX source_order ON units(source,seq);
            CREATE VIRTUAL TABLE search USING fts5(terms);
            CREATE TABLE assignments(unit_id TEXT PRIMARY KEY, reference TEXT);
        """)
        for sequence, unit in enumerate(rows):
            workflow.check_cancel()
            if unit.get("kind") != "document":
                continue
            material = _material(unit, numbered=numbered)
            encoded = canonical(material)
            source_hash.update(encoded.encode("utf-8"))
            source_hash.update(b"\n")
            # Capacity follows the actual teacher projection: numbering adds
            # bytes, while the audited raw body is not transmitted twice.
            size = _bytes(teacher_material(material)) + 1
            # Never clip away the useful body of a source unit.
            if size + 512 > capacity:
                raise ValueError("source_context_model_capacity_too_small")
            database.execute("INSERT INTO units VALUES(?,?,?,?,?)",
                             (sequence, unit["id"], unit["source_id"], encoded, size))
            database.execute("INSERT INTO search(rowid,terms) VALUES(?,?)",
                             (sequence, " ".join(_terms(material["text"], limit=4096))))
        # Numbered requests carry only an address in the dynamic task; the full
        # body is already in source memory. Legacy recipes retain their exact
        # reserve; unnumbered requests still need a second complete body.
        largest_anchor = (0 if numbered and workflow.recipe.get("source_context_capacity_version") == 2 else
            database.execute("SELECT COALESCE(MAX(size),0) FROM units").fetchone()[0])
        capacity -= largest_anchor
        if largest_anchor and capacity < largest_anchor + 512:
            raise ValueError("source_context_model_capacity_too_small")
        source_hash.update(canonical({"final_capacity": capacity}).encode())
        signature = source_hash.hexdigest()
        manifest_path = directory / "manifest.json"
        if index_path.exists() and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_sha256") == signature:
                database.close()
                pending.unlink(missing_ok=True)
                return ContextPackRows(rows, index_path)

        pack_count = 0
        material_bytes = capacity - 512  # pack envelope and separators

        def persist_window(base):
            nonlocal pack_count
            if not base:
                return
            selected = {row[0]: row for row in base}
            used = sum(row[4] for row in base)
            source = base[0][2]
            # Complete nearby paragraphs precede retrieval from other documents.
            # Indexed scans avoid sorting the whole document for every window.
            # Both candidate streams and the material buffer remain bounded.
            before = database.execute("SELECT * FROM units WHERE source=? AND seq<? ORDER BY seq DESC LIMIT ?",
                                      (source, base[0][0], MAX_MATERIALS))
            after = database.execute("SELECT * FROM units WHERE source=? AND seq>? ORDER BY seq LIMIT ?",
                                     (source, base[-1][0], MAX_MATERIALS))
            candidates = heapq.merge(before, after, key=lambda row:
                (base[0][0] - row[0] if row[0] < base[0][0] else row[0] - base[-1][0], row[0]))
            for row in candidates:
                if len(selected) >= MAX_MATERIALS:
                    break
                if used + row[4] <= material_bytes:
                    selected[row[0]] = row
                    used += row[4]
            if used < min(4096, material_bytes * 0.6):
                query = " OR ".join(_terms("\n".join(json.loads(row[3])["text"] for row in base), limit=16))
                if query:
                    related = database.execute("SELECT units.* FROM search JOIN units ON units.seq=search.rowid "
                        "WHERE search MATCH ? AND units.source!=? ORDER BY bm25(search),units.seq LIMIT 24",
                        (query, source))
                    for row in related:
                        if used + row[4] <= material_bytes and len(selected) < MAX_MATERIALS:
                            selected[row[0]] = row
                            used += row[4]
            # Fixed corpus order, never anchor-first: all base-window units share
            # byte-identical prefixes even when their question IDs differ.
            materials = [json.loads(row[3]) for row in sorted(selected.values(), key=lambda row: (row[2], row[0]))]
            reference = _save_pack(directory, materials)
            if reference["input_bytes"] > capacity:
                raise ValueError("source_context_model_capacity_too_small")
            for row in base:
                database.execute("INSERT INTO assignments VALUES(?,?)", (row[1], canonical(reference)))
            pack_count += 1

        for source, total, count in database.execute("SELECT source,SUM(size),COUNT(*) FROM units GROUP BY source ORDER BY source"):
            block, block_size = [], 0
            whole_document = total <= material_bytes and count <= MAX_MATERIALS
            base_capacity = material_bytes if whole_document else max(1, int(material_bytes * 0.7))
            for row in database.execute("SELECT * FROM units WHERE source=? ORDER BY seq", (source,)):
                workflow.check_cancel()
                if block and (block_size + row[4] > base_capacity or len(block) >= MAX_MATERIALS):
                    persist_window(block)
                    block, block_size = [], 0
                block.append(row)
                block_size += row[4]
            persist_window(block)
        database.commit()
        manifest = {"version": VERSION, "source_sha256": signature, "capacity_bytes": capacity,
                    "pack_count": pack_count, "document_units": database.execute("SELECT COUNT(*) FROM units").fetchone()[0]}
    pending.replace(index_path)
    temporary_manifest = directory / "manifest.pending.json"
    temporary_manifest.write_text(canonical(manifest), encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return ContextPackRows(rows, index_path)
