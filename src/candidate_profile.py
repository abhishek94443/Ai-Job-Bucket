from typing import List, Dict, Optional, Set, Any
from pydantic import BaseModel, Field


class CandidateProfile(BaseModel):
    """
    Candidate profile separating:
    1. Resume-derived facts (skills, demonstrated experience years).
    2. Inferred capabilities (engineering competencies inferred from projects).
    3. User-configured search preferences (target titles, location filters, hard experience cutoffs).
    """
    name: str = "Candidate"

    # 1. Resume-derived facts
    total_experience_years: Optional[float] = None
    experience_confirmed: bool = False  # True only when user verifies/confirms experience years

    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    apis_and_auth: List[str] = Field(default_factory=list)
    messaging_and_cache: List[str] = Field(default_factory=list)
    databases: List[str] = Field(default_factory=list)
    devops_and_tools: List[str] = Field(default_factory=list)

    # 2. Inferred capabilities
    inferred_capabilities: List[str] = Field(default_factory=list)

    # 3. User-configured search preferences (editable by candidate, never assumed as resume facts)
    target_title: Optional[str] = None
    experience_range_min: Optional[float] = None
    experience_range_max: Optional[float] = None
    hard_limit_years: Optional[float] = None
    target_cities: List[str] = Field(default_factory=list)
    remote_preference: str = "flexible"

    # Preference provenance tracking ('editable_default', 'user_configured', 'seed_default')
    preferences_source: str = "editable_default"

    # 4. Rich semantic analysis payload and provenance ('AI_LIVE', 'DETERMINISTIC_FALLBACK', 'USER_EDITED')
    analysis_provenance: str = "editable_default"
    resume_analysis: Optional[Dict[str, Any]] = None

    @property
    def target_experience_years(self) -> Optional[float]:
        """Backward-compatible accessor for total_experience_years."""
        return self.total_experience_years

    @property
    def is_experience_known(self) -> bool:
        """Returns True if experience years were confidently extracted or confirmed."""
        return self.total_experience_years is not None

    @property
    def all_confirmed_skills(self) -> List[str]:
        """Flattened list of all verified skills from the resume."""
        skills = set()
        for group in [
            self.languages,
            self.frameworks,
            self.apis_and_auth,
            self.messaging_and_cache,
            self.databases,
            self.devops_and_tools,
        ]:
            for s in group:
                skills.add(s)
        return sorted(list(skills))

    @property
    def core_search_skill_combinations(self) -> List[str]:
        """
        Bounded, high-yield skill combinations for cross-source querying.
        Focuses on resume-skill-first discovery rather than exact role titles alone.
        """
        return [
            "Java Spring Boot",
            "Java Microservices",
            "Java Backend",
            "Java REST API",
            "Java Kafka",
            "Java PostgreSQL",
            "Spring Boot Microservices",
            "Spring Boot AWS",
            "Java Developer",
            "Backend Developer Java",
        ]


DEFAULT_TARGET_CITIES: List[str] = [
    "bengaluru", "bangalore",
    "delhi", "new delhi",
    "gurgaon", "gurugram",
    "noida", "greater noida",
    "hyderabad", "secunderabad",
    "pune",
    "mumbai", "navi mumbai", "thane",
]


