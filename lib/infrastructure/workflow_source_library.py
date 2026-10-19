"""Project reusable complete readings into each run's own source chunks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lib.doc2corpus import chunk_text, SUPPORTED_EXTS
from lib.domain.source_conversation import source_conversation_issue
from lib.domain.workflow_quality import canonical, text_issue
from lib.infrastructure.json_stream import iter_source_json_records
from lib.infrastructure.source_library import SourceLibrary, SOURCE_LIBRARY_VERSION, MODEL_PARSE_CHARS


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class WorkflowSourceLibrary:
    def _library_native_text(self, path):
        # Preserve the executor's parser injection seam for offline verification
        # and existing integrations while sharing the durable extraction store.
        from lib.infrastructure.training_workflow import import_text
        return import_text(path, source_processing_version=self.recipe.get("source_processing_version", 1))

    def _source_library_status(self, source, receipt):
        with self._lock:
            summary = self.state.setdefault("source_library", {"version": SOURCE_LIBRARY_VERSION,
                "files": 0, "hits": 0, "misses": 0, "parts": 0, "text_chars": 0, "sources": []})
            rows = summary["sources"]
            previous = next((row for row in rows if row["file"] == source["file"]), None)
            row = {"file": source["file"], "name": source["name"], "sha256": source["sha256"],
                   **{key: receipt[key] for key in ("id", "source_id", "mode", "status", "cached",
                                                    "parts", "text_chars", "usable_parts", "requires_vision",
                                                    "empty_parts", "location", "parse_segment") if key in receipt}}
            if previous is not None:
                # Completion and failure receipts retain the last actual source
                # position instead of erasing the user's progress context.
                for key in ("location", "parse_segment"):
                    if key not in row and key in previous:
                        row[key] = previous[key]
            if previous is None:
                rows.append(row)
            else:
                rows[rows.index(previous)] = row
            summary.update(files=len(rows), hits=sum(item.get("cached") is True for item in rows),
                misses=sum(item.get("cached") is False for item in rows),
                parts=sum(item.get("parts", 0) for item in rows),
                text_chars=sum(item.get("text_chars", 0) for item in rows),
                usable_parts=sum(item.get("usable_parts", 0) for item in rows),
                requires_vision=sum(item.get("requires_vision", 0) for item in rows),
                empty_parts=sum(item.get("empty_parts", 0) for item in rows))
            ingest = self.state.get("stages", {}).get("ingest")
            if ingest is not None:
                ingest.update(parsed_parts=summary["parts"], input_chars=summary["text_chars"],
                    parsed_sources=sum(item.get("status") == "complete" for item in rows),
                    cached_sources=summary["hits"], current_source=source["name"],
                    current_location=row.get("location"), current_segment=row.get("parse_segment"))
            # iter_parts already throttles partial receipts. A just-finished
            # model event must not consume save's interval and hide this update
            # until another expensive request finishes.
            self.save(force=True)

    def _source_library_signature(self, mode):
        prompt_id = "workflow.document_vision" if mode == "vision" else "workflow.document_parse"
        role = "vision" if mode == "vision" else "generation"
        parser = self.recipe["document_parser"]
        binding = parser["binding"]
        pin = self.recipe.get("endpoint_pins", {}).get("ingest", {}).get(role, {})
        # Transport identity affects parsing; keys, quotas, prices and mutable
        # provider model inventories do not. Only hashes of public URLs are kept.
        endpoint = {key: pin[key] for key in ("base_url_sha256", "api_format") if key in pin}
        prompt = self.prompt_text(prompt_id, stage="ingest")
        system = self.recipe.get("node_prompt_system", "")
        from lib.infrastructure.workflow_agent_experience import experience_system
        experience = experience_system(self.recipe, prompt_id)
        available = binding.get("context_window_tokens", 131072) - binding.get("max_output_tokens", 32768)
        available -= len((system + "\n" + prompt + experience).encode("utf-8")) + 512
        if mode in {"model", "vision"} and available <= 0:
            raise ValueError("model_context_window_exceeded")
        parser_chunk_chars = min(MODEL_PARSE_CHARS, max(1, available // 4))
        model = {key: binding[key] for key in ("backend", "model", "context_window_tokens",
                                              "max_output_tokens") if key in binding}
        # Auto leaves the wire request unchanged, including for older recipes
        # that predate this setting. Only explicit overrides split the cache.
        reasoning = binding.get("reasoning") or {}
        if reasoning.get("mode", "auto") != "auto":
            model["reasoning"] = reasoning
        # Hash only the guidance actually sent by this parsing role. Keep the
        # historical two-member fingerprint when experience is absent/off;
        # catalog versions or unrelated roles must not trigger a paid reread.
        prompt_components = [self.recipe.get("node_prompt_system"), prompt]
        if experience:
            prompt_components.append(experience)
        return {"mode": mode, "parser_version": SOURCE_LIBRARY_VERSION,
                "source_processing_version": self.recipe.get("source_processing_version", 1),
                "model": model,
                "endpoint": endpoint,
                "prompt_sha256": _digest(prompt_components),
                **({"parser_chunk_chars": parser_chunk_chars} if mode == "model" else {})}

    def _iter_library_source_units(self, source):
        path = self.path / "inputs" / source["file"]
        if Path(source["file"]).name != source["file"] or _hash_file(path) != source["sha256"]:
            raise ValueError("source_snapshot_changed")
        library = SourceLibrary(self.path.parent.parent)
        saved_source = library.register_source(path, source["sha256"], source["name"])
        mode = self.recipe.get("document_parser", {}).get("mode", "native")
        if mode == "docling" and path.suffix.lower() != ".pdf":
            # Docling is an opt-in PDF layout backend. Preserve existing TeX,
            # DOCX and text handling instead of inventing format capabilities.
            mode = "native"
        if mode == "vision" and path.suffix not in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}:
            mode = "native"  # Plain text must never acquire an invented image modality.
        def status(receipt):
            self._source_library_status(source, receipt)
        def unit(index, **fields):
            return {"id": _digest([source["sha256"], source["file"], index]),
                    "source_id": source["sha256"], "source_name": source["name"],
                    "location": index, **fields}
        def documents(part):
            location, text = part["location"], part.get("text", "")
            if part.get("status") in {"quarantined", "skipped"}:
                evidence = {}
                if (part.get("document_reading") or {}).get("mode") == "docling":
                    evidence = {"text": text, "document_reading": part["document_reading"]}
                yield unit(location, status=part["status"], reason=part["reason"],
                           source_location={"file": source["name"], "record": location}, **evidence)
                return
            if part.get("requires_vision"):
                yield unit(location, status="quarantined", reason="document_text_requires_vision",
                           source_location={"file": source["name"], "record": location})
                return
            if not text.strip() and isinstance(location, str) and location.startswith("page:"):
                yield unit(location, status="skipped", reason="empty_document_page",
                           source_location={"file": source["name"], "record": location})
                return
            text = text.replace("\r\n", "\n").strip()
            issue = text_issue(text)
            if issue:
                yield unit(location, status="quarantined", reason=issue)
                return
            retrieved = self.recipe.get("version", 0) >= 14 and any(
                (hit.get("snapshot") or {}).get("file") == source["file"]
                for hit in (self.recipe.get("knowledge_retrieval") or {}).get("hits", []))
            if retrieved:
                chunks = [text]
            elif path.suffix in {".tex", ".latex"}:
                from lib.infrastructure.latex_document import chunk_latex
                chunks = chunk_latex(text, self.recipe["chunk_chars"])
            else:
                chunks = chunk_text(text, self.recipe["chunk_chars"],
                                    source_processing_version=self.recipe.get("source_processing_version", 1))
            for index, chunk in enumerate(chunks):
                key = (f"{location}:parse:{part['parse_segment']}:chunk:{index}"
                       if "parse_segment" in part else f"{location}:chunk:{index}")
                row = unit(key, source_location={"file": source["name"], "record": location, "chunk": index},
                           kind="document", text=chunk, status="ready")
                if len(chunk) > max(12000, self.recipe["chunk_chars"] * 4):
                    row.update(status="quarantined", reason="oversized_source_block")
                if "document_reading" in part:
                    row["document_reading"] = part["document_reading"]
                yield row
        try:
            if path.suffix in {".json", ".jsonl"}:
                signature = {"mode": "native", "parser_version": SOURCE_LIBRARY_VERSION,
                             "format": path.suffix, "json_stream_version": 1}
                def records():
                    if path.suffix == ".jsonl":
                        with library.source_path(saved_source["id"]).open(encoding="utf-8-sig") as handle:
                            for index, line in enumerate(handle, 1):
                                self.check_cancel()
                                if not line.strip():
                                    continue
                                try:
                                    value = json.loads(line)
                                except ValueError:
                                    value = None
                                yield {"location": index, "record": value}
                    else:
                        for index, value in enumerate(iter_source_json_records(
                                library.source_path(saved_source["id"])), 1):
                            yield {"location": index, "record": value}
                for part in library.iter_parts(saved_source, signature, records,
                        valid=lambda part: isinstance(part.get("record"), dict),
                        check_cancel=self.check_cancel, on_status=status):
                    index, row = part["location"], part["record"]
                    if not isinstance(row, dict):
                        yield unit(index, status="quarantined", reason="invalid_json_record")
                    elif isinstance(row.get("text"), str) and not (row.get("messages") or row.get("conversations")):
                        yield from documents({"location": index, "text": row["text"]})
                    else:
                        try:
                            if "request" in row and "response" in row:
                                from lib.adapters.rollout_import import record_status, record_to_sample
                                if record_status(row) != "ok" or row["request"].get("messageOffset", 0):
                                    raise ValueError("incomplete_rollout_context")
                                sample = record_to_sample(row)
                            else:
                                from lib.review_editor import normalize_sample
                                sample = normalize_sample(row)
                            issue = source_conversation_issue(sample, self.recipe["targets"])
                            if issue:
                                yield unit(index, status="quarantined", reason=issue)
                                continue
                            yield unit(index, kind="conversation", messages=sample["messages"],
                                tools=sample.get("tools", []),
                                tool_snapshots=sample.get("tool_snapshots") if "agent" in self.recipe["targets"] else None,
                                verification_status=sample.get("verification_status"), status="ready")
                        except (ValueError, TypeError, KeyError, AttributeError):
                            yield unit(index, status="quarantined", reason="invalid_or_incomplete_conversation")
                return
            if mode == "docling":
                from lib.infrastructure.docling_document import docling_parse_signature, docling_parts
                parser = self.recipe["document_parser"]
                signature = docling_parse_signature(parser)
                if signature != self.recipe.get("document_parser_signature"):
                    raise ValueError("document_docling_configuration_changed")
                def parsed_docling():
                    yield from docling_parts(library.source_path(saved_source["id"]), parser,
                        expected_signature=signature, check_cancel=self.check_cancel)
                for part in library.iter_parts(saved_source, signature, parsed_docling,
                        valid=lambda part: part.get("status") != "quarantined",
                        check_cancel=self.check_cancel, on_status=status):
                    yield from documents(part)
                return
            if mode == "native":
                for part in library.native_parts(saved_source,
                        source_processing_version=self.recipe.get("source_processing_version", 1),
                        pdf_pages=self.recipe.get("version", 0) >= 14 or self.recipe.get("source_processing_version", 1) >= 2,
                        check_cancel=self.check_cancel, on_status=status, text_reader=self._library_native_text):
                    yield from documents(part)
                return
            signature = self._source_library_signature(mode)
            parser_chunk_chars = signature.get("parser_chunk_chars", MODEL_PARSE_CHARS)
            stable_name = "document" + path.suffix
            parse_key = library.parse_id(saved_source, signature)
            def valid(result):
                return (isinstance(result, dict) and set(result) == {"text", "uncertain"}
                        and result.get("uncertain") is False and isinstance(result.get("text"), str)
                        and (mode != "model" or bool(result["text"].strip()))
                        and len(result["text"]) <= (120000 if mode == "vision" else 80000))
            def parsed():
                if mode == "model":
                    raw_parts = library.native_parts(saved_source,
                        source_processing_version=self.recipe.get("source_processing_version", 1),
                        check_cancel=self.check_cancel, text_reader=self._library_native_text)
                    for part in raw_parts:
                        self.check_cancel()
                        location, text = part["location"], part["text"].replace("\r\n", "\n").strip()
                        if part.get("requires_vision") or not text.strip():
                            yield part
                            continue
                        if path.suffix in {".tex", ".latex"}:
                            from lib.infrastructure.latex_document import chunk_latex
                            segments = chunk_latex(text, parser_chunk_chars)
                        else:
                            segments = chunk_text(text, parser_chunk_chars,
                                source_processing_version=self.recipe.get("source_processing_version", 1))
                        for index, segment in enumerate(segments):
                            self.check_cancel()
                            issue = text_issue(segment)
                            if issue or len(segment) > max(12000, parser_chunk_chars * 4):
                                yield {"location": location, "status": "quarantined",
                                       "reason": issue or "oversized_source_block"}
                                continue
                            key = [source["sha256"], location, index, "library_document_parse", _digest(segment)]
                            result, _ = library.cached_value(saved_source, signature, [location, index, _digest(segment)],
                                lambda: self.ask(key, "generation", "workflow.document_parse",
                                    {"source": stable_name, "location": location, "text": segment},
                                    allow_reasoning_fallback=False), valid=valid, refresh_invalid=True)
                            if not valid(result):
                                self.invalidate_checkpoint(["call", key])
                                yield {"location": location, "status": "quarantined",
                                    "reason": "document_parse_uncertain" if isinstance(result, dict)
                                    and result.get("uncertain") is True else "invalid_document_parse_schema"}
                                continue
                            yield {"location": location, "parse_segment": index, "text": result["text"],
                                "document_reading": {"mode": "model", "source_text": segment,
                                    "source_text_sha256": _digest(segment), "parsed_text": result["text"],
                                    "parsed_text_sha256": _digest(result["text"]), "parse_id": parse_key,
                                    "evidence_level": "model_restructured_source_text"}}
                else:
                    from lib.infrastructure.document_vision import visual_parts
                    for part in visual_parts(library.source_path(saved_source["id"]),
                            source_processing_version=self.recipe.get("source_processing_version", 1)):
                        self.check_cancel()
                        location = part["location"]
                        if "image" not in part:
                            yield part
                            continue
                        key = [source["sha256"], location, part["image_sha256"], "library_document_vision"]
                        result, _ = library.cached_value(saved_source, signature, [location, part["image_sha256"]],
                            lambda: self.ask(key, "vision", "workflow.document_vision",
                                {"source": stable_name, "location": location}, image=part["image"]), valid=valid)
                        if not valid(result):
                            self.invalidate_checkpoint(["call", key])
                            yield {"location": location, "status": "quarantined", "reason": "document_vision_uncertain"}
                            continue
                        yield {"location": location, "text": result["text"], "document_reading": {
                            "mode": "vision", "image_sha256": part["image_sha256"], "parse_id": parse_key,
                            "evidence_level": "model_transcribed_visual_source"}}
            def reusable_part(part):
                reading = part.get("document_reading") or {}
                # Native blank pages never ran the model and remain reusable.
                # Previously cached empty transcriptions of nonempty source
                # segments must be retried without discarding the good pages.
                return (mode != "model" or reading.get("mode") != "model"
                        or isinstance(part.get("text"), str) and bool(part["text"].strip()))
            for part in library.iter_parts(saved_source, signature, parsed,
                    valid=lambda part: part.get("status") != "quarantined" and not part.get("requires_vision"),
                    check_cancel=self.check_cancel, on_status=status, cached_valid=reusable_part):
                yield from documents(part)
        except UnicodeError:
            yield unit("document", status="quarantined", reason="invalid_encoding")
        except ValueError as error:
            source_errors = {"document_docx_parse_failed", "document_docx_expansion_limit",
                "document_pdf_parse_failed", "document_pdf_page_invalid", "document_pdf_encrypted",
                "document_vision_page_limit", "invalid_document_image", "document_image_too_large",
                "document_external_image_unsupported"}
            if (self.recipe.get("source_processing_version", 1) >= 2
                    and (str(error) in source_errors or str(error).startswith("latex_"))):
                yield unit("document", status="quarantined", reason=str(error),
                           source_location={"file": source["name"], "record": "document"})
                return
            raise
