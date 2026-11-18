"""One pass over an OpenThoughts math split and an equal-backbone-call comparison."""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import gc
import math
import shutil
from pathlib import Path
import re
import time

import torch
from torch.nn import functional as F
from transformers import AutoProcessor, AutoTokenizer
from safetensors.torch import load_file, save_file

from . import ot_latent_model as model
from . import recurrent_runtime as runtime
from . import runner


SYSTEM = "Solve the problem. Give only the final numerical answer."
SEED = 1718


def messages(question):
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]


def rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def fraction(value):
    value = str(value).strip().replace("−", "-").replace(r"\,", "").replace(" ", "")
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        value = value.replace(",", "")
    value = re.sub(
        r"^([+-]?)\\(?:d?frac|tfrac)\{([+-]?\d+)\}\{([+-]?\d+)\}$", r"\1\2/\3", value
    )
    if not re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/[+-]?\d+|[eE][+-]?\d+)?", value
    ):
        return None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def final_number(text):
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    line = lines[-1].strip("$* ").removesuffix(".").strip()
    line = re.sub(
        r"^(?:final answer|answer|the final answer is|the answer is)\s*[:=]?\s*",
        "",
        line,
        flags=re.I,
    )
    line = (
        line.strip("$* ")
        .removeprefix(r"\[")
        .removesuffix(r"\]")
        .removeprefix(r"\(")
        .removesuffix(r"\)")
        .strip()
    )
    if line.startswith(r"\boxed{") and line.endswith("}"):
        line = line[7:-1]
    return fraction(line)


def teacher_excerpt(ids, budget, gap):
    if len(ids) <= budget:
        return ids, len(ids)
    edge = budget // 3
    middle = budget - 2 * edge
    start = (len(ids) - middle) // 2
    return ids[:edge] + gap + ids[start : start + middle] + gap + ids[-edge:], budget


@torch.inference_mode()
def teacher_targets(base, tokenizer, records, output, fingerprint, budget):
    receipt = output / "teacher-cache.json"
    targets = output / "teacher-cache.safetensors"
    if receipt.exists():
        meta = json.loads(receipt.read_text("utf-8"))
        if meta["binding"] != fingerprint or meta["window"] != budget:
            raise ValueError("teacher cache does not belong to this split and budget")
        return load_file(str(targets))["hidden"], meta["cases"]
    tag = tokenizer.encode("\n[...]\n", add_special_tokens=False)
    hidden, cases = [], []
    start = time.perf_counter()
    for record in records:
        teacher = tokenizer.encode(record["reasoning"], add_special_tokens=False)
        excerpt, used = teacher_excerpt(teacher, budget, tag)
        prompt = model.encode_prompt(tokenizer, messages(record["question"]))
        cache, vector, inputs = runtime._prefill(
            base, prompt + excerpt, differentiable=False
        )
        hidden.append(vector[0].float().cpu())
        cases.append(
            dict(id=record["id"], original_tokens=len(teacher), retained_tokens=used)
        )
        del cache, vector, inputs
        if len(hidden) % 16 == 0:
            print(f"Teacher targets {len(hidden)}/{len(records)}", flush=True)
    all_hidden = torch.stack(hidden)
    save_file({"hidden": all_hidden}, str(targets))
    runner.write_json(
        receipt,
        dict(
            binding=fingerprint,
            window=budget,
            cases=cases,
            elapsed_seconds=time.perf_counter() - start,
            retained_tokens=sum(r["retained_tokens"] for r in cases),
            original_tokens=sum(r["original_tokens"] for r in cases),
            procedure="Frozen Qwen final hidden after problem and first/middle/last contiguous spans of the original training reasoning, separated by literal omitted-text markers. No hidden target or reference in student input.",
        ),
    )
    return all_hidden, cases


def parameter_group(name):
    if name.startswith("routes."):
        return "routes"
    if ".visual." in name:
        match = re.search(r"\.blocks\.(\d+)", name)
        return (
            f"vision.block.{match[1]}"
            if match
            else "vision." + name.split(".visual.")[1].split(".")[0]
        )
    match = re.search(r"\.layers\.(\d+)", name)
    return (
        f"text.block.{match[1]}"
        if match
        else name.removeprefix("model.language_model.")
    )


def checksum_tensor(value):
    host = value.detach().contiguous().view(-1).view(torch.uint8).cpu().numpy()
    return hashlib.sha256(memoryview(host)).hexdigest()


