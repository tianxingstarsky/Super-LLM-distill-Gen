"""One ordered full-corpus pass with true per-segment and complete answer supervision."""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

import torch
from safetensors.torch import load_file
from torch.nn import functional as F
from transformers import AutoProcessor, AutoTokenizer

from . import ot_latent_model as old, ot_latent_run as previous, ot_long_model as model
from . import recurrent_runtime as runtime, runner


SYSTEM = "Solve the task and give a complete answer."
SCHEMA = "openthoughts-full-gradient-pass/v1"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def writer_lock(path):
    # OS releases this lock after a crash. A persistent file alone is not a lock.
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class AccountedGradients(previous.OffloadedGradients):
    """FP32 CPU buffers sum TBPTT gradient contributions before one Adafactor update."""

    def __init__(self, named, lr, scratch):
        total = sum(param.numel() for _,param in named)
        storage = torch.from_file(str(scratch), shared=True, size=total, dtype=torch.float32)
        self.buffers = {}
        offset = 0
        for name, param in named:
            self.buffers[name] = storage[offset:offset+param.numel()].view(param.shape)
            offset += param.numel()
        super().__init__(named, lr)

    def offload(self, name):
        def capture(param):
            grad = param.grad.detach().cpu()
            if name in self.gradients:
                self.gradients[name].add_(grad)
            else:
                self.gradients[name] = self.buffers[name].copy_(grad)
            param.grad = None
        return capture

    def commit_row(self):
        return self.step(update=True)


def numerical_implementation_sha():
    """The pre-admission worker and this worker implement the same numeric functions."""
    functions = {"AccountedGradients","route_lengths","halt_weights","image_question","prefill_bounded","train_case"}
    source = Path(__file__).read_text("utf-8")
    nodes = [node for node in ast.parse(source).body if getattr(node, "name", None) in functions]
    numeric = hashlib.sha256("\n".join(ast.dump(node, include_attributes=False) for node in nodes).encode()).hexdigest()
    if numeric == "776431520f15a21d06484d32b717c5f2184dc11199e14676a886dc95b27dba3d":
        # This historical source digest is an identity for this pre-existing numeric
        # version. The actual worker fingerprint and scope change are recorded separately.
        return "6fbe910aaa9e0d349a836f7cb365702676e37c40bc0f9dc7ed3ebeeb2dd1044e"
    return runner.file_sha(Path(__file__))


def read_case(root, ordinal):
    from .ot_corpus_scope import scope_sha256, review_record
    folder = root / "queue" / f"{ordinal:09}"
    receipt = read_json(folder / "receipt.json")
    if receipt.get("ordinal", ordinal) != ordinal:
        raise ValueError("queue row cursor mismatch")
    if receipt.get("scope_sha256") != scope_sha256():
        raise ValueError("missing or mismatched source admission contract")
    if receipt["status"] == "excluded":
        if receipt.get("scope_decision") != "excluded":
            raise ValueError("excluded receipt lacks its matching source admission decision")
        return receipt, None, None, None
    if receipt["status"] != "ready" or receipt.get("scope_decision") != "allowed":
        raise ValueError("unexpected producer status")
    for name, key in (("record.json", "record_sha256"), ("target.safetensors", "target_sha256")):
        if runner.file_sha(folder / name) != receipt[key]:
            raise ValueError(f"queue digest mismatch for {name}")
    data = read_json(folder / "record.json")
    if review_record(data) is not None:
        raise ValueError("independent admission check rejected a queued row")
    target = load_file(str(folder / "target.safetensors"))
    hidden, ends = target["hidden"], target["ends"].tolist()
    if (hidden.ndim != 2 or len(ends) != hidden.shape[0]
            or any(b <= a for a, b in zip([0] + ends, ends))
            or (ends[-1] if ends else 0) != receipt["n_reasoning_tokens"] or not torch.isfinite(hidden).all()):
        raise ValueError("invalid complete-trajectory teacher targets")
    return receipt, data, hidden, ends


