"""Build a clean, per-user Windows online installer from one immutable Git tree."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import re
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
if not re.fullmatch(r"\d+\.\d+\.\d+", VERSION):
    raise ValueError("VERSION must contain a numeric release version")
ASSET = f"ShuJian-Cube-{VERSION}-Windows-x64-Setup.exe"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256")
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def download(url: str, target: Path, expected: str) -> Path:
    if target.is_file() and sha256(target) == expected:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".download")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": f"ShuJian-Cube-release/{VERSION}"})
            with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as sink:
                shutil.copyfileobj(response, sink, length=1024 * 256)
            if sha256(partial) != expected:
                raise ValueError(f"Checksum mismatch: {target.name}")
            partial.replace(target)
            return target
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("unreachable")


def source_path_allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if path.parts[0] in {".venv", ".dataforge", "node_modules", ".git", "参考", "截图", "components"}:
        return False
    if name == ".env" or name.startswith("configs/backends.local.yaml"):
        return False
    if name in {"configs/review_remote.yaml", "plugins/dsh-dataforge/cordis.yml", "plugins/dsh-dataforge/team.yml"}:
        return False
    if name.startswith(("data/output/", "data/workspaces/", "data/seeds/chatlogs/",
                        "data/seeds/images/", "data/seeds/screenshots/", "data/seeds/uploads/")):
        return path.name == ".gitkeep"
    return not (name.endswith(".log") or name.endswith(".pyc"))


def extract_source(ref: str, archive: Path, payload: Path) -> str:
    tree = git("rev-parse", "--verify", ref + "^{tree}")
    with archive.open("wb") as output:
        subprocess.run(["git", "archive", "--format=zip", ref], cwd=ROOT, stdout=output, check=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if item.is_dir():
                continue
            if not source_path_allowed(item.filename) or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError(f"Release source contains an excluded path: {item.filename}")
            target = payload.joinpath(*PurePosixPath(item.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    if (payload / "VERSION").read_text(encoding="utf-8").strip() != VERSION:
        raise ValueError("Commit VERSION does not match this installer")
    return tree


def prepare_compiler(spec: dict, tools: Path) -> Path:
    compiler = tools / ("inno-" + spec["version"]) / "ISCC.exe"
    if compiler.is_file():
        return compiler
    setup = download(spec["url"], tools / spec["url"].rsplit("/", 1)[-1], spec["sha256"])
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.run([str(setup), "/VERYSILENT", "/CURRENTUSER", "/SP-", "/SUPPRESSMSGBOXES",
                    "/NORESTART", "/NOICONS", f"/DIR={compiler.parent}"], check=True,
                   startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    if not compiler.is_file():
        raise FileNotFoundError("Inno Setup did not provide ISCC.exe")
    return compiler


def build(ref: str, work: Path) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Build this Windows installer on Windows x64")
    if not work.is_relative_to(ROOT / ".dataforge"):
        raise ValueError("Build output must stay inside the ignored .dataforge directory")
    tools = work.parent / "tools"
    payload = work / ("payload-" + uuid.uuid4().hex[:12])
    payload.mkdir(parents=True)
    tree = extract_source(ref, work / "source.zip", payload)
    specifications = json.loads((payload / "scripts/release/tools.json").read_text(encoding="utf-8"))
    uv = specifications["uv"]
    archive = download(uv["url"], tools / f"uv-{uv['version']}.zip", uv["sha256"])
    runtime = payload / ".runtime"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        candidates = [name for name in bundle.namelist() if PurePosixPath(name).name == "uv.exe"]
        if len(candidates) != 1:
            raise ValueError("Official uv archive does not contain exactly one uv.exe")
        (runtime / "uv.exe").write_bytes(bundle.read(candidates[0]))
    python = specifications["python"]
    python_archive = download(python["url"], tools / f"cpython-{python['version']}-{python['build']}.tar.gz", python["sha256"])
    python_home = runtime / "python" / python["directory"]
    python_home.mkdir(parents=True)
    with tarfile.open(python_archive, "r:gz") as bundle:
        for item in bundle:
            name = PurePosixPath(item.name)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != "python":
                raise ValueError(f"Invalid standalone Python archive path: {item.name}")
            # Python regenerates timestamp-based bytecode. It is cache, not source.
            if "__pycache__" in name.parts or name.suffix == ".pyc":
                continue
            target = python_home.joinpath(*name.parts[1:])
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(item) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
            else:
                raise ValueError(f"Unsupported standalone Python archive member: {item.name}")
    if not (python_home / "python.exe").is_file():
        raise FileNotFoundError("The standalone Python archive lacks python.exe")
    for filename in ("UV-LICENSE-APACHE.txt", "UV-LICENSE-MIT.txt"):
        license_file = payload / "scripts/release" / filename
        if not license_file.is_file():
            raise FileNotFoundError(filename)
    from PIL import Image
    with Image.open(payload / "assets/brand/shujian-cube-mark.png") as logo:
        logo.save(payload / "assets/brand/shujian-cube.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    commit = subprocess.run(["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=ROOT,
                            text=True, capture_output=True, encoding="utf-8")
    manifest = {"product_id": "shujian-cube", "version": VERSION, "source_tree": tree,
                "source_commit": commit.stdout.strip() if commit.returncode == 0 else None,
                "requirements_sha256": sha256(payload / "scripts/release/requirements-windows-x64.lock"),
                "tools": specifications, "uv_executable_sha256": sha256(runtime / "uv.exe"),
                "distribution": "Windows x64 online setup", "model_api_calls": 0}
    (payload / "INSTALLATION.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    files, seeds = {}, {}
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            relative = path.relative_to(payload).as_posix()
            (seeds if relative.startswith(("configs/", "data/")) else files)[relative] = sha256(path)
    (payload / "PAYLOAD.json").write_text(json.dumps(
        {"files": files, "initial_seeds": seeds}, indent=2) + "\n", encoding="utf-8")
    entries = []
    for path in sorted(payload.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(payload)
        source = str(path).replace('"', '""')
        directory = str(relative.parent)
        destination = "{app}" + ("\\" + directory if directory != "." else "")
        if relative.as_posix().startswith(("configs/", "data/")):
            flags = "onlyifdoesntexist uninsneveruninstall"
            condition = ""
        else:
            flags = "ignoreversion"
            argument = str(relative).replace("'", "''")
            condition = f"; Check: FileNeedsUpdate('{argument}', '{sha256(path)}')"
        entries.append(f'Source: "{source}"; DestDir: "{destination}"; Flags: {flags}{condition}')
    install_files = work / ("files-" + payload.name + ".iss")
    install_files.write_text("\n".join(entries) + "\n", encoding="utf-8")
    compiler = prepare_compiler(specifications["inno"], tools)
    manifest["compiler_executable_sha256"] = sha256(compiler)
    output = work / "dist"
    output.mkdir(exist_ok=True)
    compile_result = subprocess.run([str(compiler), f"/DPayloadDir={payload}", f"/DReleaseOutput={output}",
                    f"/DReleaseVersion={VERSION}",
                    f"/DInstallFileList={install_files}",
                    str(payload / "scripts/release/windows.iss")], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    print(compile_result.stdout)
    compile_result.check_returncode()
    engine = re.search(r"Compiler engine version:\s*Inno Setup\s+([0-9.]+)", compile_result.stdout)
    if engine is None or engine.group(1) != specifications["inno"]["version"]:
        raise ValueError("Compiler engine output does not match the pinned tool version")
    manifest["compiler_version"] = engine.group(1)
    manifest["install_file_list_sha256"] = sha256(install_files)
    installer = output / ASSET
    digest = sha256(installer)
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {ASSET}\n", encoding="ascii")
    manifest.update({"installer": ASSET, "installer_sha256": digest,
                     "installer_bytes": installer.stat().st_size,
                     "payload_files": sum(path.is_file() for path in payload.rglob("*"))})
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"installer": str(installer), "payload": str(payload), **manifest}, indent=2))
    return installer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", default="HEAD")
    parser.add_argument("--work-dir", type=Path, default=ROOT / f".dataforge/releases/v{VERSION}/build")
    args = parser.parse_args()
    build(args.source_ref, args.work_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
