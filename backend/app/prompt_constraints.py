RUBRIC_REFINEMENT_PRESERVATION_RULES = (
    "When returning update_criterion to refine an existing criterion, preserve its existing scoring points "
    "and deduction rules by default. Remove an existing scoring point or deduction rule only when the "
    "teacher's grading behavior provides a substantive reason to change the grading standard. In that case, "
    "explicitly identify every removed point_id or rule_id and explain in rationale why each removal is "
    "necessary. A rule being irrelevant to, unmentioned in, or untriggered by the current report is never a "
    "valid reason to remove it. Retained or modified scoring points and deduction rules must keep their "
    "existing point_id and rule_id. Preserve the criterion's existing section because it is an organizational "
    "grading category, not evidence location and not part of the scoring rule. "
)


RUBRIC_CATEGORY_RULES = (
    "The section field is required and means a semantic grading category, never a report heading or evidence "
    "location. Prefer reusing a suitable category already present in current_rubric. Avoid creating a singleton "
    "category for one newly induced criterion when an existing broader category is suitable. "
)
