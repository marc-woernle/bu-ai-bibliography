"""
BU AI Bibliography Harvester — Configuration
=============================================
Centralized config for all data sources, search terms, and parameters.
"""

import os

# ── BU Institutional Identifiers ──────────────────────────────────────────────
BU_ROR_ID = "https://ror.org/05qwgg493"
BU_GRID_ID = "grid.189504.1"
BU_OPENALEX_INSTITUTION_ID = "I40120149"  # OpenAlex ID for Boston University

# ── Canonical Data Sources ────────────────────────────────────────────────────
# Single source of truth for sources displayed on the site, README, and GitHub
# repo description. NBER and arXiv are harvested via OpenAlex filters so they
# don't get distinct source tags in the data (count of `all_sources` tags = 11).
# This list (13) is the authoritative project-level count.
DATA_SOURCES = [
    "OpenAlex",
    "PubMed",
    "DBLP",
    "SSRN",
    "NBER",
    "Scholarly Commons",
    "OpenBU",
    "NIH Reporter",
    "NSF Awards",
    "arXiv",
    "CrossRef",
    "Semantic Scholar",
    "bioRxiv",
]

# ── Classification Model (display name) ───────────────────────────────────────
CLASSIFIER_DISPLAY_NAME = "Sonnet 4.6"

# ── OpenAlex AI-Related Concept IDs ───────────────────────────────────────────
# These are OpenAlex concept IDs covering AI broadly + key application domains.
# We cast a VERY wide net here — classification happens downstream.
OPENALEX_AI_CONCEPT_IDS = [
    "C154945302",   # Artificial intelligence
    "C119857082",   # Machine learning
    "C31972630",    # Computer vision
    "C204321447",   # Natural language processing
    "C108583219",   # Deep learning
    "C50644808",    # Artificial neural network
    "C126322002",   # Reinforcement learning
    "C4249254",     # Robotics
    "C23123220",    # Data mining
    "C124101348",   # Data science
    "C41008148",    # Computer science (broad — will catch edge cases)
    "C136764020",   # World Wide Web (catches web AI, search, recommendation)
    "C105795698",   # Statistics (catches statistical ML)
    "C77088390",    # Computational biology
    "C71924100",    # Medicine (catches medical AI)
    "C17744445",    # Political science (catches AI policy/governance)
    "C111919701",   # Law (catches AI law/regulation)
    "C162324750",   # Economics (catches AI economics)
    "C15744967",    # Psychology (catches computational psych, AI ethics)
    "C127413603",   # Engineering (catches AI engineering applications)
    "C121332964",   # Physics (catches computational physics, ML in physics)
]

# ── Keyword Search Terms ──────────────────────────────────────────────────────
# Two tiers: PRIMARY terms catch obvious AI work; SECONDARY terms catch
# applied/interdisciplinary AI work that might not be concept-tagged.
AI_KEYWORDS_PRIMARY = [
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural network",
    "natural language processing",
    "computer vision",
    "large language model",
    "generative AI",
    "generative artificial intelligence",
    "reinforcement learning",
    "transformer model",
    "GPT",
    "BERT",
    "LLM",
    "chatbot",
    "autonomous system",
    "autonomous vehicle",
    "robotics",
    "algorithmic",
    "algorithm bias",
    "algorithmic fairness",
    "algorithmic accountability",
    "algorithmic decision",
    "automated decision",
    "predictive model",
    "predictive analytics",
    "recommendation system",
    "knowledge graph",
    "expert system",
    "intelligent system",
    "intelligent agent",
    "multi-agent",
    "AI governance",
    "AI regulation",
    "AI policy",
    "AI ethics",
    "AI safety",
    "AI alignment",
    "responsible AI",
    "explainable AI",
    "interpretable machine learning",
    "fairness in machine learning",
    "foundation model",
    "diffusion model",
    "text-to-image",
    "speech recognition",
    "image recognition",
    "object detection",
    "sentiment analysis",
    "named entity recognition",
    "transfer learning",
    "few-shot learning",
    "zero-shot",
    "federated learning",
    "synthetic data",
    "data augmentation",
]

