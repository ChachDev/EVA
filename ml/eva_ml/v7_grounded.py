"""Use the existing corroboration gate before combining the pinned v7 and EVA readings."""
from .interaction_guard import GuardedInteractionReviewer
from .issai import digest
from .v7_fusion import V7Fusion


class V7Grounded(V7Fusion):
    def __init__(self, primary, archive):
        super().__init__(primary, archive)
        self.guard = GuardedInteractionReviewer(self.engine, apply="both")
        self.revision = digest([self.revision, self.guard.guarded_hash, "v7-grounded-v1"])

    def combine(self, req, base, v7, interaction):
        corroborated = self.guard.gate(req, base, interaction)
        return super().combine(req, base, v7, corroborated)
