# Windows preview distribution

This directory builds the per-user Windows x64 online installer. It does not
contain product workflow logic. The existing Python launchers remain the entry
points. No system Python, administrator account, or Node installation is needed.
The first installation needs an internet connection.

## Files and boundaries

- `build_windows.py` reads one Git tree, creates an isolated payload, verifies
  downloaded build tools, and invokes Inno Setup. It never copies the working
  `.venv`, ignored files, local configuration, or dirty submodules.
- `tools.json` pins the official uv and Inno Setup downloads and their SHA256.
- `requirements-windows-x64.lock` is the hashed Python 3.11 / Windows x64 lock.
  Its package versions were constrained to the tested main environment. It does
  not include optional Docling or model weights. `requirements.txt` remains the
  declaration used to regenerate it.
- `validated-windows-x64.txt` is the version constraint input used to reproduce
  this lock from a clean checkout. Regenerate with the command in the lock header.
- `setup_runtime.cmd` uses the bundled Python 3.11.9 archive and uv to prepare
  the private environment, then syncs the exact hashed dependencies.
  It checks the environment before recording success. Downloads and retries use
  the application-local cache. It does not change system Python or `PATH`.
  The standard `UV_CACHE_DIR` environment variable is respected when already set.
  This can reuse a shared cache without copying another environment. Uninstall
  removes only the app-local cache; an external cache is never removed.
- `build-constraints.txt` fixes the isolated setuptools version used for small
  pure-Python source dependencies. Their archives are hashed in the runtime lock.
  They do not require a C/C++ compiler.
- `verify_runtime.py` checks every immutable file in the generated `PAYLOAD.json`
  hash map, installed imports, source identity, and UI assets. Configurations and
  supplied data examples are recorded as initial seeds. Required configurations
  must be present, but user edits are allowed. Examples can be changed or removed.
  Setup seeds missing files and preserves existing ones.
  User data is not part of the immutable-file map.
- `start_browser.vbs` opens the existing browser launcher. Native desktop and
  browser shortcuts use the same saved work.
- `windows.iss` supplies the installer and uninstaller. User-generated work and
  configuration are kept on uninstall. The app-local environment and cache are
  removed. The installer is unsigned unless the publisher signs it separately.
  For owned installations with an existing uninstaller and file register,
  immutable files are copied only when their SHA256 differs or they are absent,
  using Inno Setup's official `Check` and `GetSHA256OfFile` functions. Changed
  files still copy, and complete runtime/payload verification still runs.
  See [Check functions](https://jrsoftware.org/ishelp/topic_scriptcheck.htm) and
  [GetSHA256OfFile](https://jrsoftware.org/ishelp/topic_isxfunc_getsha256offile.htm).

## Build

From the repository, using the existing development environment:

```powershell
.venv\Scripts\python.exe scripts\release\build_windows.py --source-ref HEAD
```

`--source-ref` may name a commit or an explicitly prepared Git tree. Final release
builds must use the committed release revision. Builds and tools stay under
`.dataforge/releases/v0.0.1/`. The output includes
`ShuJian-Cube-0.0.1-Windows-x64-Setup.exe`, `SHA256SUMS.txt`, and a build manifest.
The manifest records source tree, commit, package lock, and tool hashes.

Inno Setup is installed without elevation into this ignored build directory.
Sources: [Inno Setup](https://jrsoftware.org/isdl.php),
[uv installation](https://docs.astral.sh/uv/getting-started/installation/), and
[uv-managed Python](https://docs.astral.sh/uv/guides/install-python/).
The bundled Python is extracted from Astral's exact `python-build-standalone`
archive, pinned by URL and SHA256 in `tools.json`. Its license is retained in
`.runtime/python/cpython-3.11.9-windows-x86_64-none/LICENSE.txt`. Setup does not run
`uv python install`, create Python-version junctions, register Python, or change
`PATH`. It uses the bundled interpreter's absolute path with Python downloads
disabled. This avoids Windows installer child-process junction restrictions.
The archive's timestamp-based `.pyc` files are omitted. Python regenerates these
caches, so they are not immutable release files. Python source and DLLs stay in
the complete hash map.

## Install and verify

Run the EXE and choose a writable local folder. Setup uses the bundled Python,
downloads dependencies, and writes `data/output/installation.log`. A Git checkout or a
foreign `.venv` / `.runtime` without this product's manifest is rejected before
files are copied. Existing owned installations can be upgraded. A failed setup exits
with an error; run setup again to retry. The **Repair runtime** shortcut can also
rerun the dependency setup. Model keys are entered in workflow nodes after
installation. No model API is called by setup or runtime verification.

The desktop shortcut requires Microsoft Edge WebView2 Runtime. The **Browser**
shortcut works without WebView2. Optional local Docling models and legacy
OpenCUA `gui-cot` are not included. See the main documentation for their separate
setup. External DSH integration also remains an independently installed plugin.

Release validation uses an empty installation directory, including a path with
spaces and Chinese characters. Check runtime verification, actual browser
startup, repeat setup, and an installed-file manifest. This verifies installation,
not semantic quality of generated training data. Do not run the entire model test
suite merely because packaging files changed.
