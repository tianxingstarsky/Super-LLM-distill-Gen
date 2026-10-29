"""Select the independent DSH converter; core workflows only see its public contract."""
from pathlib import Path
import shutil

from lib.contexts.workflows.application.conversation_import import ImportConversations
from lib.contexts.workflows.infrastructure.conversation_import import ExternalConversationImporter
from .local_inputs import local_input_application


def conversation_import_application(root: Path | None = None, *, command: tuple[str, ...] | None = None) -> ImportConversations:
    """Build DSH import preview and confirmation against a persistent local cache.

    The repository root uses the active cache paths; an explicit alternative
    root isolates converter and cache locations. Converter availability is
    checked when importing, so constructing an import page stays offline.
    """
    repository_root = Path(__file__).resolve().parents[2]
    import_root = Path(root) if root is not None else repository_root
    if command is None:
        node = shutil.which("node") or ""
        converter_script = import_root / "plugins" / "dsh-dataforge" / "import-conversations.mjs"
        command = (node, str(converter_script))
    from lib import workspace

    storage_root = None if import_root.resolve() == repository_root else import_root
    cache_directory = (Path(workspace.SEEDS_DIR).parent / "conversation-imports"
                       if storage_root is None else import_root / "data" / "conversation-imports")
    return ImportConversations(ExternalConversationImporter(import_root, command, local_input_application(storage_root),
        cache_directory=cache_directory, suffixes=(".jsonl", ".jsonl.zstd", ".db", ".sqlite", ".sqlite3")))
