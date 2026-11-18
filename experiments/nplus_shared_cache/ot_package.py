"""Ship both fully trained route variants with their own native Qwen weights."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import zipfile


RUNTIME = ("ot_latent_model.py", "runner.py", "recurrent_runtime.py", "differentiable_cache.py")
ENTRY = '''"""Local command-line entry point for the two trained route variants."""
from pathlib import Path
import sys
from experiments.nplus_shared_cache.ot_latent_model import main

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    variant = "single" if "--single" in sys.argv else "five"
    if "--single" in sys.argv:
        sys.argv.remove("--single")
    sys.argv[1:1] = ["--bundle", str(root / variant)]
    main()
'''


def checksum(file):
    with file.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def assemble(output, single, five, license_file):
    sources = Path(__file__).parent
    runtime = output / "experiments" / sources.name
    runtime.mkdir(parents=True, exist_ok=False)
    for name in RUNTIME:
        shutil.copyfile(sources / name, runtime / name)
    for folder in (runtime, runtime.parent):
        (folder / "__init__.py").touch()
    (output / "run.py").write_text(ENTRY, encoding="utf-8")
    for directory, name in ((single, "single"), (five, "five")):
        target = output / name
        target.mkdir()
        receipt = json.loads((directory / "route_model.json").read_text(encoding="utf-8"))
        if receipt["base_ref"] != "base" or not (directory / "base").is_dir():
            raise ValueError("each full-model variant must include its own trained base")
        shutil.copytree(directory / "base", target / "base")
        if not (target / "base" / "LICENSE").exists():
            shutil.copyfile(license_file, target / "base" / "LICENSE")
        for filename in ("route_model.json", "route_model.safetensors"):
            shutil.copyfile(directory / filename, target / filename)
    shutil.copyfile(sources.parents[1] / "LICENSE", output / "LICENSE")
    shutil.copyfile(sources / "OT_MODEL_CARD.md", output / "README.md")
    package_files = sorted(file for file in output.rglob("*") if file.is_file())
    manifest = {file.relative_to(output).as_posix(): dict(bytes=file.stat().st_size, sha256=checksum(file))
                for file in package_files}
    manifest_file = output / "SHA256.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return [*package_files, manifest_file]


def finish(output, single, five, archive, license_file):
    output.parent.mkdir(parents=True, exist_ok=True)
    if (output.exists() and any(output.iterdir())) or (archive and archive.exists()):
        raise FileExistsError("choose an empty delivery directory and a new archive")
    if archive and archive.parent.resolve() != output.parent.resolve():
        raise ValueError("place the archive next to the extracted delivery")
    with TemporaryDirectory(dir=output.parent, prefix=".pack-") as staging:
        staged = Path(staging) / output.name
        staged.mkdir()
        files = assemble(staged, single, five, license_file)
        if archive:
            temporary_archive = Path(staging) / archive.name
            with zipfile.ZipFile(temporary_archive, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
                for file in files:
                    bundle.write(file, (Path(output.name) / file.relative_to(staged)).as_posix())
        if output.exists():
            output.rmdir()
        staged.rename(output)
        if archive:
            temporary_archive.rename(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--single", type=Path, required=True)
    parser.add_argument("--five", type=Path, required=True)
    parser.add_argument("--base-license", type=Path, required=True)
    parser.add_argument("--zip", type=Path)
    args = parser.parse_args()
    finish(args.output, args.single, args.five, args.zip, args.base_license)
