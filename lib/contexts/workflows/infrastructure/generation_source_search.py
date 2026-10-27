"""SQLite FTS5/BM25 navigation over the task's authenticated source scope."""
from collections import OrderedDict
import re
import sqlite3
import threading

from lib.domain.knowledge import search_tokens
from ..domain.reasoning_checklist import checklist_digest


_INDEXES = OrderedDict()
_LOCK = threading.RLock()


def _query_terms(query):
    # Keep decimal numbers and hyphenated metric names intact. Splitting 44.4
    # into 44 and 4 retrieves unrelated figure numbers instead of the value.
    terms = []
    for phrase, term in re.findall(r'"([^"\n]+)"|([\w]+(?:[.-][\w]+)*)', query.casefold()):
        if phrase:
            terms.append(phrase)
        else:
            terms.extend(search_tokens(term) if re.search(r"[\u3400-\u9fff]", term) else [term])
    return list(dict.fromkeys(terms))[:64]


def search_generation_sources(passages, query, limit):
    tokens = _query_terms(query)
    if not tokens:
        return []
    # Scope/text/location all bind the index; a selected range never inherits a
    # larger source's cached results. FTS only ranks, it never edits quotations.
    identity = checklist_digest(passages)
    with _LOCK:
        if identity not in _INDEXES:
            database = sqlite3.connect(":memory:", check_same_thread=False)
            database.execute("CREATE VIRTUAL TABLE passages USING fts5(terms, source_id UNINDEXED, start UNINDEXED, length UNINDEXED, tokenize = \"unicode61 tokenchars '.-'\")")
            for passage in passages:
                text = passage["text"]
                for start in range(0, len(text), 1000):
                    end = min(start + 1400, len(text))
                    chunk = text[start:end]
                    chinese = " ".join(token for token in search_tokens(chunk) if re.search(r"[\u3400-\u9fff]", token))
                    database.execute("INSERT INTO passages VALUES (?,?,?,?)",
                        (chunk + "\n" + chinese, passage["id"], start, end - start))
            _INDEXES[identity] = database
            while len(_INDEXES) > 4:
                _, old = _INDEXES.popitem(last=False)
                old.close()
        database = _INDEXES[identity]
        _INDEXES.move_to_end(identity)
        expression = " OR ".join('"' + t + '"' for t in tokens)
        matches = database.execute("SELECT source_id,start,length FROM passages WHERE passages MATCH ? ORDER BY bm25(passages),rowid LIMIT ?",
                                   (expression, limit)).fetchall()
    return [{"source_id": identity, "start": start, "length": length} for identity, start, length in matches]
