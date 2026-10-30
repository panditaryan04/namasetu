"""Rule-based FHIR R4 ConceptMap equivalence for each candidate (documented in docs/matching.md).

Rules, in order:
1. Deterministic baseline (official crosswalk / approved mapping) -> its recorded relationship.
2. The best-ranked candidate that *names* the source (exact, transliterated or near-identical term,
   with agreeing entity type) is the anchor -> "equivalent".
3. A candidate that is an ancestor of the anchor -> "wider"; a descendant of the anchor -> "narrower".
4. With no anchor: a candidate that names an ancestor of the NAMASTE source -> "wider"
   (e.g. Vataja Jvara -> Fever disorder); one that names a descendant -> "narrower".
5. Otherwise -> "relatedto".
"""

from __future__ import annotations

from typing import Literal

from app.engine.matcher import Candidate, Matcher, SourceProfile

Relationship = Literal["equivalent", "wider", "narrower", "relatedto"]


def infer_relationships(
    ranked: list[Candidate], profile: SourceProfile, matcher: Matcher
) -> list[Relationship]:
    index = matcher.index
    anchor = next(
        (
            c
            for c in ranked
            if c.stage0 is None
            and c.identity
            and (profile.entity_type is None or profile.entity_type == c.target.entity_type)
        ),
        None,
    )
    out: list[Relationship] = []
    for c in ranked:
        if c.stage0 is not None:
            out.append(c.stage0_relationship or "equivalent")  # type: ignore[arg-type]
        elif anchor is not None and c is anchor:
            out.append("equivalent")
        elif anchor is not None and c.target.code in anchor.target.ancestors:
            out.append("wider")
        elif anchor is not None and anchor.target.code in c.target.ancestors:
            out.append("narrower")
        elif profile.ancestor_surfaces and matcher.identity_with(profile.ancestor_surfaces, c.target):
            out.append("wider")
        elif profile.descendant_surfaces and matcher.identity_with(profile.descendant_surfaces, c.target):
            out.append("narrower")
        else:
            out.append("relatedto")
    _ = index  # rules only need hierarchy already denormalised onto the records
    return out
