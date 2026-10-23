"""Select the independent DSH converter; core workflows only see its public contract."""
from pathlib import Path
import shutil

from lib.contexts.workflows.application.conversation_import import ImportConversations
from lib.contexts.workflows.infrastructure.conversation_import import ExternalConversationImporter
from .local_inputs import local_input_application


def conversation_import_application(root: Path | None = None, *, command: tuple[str, ...] | None = None) -> ImportConversations:
    repository = Path(__file__).resolve().parents[2]
    root = Path(root) if root is not None else repository
    if command is None:
        node = shutil.which("node") or ""
        script = root / "plugins" / "dsh-dataforge" / "import-conversations.mjs"
        command = (node, str(script))
    from lib import workspace
    storage_root = None if root.resolve() == repository else root
    cache_directory = (Path(workspace.SEEDS_DIR).parent / "conversation-imports"
                       if storage_root is None else root / "data" / "conversation-imports")
    return ImportConversations(ExternalConversationImporter(root, command, local_input_application(storage_root),
        cache_directory=cache_directory, suffixes=(".jsonl", ".jsonl.zstd", ".db", ".sqlite", ".sqlite3")))
