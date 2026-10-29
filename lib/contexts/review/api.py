"""Public review decisions, bounded repair and preference-validation use cases.

The *ReviewApplication services expose human queues and version-bound decisions.
ReviewCandidate scores or repairs one candidate; ReviewGeneratedCollections
coordinates streaming collections; ExecuteRepairBranch authenticates selected
parent versions before repair. ReviewReworkApplication stores rejected work.

Preference assessment, format lineage, claim windows and numeric reference
verification have separate interfaces below. A numeric match or an authorized
format view is not a general quality approval. Concrete adapters live outside
this public module and are selected by lib.bootstrap.
"""
from .application.agent_review import AgentReviewApplication
from .application.ports import AgentReviewPort

from .application.sft_review_service import SftReviewApplication
from .application.corpus_review_service import CorpusReviewApplication
from .application.preference_review_service import PreferenceReviewApplication

# Automatic review and persistent rework use the same candidate identities.
from .application.review_candidate import ReviewCandidate

from .application.review_collections import ReviewGeneratedCollections
from .application.execute_repair import ExecuteRepairBranch
from .application.rework_collection import ReviewReworkApplication

# These bounded checks support review; none independently publishes a dataset.
from .application.review_claim_windows import ReviewClaimWindows
from .application.authorize_format_view import AuthorizeTrainingFormatView
from .application.assess_preference_pair import AssessPreferencePair
from .application.review_preference_view import ReviewPreferenceView
from .application.verify_reference_answer import VerifyReferenceAnswer
