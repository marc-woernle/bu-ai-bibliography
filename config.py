"""
BU AI Bibliography Harvester — Configuration
=============================================
Centralized config for all data sources, search terms, and parameters.
"""

# ── BU Institutional Identifiers ──────────────────────────────────────────────
BU_ROR_ID = "https://ror.org/05qwgg493"
BU_GRID_ID = "grid.189504.1"
BU_OPENALEX_INSTITUTION_ID = "I40120149"  # OpenAlex ID for Boston University

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

# Combined flat list for simple matching
ALL_AI_KEYWORDS = AI_KEYWORDS_PRIMARY + AI_KEYWORDS_SECONDARY

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

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "data"
LOG_DIR = "logs"