def full_update_issues(receipt, text_layers, vision_layers):
    """Check the whole registered native model before publishing full-tune weights."""
    groups = receipt["parameter_groups"]
    expected = {
        "embed_tokens.weight",
        "norm.weight",
        "vision.patch_embed",
        "vision.pos_embed",
        "vision.merger",
        "routes",
        *(f"text.block.{i}" for i in range(text_layers)),
        *(f"vision.block.{i}" for i in range(vision_layers)),
    }
    missing = sorted(expected - groups.keys())
    issues = [f"absent parameter group: {name}" for name in missing]
    if receipt["frozen_parameters"] or receipt["unreached_tensors"]:
        issues.append(
            "some registered parameters were frozen or never received a nonzero gradient"
        )
    if not receipt["tied_word_embeddings"]:
        issues.append(
            "expected the Qwen output head to share the trained input embedding"
        )
    for name, group in groups.items():
        if group["nonzero_grad"] != group["tensors"]:
            issues.append(f"{name}: incomplete nonzero-gradient coverage")
        if not group["changed"]:
            issues.append(f"{name}: no stored parameter changed")
    return issues


class OffloadedGradients:
    """Keep complete parameter gradients on CPU until all backwards have finished."""

    def __init__(self, named, lr):
        self.params = dict(named)
        self.gradients = {}
        self.stats = {}
        self.hooks = []
        self.optimizers = {}
        self.parameter_storages = {
            p.untyped_storage().data_ptr() for p in self.params.values()
        }
        for name, param in self.params.items():
            optimizer = torch.optim.Adafactor(
                [param],
                lr=lr if not name.startswith("routes.") else 0.015,
                eps=(1e-18, 1e-3),
                weight_decay=0.0,
                foreach=False,
            )
            self.optimizers[name] = optimizer
            self.hooks.append(
                param.register_post_accumulate_grad_hook(self.offload(name))
            )

    def offload(self, name):
        def capture(param):
            if name in self.gradients:
                raise RuntimeError(f"duplicate parameter gradient: {name}")
            self.gradients[name] = param.grad.detach().cpu()
            param.grad = None

        return capture

    def pack(self, tensor):
        if (
            tensor.is_cuda
            and tensor.numel() > 4096
            and tensor.untyped_storage().data_ptr() not in self.parameter_storages
        ):
            return (tensor.device, tensor.detach().cpu())
        return tensor.detach()

    @staticmethod
    def unpack(saved):
        return saved[1].to(saved[0]) if isinstance(saved, tuple) else saved

    def step(self, *, update):
        # Validate every gradient before committing any changes to this step.
        for name, grad in self.gradients.items():
            smallest, largest = torch.aminmax(grad)
            span = max(abs(float(smallest)), abs(float(largest)))
            if not math.isfinite(span):
                raise FloatingPointError(f"nonfinite gradient in {name}")
            if span > 0:
                self.stats[name] = self.stats.get(name, 0) + 1
        count = len(self.gradients)
        for name in self.params:
            if name not in self.gradients:
                continue
            grad = self.gradients.pop(name)
            if update:
                param = self.params[name]
                param.grad = grad.to(param.device)
                self.optimizers[name].step()
                param.grad = None
        return count

    def close(self):
        for hook in self.hooks:
            hook.remove()


