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
        "sql", "bash", "go", "rust",
    ],

    # ── ML / AI core ─────────────────────────────────────────────────────────
    "ml_core": [
        "machine learning", "deep learning", "neural network",
        "reinforcement learning", "supervised learning", "unsupervised learning",
        "transfer learning", "federated learning",
    ],

    # ── LLMs & Generative AI ──────────────────────────────────────────────────
    "generative_ai": [
        "large language model", "llm", "gpt", "chatgpt", "openai",
        "prompt engineering", "rag", "retrieval augmented generation",
        "fine-tuning", "langchain", "llamaindex", "llama",
        "hugging face", "transformers",
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
        "flink", "databricks", "snowflake",
    ],

    # ── MLOps / infrastructure ────────────────────────────────────────────────
    "mlops": [
        "mlflow", "kubeflow", "sagemaker", "vertex ai",
        "docker", "kubernetes", "k8s", "ci/cd", "github actions",
        "terraform", "mlops", "model monitoring",
    ],

    # ── Databases ─────────────────────────────────────────────────────────────
    "databases": [
        "postgresql", "postgres", "mysql", "mongodb", "redis",
        "elasticsearch", "pinecone", "weaviate", "chroma",
        "vector database", "nosql",
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
}
