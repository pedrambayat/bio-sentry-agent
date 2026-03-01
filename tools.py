"""
tools.py  —  Bio-Sentry Agent Tools
====================================
Two tools:
  1. biosecurity_screener  – runs homology check, returns a JSON report.
  2. synthesis_order       – places a synthesis order.  Its *arguments include
                             the homology score* so the Cedar policy can read
                             context.parameters.homology_score and make a
                             deterministic permit / deny decision.
"""

from __future__ import annotations

import json
from typing import Annotated

from Bio import Align
from Bio.Align import substitution_matrices
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Threat Database
# ---------------------------------------------------------------------------
THREAT_DB: dict[str, str] = {
    "Ricin_A_Chain": (
        "IFPKQYPIINFTTAGATVQSYTNFIRAVRGRLTTGADVRHEIPVLPNRVGLPINQRFILVELSNHAELSVTLALD"
        "VTNAYVVGYRAGNSAYFFHPDNQEDAEAITHLFTDVQNRYTFAFGGNYDRLEQLAGNLRENIELGNGPLEAISAL"
        "ALYYYSTGGTQLPTLARSFIICIQMISEAARFQYIEGEMRTRIRYNRRSAPDPSVITLENSWGRLSTAIQESNQG"
        "AFASPIQLQRRNGSKFSVYDVSILIPIIALMVYRCAPPPSSQF"
    ),
    "Abrin_A_Chain": (
        "IIFPKQYPIINFTTAGATVQSYTNFIRAVRGRLTTGADVRHEIPVLPNRVGLPINQRFILV"
        "ELSNHAELSVTLALDVTNAYVVGYRAGNSAYFFHPDNQEDAEAITHLFTDVQNRYTFAFGG"
        "NYDRLEQLAGNLRENIELGNGPLEAISALALYYYSTGGTQLPTLARSFIICIQMISEAARFQ"
        "YIEGEMRTRIRYNRRSAPDPSVITLENSWGRLSTAIQESNQGAFASPIQLQRRNGSKFSVYD"
        "VSILIPIIALMVYRCAPPPSSQF"
    ),
    "Shiga_Toxin_1_A": (
        "MEFNNTIEMLKDLFAELTQEHFKTLKQSGYDLVEIMKQFGKDAVSVFVRGEKFLHSNLNIDL"
        "SPSIPDDEILRTVAVGISNTLNPFIHSYTTMLNLYRAIVSSNTGLGTQSTKTLAEIMGVTYY"
        "FSNKMYFNNTAAAFKDQGFISSFLRGMELTDPQDLQPFLDIVAKELAKKYGVTPLEIQGTNN"
        "ASQFNPDPSSSSIFMSDKNVKVIMKGNQARQLFGEGTFSVSMAMRNSMQHKEPQVITGLKSR"
        "DAGLSSNQSTPQEFLRSLVKQ"
    ),
}

HOMOLOGY_THRESHOLD = 0.40  # The red line


def _make_aligner() -> Align.PairwiseAligner:
    aligner = Align.PairwiseAligner()
    aligner.mode = "local"
    aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
    aligner.open_gap_score = -10
    aligner.extend_gap_score = -0.5
    return aligner


_ALIGNER = _make_aligner()


def _calculate_max_homology(candidate: str) -> tuple[float, str]:
    """Return (normalized_score, threat_name) using Smith-Waterman / BLOSUM62.

    Score is normalized by the self-alignment score of the candidate so the
    result is in [0, 1]: 1.0 = perfect local match to a threat fragment.
    """
    candidate = candidate.upper().strip()
    if not candidate:
        return 0.0, "none"

    self_score = _ALIGNER.score(candidate, candidate)
    if self_score <= 0:
        return 0.0, "none"

    best_score = 0.0
    best_name = "none"
    for name, threat_seq in THREAT_DB.items():
        raw = _ALIGNER.score(candidate, threat_seq.upper())
        normalized = raw / self_score
        if normalized > best_score:
            best_score = normalized
            best_name = name
    return best_score, best_name