AI_KEYWORDS_SECONDARY = [
    "computational",          # catches "computational linguistics", "computational law", etc.
    "automated",              # catches "automated contract review", "automated diagnosis"
    "classification model",
    "clustering algorithm",
    "random forest",
    "support vector machine",
    "gradient boosting",
    "convolutional",
    "recurrent neural",
    "attention mechanism",
    "word embedding",
    "semantic similarity",
    "topic model",
    "Bayesian network",
    "Markov",
    "Monte Carlo",
    "optimization algorithm",
    "genetic algorithm",
    "evolutionary computation",
    "swarm intelligence",
    "fuzzy logic",
    "image segmentation",
    "medical imaging",
    "clinical decision support",
    "precision medicine",
    "drug discovery",
    "legal tech",
    "legal technology",
    "computational law",
    "RegTech",
    "FinTech",
    "robo-advisor",
    "smart contract",
    "blockchain",
    "Internet of Things",
    "edge computing",
    "natural language generation",
    "question answering",
    "information retrieval",
    "information extraction",
    "text mining",
    "bioinformatics",
    "proteomics",
    "genomics machine learning",
    "neural architecture",
    "model compression",
    "quantization",
    "knowledge distillation",
    "prompt engineering",
    "in-context learning",
    "retrieval augmented generation",
    "RAG",
    "vector database",
    "embedding model",
    # Crypto/security/privacy (AI-adjacent, missed in initial harvest)
    "differential privacy",
    "differentially private",
    "secure computation",
    "secure multi-party",
    "zero-knowledge",
    "zero knowledge",
    "formal verification",
    "mechanism design",
    "homomorphic encryption",
    "federated learning",
    "privacy-preserving",
    "privacy preserving",
    "adversarial robustness",
    "adversarial attack",
    "malware detection",
    "intrusion detection",
    "anomaly detection",
    # Theoretical ML (often uses different vocabulary)
    "bandit",
    "regret bound",
    "online learning",
    "stochastic optimization",
    "convex optimization",
    "distributional robust",
    "sample complexity",
    "PAC learning",
    "reward model",
    "reinforcement learning from human",
    "RLHF",
]

# ── Vocabulary added 2026-08 after measuring the filter against master ────────
# Re-running the pre-filter over the 11,903 Sonnet-confirmed papers showed 3,018
# of them (25.4%) failing the keyword stage. These are the terms that rescue the
# most, measured individually against that miss set. They are ordinary applied-ML
# words, not LLM-era jargon: the list had "robotics" but not "robot", "image
# segmentation" but not "segmentation", "classification model" but not
# "classifier", "transformer model" but not "transformer".
AI_KEYWORDS_APPLIED = [
    "robot",                     # rescues 140
    "segmentation",              # 119
    "classifier",                # 84
    "robotic",                   # 50
    "prediction model",          # 25
    "training data",             # 24
    "feature selection",         # 24
    "supervised learning",       # 17
    "multimodal",                # 17
    "pattern recognition",       # 16
    "risk prediction",           # 15
    "gradient descent",          # 15
    "decision tree",             # 14
    "electronic health record",  # 14
    "language model",            # 13
    "feature extraction",        # 13
    "transformer",               # 11
    "unsupervised learning",
    "self-supervised",
    "predictive analytics",
    "random forest",
    "support vector",
    "convolutional",
    "recurrent neural",
]

# Forward-looking 2025-26 vocabulary. Each of these rescues 0-3 papers today,
# because a 2026 paper using them almost always also says "LLM" or "foundation
# model" -- they are here so the filter doesn't rot as the vocabulary moves on,
# not because they buy recall now.
AI_KEYWORDS_FRONTIER = [
    "agentic",
    "ai agent",
    "chain-of-thought",
    "mixture-of-experts",
    "state space model",
    "vision-language",
    "mechanistic interpretability",
    "interpretability",
    "hallucination",
    "jailbreak",
    "test-time",
    "scaling law",
    "retrieval-augmented",
    "diffusion model",
    "foundation model",
    "fine-tuning",
    "prompt engineering",
    "model evaluation benchmark",
]

