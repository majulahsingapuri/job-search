# config/resumes.py
# Your 3 resume variants as structured dicts.
# The routing agent uses these to decide which to send for each job.
# Update the `summary` and `highlights` fields as you refine each variant.

RESUME_VARIANTS = {

    "ml_engineer": {
        "label": "ML Engineer",
        "file": "Bhargav_Resume_MLEng.pdf",   # filename you'll use when applying
        "target_roles": ["ML Engineer", "AI Engineer", "MLOps Engineer", "Applied ML"],
        "summary": (
            "Production-focused ML/AI engineer with experience deploying LLM-powered systems, "
            "REST APIs, and monitoring pipelines at Biogen. Re-architected Flask backend (10% → 80% test coverage), "
            "integrated LangSmith for token monitoring (34% cost reduction), and built summarization "
            "systems that cut turnaround from 2 weeks to 2 minutes. Strong in Python, Docker, CI/CD, "
            "AWS/GCP, and MLOps tooling."
        ),
        "highlights": [
            "LangSmith monitoring integration, 34% inference cost reduction",
            "Flask backend modularization, 10% → 80% test coverage",
            "LLM summarization pipeline: 2 weeks → 2 minutes turnaround",
            "Claude Code / OpenAI Codex playbook adopted by 10+ engineers",
            "Docker, CI/CD, REST APIs, Terraform, Airflow",
        ],
        "keywords": [
            "mlops", "deployment", "api", "backend", "docker", "production",
            "infrastructure", "pipeline", "langsmith", "monitoring", "flask",
            "engineer", "software", "system design", "ci/cd", "cloud"
        ],
    },

    "data_scientist": {
        "label": "Data Scientist",
        "file": "Bhargav_Resume_DS.pdf",
        "target_roles": ["Data Scientist", "Applied Scientist", "Quantitative Analyst", "Analytics Engineer"],
        "summary": (
            "Data scientist with production experience building analytics dashboards, ETL pipelines, "
            "and ML models across finance and healthcare. Built Splunk dashboard processing 10M+ daily API records "
            "(incident diagnosis: 10 hours → 10 seconds) at UOB. Developed Airflow/Snowflake ETL ingesting 1M+ "
            "investment records at Vertex Holdings. Fine-tuned ViTs for cancer cell detection (99% accuracy). "
            "Strong in SQL, Pandas, statistical modeling, and data visualization."
        ),
        "highlights": [
            "Splunk dashboard: 10M+ daily records, incident time 10h → 10s",
            "Airflow ETL: 1M+ records into Snowflake for investment intelligence",
            "Cancer cell ViT: 99% accuracy, Monte-Carlo Dropout uncertainty",
            "CRM + relationship graph for investor network mapping",
            "SQL (PostgreSQL, Snowflake), Pandas, Tableau, PowerQuery",
        ],
        "keywords": [
            "data scientist", "analytics", "sql", "statistical", "modelling",
            "tableau", "snowflake", "etl", "visualization", "business intelligence",
            "experiment", "a/b test", "forecasting", "insight", "reporting", "analyst"
        ],
    },

    "ai_researcher": {
        "label": "AI Researcher",
        "file": "Bhargav_Resume_Research.pdf",
        "target_roles": ["AI Research Intern", "ML Research Intern", "Research Engineer", "Research Scientist"],
        "summary": (
            "AI/ML researcher focused on model interpretability and LLM safety. Currently investigating "
            "transliteration as a jailbreak attack vector on Gemma 3 models and building mitigations via "
            "linear probes and SAEs (Sparse Autoencoders). Benchmarking Cross-coders for binary decision tasks. "
            "MS in AI at Northeastern; BS in Data Science & AI from NTU. Hands-on with TransformerLens, SAELens, "
            "PyTorch, and Hugging Face. Strong interest in mechanistic interpretability and alignment."
        ),
        "highlights": [
            "Transliteration jailbreak research on Gemma 3 + SAE-based mitigations",
            "Cross-coder interpretability benchmarking on binary decision datasets",
            "TransformerLens, SAELens, linear probes",
            "Cancer cell ViT with Monte-Carlo Dropout uncertainty quantification",
            "MS AI @ Northeastern; BS DSAI @ NTU Singapore",
        ],
        "keywords": [
            "research", "researcher", "interpretability", "alignment", "safety",
            "mechanistic", "llm", "transformer", "attention", "representation",
            "probe", "sae", "sparse autoencoder", "novel", "paper", "publication",
            "phd", "lab", "academic", "science", "theory", "benchmark"
        ],
    },
}