def train_arm(args, base, tokenizer, processor, records, targets, count, binding):
    root = args.output
    out = root / "models" / f"routes-{count}"
    state_dir = root / f"state-{count}"
    state_dir.mkdir(exist_ok=True)
    receipt = root / f"training-routes-{count}.json"
    torch.manual_seed(SEED)
    device = base.get_input_embeddings().weight.device
    config = model.RouteConfig(
        count,
        width=base.config.text_config.hidden_size,
        rank=args.rank,
        max_rounds=args.rounds,
    )
    heads = model.LatentRoutes(config).to(device)
    base.requires_grad_(True).eval()
    named = list(base.named_parameters()) + [
        ("routes." + n, p) for n, p in heads.named_parameters()
    ]
    owner = OffloadedGradients(named, args.learning_rate)
    initial = {name: checksum_tensor(p) for name, p in named}
    journal = state_dir / "progress.json"
    if journal.exists() and json.loads(journal.read_text("utf-8")).get("committed", 0):
        raise RuntimeError(
            "an earlier partial parameter update exists; preserve it and never replay its examples"
        )
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats(device)
    history, closing = [], model.boundary_ids(tokenizer)
    for index, record in enumerate(records):
        prefix = model.encode_prompt(tokenizer, messages(record["question"]))
        image_batch, pixel_sha = None, None
        if index % args.image_every == 0:
            image_batch, pixels = model.render_image_prompt(
                processor, messages(record["question"])
            )
            pixel_sha = hashlib.sha256(pixels.tobytes()).hexdigest()
            image_dir = root / "image-prompts"
            image_dir.mkdir(exist_ok=True)
            pixels.save(image_dir / f"{record['id']}.png")
        labels = tokenizer.encode(
            str(record["reference"]), add_special_tokens=False
        ) + [tokenizer.eos_token_id]
        # During training observe all possible stopping positions. Each slot's
        # hazard receives its own quality/time trade-off, without shared labels.
        active = torch.ones(args.rounds, count, device=device, dtype=torch.bool)
        with torch.autograd.graph.saved_tensors_hooks(owner.pack, owner.unpack):
            roll = model.latent_rollout(
                base,
                heads,
                prefix,
                matched=True,
                teacher_alive=active,
                multimodal_inputs=image_batch,
            )
            ids = torch.tensor([closing + labels[:-1]], device=device)
            hidden = runtime._forward(
                base, roll.cache, base.get_input_embeddings()(ids)
            )[0, len(closing) - 1 :]
            scores = base.lm_head(hidden.to(base.lm_head.weight.dtype)).float()
            answer_loss = F.cross_entropy(scores, torch.tensor(labels, device=device))
            alignment = (
                1
                - F.cosine_similarity(
                    roll.hidden.float(), targets[index : index + 1].to(device)
                ).mean()
            )
            hazard = roll.halt_logits.sigmoid()
            survival = torch.cat(
                (torch.ones_like(hazard[:1]), (1 - hazard[:-1]).cumprod(0))
            )
            probability = survival * torch.cat(
                (hazard[:-1], torch.ones_like(hazard[-1:]))
            )
            costs = (
                torch.arange(args.rounds, device=device, dtype=torch.float32)[:, None]
                * 0.005
            )
            error = 1 - F.cosine_similarity(
                roll.route_history.float(),
                targets[index].to(device)[None, None],
                dim=-1,
            )
            halt = ((error + costs) * probability).sum(0).mean()
            loss = answer_loss + 0.5 * alignment + 0.25 * halt
        if not torch.isfinite(loss):
            raise FloatingPointError("nonfinite training objective")
        loss.backward()
        if not args.smoke:
            runner.write_json(
                journal,
                dict(binding=binding, committed=index + 1, in_progress=record["id"]),
            )
        count_grads = owner.step(update=not args.smoke)
        history.append(
            dict(
                id=record["id"],
                loss=float(loss.detach()),
                answer_ce=float(answer_loss.detach()),
                teacher_alignment=float(alignment.detach()),
                expected_route_cost=float(halt.detach()),
                route_stop_probabilities=probability.detach().cpu().tolist(),
                supervised_answer_tokens=len(labels),
                backbone_calls=1 + roll.latent_calls + 1,
                image_sha256=pixel_sha,
                gradient_tensors=count_grads,
            )
        )
        del roll, hidden, loss, answer_loss, scores, halt, alignment
        torch.cuda.synchronize(device)
        elapsed = time.perf_counter() - started
        peak = torch.cuda.max_memory_allocated(device)
        if not args.smoke:
            runner.write_json(
                journal,
                dict(
                    binding=binding,
                    committed=len(history),
                    history=history,
                    elapsed_seconds=elapsed,
                    peak_cuda_bytes=peak,
                ),
            )
        print(
            f"Arm {count}: {len(history)}/{len(records)} {elapsed:.2f}s, loss {history[-1]['loss']:.3f}, {count_grads} gradient tensors, peak {peak / 2**30:.2f} GiB",
            flush=True,
        )
    owner.close()
    groups = {}
    changed, nonzero, unreached = [], [], []
    for name, p in named:
        group = groups.setdefault(
            parameter_group(name),
            dict(tensors=0, parameters=0, nonzero_grad=0, changed=0),
        )
        group["tensors"] += 1
        group["parameters"] += p.numel()
        if owner.stats.get(name, 0):
            group["nonzero_grad"] += 1
            nonzero.append(name)
        else:
            unreached.append(name)
        if checksum_tensor(p) != initial[name]:
            group["changed"] += 1
            changed.append(name)
    trainable_parameters = sum(p.numel() for _, p in named if p.requires_grad)
    frozen_parameters = sum(p.numel() for _, p in named if not p.requires_grad)
    base.requires_grad_(False)
    tied = (
        base.get_input_embeddings().weight.data_ptr()
        == base.get_output_embeddings().weight.data_ptr()
    )
    details = dict(
        n_rows=len(records),
        n_updates=0 if args.smoke else len(history),
        elapsed_seconds=elapsed,
        peak_cuda_bytes=peak,
        trainable_parameters=trainable_parameters,
        base_parameters=sum(p.numel() for p in base.parameters()),
        route_parameters=sum(p.numel() for p in heads.parameters()),
        frozen_parameters=frozen_parameters,
        parameter_groups=groups,
        nonzero_gradient_tensors=nonzero,
        changed_tensors=changed,
        unreached_tensors=unreached,
        nonzero_gradient_without_stored_change=sorted(set(nonzero) - set(changed)),
        tied_word_embeddings=tied,
        epochs=0 if args.smoke else 1,
        model_bundle=out.relative_to(root).as_posix(),
        binding=binding,
        training_losses=history,
        seed=SEED,
        learning_rate=args.learning_rate,
        route_learning_rate=0.015,
        optimizer="PyTorch Adafactor; full gradients offloaded to CPU before per-parameter device update; zero weight decay.",
        distillation="Final numerical answer and native EOS cross-entropy, 0.5 cosine loss to frozen teacher hidden, 0.25 expected per-route candidate error under its own stop distribution plus a 0.005 cost for each extra round. Enumerate four positions during training. No shared stopping label. Evaluation receives the question only.",
    )
    if args.smoke:
        runner.write_json(root / "smoke.json", details)
        return heads.eval()
    issues = full_update_issues(
        details,
        base.config.text_config.num_hidden_layers,
        base.config.vision_config.depth,
    )
    if issues:
        runner.write_json(
            state_dir / "coverage-review.json", {"issues": issues, "receipt": details}
        )
        raise RuntimeError("incomplete full-model update: " + ", ".join(issues))
    out.mkdir(parents=True, exist_ok=True)
    full = out / "base"
    base.save_pretrained(full, safe_serialization=True, max_shard_size="4GB")
    processor.save_pretrained(full)
    tokenizer.save_pretrained(full)
    for filename in ("LICENSE", "video_preprocessor_config.json"):
        if (args.base / filename).exists() and not (full / filename).exists():
            shutil.copyfile(args.base / filename, full / filename)
    model.save_bundle(
        out,
        heads,
        full,
        base_ref="base",
        metadata={
            "training_binding": binding,
            "epochs": 1,
            "train_rows": len(records),
            "full_parameter_finetune": True,
            "task_system": SYSTEM,
        },
    )
    runner.write_json(receipt, details)
    return heads.eval()