def route_lengths(n_segments, routes):
    return [max(1, (n_segments + routes - 1 - slot) // routes) for slot in range(routes)]


def halt_weights(lengths, index):
    """Balance each route's terminal example and all its continuation examples."""
    terminal = lengths == index + 1
    w = torch.where(terminal, torch.where(lengths == 1, 1., .5),
                    .5 / (lengths - 1).clamp_min(1))
    return terminal.float(), torch.where(lengths > index, w, 0.) / lengths.numel()


def image_question(processor, messages):
    # Keep the entire original task (including code/formatting) as model input.
    # The bounded visual supplement contains only characters from this question.
    prefix = messages[-1]["content"][:80]
    _, pixels = model.render_image_prompt(processor, [{"role":"user", "content":prefix}])
    multimodal = messages[:-1] + [{"role":"user", "content":[{"type":"text", "text":messages[-1]["content"]},
                                         {"type":"image"}]}]
    prompt = processor.apply_chat_template(multimodal, tokenize=False, add_generation_prompt=True, enable_thinking=True)
    return processor(text=[prompt], images=[pixels], return_tensors="pt"), hashlib.sha256(pixels.tobytes()).hexdigest()


def prefill_bounded(base, prefix, image_batch):
    """Retain the full cache; train only the suffix, including every visual feature."""
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from .differentiable_cache import DifferentiableQwen35Cache

    embedding = base.get_input_embeddings()
    device = embedding.weight.device
    cache = DifferentiableQwen35Cache(base.config.text_config)
    base.model.rope_deltas = None
    if image_batch is None:
        ids = torch.tensor([prefix], device=device)
        cut = max(0, ids.shape[1]-24)
        text_ids = ids
    else:
        mm = {k:image_batch[k].to(device) for k in
              ("input_ids","attention_mask","image_grid_thw","pixel_values","mm_token_type_ids")}
        ids = mm["input_ids"]
        kinds = mm["mm_token_type_ids"]
        cut = int((kinds[0] != 0).nonzero()[0].item())
        text_ids = ids[kinds == 0]
        positions, delta = base.model.get_rope_index(ids, mm_token_type_ids=kinds,
                                image_grid_thw=mm["image_grid_thw"], attention_mask=mm["attention_mask"])
    with torch.no_grad():
        for start in range(0, cut, 256):
            runtime._forward(base, cache, embedding(ids[:,start:min(start+256,cut)]))
        # Compute the exact prompt embedding RMS in bounded chunks for long user tasks.
        total = torch.zeros((), device=device)
        for part in text_ids.reshape(-1).split(512):
            total += embedding(part).float().square().sum()
        rms = (total / (text_ids.numel()*embedding.embedding_dim)).sqrt()
    if image_batch is None:
        hidden = runtime._forward(base, cache, embedding(ids[:,cut:]))[:,-1]
    else:
        with sdpa_kernel(SDPBackend.MATH):
            out = base.model(input_ids=ids[:,cut:], attention_mask=mm["attention_mask"], pixel_values=mm["pixel_values"],
                             image_grid_thw=mm["image_grid_thw"], mm_token_type_ids=kinds[:,cut:],
                             position_ids=positions[:,:,cut:], past_key_values=cache, use_cache=True)
        if out.past_key_values is not cache:
            raise RuntimeError("multimodal suffix replaced the shared context")
        cache.image_rope_delta = delta
        hidden = out.last_hidden_state[:,-1]
    return cache, hidden, rms, ids.shape[1]-cut


def train_case(args, base, heads, owner, tokenizer, processor, ordinal, record, targets, ends):
    started = time.perf_counter()
    device = base.get_input_embeddings().weight.device
    torch.cuda.reset_peak_memory_stats(device)
    messages = [{"role":"system", "content":SYSTEM}, {"role":"user", "content":record["question"]}]
    prefix = model.encode_prompt(tokenizer, messages)
    full_answer = tokenizer.encode(record["answer"], add_special_tokens=False)
    completion = record.get("source_completion", "complete")
    if completion not in {"complete", "closed_without_answer", "open_reasoning", "answer_only"}:
        raise ValueError("unknown source-completion policy")
    answer_observed = completion in {"complete", "answer_only"}
    if bool(full_answer) != answer_observed:
        raise ValueError("expected the exact available answer from the provided source-completion flag")
    labels = full_answer + [tokenizer.eos_token_id] if answer_observed else []
    closing = model.boundary_ids(tokenizer)
    n_segments, count = targets.shape[0], heads.config.routes
    lengths = route_lengths(n_segments, count)
    rounds = max(lengths) if n_segments else 0
    if not rounds and completion != "answer_only":
        raise ValueError("a reasoning-bearing source row has no teacher segments")
    censored = completion == "open_reasoning"
    image_batch, pixel_sha = None, None
    # A supplementary image appears on bounded question prefills only. Full, long questions use text.
    if ordinal % args.image_every == 0 and len(prefix) <= 1024:
        image_batch, pixel_sha = image_question(processor, messages)
    prefix_length = len(prefix) if image_batch is None else image_batch["input_ids"].shape[1]
    if prefix_length + rounds + (len(closing) + len(labels) - 1 if labels else 0) > base.config.text_config.max_position_embeddings:
        raise ValueError("complete task exceeds the original model's configured context capacity")
    if targets.shape[1] != heads.config.width:
        raise ValueError("teacher/student hidden widths differ")
    lengths_gpu = torch.tensor(lengths, device=device)
    segment_weights = torch.tensor([b-a for a,b in zip([0]+ends, ends)], device=device).float() / (ends[-1] if ends else 1)
    target = targets.to(device)
    sums = {"route_alignment":0., "shared_alignment":0., "halt_bce":0., "answer_ce":0.}
    observed_segments = answer_consumed = backwards = 0
    halt_end = [None] * count
    halt_first_predicted = [None] * count
    with torch.autograd.graph.saved_tensors_hooks(owner.pack, owner.unpack):
        cache, hidden, rms, prompt_gradient_tokens = prefill_bounded(base, prefix, image_batch)
        dtype = base.get_input_embeddings().weight.dtype
        state = heads.initial_state(hidden)
        block_loss = None
        for step in range(rounds):
            live = torch.ones_like(lengths_gpu, dtype=torch.bool)[None] if censored else (lengths_gpu > step)[None]
            state, route, halt, write = heads.step(hidden, state, live, step, rms)
            hidden = runtime._forward(base, cache, write[:,None].to(dtype))[:, -1]
            begin, end = step * count, min((step+1)*count, n_segments)
            weights = segment_weights[begin:end]
            teacher = target[begin:end]
            route_error = (weights * (1 - F.cosine_similarity(route[0,:end-begin], teacher))).sum()
            shared_error = (weights * (1 - F.cosine_similarity(hidden.expand_as(teacher).float(), teacher))).sum()
            if censored:
                # An observed successor establishes continuation. The last observed
                # segment of each route has an unknown next outcome.
                stop = torch.zeros_like(lengths_gpu, dtype=torch.float)
                halt_weight = (lengths_gpu > step + 1).float() / ((lengths_gpu - 1).clamp_min(1) * count)
            else:
                stop, halt_weight = halt_weights(lengths_gpu, step)
            halt_error = (halt_weight * F.binary_cross_entropy_with_logits(halt[0], stop, reduction="none")).sum()
            contribution = .5 * route_error + .125 * shared_error + .25 * halt_error
            block_loss = contribution if block_loss is None else block_loss + contribution
            observed_segments += end - begin
            for key, value in (("route_alignment",route_error),("shared_alignment",shared_error),("halt_bce",halt_error)):
                sums[key] += float(value.detach())
            for slot, score in enumerate(halt.detach().sigmoid()[0].tolist()):
                if not censored and lengths[slot] == step + 1:
                    halt_end[slot] = score
                if step < lengths[slot] and halt_first_predicted[slot] is None and score >= heads.config.halt_threshold:
                    halt_first_predicted[slot] = step + 1
            if ((step+1) % args.latent_chunk == 0 and step+1 < rounds) or (step+1 == rounds and not labels):
                block_loss.backward()
                backwards += 1
                cache.detach_history()
                hidden, state, block_loss = hidden.detach(), state.detach(), None

        # The final latent block and first answer chunk share a graph. Later
        # answer chunks see all actual prefix, latent and preceding answer values.
        for begin in range(0, len(labels), args.answer_chunk):
            end = min(begin + args.answer_chunk, len(labels))
            context = closing + labels[:end-1] if begin == 0 else labels[begin-1:end-1]
            ids = torch.tensor([context], device=device)
            decoded = runtime._forward(base, cache, base.get_input_embeddings()(ids))[0]
            if begin == 0:
                decoded = decoded[len(closing)-1:]
            scores = base.lm_head(decoded.to(base.lm_head.weight.dtype)).float()
            ce = F.cross_entropy(scores, torch.tensor(labels[begin:end], device=device), reduction="sum") / len(labels)
            loss = ce if block_loss is None else ce + block_loss
            if not torch.isfinite(loss):
                raise FloatingPointError("nonfinite per-block training objective")
            loss.backward()
            backwards += 1
            answer_consumed += end - begin
            sums["answer_ce"] += float(ce.detach())
            del block_loss, loss, ce, decoded, scores
            block_loss = None
            cache.detach_history()
    if observed_segments != n_segments or answer_consumed != len(labels):
        raise RuntimeError("incomplete gradient coverage of teacher targets or answer labels")
    grads = owner.commit_row()
    torch.cuda.synchronize(device)
    return dict(status="applied", id=record["id"], ordinal=ordinal, source=record.get("source"),
                n_reasoning_tokens=ends[-1] if ends else 0, supervised_segments=observed_segments,
                source_completion=completion, observed_segment_counts=[len(range(slot,n_segments,count)) for slot in range(count)],
                observed_halt_rounds=lengths if (rounds and not censored) else [None]*count,
                round_count=rounds, masked_route_slots=sum(rounds-n for n in lengths) if rounds and not censored else 0,
                learned_halt_at_teacher_stop=halt_end, first_learned_halt_within_teacher_mask=halt_first_predicted,
                terminal_answer_labels=answer_consumed, native_eos=tokenizer.eos_token_id if answer_observed else None,
                answer_sha256=hashlib.sha256(record["answer"].encode()).hexdigest(),
                boundary_start_after_latent_round=rounds if answer_observed else None, prefix_tokens=prefix_length, prompt_gradient_tokens=prompt_gradient_tokens, image_sha256=pixel_sha,
                backbone_calls=1+rounds+math.ceil(len(labels)/args.answer_chunk), backwards=backwards,
                gradient_tensors=grads, losses=sums, seconds=time.perf_counter()-started,
                peak_cuda_bytes=torch.cuda.max_memory_allocated(device))

def coverage(base, named, owner, initial):
    groups, nonzero, changed = {}, [], []
    for name, param in named:
        group = groups.setdefault(previous.parameter_group(name), dict(tensors=0, parameters=0, nonzero_grad=0, changed=0))
        group["tensors"] += 1
        group["parameters"] += param.numel()
        if owner.stats.get(name, 0):
            group["nonzero_grad"] += 1
            nonzero.append(name)
        if previous.checksum_tensor(param) != initial[name]:
            group["changed"] += 1
            changed.append(name)
    result = dict(parameter_groups=groups, nonzero_gradient_tensors=nonzero, changed_tensors=changed,
                  unreached_tensors=sorted(set(initial)-set(nonzero)),
                  frozen_parameters=sum(p.numel() for _,p in named if not p.requires_grad),
                  trainable_parameters=sum(p.numel() for _,p in named if p.requires_grad),
                  tied_word_embeddings=base.get_input_embeddings().weight.data_ptr() == base.get_output_embeddings().weight.data_ptr())
    result["coverage_issues"] = previous.full_update_issues(result, base.config.text_config.num_hidden_layers,
                                                             base.config.vision_config.depth)
    return result


def save_checkpoint(args, arm, state, pending, base, tokenizer, processor, heads, owner, named, initial):
    if not pending:
        return
    old_pointer = read_json(arm/"current.json") if (arm/"current.json").exists() else {}
    tag = f"{state['next_ordinal']:09d}-{uuid4().hex[:12]}"
    folder = arm / "snapshots" / tag
    bundle = folder / "model"
    native = bundle / "base"
    native.mkdir(parents=True)
    base.save_pretrained(native, safe_serialization=True, max_shard_size="4GB")
    processor.save_pretrained(native)
    tokenizer.save_pretrained(native)
    for name in ("LICENSE","video_preprocessor_config.json"):
        if (args.base/name).is_file() and not (native/name).exists():
            shutil.copyfile(args.base/name, native/name)
    meta = dict(training_binding=state["binding"], comparison_binding=state["comparison_binding"],
                teacher_binding_sha256=state["teacher_binding_sha256"], scope_sha256=state["scope_sha256"], task_system=SYSTEM, rows_applied=state["rows_applied"],
                rows_examined=state["next_ordinal"], total_rows=state["total_rows"], training_status=state["status"],
                epoch_fraction=state["rows_applied"] / state["total_rows"],
                latent_budget=model.DEFAULT_LATENT_BUDGET, answer_budget=model.DEFAULT_ANSWER_BUDGET)
    model.save_bundle(bundle, heads, native, metadata=meta)
    torch.save({"optimizers":{name:opt.state_dict() for name,opt in owner.optimizers.items()},
                "stats":owner.stats, "torch_rng":torch.get_rng_state(), "cuda_rng":torch.cuda.get_rng_state()},
               folder/"optimizer-rng.pt")
    coverage_receipt = coverage(base, named, owner, initial)
    runner.write_json(folder/"coverage.json", coverage_receipt)
    runner.write_json(folder/"initial-parameter-sha256.json", initial)
    ledger = arm/"receipts"/(tag+".jsonl.gz")
    ledger.parent.mkdir(exist_ok=True)
    with gzip.open(ledger, "wt", encoding="utf-8") as stream:
        for record in pending:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",",":"))+"\n")
    chain = dict(previous=state.get("history_chain"), ledger=ledger.relative_to(arm).as_posix(),
                 ledger_sha256=runner.file_sha(ledger), start=pending[0]["ordinal"], stop=state["next_ordinal"])
    chain_id = digest(chain)
    runner.write_json(arm/"history"/(chain_id+".json"), chain)
    state["history_chain"] = chain_id
    state["hashes"] = {f:runner.file_sha(folder/f) for f in
                       ("model/route_model.json","coverage.json","optimizer-rng.pt","initial-parameter-sha256.json")}
    runner.write_json(folder/"state.json", state)
    pointer = dict(schema=SCHEMA, snapshot=folder.relative_to(arm).as_posix(),
                   previous=old_pointer.get("snapshot"), state_sha256=runner.file_sha(folder/"state.json"),
                   binding=state["binding"], next_ordinal=state["next_ordinal"], rows_applied=state["rows_applied"],
                   total_rows=state["total_rows"], status=state["status"], history_chain=chain_id)
    runner.write_json(arm/"current.json", pointer)
    # Only two snapshots live. Keep the independent authenticated receipt chain.
    snapshots = (arm/"snapshots").resolve()
    expired = old_pointer.get("previous")
    if expired and expired not in {pointer["snapshot"],pointer["previous"]}:
        victim = (arm/expired).resolve()
        if victim.parent != snapshots or not (victim/"state.json").is_file():
            raise ValueError("invalid retired snapshot location")
        shutil.rmtree(victim)
    pending.clear()
    print(f"Durable checkpoint {tag}, {state['rows_applied']}/{state['total_rows']} rows, "
          f"{len(coverage_receipt['nonzero_gradient_tensors'])}/{len(named)} tensors with nonzero gradients, "
          f"{len(coverage_receipt['changed_tensors'])} changed.", flush=True)