# ── Vocabulary the old list could not see ────────────────────────────────
# Measured, not guessed. Of 9,945 papers Sonnet had already confirmed as AI,
# the keyword arm matched 80.3% -- and the 1,259 it missed were not random.
# They clustered into families the list simply had no words for: classical
# computer vision from before anyone said "deep learning", BU's own Center for
# Adaptive Systems tradition of neural and cognitive modelling, control and
# formal methods, network science, and computational biology where the method
# is machine learning and every word in the abstract is biology.
#
# Every entry below appears in at least one of those missed papers. Each was
# then checked against 3,877 real BU biomedical abstracts to see how much
# non-AI traffic it would let through: the worst single term admits 1.7% of
# them and most admit none. Together they recover 326 of the 1,259 (25.9%)
# for 86 extra control admissions.
AI_KEYWORDS_UNDERSERVED = [
    "image processing",
    "image analysis",
    "image classification",
    "object tracking",
    "object recognition",
    "saliency",
    "optical flow",
    "pose estimation",
    "face recognition",
    "facial recognition",
    "gesture recognition",
    "scene understanding",
    "visual tracking",
    "particle tracking",
    "image registration",
    "3d reconstruction",
    "view synthesis",
    "radiance field",
    "visual recognition",
    "video analysis",
    "signal processing",
    "speech processing",
    "speech synthesis",
    "acoustic model",
    "speaker recognition",
    "time series forecasting",
    "state estimation",
    "kalman filter",
    "sparse coding",
    "compressed sensing",
    "blind source separation",
    "independent component analysis",
    "principal component analysis",
    "dimensionality reduction",
    "dimension reduction",
    "manifold learning",
    "spectral clustering",
    "semi-supervised",
    "representation learning",
    "feature learning",
    "statistical learning",
    "learning algorithm",
    "regression model",
    "latent variable model",
    "graphical model",
    "hidden markov",
    "maximum likelihood estimation",
    "expectation maximization",
    "variational inference",
    "gaussian process",
    "kernel method",
    "ensemble learning",
    "cross-validation",
    "generalization bound",
    "empirical risk",
    "nearest neighbor",
    "naive bayes",
    "regularization",
    "adaptive resonance",
    "neural model",
    "computational neuroscience",
    "spiking neural",
    "brain-computer interface",
    "brain computer interface",
    "neural decoding",
    "neural encoding",
    "electroencephalogram",
    "cortical model",
    "perceptual learning",
    "cognitive model",
    "neuromorphic",
    "memristive",
    "hebbian",
    "temporal logic",
    "control barrier",
    "optimal control",
    "model predictive control",
    "motion planning",
    "path planning",
    "trajectory optimization",
    "swarm robotics",
    "distributed control",
    "reachability analysis",
    "autonomous navigation",
    "unmanned aerial",
    "cyber-physical",
    "feedback control",
    "adaptive control",
    "complex network",
    "network science",
    "graph neural",
    "link prediction",
    "community detection",
    "network inference",
    "sensor network",
    "graph algorithm",
    "network embedding",
    "social network analysis",
    "network topology",
    "centrality measure",
    "protein structure prediction",
    "protein function prediction",
    "sequence alignment",
    "computational biology",
    "systems biology",
    "gene regulatory network",
    "single-cell analysis",
    "variant calling",
    "molecular docking",
    "virtual screening",
    "drug-target",
    "biomarker prediction",
    "in silico",
    "structural bioinformatics",
    "linear programming",
    "integer programming",
    "combinatorial optimization",
    "scheduling algorithm",
    "network flow",
    "dynamic programming",
    "social media",
    "text classification",
    "topic modeling",
    "computational social science",
    "recommender",
    "collaborative filtering",
    "opinion mining",
    "misinformation detection",
]

# Combined flat list for simple matching. De-duplicated: "federated learning"
# appeared in both PRIMARY and SECONDARY.
ALL_AI_KEYWORDS = list(dict.fromkeys(
    AI_KEYWORDS_PRIMARY + AI_KEYWORDS_SECONDARY
    + AI_KEYWORDS_APPLIED + AI_KEYWORDS_FRONTIER
    + AI_KEYWORDS_UNDERSERVED
))

# ── PubMed MeSH Terms ─────────────────────────────────────────────────────────
PUBMED_MESH_TERMS = [
    "Artificial Intelligence",
    "Machine Learning",
    "Deep Learning",
    "Neural Networks, Computer",
    "Natural Language Processing",
    "Robotics",
    "Expert Systems",
    "Decision Support Systems, Clinical",
    "Image Processing, Computer-Assisted",
    "Pattern Recognition, Automated",
    "Algorithms",
]

# ── arXiv Categories ──────────────────────────────────────────────────────────
ARXIV_AI_CATEGORIES = [
    "cs.AI",    # Artificial Intelligence
    "cs.CL",    # Computation and Language (NLP)
    "cs.CV",    # Computer Vision
    "cs.LG",    # Machine Learning
    "cs.MA",    # Multiagent Systems
    "cs.NE",    # Neural and Evolutionary Computing
    "cs.RO",    # Robotics
    "cs.IR",    # Information Retrieval
    "cs.CR",    # Cryptography and Security (AI security)
    "cs.CY",    # Computers and Society (AI ethics/policy)
    "cs.HC",    # Human-Computer Interaction
    "stat.ML",  # Machine Learning (statistics)
    "eess.IV",  # Image and Video Processing
    "eess.SP",  # Signal Processing
    "q-bio.QM", # Quantitative Methods in Biology
]

