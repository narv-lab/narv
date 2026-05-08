import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class AppConfig:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.data = self._load_yaml()
        
    def _load_yaml(self) -> dict:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f) or {}

    @property
    def openrouter_api_key(self) -> str:
        return os.environ.get("OPENROUTER_API_KEY", "")

    @property
    def google_api_key(self) -> str:
        return os.environ.get("GOOGLE_API_KEY", "")

    @property
    def openrouter_budget_daily(self) -> int:
        return int(os.environ.get("OPENROUTER_BUDGET_DAILY", self.data.get("api", {}).get("openrouter_budget_daily", 1000)))

    @property
    def api_retry_base_delay_sec(self) -> float:
        return float(os.environ.get("RETRY_BASE_DELAY_SEC", self.data.get("api", {}).get("retry_base_delay_sec", 1.0)))

    @property
    def api_max_retries(self) -> int:
        return int(self.data.get("api", {}).get("max_retries", 3))

    @property
    def api_model_fast(self) -> str:
        return self.data.get("api", {}).get("model_fast", "openai/gpt-3.5-turbo")

    @property
    def api_model_slow(self) -> str:
        return self.data.get("api", {}).get("model_slow", "openai/gpt-4o")

    @property
    def api_model_embed(self) -> str:
        return self.data.get("api", {}).get("model_embed", "openai/text-embedding-ada-002")

    @property
    def cognitive_urgency_threshold_fast(self) -> float:
        return float(self.data.get("cognitive", {}).get("urgency_threshold_fast", 0.8))

    @property
    def cognitive_load_max(self) -> float:
        return float(self.data.get("cognitive", {}).get("cognitive_load_max", 0.8))

    @property
    def cognitive_fast_tokens(self) -> int:
        return int(self.data.get("cognitive", {}).get("fast_tokens", 512))

    @property
    def cognitive_slow_tokens(self) -> int:
        return int(self.data.get("cognitive", {}).get("slow_tokens", 2048))

    @property
    def cognitive_dmn_tokens(self) -> int:
        return int(self.data.get("cognitive", {}).get("dmn_tokens", 1024))

    @property
    def cognitive_sleep_tokens(self) -> int:
        return int(self.data.get("cognitive", {}).get("sleep_tokens", 2048))

    @property
    def system_idle_cycles_for_sleep(self) -> int:
        return int(self.data.get("system", {}).get("idle_cycles_for_sleep", 5))

    @property
    def system_max_cognition_failures(self) -> int:
        return int(self.data.get("system", {}).get("max_cognition_failures", 3))

    @property
    def system_max_resume_loop(self) -> int:
        return int(self.data.get("system", {}).get("max_resume_loop", 10))

    @property
    def system_cycle_interval_seconds(self) -> float:
        return float(os.environ.get("CYCLE_INTERVAL_SECONDS", self.data.get("system", {}).get("cycle_interval_seconds", 5.0)))

    @property
    def system_session_memory_keep_count(self) -> int:
        return int(self.data.get("system", {}).get("session_memory_keep_count", 20))

    @property
    def system_context_window_size(self) -> int:
        return int(self.data.get("system", {}).get("context_window_size", 40))

    @property
    def perceptor_urgency_anomaly(self) -> float:
        return float(self.data.get("perceptor", {}).get("urgency_anomaly", 0.9))

    @property
    def perceptor_urgency_normal(self) -> float:
        return float(self.data.get("perceptor", {}).get("urgency_normal", 0.2))

    @property
    def perceptor_urgency_shell_output(self) -> float:
        return float(self.data.get("perceptor", {}).get("urgency_shell_output", 0.3))

    @property
    def actor_sandbox_root(self) -> str:
        return os.environ.get("ACTOR_SANDBOX_ROOT", self.data.get("actor", {}).get("sandbox_root", "/tmp/narv_sandbox"))

    @property
    def actor_allowed_commands(self) -> list[str]:
        val = os.environ.get("ACTOR_ALLOWED_COMMANDS", self.data.get("actor", {}).get("allowed_commands", "echo,ls,cat,pwd,python,python3"))
        if isinstance(val, str):
            return val.split(",")
        return val

    @property
    def actor_timeout_ms(self) -> int:
        return int(self.data.get("actor", {}).get("timeout_ms", 30_000))

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    @property
    def memory_redis_url(self) -> str:
        return os.environ.get("MEMORY_REDIS_URL", self.data.get("memory", {}).get("redis_url", "redis://localhost:6379/0"))

    @property
    def memory_chroma_persist_dir(self) -> str:
        return os.environ.get("MEMORY_CHROMA_DIR", self.data.get("memory", {}).get("chroma_persist_dir", "./data/chroma"))

    @property
    def memory_neo4j_uri(self) -> str:
        return os.environ.get("MEMORY_NEO4J_URI", self.data.get("memory", {}).get("neo4j_uri", "bolt://localhost:7687"))

    @property
    def memory_neo4j_user(self) -> str:
        return os.environ.get("MEMORY_NEO4J_USER", self.data.get("memory", {}).get("neo4j_user", "neo4j"))

    @property
    def memory_neo4j_password(self) -> str:
        return os.environ.get("MEMORY_NEO4J_PASSWORD", self.data.get("memory", {}).get("neo4j_password", "narv_memory_2026"))

    @property
    def memory_cosine_threshold(self) -> float:
        return float(self.data.get("memory", {}).get("cosine_threshold", 0.75))

    @property
    def memory_top_n_default(self) -> int:
        return int(self.data.get("memory", {}).get("top_n_default", 5))

    @property
    def memory_redis_max_entries(self) -> int:
        return int(self.data.get("memory", {}).get("redis_max_entries", 500))

    @property
    def memory_redis_ttl_seconds(self) -> int:
        return int(self.data.get("memory", {}).get("redis_ttl_seconds", 86400))

    @property
    def memory_chroma_max_entries(self) -> int:
        return int(self.data.get("memory", {}).get("chroma_max_entries", 10000))

    @property
    def memory_chroma_eviction_batch(self) -> int:
        return int(self.data.get("memory", {}).get("chroma_eviction_batch", 500))

    @property
    def memory_chroma_eviction_threshold(self) -> float:
        return float(self.data.get("memory", {}).get("chroma_eviction_threshold", 0.7))

    @property
    def memory_neo4j_max_nodes(self) -> int:
        return int(self.data.get("memory", {}).get("neo4j_max_nodes", 5000))

    @property
    def memory_neo4j_archive_threshold(self) -> int:
        return int(self.data.get("memory", {}).get("neo4j_archive_threshold", 4000))

    @property
    def memory_neo4j_min_importance(self) -> float:
        return float(self.data.get("memory", {}).get("neo4j_min_importance", 0.3))

config = AppConfig()