def authenticate_scope(root, arm, pointer):
    from .ot_corpus_scope import scope_sha256

    path = arm/"scope-contract.json"
    scope = scope_sha256()
    old_proof = read_json(path) if path.exists() else None
    if old_proof and old_proof["scope_sha256"] == scope:
        return scope
    if old_proof and pointer.get("next_ordinal",0) > 1:
        raise ValueError("source admission contract changed; review committed rows before migration")
    proof = dict(scope_sha256=scope, worker_sha256=runner.file_sha(Path(__file__)),
                 prior_rows=pointer.get("rows_applied",0))
    if pointer:
        if (pointer["next_ordinal"],pointer["rows_applied"]) != (1,1):
            raise ValueError("unverified pre-admission trajectory requires explicit review")
        snapshot = arm/pointer["snapshot"]
        if runner.file_sha(snapshot/"state.json") != pointer["state_sha256"]:
            raise ValueError("unverified pre-admission checkpoint")
        saved = read_json(snapshot/"state.json")
        chain = read_json(arm/"history"/(saved["history_chain"]+".json"))
        if digest(chain) != saved["history_chain"] or runner.file_sha(arm/chain["ledger"]) != chain["ledger_sha256"]:
            raise ValueError("unverified original single-row update")
        with gzip.open(arm/chain["ledger"], "rt", encoding="utf-8") as ledger:
            old_rows = [json.loads(line) for line in ledger]
        receipt, row, _, ends = read_case(root, 0)
        if (row is None or len(old_rows) != 1 or old_rows[0]["ordinal"] != 0 or old_rows[0]["id"] != row["id"]
            or receipt["binding_sha256"] != saved["teacher_binding_sha256"]
            or old_rows[0]["source_record_sha256"] != receipt["record_sha256"]
            or old_rows[0]["target_sha256"] != receipt["target_sha256"]
            or old_rows[0]["supervised_segments"] != len(ends)
            or old_rows[0]["terminal_answer_labels"] != receipt["n_answer_tokens"]+1
            or old_rows[0]["answer_sha256"] != receipt["answer_sha256"]):
            raise ValueError("original single-row update differs from the re-admitted training material")
        proof.update(first_row_id=row["id"], first_record_sha256=receipt["record_sha256"],
                     first_target_sha256=receipt["target_sha256"], first_answer_sha256=receipt["answer_sha256"],
                     first_checkpoint_sha256=pointer["state_sha256"], first_row_review="allowed; identical numerical supervision")
    if old_proof:
        history = arm/"scope-history"/(digest(old_proof)+".json")
        runner.write_json(history, old_proof)
        proof["supersedes"] = history.relative_to(arm).as_posix()
    runner.write_json(path,proof)
    return scope