# ── BU Schools/Departments to Target for Profile Scraping ─────────────────────
BU_DEPARTMENTS = {
    "CAS Computer Science": "https://www.bu.edu/cs/people/faculty/",
    "CAS Mathematics & Statistics": "https://www.bu.edu/math/people/faculty/",
    "College of Engineering - ECE": "https://www.bu.edu/eng/academics/departments-and-divisions/electrical-and-computer-engineering/people/",
    "College of Engineering - ME": "https://www.bu.edu/eng/academics/departments-and-divisions/mechanical-engineering/people/",
    "College of Engineering - BME": "https://www.bu.edu/eng/academics/departments-and-divisions/biomedical-engineering/people/",
    "College of Engineering - SE": "https://www.bu.edu/eng/academics/departments-and-divisions/systems-engineering/people/",
    "School of Law": "https://www.bu.edu/law/faculty-scholarship/faculty-directory/",
    "Questrom School of Business": "https://www.bu.edu/questrom/faculty-research/faculty-directory/",
    "School of Public Health": "https://www.bu.edu/sph/about/departments/",
    "School of Medicine": "https://www.bumc.bu.edu/busm/research/",
    "CAS Economics": "https://www.bu.edu/econ/people/faculty/",
    "CAS Philosophy": "https://www.bu.edu/philosophy/people/faculty/",
    "CAS Political Science": "https://www.bu.edu/polisci/people/faculty/",
    "Wheelock College of Education": "https://www.bu.edu/wheelock/faculty-staff/",
    "CAS Psychology": "https://www.bu.edu/psych/people/faculty/",
    "CAS Biology": "https://www.bu.edu/biology/people/faculty/",
    "School of Social Work": "https://www.bu.edu/ssw/faculty-staff/faculty/",
    "College of Communication": "https://www.bu.edu/com/faculty-staff/",
    "Pardee School of Global Studies": "https://www.bu.edu/pardeeschool/faculty-staff/",
    "CAS Linguistics": "https://www.bu.edu/linguistics/people/faculty/",
    "Rafik B. Hariri Institute": "https://www.bu.edu/hic/people/",
    "Center for Information & Systems Engineering": "https://www.bu.edu/cise/people/",
    "Faculty of Computing & Data Sciences": "https://www.bu.edu/cds-faculty/",
}

# ── OpenBU (DSpace) ───────────────────────────────────────────────────────────
OPENBU_BASE_URL = "https://open.bu.edu"
OPENBU_REST_API = "https://open.bu.edu/server/api"

# ── Rate Limiting ─────────────────────────────────────────────────────────────
OPENALEX_RATE_LIMIT = 10       # requests/second (polite pool: use mailto)
SEMANTIC_SCHOLAR_RATE_LIMIT = 1  # requests/second without API key
PUBMED_RATE_LIMIT = 3           # requests/second (NCBI guideline)
ARXIV_RATE_LIMIT = 1            # requests/3 seconds (arXiv guideline)
CROSSREF_RATE_LIMIT = 5         # requests/second (polite pool: use mailto)

# ── Contact Email (for polite pools) ──────────────────────────────────────────
# OpenAlex and CrossRef give faster access if you identify yourself
CONTACT_EMAIL = "marcwho@bu.edu"  # ← Set this to your BU email


# ── OpenAlex authentication ──────────────────────────────────────────────────
# OpenAlex started metering in 2026. Filtered list queries -- which is every
# harvest query we make -- cost $0.10 per 1,000 calls against a daily budget
# that resets at midnight UTC. Single-entity GETs (/authors/A123) and
# /autocomplete are free and always have been.
#
# A key is NOT required and costs nothing, but a free key gives 10x the keyless
# budget: $1/day instead of $0.10/day. At per_page=200 that is roughly two
# million works a day against two hundred thousand, and a full BU sweep is
# about a quarter of a million records. Without a key the sweep cannot finish;
# with a free key it costs about three cents.
#
# Get one in about thirty seconds at https://openalex.org/settings/api and set
# OPENALEX_API_KEY in the environment (and in the repo's Actions secrets).
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()


def openalex_headers() -> dict:
    """Headers for every OpenAlex request: polite-pool identification, plus the
    API key when one is configured. Never build these by hand -- a call site
    that misses the key silently spends the keyless budget and then fails with
    'Insufficient budget' instead of saying what is wrong."""
    h = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}
    if OPENALEX_API_KEY:
        h["Authorization"] = f"Bearer {OPENALEX_API_KEY}"
    return h

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "data"
LOG_DIR = "logs"
