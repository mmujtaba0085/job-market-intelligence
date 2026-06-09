"""
config/taxonomy.py
──────────────────
SKILL_TAXONOMY  — canonical skill categories + their member skills.
SKILL_SYNONYMS  — maps abbreviations/aliases → normalized skill names.

Rules:
- All keys/values are lowercase.
- Adding categories or synonyms here requires no code changes elsewhere.
- To disable a category, prefix its key with "_" (e.g., "_deprecated_tools").
"""

SKILL_TAXONOMY: dict[str, list[str]] = {
    # ── Cloud platforms ───────────────────────────────────────────────────────
    "cloud": [
        "aws", "azure", "gcp", "google cloud", "amazon web services",
        "microsoft azure", "cloud computing", "serverless",
    ],

    # ── Programming languages ──────────────────────────────────────────────────
    "programming": [
        "python", "r", "java", "scala", "c++", "c#", "julia",
        "sql", "bash", "go", "rust", "typescript", "javascript",
        "kotlin", "swift",
    ],

    # ── ML / AI core ─────────────────────────────────────────────────────────
    "ml_core": [
        "machine learning", "deep learning", "neural network",
        "reinforcement learning", "supervised learning", "unsupervised learning",
        "transfer learning", "federated learning",
        "multimodal", "multi-modal",
    ],

    # ── LLMs & Generative AI ──────────────────────────────────────────────────
    "generative_ai": [
        "large language model", "llm", "llm engineer", "gpt", "chatgpt", "openai",
        "prompt engineering", "rag", "retrieval augmented generation",
        "retrieval augmented", "fine-tuning", "finetuning",
        "langchain", "llamaindex", "llama", "llama index",
        "hugging face", "transformers",
        # Diffusion / image generation
        "diffusion", "stable diffusion", "diffusion model",
        "image generation", "text-to-image",
        # Gen AI umbrella terms
        "generative ai", "gen ai", "generative model",
        # Agents & tooling
        "ai agent", "agentic", "function calling", "tool use",
        "claude", "gemini", "mistral",
    ],

    # ── ML frameworks ─────────────────────────────────────────────────────────
    "ml_frameworks": [
        "pytorch", "tensorflow", "keras", "scikit-learn", "sklearn",
        "xgboost", "lightgbm", "catboost", "jax",
    ],

    # ── Data engineering tools ────────────────────────────────────────────────
    "data_tools": [
        "pandas", "numpy", "spark", "apache spark", "airflow",
        "apache airflow", "dbt", "kafka", "apache kafka",
        "flink", "databricks", "snowflake", "duckdb",
        "data pipeline", "etl", "elt",
    ],

    # ── MLOps / ML infrastructure ─────────────────────────────────────────────
    "mlops": [
        "mlflow", "kubeflow", "sagemaker", "vertex ai",
        "docker", "kubernetes", "k8s", "ci/cd", "github actions",
        "terraform", "mlops", "model monitoring",
        "ml platform", "ml infrastructure", "feature store",
        "model serving", "triton", "bentoml", "ray",
    ],

    # ── Backend & distributed systems ─────────────────────────────────────────
    "backend": [
        "microservices", "grpc", "rest api", "graphql", "api design",
        "distributed systems", "service mesh", "istio",
        "message queue", "event driven", "event-driven",
        "node.js", "nodejs", "fastapi", "django", "flask", "spring boot",
        "celery", "websocket",
    ],

    # ── DevOps / platform engineering ─────────────────────────────────────────
    "devops": [
        "devops", "site reliability", "sre", "platform engineering",
        "infrastructure as code", "helm", "argocd", "jenkins",
        "github actions", "gitlab ci", "circleci",
        "prometheus", "grafana", "datadog", "observability",
        "devsecops", "shift left",
    ],

    # ── Security ──────────────────────────────────────────────────────────────
    "security": [
        "appsec", "application security", "devsecops",
        "penetration testing", "pen testing", "vulnerability",
        "sast", "dast", "owasp", "zero trust",
    ],

    # ── Frontend / full-stack ─────────────────────────────────────────────────
    "frontend": [
        "react", "vue", "angular", "next.js", "nextjs",
        "html", "css", "tailwind", "webpack", "vite",
        "react native", "flutter",
    ],

    # ── Databases ─────────────────────────────────────────────────────────────
    "databases": [
        "postgresql", "postgres", "mysql", "mongodb", "redis",
        "elasticsearch", "pinecone", "weaviate", "chroma",
        "vector database", "nosql", "sqlite", "cassandra",
    ],

    # ── Computer vision ───────────────────────────────────────────────────────
    "computer_vision": [
        "computer vision", "opencv", "image recognition",
        "object detection", "yolo", "image segmentation",
        "convolutional neural network", "cnn",
    ],

    # ── NLP ───────────────────────────────────────────────────────────────────
    "nlp": [
        "natural language processing", "nlp", "text mining",
        "sentiment analysis", "named entity recognition", "ner",
        "text classification", "speech recognition",
        "nlp engineer",
    ],

    # ── Soft / domain skills ──────────────────────────────────────────────────
    "soft_skills": [
        "communication", "leadership", "teamwork", "problem solving",
        "research", "agile", "scrum",
    ],
}


SKILL_SYNONYMS: dict[str, str] = {
    # abbreviations → normalized
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "tf": "terraform",
    "gpt-4": "gpt",
    "gpt4": "gpt",
    "gpt-3": "gpt",
    "gpt3": "gpt",
    "aws lambda": "aws",
    "ec2": "aws",
    "s3": "aws",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "rag": "retrieval augmented generation",
    "pg": "postgresql",
    "pgsql": "postgresql",
    "cv": "computer vision",
    "dl": "deep learning",
    "ml": "machine learning",
    "pytorch lightning": "pytorch",
    "hf": "hugging face",
    "huggingface": "hugging face",
    "llamaindex": "llama index",
    "langchain": "langchain",
    # New
    "gen ai": "generative ai",
    "genai": "generative ai",
    "sd": "stable diffusion",
    "sdxl": "stable diffusion",
    "nodejs": "node.js",
    "node js": "node.js",
    "next.js": "nextjs",
    "multi-modal": "multimodal",
    "grpc": "grpc",
    "rest": "rest api",
    "finetuning": "fine-tuning",
    "fine tuning": "fine-tuning",
    "rag pipeline": "retrieval augmented generation",
}