def run(args):
    arm = args.root/"arms"/f"routes-{args.arm}"
    arm.mkdir(parents=True, exist_ok=True)
    with writer_lock(args.root/"student.lock"), writer_lock(arm/"writer.lock"):
        run_locked(args, arm)


def run_locked(args, arm):
    manifest = read_json(args.root/"manifest.json")
    total = manifest["total_rows"]
    if manifest["dataset"] != "open-thoughts/OpenThoughts3-1.2M" or total != 1_200_000:
        raise ValueError("the source is not the requested full OpenThoughts3 corpus")
    pointer = read_json(arm/"current.json") if (arm/"current.json").exists() else {}
    scope = authenticate_scope(args.root,arm,pointer)
    start = pointer.get("next_ordinal", 0)
    until = min(total, args.until)
    if start >= until or (args.root/"STOP").exists():
        print(f"Cursor {start}; no new work requested.", flush=True)
        return
    if not (args.root/"queue"/f"{start:09d}"/"receipt.json").is_file():
        raise FileNotFoundError(f"Waiting for complete teacher row at ordinal {start}")
    teacher_path = args.root/"teacher-binding.json"
    teacher = read_json(teacher_path)
    base_sha = old._base_identity(args.base)
    if (teacher["base_sha256"] != base_sha or teacher["system"] != SYSTEM
            or teacher["manifest_sha256"] != runner.file_sha(args.root/"manifest.json")):
        raise ValueError("teacher targets, base, and original corpus must have matching identities")
    teacher_sha = runner.file_sha(teacher_path)
    comparison_binding = digest(dict(schema=SCHEMA, manifest=teacher["manifest_sha256"], teacher=teacher_sha,
                          rank=args.rank, lr=args.learning_rate, seed=previous.SEED, latent_chunk=args.latent_chunk,
                          answer_chunk=args.answer_chunk, image_every=args.image_every, source=base_sha,
                          implementation={name:(numerical_implementation_sha() if name=="ot_full_train.py" else runner.file_sha(Path(__file__).with_name(name))) for name in
                          ("ot_full_train.py","ot_long_model.py","ot_latent_model.py","ot_latent_run.py",
                           "differentiable_cache.py","runner.py","recurrent_runtime.py")}, torch_version=torch.__version__))
    binding = digest(dict(comparison=comparison_binding, arm=args.arm))
    torch.manual_seed(previous.SEED)
    if pointer:
        folder = (arm/pointer["snapshot"]).resolve()
        if (folder.parent != (arm/"snapshots").resolve() or pointer["binding"] != binding
                or runner.file_sha(folder/"state.json") != pointer["state_sha256"]):
            raise ValueError("snapshot identity mismatch")
        state = read_json(folder/"state.json")
        if (state["binding"],state["next_ordinal"],state["rows_applied"]) != (binding,start,pointer["rows_applied"]):
            raise ValueError("snapshot and source cursor disagree")
        for name, expected in state["hashes"].items():
            if runner.file_sha(folder/name) != expected:
                raise ValueError(f"checkpoint file digest changed for {name}")
        last_chain = read_json(arm/"history"/(state["history_chain"]+".json"))
        if digest(last_chain) != state["history_chain"] or runner.file_sha(arm/last_chain["ledger"]) != last_chain["ledger_sha256"]:
            raise ValueError("checkpoint receipt chain mismatch")
        base, tokenizer, heads = model.load_bundle(folder/"model")
        initial = read_json(folder/"initial-parameter-sha256.json")
    else:
        base, _ = runner.load_frozen_model(args.base)
        tokenizer = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
        heads = model.LatentRoutes(model.RouteConfig(args.arm, width=base.config.text_config.hidden_size,
                                                     rank=args.rank)).to(base.get_input_embeddings().weight.device)
        state = dict(schema=SCHEMA, binding=binding, comparison_binding=comparison_binding, teacher_binding_sha256=teacher_sha,
                     total_rows=total, rows_applied=0, next_ordinal=0, status="in_progress",
                     reasoning_tokens=0, target_segments=0, answer_labels=0, excluded_rows=0, training_seconds=0.,
                     source_completion_counts={k:0 for k in ("complete","open_reasoning","closed_without_answer","answer_only")},
                     source_revision=manifest["revision"], latent_chunk=args.latent_chunk, answer_chunk=args.answer_chunk,
                     trajectory="Each contiguous segment mean includes all original teacher hidden values. Row-major interleaving allocates each segment once. Only live predicted vectors enter one shared cache.",
                     objective="0.5 route plus 0.125 shared hidden cosine distillation weighted by original segment token count; 0.25 balanced per-route halt BCE; full answer and EOS CE only when the source provides a final answer. Censored reasoning rows train observed segment/continuation only. Explicitly closed reasoning without answer trains halting but no answer/EOS. Learned stopping during inference.",
                     optimizer="PyTorch Adafactor, one update per source row after FP32 CPU accumulation of all TBPTT block gradients, zero weight decay.",
                     gradients="Shared-cache history and recurrent values are preserved; gradients detach only between completed blocks. Complete user input stays in cache, prompt gradients are limited to the last 24 text tokens or the visual suffix. Only the terminal latent block connects to the answer CE.")
    state["scope_sha256"] = scope
    state["worker_sha256"] = runner.file_sha(Path(__file__))
    base.requires_grad_(True).eval()
    heads.requires_grad_(True).train()
    named = list(base.named_parameters()) + [("routes."+n,p) for n,p in heads.named_parameters()]
    owner = AccountedGradients(named, args.learning_rate, args.root/"gradient-scratch.fp32")
    processor = AutoProcessor.from_pretrained(args.base, local_files_only=True)
    if pointer:
        saved = torch.load(folder/"optimizer-rng.pt", map_location="cpu", weights_only=True)
        if saved["optimizers"].keys() != owner.optimizers.keys() or set(initial) != set(owner.params):
            raise ValueError("optimizer/parameter registry changed across restart")
        for name, opt in owner.optimizers.items():
            opt.load_state_dict(saved["optimizers"][name])
        owner.stats = saved["stats"]
        torch.set_rng_state(saved["torch_rng"])
        torch.cuda.set_rng_state(saved["cuda_rng"])
        del saved
    else:
        initial = {name:previous.checksum_tensor(p) for name,p in named}
    pending = []
    while state["next_ordinal"] < until and not (args.root/"STOP").exists():
        ordinal = state["next_ordinal"]
        if not (args.root/"queue"/f"{ordinal:09d}"/"receipt.json").is_file():
            break
        try:
            receipt, row, targets, ends = read_case(args.root, ordinal)
            if receipt["binding_sha256"] != teacher_sha:
                raise ValueError("different producer binding for row")
            if row is None:
                entry = dict(ordinal=ordinal,status="excluded",reason=receipt.get("reason"),id=receipt["id"])
                state["excluded_rows"] += 1
            else:
                if row["id"] != receipt["id"] or row["ordinal"] != ordinal or ends != receipt["segment_ends"]:
                    raise ValueError("source row and teacher token boundaries disagree")
                print(f"Starting {args.arm} routes, ordinal {ordinal}, {len(ends)} segments, "
                      f"{receipt['n_answer_tokens']} answer tokens.", flush=True)
                entry = train_case(args, base, heads, owner, tokenizer, processor, ordinal, row, targets, ends)
                state["source_completion_counts"][entry["source_completion"]] += 1
                eos_count = int(entry["native_eos"] is not None)
                if entry["terminal_answer_labels"] != receipt["n_answer_tokens"]+eos_count or entry["answer_sha256"] != receipt["answer_sha256"]:
                    raise ValueError("teacher and student answer supervision diverged")
                state["rows_applied"] += 1
                state["reasoning_tokens"] += entry["n_reasoning_tokens"]
                state["target_segments"] += entry["supervised_segments"]
                state["answer_labels"] += entry["terminal_answer_labels"]
                state["training_seconds"] += entry["seconds"]
                print(f"Applied {args.arm} routes row {ordinal} in {entry['seconds']:.2f}s, "
                      f"peak {entry['peak_cuda_bytes']/2**30:.2f} GiB.", flush=True)
        except BaseException as error:
            # No cursor moves on failure; the durable snapshot is the restart boundary.
            runner.write_json(arm/"last-failure.json", dict(ordinal=ordinal, kind=type(error).__name__, reason=str(error),
                              last_durable_cursor=read_json(arm/"current.json")["next_ordinal"] if (arm/"current.json").exists() else 0))
            raise
        entry.update(scope_sha256=scope, scope_decision=receipt["scope_decision"], target_sha256=receipt.get("target_sha256"), source_record_sha256=receipt.get("record_sha256"))
        pending.append(entry)
        state["next_ordinal"] += 1
        if state["next_ordinal"] == total:
            state["status"] = "complete" if state["rows_applied"] == total else "finished_with_exclusions"
        if state["next_ordinal"] == 1 or len(pending) >= args.checkpoint_every:
            save_checkpoint(args,arm,state,pending,base,tokenizer,processor,heads,owner,named,initial)
    save_checkpoint(args,arm,state,pending,base,tokenizer,processor,heads,owner,named,initial)
    owner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--arm", type=int, choices=(1,5), required=True)
    parser.add_argument("--until", type=int, default=1_200_000, help="Exclusive source ordinal; resumes at the durable cursor.")
    parser.add_argument("--checkpoint-every", type=int, default=32)
    parser.add_argument("--rank", type=int, default=96)
    parser.add_argument("--latent-chunk", type=int, default=3)
    parser.add_argument("--answer-chunk", type=int, default=48)
    parser.add_argument("--image-every", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=.0015)
    args = parser.parse_args()
    if min(args.until,args.checkpoint_every,args.rank,args.latent_chunk,args.answer_chunk,args.image_every,args.learning_rate) <= 0:
        parser.error("training and checkpoint dimensions must be positive")
    torch.set_num_threads(min(6, torch.get_num_threads()))
    run(args)


if __name__ == "__main__":
    main()
