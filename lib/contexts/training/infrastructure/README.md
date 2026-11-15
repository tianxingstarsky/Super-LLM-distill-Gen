# Local training adapters

`transformers_backend.py` implements the optional local validation port with
Hugging Face Transformers `Trainer`, PEFT and bitsandbytes. It consumes already
encoded examples and preserves their labels; it does not create training data,
discard system messages or rewrite reasoning. Optional ML packages are imported
only when a method is called.

The ordered sampler is a Trainer extension for a single curriculum pass over
CPT examples followed by SFT examples. The standard Trainer owns optimization,
gradient accumulation, checkpoint writing and the epoch counter. No custom
optimizer or training loop is maintained here.

Training writes a model/example/config identity before loading weights. Standard
Trainer checkpoints default to every optimizer step, retaining two, and include adapter digests. They are resumed
through `get_last_checkpoint` / `resume_from_checkpoint`; a changed identity or
adapter is rejected. A finished receipt is reused only after file hashes match.
No resume starts a second epoch. Uncheckpointed optimizer progress requires
explicit recovery rather than silently repeating training.
If recorded progress/failure is newer than the last checkpoint, recovery stops.
A started marker with no checkpoint and no explicit zero-step failure also
stops; a hard interruption is not treated as proof that nothing was trained.

Evaluation uses the same local base weights, quantization and official chat
template for all arms. Raw generated token IDs/text, thinking and answer
boundaries, completion reasons and per-case configuration are retained. An
empty answer or output cap is recorded rather than repaired or counted correct.
Optional pre-encoded validation examples use the model's native Hugging Face
forward loss under `torch.inference_mode()`, weighted by shifted, unmasked target
tokens. This supports a quantized baseline without bypassing Trainer's training
guards. Masks are preserved; reference answers enter only this teacher-forced
loss calculation, never generation. No epoch is selected from validation loss,
which is reported separately from generated accuracy.
Sampling records requested and supported settings; unsupported presence penalty
is explicitly not sent, while supported native GenerationConfig fields are used.

The application and composition root supply model paths, immutable examples,
evaluation cases and an optional prompt renderer. This directory does not
download weights, call paid APIs, select experiments or publish artifacts.
Validation combines the scoped application tests with an actual local model
preflight and the resulting private training/evaluation receipts.
