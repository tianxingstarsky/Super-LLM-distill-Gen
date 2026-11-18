"""Classify source material for this general-purpose reasoning experiment before encoding it."""

from functools import lru_cache
import hashlib
from html import unescape
from pathlib import Path
import re
import unicodedata


def vocabulary(words):
    # Bound words so ordinary identifiers and mathematical substrings do not trigger a hold.
    return re.compile(r"\b(?:" + "|".join(words.splitlines()) + r")\b", re.IGNORECASE)


CYBER = vocabulary(r"""remote code execution
arbitrary code execution
command injection
sql injection
cross.site scripting
privilege escalation
buffer overflow exploit
reverse shell
shellcode
malware
ransomware
botnet
backdoor
rootkit
keylogger
phishing
unpatched vulnerabilit\w*
zero.day exploit
exploit payload
penetration test\w*
exploiting (?:a |an |the )?(?:vulnerabilit\w*|system|server)
(?:bypass|evade|defeat|bypassing) (?:the |a |an )?(?:authentication|authorization|login|sandbox|antivirus|firewall|input validation)
(?:steal|harvest|exfiltrate|dump) (?:user |the )?(?:passwords|credentials|tokens)
unauthorized access
CVE.\d{4}.\d{4,}""")

BIOLOGY = vocabulary(r"""virus(?:es)?
viral
virolog\w*
virulence
pathogen\w*
infectious
bacteriophag\w*
plasmid\w*
recombinant
crispr
genome editing
gene editing
gain.of.function
toxin\w*
DNA synthesis
nucleic acid synthesis
select agent
aerosol dissemination
bioweapon\w*""")

WEAPONS = vocabulary(r"""explosiv\w*
detonat\w*
firearms?
ammunition
gunpowder
warhead\w*
ballistic missile\w*
improvised weapon\w*
weaponiz\w*
grenades?
silencers?
incendiary
bomb.making
homemade bomb\w*
poison gas
mass shooting
assassinat\w*
active shooter""")

SENSITIVE_TOPIC = re.compile(
    r"\b(?:weapon|rifle|handgun|pistol|sniper|exploit|payload|bomb|intrusion|"
    r"cybersecurity|biosafety|synthetic biology|pathogen engineering)s?\b", re.I
)
OPERATIONAL = re.compile(
    r"\b(?:build|construct|fabricate|attack|target|fire|shoot|kill|bypass|evade|"
    r"vulnerab\w*|assembling|step.by.step|recipe|synthesi[sz]e|deploy|trigger)\b", re.I
)
SECURITY_CONTEXT = re.compile(
    r"\b(?:web server|untrusted user|access control|obfuscat\w*|attack vector|adversarial payload|"
    r"memory corruption|operating.system command|attacker|mechanism of infection|gene synthesis|"
    r"blast radius|lethal dosage|munitions|armed assault|weaponry)\b|"
    r"漏洞利用|免杀|恶意载荷|生物武器|炸药|致病性增强", re.I
)
BENIGN_TASK = re.compile(
    r"\b(?:matrix|matrices|permutation|integer|integers|floating.point|factorial|prime numbers?|"
    r"triangles?|polynomials?|geometry|algebra|equations?|probability|combinatorics|calculus|"
    r"derivatives?|integrals?|sequence|theorem|array|string|grid|puzzle|sorting|patterns?|"
    r"code.golf|shortest code|characters|graphs?|algorithm|substrings?|subarrays?|"
    r"noise.cancell?ing|arithmetic|coordinates?|digits|recursi\w*|binary tree|"
    r"momentum|thermodynamic\w*|harmonic oscillator|angular velocity|electric field)\b", re.I
)


@lru_cache(maxsize=1)
def scope_sha256():
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def review_record(record):
    parts = [record.get(key) for key in ("question", "reasoning", "answer")]
    if any(not isinstance(part, str) for part in parts) or not parts[0].strip():
        return "unclassified_input"
    body = unicodedata.normalize("NFKC", unescape("\n".join(parts))).casefold()
    body = " ".join("".join(c for c in body if unicodedata.category(c) != "Cf").split())
    for classification, terms in (
        ("security_review", CYBER), ("biological_review", BIOLOGY), ("weapons_review", WEAPONS)
    ):
        if terms.search(body):
            return classification
    if SENSITIVE_TOPIC.search(body) and OPERATIONAL.search(body):
        return "needs_context_review"
    if SECURITY_CONTEXT.search(body):
        return "needs_context_review"
    source = record.get("source") or {}
    domain = str(source.get("domain", "")).casefold()
    if domain in {"math", "mathematics", "code", "coding", "science", "stem"} and BENIGN_TASK.search(parts[0]):
        return None
    return "unclassified_task"
