import os
from pathlib import Path
from dotenv import load_dotenv

from src.core.logger import setup_logger
from src.core.config import config

logger = setup_logger("di_container")


class DIContainer:
    """
    Dependency Injection Container (Service Locator Pattern).
    Centralizes the instantiation of all modules and the wiring of their dependencies.
    """
    def __init__(self):
        load_dotenv()
        self.kernel = None
        self.llm_gateway = None
        self.cognitive_engine = None
        self.memory = None
        self.perceptor = None
        self.actor = None

    def wire(self):
        """Instantiates all modules and wires their dependencies.

        Wiring Order (from least dependent to most dependent):
        1. LLMGateway      — External API (No dependencies)
        2. Memory          — Memory management (No dependencies)
        3. Perceptor       — Perception collection (No dependencies)
        4. Actor           — Action execution (No dependencies)
        5. CognitiveEngine — Inference (No dependencies: via kernel protocol)
        6. Kernel          — Orchestrator (Injects all of the above)
        """
        logger.info("DIContainer: wiring modules...")

        # 1. LLMGateway
        from src.llm_gateway.llm_gateway import LLMGateway
        self.llm_gateway = LLMGateway(api_key=config.openrouter_api_key)
        logger.info("  [OK] LLMGateway")

        # 2. Memory
        from src.memory.memory import Memory
        self.memory = Memory(
            redis_url=config.memory_redis_url,
            chroma_persist_dir=config.memory_chroma_persist_dir,
            neo4j_uri=config.memory_neo4j_uri,
            neo4j_user=config.memory_neo4j_user,
            neo4j_password=config.memory_neo4j_password,
        )
        logger.info("  [OK] Memory")

        # 3. Perceptor
        from src.perceptor.perceptor import Perceptor
        watch_dirs_raw = os.getenv("PERCEPTOR_WATCH_DIRS", "")
        watch_dirs = [d for d in watch_dirs_raw.split(",") if d] if watch_dirs_raw else []
        self.perceptor = Perceptor(watch_dirs=watch_dirs)
        logger.info("  [OK] Perceptor")

        # 4. Actor
        from src.actor.actor import Actor
        self.actor = Actor(
            sandbox_root=Path(config.actor_sandbox_root),
            allowed_commands=config.actor_allowed_commands
        )
        logger.info("  [OK] Actor")

        # 5. CognitiveEngine
        from src.cognitive_engine.cognitive_engine import CognitiveEngine
        self.cognitive_engine = CognitiveEngine()
        logger.info("  [OK] CognitiveEngine")

        # 6. Kernel (Decomposed)
        from src.kernel import KernelStateManager, KernelMediator, KernelOrchestrator
        self.kernel_state = KernelStateManager()
        self.kernel_mediator = KernelMediator(
            llm_gateway=self.llm_gateway,
            cognitive_engine=self.cognitive_engine,
            memory=self.memory,
            perceptor=self.perceptor,
            actor=self.actor,
        )
        self.kernel = KernelOrchestrator(
            state_manager=self.kernel_state,
            mediator=self.kernel_mediator,
        )
        logger.info("  [OK] Kernel (Decomposed)")

        logger.info("DIContainer: all modules wired successfully.")


container = DIContainer()

