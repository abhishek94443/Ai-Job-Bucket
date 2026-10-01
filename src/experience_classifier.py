import re
from typing import Optional, List, Tuple
from .models import ExperienceLevel, ExperienceEligibility


class ExperienceClassifier:
    """
    Classifies job experience requirements based on explicit evidence in job title and description.
    Rules:
    - Never uses title alone if description contradicts it.
    - Uses explicit numerical year ranges when available.
    - If contradictory (e.g. title says Junior but text says 8+ years) or insufficient, returns UNKNOWN.
    - Candidate has ~2 years experience:
        - 0-1 years / Fresher -> ENTRY_LEVEL (MATCH)
        - 1-3 years -> JUNIOR (MATCH)
        - Requirements of exactly 4 years -> MID_LEVEL (STRETCH)
        - Requirements > 4 years -> SENIOR / MID_LEVEL (OVER_LIMIT)
        - Missing experience -> UNKNOWN
    """

    @classmethod
    def extract_years_requirement(cls, text: str) -> List[Tuple[float, float]]:
        """
        Finds patterns like '2-4 years', '2 to 5 years', '3+ years', 'min 2 yrs', 'up to 3 years', etc.
        Distinguishes total work experience from notice periods, graduation years, or unrelated numbers.
        Returns list of (min_years, max_years).
        """
        if not text:
            return []

        clean_text = text.lower()

        # Check for explicit "experience not required" / "no experience required"
        if re.search(r"\b(?:no|not)\s+(?:prior\s+)?experience\s+required\b", clean_text) or re.search(r"\b0\s*(?:-|to)?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+relevant|\s+work)?\s+experience\b", clean_text):
            return [(0.0, 1.0)]

        ranges: List[Tuple[float, float]] = []

        # Pattern 0: "up to (\d+) years"
        p0 = re.findall(r"up\s+to\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", clean_text)
        for max_y in p0:
            try:
                val = float(max_y)
                if val <= 25.0:
                    ranges.append((0.0, val))
            except ValueError:
                pass

        # Pattern 1: (\d+)\s*(?:-|to)\s*(\d+)\+?\s*(?:years?|yrs?)
        p1 = re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)", clean_text)
        for min_y, max_y in p1:
            try:
                min_v = float(min_y)
                max_v = float(max_y)
                # Filter out obvious false positives (e.g. graduation years 2020-2024 or notice periods 30-60 days)
                if min_v <= 25.0 and max_v <= 30.0 and min_v <= max_v:
                    ranges.append((min_v, max_v))
            except ValueError:
                pass

        # Pattern 2: (\d+)\+\s*(?:years?|yrs?) e.g. "4+ Years", "5+ yrs"
        p2 = re.findall(r"(\d+(?:\.\d+)?)\+\s*(?:years?|yrs?)", clean_text)
        for y in p2:
            try:
                val = float(y)
                if val <= 25.0 and not any(r[0] <= val <= r[1] for r in ranges):
                    ranges.append((val, val + 2.0))
            except ValueError:
                pass

        # Pattern 3: (minimum|at least|min)\s*(\d+)\s*(?:years?|yrs?)
        p3 = re.findall(r"(?:minimum|at\s+least|min)\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", clean_text)
        for y in p3:
            try:
                val = float(y)
                if val <= 25.0 and not any(r[0] <= val <= r[1] for r in ranges):
                    ranges.append((val, val + 2.0))
            except ValueError:
                pass

        # Pattern 4: (\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s*|work\s*|hands-on\s*|[a-z\s-]{1,25})?\s*experience
        p4 = re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[a-z\s-]{1,25})?\s+experience", clean_text)
        for y in p4:
            try:
                val = float(y)
                if val <= 25.0 and not any(r[0] <= val <= r[1] for r in ranges):
                    ranges.append((val, val + 2.0))
            except ValueError:
                pass

        return ranges

    @classmethod
    def classify(cls, title: str, description: Optional[str] = None) -> Tuple[ExperienceLevel, Optional[str]]:
        t_clean = (title or "").lower()
        d_clean = (description or "").lower()
        full_text = f"{t_clean}\n{d_clean}"

        # 1. Check for explicit numerical year mentions in description or title
        ranges = cls.extract_years_requirement(full_text)
        
        # Check contradictory numerical ranges (e.g. one mentions 1-2 years, another mentions 8-10 years)
        if len(ranges) >= 2:
            min_all = min(r[0] for r in ranges)
            max_all = max(r[1] for r in ranges)
            if min_all <= 2.0 and max_all >= 7.0:
                return ExperienceLevel.UNKNOWN, f"Contradictory experience requirements found in text ({ranges})"

        # Check contradiction between title and explicit numbers:
        # e.g., Title says "Junior" or "Fresher", but description explicitly demands 5+ years
        is_title_junior = any(w in t_clean for w in ["junior", "jr.", "jr ", "entry level", "fresher", "intern"])
        is_title_senior = any(w in t_clean for w in ["senior", "sr.", "sr ", "lead", "staff", "principal", "architect"])

        if ranges:
            primary_min, primary_max = ranges[0]

            if is_title_junior and primary_min >= 4.0:
                return ExperienceLevel.UNKNOWN, f"Contradiction: Title suggests Junior/Fresher but text requires {primary_min}+ years"
            if is_title_senior and primary_max <= 2.0:
                return ExperienceLevel.UNKNOWN, f"Contradiction: Title suggests Senior but text requires only {primary_min}-{primary_max} years"

            if primary_min <= 1.0 and primary_max <= 1.5:
                return ExperienceLevel.ENTRY_LEVEL, f"Explicit {primary_min}-{primary_max} yrs requirement"
            elif primary_min <= 3.0:
                return ExperienceLevel.JUNIOR, f"Explicit {primary_min}-{primary_max} yrs requirement"
            elif primary_min < 5.0:
                # 4+ years or 3-4 years is a stretch for ~2 YOE candidate
                return ExperienceLevel.MID_LEVEL, f"Explicit {primary_min}+ yrs requirement (stretch for ~2 YOE)"
            else:
                return ExperienceLevel.SENIOR, f"Explicit {primary_min}+ yrs requirement (exceeds target experience)"

        # 2. Check title cues ONLY if no explicit numbers found
        is_title_senior = any(w in t_clean for w in ["senior", "sr.", "sr ", "lead", "staff", "principal", "architect"])
        is_title_junior = any(w in t_clean for w in ["junior", "jr.", "jr ", "entry level", "associate", "intern", "fresher", "trainee"])

        if is_title_senior and is_title_junior:
            return ExperienceLevel.UNKNOWN, "Contradictory title experience indicators"

        if is_title_senior:
            return ExperienceLevel.SENIOR, "Title indicates Senior/Lead role (no explicit years stated)"
        if is_title_junior:
            return ExperienceLevel.JUNIOR, "Title indicates Junior/Associate role (no explicit years stated)"

        # 3. Text cues without numbers
        if "fresher" in d_clean or "recent graduate" in d_clean or "entry level" in d_clean:
            return ExperienceLevel.ENTRY_LEVEL, "Description indicates fresher/entry level"

        return ExperienceLevel.UNKNOWN, "No conclusive experience level requirements found"

    @classmethod
    def evaluate_eligibility(
        cls,
        exp_level: ExperienceLevel,
        exp_reason: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None
    ) -> Tuple[ExperienceEligibility, str]:
        """
        Evaluates explicit experience eligibility for ~2 YOE candidate:
        - MATCH: Requirements of 0-3 years (ENTRY_LEVEL / JUNIOR)
        - STRETCH: Borderline (e.g. exactly 4 years, 3-4 years, 2-5 years)
        - OVER_LIMIT: Explicit minimum requirements > 4 years, 5+ years, 7+, Senior/Lead
        - UNKNOWN: Missing or contradictory experience in listing and description
        """
        full_text = f"{(title or '').lower()}\n{(description or '').lower()}"
        ranges = cls.extract_years_requirement(full_text)
        reason_str = exp_reason or ""

        if ranges:
            primary_min, primary_max = ranges[0]

            # Rule 1: Explicit minimum requirement > 4 years -> OVER_LIMIT
            if primary_min > 4.0:
                return ExperienceEligibility.OVER_LIMIT, f"Requires {primary_min}+ years (exceeds hard limit of 4 years)"

            # Rule 3: Requirements of exactly 4 years -> STRETCH (borderline review)
            if primary_min == 4.0:
                return ExperienceEligibility.STRETCH, "Requires 4 years (borderline stretch for ~2 YOE candidate; review required)"

            # Wide stretch ranges like 2-5 years or 3-5 years where max > 4.0
            if primary_max > 4.0:
                return ExperienceEligibility.STRETCH, f"Requires {primary_min}-{primary_max} years (upper range exceeds 4 years)"

            # Rule 2: Requirements of 0-3 years -> MATCH
            if primary_min <= 3.0:
                return ExperienceEligibility.MATCH, f"Compatible experience requirement ({primary_min}-{primary_max} years)"

        # Title cues when ranges are absent
        if exp_level == ExperienceLevel.SENIOR:
            return ExperienceEligibility.OVER_LIMIT, f"Senior / Lead role exceeds target experience: {reason_str}"

        if exp_level in (ExperienceLevel.JUNIOR, ExperienceLevel.ENTRY_LEVEL):
            return ExperienceEligibility.MATCH, f"Junior / Entry level compatible with ~2 YOE: {reason_str}"

        if exp_level == ExperienceLevel.MID_LEVEL:
            return ExperienceEligibility.STRETCH, f"Mid-level role without explicit range: {reason_str}"

        # Rule 4: Missing experience in both listing and description -> UNKNOWN
        return ExperienceEligibility.UNKNOWN, "Experience requirements unknown; retained for manual review"