# ---------------------------------------------------------------------------
# Tool 1 — Biosecurity Screener  (NOT wrapped by Sondera; runs first)
# ---------------------------------------------------------------------------
@tool
def biosecurity_screener(
    sequence: Annotated[str, "The amino-acid sequence to screen for homology with regulated toxins."],
) -> str:
    """
    Screen an amino-acid sequence against the threat database using Smith-Waterman
    local alignment with BLOSUM62 substitution matrix.  Returns a JSON report containing:
      - max_score      : float [0, 1]  — similarity ratio to the closest threat
      - homology_score_int : int       — score × 1000 (used in Cedar policy as Long)
      - threat_name    : str           — name of the closest match
      - verdict        : str           — SAFE or FLAGGED

    You MUST pass homology_score_int and threat_name to synthesis_order when ordering.
    """
    max_score, threat_name = _calculate_max_homology(sequence)
    homology_score_int = int(max_score * 1000)  # e.g. 0.82 → 820
    verdict = "FLAGGED" if max_score > HOMOLOGY_THRESHOLD else "SAFE"

    report = {
        "max_score": round(max_score, 4),
        "homology_score_int": homology_score_int,
        "threat_name": threat_name,
        "verdict": verdict,
        "threshold": HOMOLOGY_THRESHOLD,
        "sequence_length": len(sequence),
    }
    return json.dumps(report)


# ---------------------------------------------------------------------------
# Tool 2 — Synthesis Order  (WRAPPED by SonderaHarnessMiddleware)
#
# CRITICAL DESIGN NOTE:
# The homology_score_int and threat_name are explicit parameters here so that
# the Cedar policy can read them from context.parameters.  The agent is
# instructed (via system prompt) to always call biosecurity_screener first and
# pass its output into this tool.  This is the "no soft alignment" guarantee:
# even if the LLM lies about the score, a correctly structured policy would
# enforce the check server-side — and for the hackathon demo we trust the
# screener output flow (which is auditable in the trajectory).
# ---------------------------------------------------------------------------
@tool
def synthesis_order(
    sequence: Annotated[str, "The amino-acid sequence to synthesize."],
    provider: Annotated[str, "The synthesis provider (e.g. 'Twist', 'IDT')."],
    homology_score_int: Annotated[
        int,
        "Integer homology score from biosecurity_screener (score × 1000). "
        "E.g. 0.82 → 820.  MUST be provided before ordering.",
    ],
    threat_name: Annotated[
        str,
        "Name of the closest threat match from biosecurity_screener. "
        "Pass 'none' if no significant match.",
    ],
    human_approved: Annotated[
        bool,
        "Set to True only after explicit user confirmation for amber-tier sequences "
        "(homology_score_int 251–400).  Must be False for green-tier (≤ 250).  "
        "Irrelevant for red-tier (> 400) which is blocked unconditionally.",
    ] = False,
) -> str:
    """
    Place a protein synthesis order with an external provider.
    biosecurity_screener MUST be called first; pass its homology_score_int
    and threat_name directly into this tool.  The Cedar policy will deny
    this call if homology_score_int > 400 (red tier), or if
    homology_score_int is 251–400 and human_approved is False (amber tier).
    """
    # If we reach this function body, the Cedar policy has already ALLOWED it.
    tier = "GREEN" if homology_score_int <= 250 else "AMBER (human-approved)"
    return (
        f"✅  Synthesis order PLACED successfully.\n"
        f"   Provider  : {provider}\n"
        f"   Sequence  : {sequence[:40]}{'...' if len(sequence) > 40 else ''}\n"
        f"   Homology  : {homology_score_int / 1000:.3f}  [{tier}]\n"
        f"   Threat hit: {threat_name}"
    )