def extract_deterministic_profile(
    extracted_text: str,
    candidate_name: str = "",
) -> CandidateProfile:
    """
    Deterministic text-based candidate profile extractor when Gemini AI is not configured or disabled.
    Guarantees:
    1. ZERO INVENTED FACTS: Missing experience years are represented as None (unknown), NEVER 2.0.
    2. Strict separation: Extracted skills vs. inferred capabilities vs. editable search preferences.
    3. Editable defaults: Search preferences are explicitly marked as 'editable_default'.
    4. Confirmation required: experience_confirmed is always False for newly extracted text.
    """
    import re

    text_lower = extracted_text.lower()

    # 1. Detect candidate name if not provided
    name = candidate_name.strip()
    if not name:
        first_line = extracted_text.strip().split("\n")[0].strip() if extracted_text else ""
        if first_line and len(first_line.split()) <= 4 and not re.search(r"[@|/{}\\]", first_line):
            name = first_line
        else:
            name = "Candidate"

    # 2. Extract technical skills present in text
    known_languages = [
        ("java", "Java"), ("python", "Python"), ("kotlin", "Kotlin"),
        ("sql", "SQL"), ("c++", "C++"), ("c#", "C#"), ("golang", "Go"), ("go", "Go"),
        ("typescript", "TypeScript"), ("javascript", "JavaScript"),
    ]
    extracted_languages = [label for kw, label in known_languages if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    known_frameworks = [
        ("spring boot", "Spring Boot"), ("spring", "Spring"), ("hibernate", "Hibernate"),
        ("jpa", "JPA"), ("django", "Django"), ("fastapi", "FastAPI"), ("flask", "Flask"),
        ("react", "React"), ("angular", "Angular"), ("express", "Express"),
    ]
    extracted_frameworks = [label for kw, label in known_frameworks if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    known_dbs = [
        ("postgresql", "PostgreSQL"), ("mysql", "MySQL"), ("oracle", "Oracle"),
        ("mongodb", "MongoDB"), ("redis", "Redis"), ("cassandra", "Cassandra"),
    ]
    extracted_dbs = [label for kw, label in known_dbs if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    known_messaging = [
        ("kafka", "Kafka"), ("activemq", "ActiveMQ"), ("rabbitmq", "RabbitMQ"),
        ("jms", "JMS"), ("sqs", "SQS"),
    ]
    extracted_messaging = [label for kw, label in known_messaging if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    known_devops = [
        ("docker", "Docker"), ("kubernetes", "Kubernetes"), ("aws", "AWS"),
        ("jenkins", "Jenkins"), ("git", "Git"), ("ci/cd", "CI/CD"), ("linux", "Linux"),
    ]
    extracted_devops = [label for kw, label in known_devops if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    known_apis = [
        ("microservices", "Microservices"), ("rest", "REST"), ("restful", "RESTful APIs"),
        ("oauth2", "OAuth2"), ("jwt", "JWT"), ("api gateway", "API Gateway"),
    ]
    extracted_apis = [label for kw, label in known_apis if re.search(r"\b" + re.escape(kw) + r"\b", text_lower)]

    # 3. Detect experience years from explicit text patterns
    # Matches: '2 years of experience', '3.5 yrs experience', 'experience: 4 years'
    exp_pattern = re.search(
        r"(?:total\s+)?(?:experience|exp)[\s:]+(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)|"
        r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:total\s+)?(?:experience|exp)",
        text_lower,
    )
    if exp_pattern:
        val = exp_pattern.group(1) or exp_pattern.group(2)
        total_exp = float(val) if val else None
    else:
        total_exp = None  # Unknown/unspecified! Never invent 2.0!

    # 4. Inferred capabilities based on co-occurring technologies
    inferred_caps = []
    if "Microservices" in extracted_apis and "Kafka" in extracted_messaging:
        inferred_caps.append("Event-driven microservices architecture")
    if "Docker" in extracted_devops and "Kubernetes" in extracted_devops:
        inferred_caps.append("Containerized application orchestration")
    if "PostgreSQL" in extracted_dbs and "Spring Boot" in extracted_frameworks:
        inferred_caps.append("Relational data modeling and persistence")

    # 5. Suggested search preferences (initialized as editable defaults, NOT resume facts)
    if "Java" in extracted_languages:
        suggested_title = "Java Developer"
    elif "Python" in extracted_languages:
        suggested_title = "Python Developer"
    else:
        suggested_title = "Software Engineer"

    # Search boundaries initialized as editable user preferences
    if total_exp is not None:
        range_min = max(0.0, total_exp - 1.0)
        range_max = total_exp + 1.0
        hard_limit = total_exp + 2.0
    else:
        range_min = 0.0
        range_max = 3.0
        hard_limit = 4.0

    return CandidateProfile(
        name=name,
        total_experience_years=total_exp,
        experience_confirmed=False,  # Unconfirmed until user explicitly verifies
        languages=extracted_languages,
        frameworks=extracted_frameworks,
        apis_and_auth=extracted_apis,
        messaging_and_cache=extracted_messaging,
        databases=extracted_dbs,
        devops_and_tools=extracted_devops,
        inferred_capabilities=inferred_caps,
        target_title=suggested_title,
        experience_range_min=range_min,
        experience_range_max=range_max,
        hard_limit_years=hard_limit,
        target_cities=DEFAULT_TARGET_CITIES,
        remote_preference="flexible",
        preferences_source="editable_default",
    )


# Verified default profile for Abhishek Dwivedi with confirmed personal facts & preferences
DEFAULT_CANDIDATE_PROFILE = CandidateProfile(
    name="Abhishek Dwivedi",
    total_experience_years=2.0,
    experience_confirmed=True,
    target_title="Java Backend Developer",
    experience_range_min=0.0,
    experience_range_max=3.0,
    hard_limit_years=4.0,
    languages=["Java", "Java 17", "SQL", "Python", "Kotlin"],
    frameworks=["Spring Boot", "Spring MVC", "Spring Batch", "Spring Cloud", "Hibernate", "JPA"],
    apis_and_auth=["REST", "RESTful APIs", "Microservices", "API Gateway", "OAuth2", "JWT", "SOAP"],
    messaging_and_cache=["Apache Kafka", "Kafka", "JMS", "ActiveMQ", "Redis"],
    databases=["PostgreSQL", "MySQL", "Oracle SQL", "Oracle"],
    devops_and_tools=["Docker", "Kubernetes", "AWS", "Jenkins", "CI/CD", "Maven", "JUnit", "Git", "Swagger", "Postman"],
    inferred_capabilities=["High-throughput backend microservices", "Relational data modeling", "Event-driven architecture"],
    target_cities=DEFAULT_TARGET_CITIES,
    preferences_source="user_configured",
)