def evaluate_arm(args, base, tokenizer, heads, count, records, binding):
    report = args.output / "eval-results.json"
    if report.exists():
        data = json.loads(report.read_text("utf-8"))
        if data["binding"] != binding:
            raise ValueError("evaluation binding changed")
    else:
        data = dict(
            binding=binding,
            system=SYSTEM,
            answer_budget=args.answer_budget,
            cases=[],
            summary={},
            score="Exact rational equality of the last complete numerical answer. Truncation or an ambiguous final result is a miss.",
        )
    for i, record in enumerate(records):
        if i == len(data["cases"]):
            data["cases"].append(
                dict(
                    id=record["id"],
                    question=record["question"],
                    reference=record["reference"],
                    source=record.get("source"),
                    source_reference=record.get("source_reference"),
                    arms={},
                )
            )
        case = data["cases"][i]
        if case["id"] != record["id"]:
            raise ValueError("evaluation order changed")
        if str(count) in case["arms"]:
            continue
        result = model.generate(
            base,
            tokenizer,
            heads,
            messages(record["question"]),
            matched=True,
            observe=False,
            max_new_tokens=args.answer_budget,
            matched_answer_steps=args.answer_budget,
        )
        expected, parsed = fraction(record["reference"]), final_number(result["text"])
        if expected is None:
            raise ValueError(f"ungradable reference: {record['id']}")
        result["parsed"] = str(parsed) if parsed is not None else None
        result["correct"] = result["stop"] == "eos" and parsed == expected
        case["arms"][str(count)] = result
        if (
            "1" in case["arms"]
            and "5" in case["arms"]
            and case["arms"]["1"]["total_calls"] != case["arms"]["5"]["total_calls"]
        ):
            raise RuntimeError("paired backbone comparison mismatch")
        runner.write_json(report, data)
        print(
            f"Eval {i + 1}/{len(records)} arm {count}, {result['stop']}, correct {result['correct']}, {result['seconds']:.1f}s",
            flush=True,
        )
    arm = [r["arms"][str(count)] for r in data["cases"]]
    data["summary"][str(count)] = dict(
        n=len(arm),
        correct=sum(r["correct"] for r in arm),
        truncated=sum(r["stop"] != "eos" for r in arm),
        unparsed=sum(r["parsed"] is None for r in arm),
        total_calls=sum(r["total_calls"] for r in arm),
        active_answer_calls=sum(r["answer_active_calls"] for r in arm),
        elapsed_seconds=sum(r["seconds"] for r in arm),
        seconds_to_stop=sum(r["seconds_to_stop"] for r in arm),
        peak_cuda_bytes=max(r["peak_cuda_bytes"] for r in arm),
    )
    runner.write_json(report, data)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "train", "eval", "output"):
        cli.add_argument("--" + name, required=True, type=Path)
    cli.add_argument("--teacher-window", type=int, default=384)
    cli.add_argument("--rank", type=int, default=96)
    cli.add_argument("--rounds", type=int, default=4)
    cli.add_argument("--learning-rate", type=float, default=0.0015)
    cli.add_argument("--answer-budget", type=int, default=96)
    cli.add_argument("--train-limit", type=int, default=32)
    cli.add_argument("--image-every", type=int, default=8)
    cli.add_argument("--arm", choices=(1, 5), type=int)
    cli.add_argument(
        "--smoke",
        action="store_true",
        help="Image forward/backward only; no parameter update.",
    )
    opt = cli.parse_args()
    if (
        min(opt.train_limit, opt.image_every, opt.answer_budget, opt.rank, opt.rounds)
        < 1
        or opt.teacher_window < 3
    ):
        cli.error("use positive counts and a teacher window of at least three tokens")
    opt.output.mkdir(exist_ok=True, parents=True)
    torch.set_num_threads(min(6, torch.get_num_threads()))
    train, test = rows(opt.train)[: opt.train_limit], rows(opt.eval)
    ids = [r["id"] for r in train + test]
    questions = [" ".join(r["question"].split()).casefold() for r in train + test]
    if (
        not train
        or not test
        or len(set(ids)) != len(ids)
        or len(set(questions)) != len(questions)
    ):
        raise ValueError("empty or overlapping train/evaluation data")
    source_path = opt.output / "input-base.json"
    source_identity = model._base_identity(opt.base, include_processor=True)
    if (
        source_path.exists()
        and json.loads(source_path.read_text("utf-8")) != source_identity
    ):
        raise ValueError("source weights, tokenizer or processor changed for this run")
    runner.write_json(source_path, source_identity)
    teacher_binding = (
        runner.file_sha(opt.train)
        + ":"
        + str(len(train))
        + ":"
        + runner.file_sha(opt.base / "config.json")
    )
    binding = hashlib.sha256(
        json.dumps(
            dict(
                train=teacher_binding,
                test=runner.file_sha(opt.eval),
                teacher_window=opt.teacher_window,
                rank=opt.rank,
                rounds=opt.rounds,
                seed=SEED,
                lr=opt.learning_rate,
                answer_budget=opt.answer_budget,
                image_every=opt.image_every,
            )
        ).encode()
    ).hexdigest()
    arms = [opt.arm] if opt.arm else [1, 5]
    tokenizer = AutoTokenizer.from_pretrained(opt.base, local_files_only=True)
    processor = AutoProcessor.from_pretrained(opt.base, local_files_only=True)
    for n in arms:
        receipt = opt.output / f"training-routes-{n}.json"
        if receipt.exists():
            if json.loads(receipt.read_text("utf-8"))["binding"] != binding:
                raise ValueError("completed arm belongs to another run")
            base, tokenizer, heads = model.load_bundle(
                opt.output / "models" / f"routes-{n}"
            )
        else:
            base, loaded = runner.load_frozen_model(opt.base)
            runner.write_json(opt.output / "base-load.json", loaded)
            targets, _ = teacher_targets(
                base, tokenizer, train, opt.output, teacher_binding, opt.teacher_window
            )
            heads = train_arm(
                opt, base, tokenizer, processor, train, targets, n, binding
            )
        if not opt.smoke:
            torch.cuda.empty_cache()
            evaluate_arm(opt, base, tokenizer, heads, n, test, binding)
        del base, heads
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
